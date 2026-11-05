"""
Public-data utility benchmark for Tumult Analytics.

Queries:
    SUM, AVG, COUNT, HISTOGRAM

Metrics:
    MAE and RMSPE (%)

Outputs:
    raw_results.csv
    summary.csv
    per_bin_metrics.csv
    sample_diagnostics.csv
    experiment.json
    Eight PNG heatmaps

IMPORTANT:
    This benchmark exports exact nonprivate results.
    Run it ONLY on already-public data.

    Sampling, exact diagnostics and the exported benchmark package
    are not a DP-safe publication pipeline for confidential data.
"""

import argparse
import csv
import json
import math
import os
import re
import sys
import threading
import traceback
import webbrowser

from decimal import Decimal
from importlib.metadata import version
from pathlib import Path

import numpy as np
import pandas as pd

from dotenv import load_dotenv


QUERIES = ("SUM", "AVG", "COUNT", "HISTOGRAM")
GENDERS = ("M", "F", "OTHER")


class TeeTextStream:
    def __init__(self, terminal, log_file, lock):
        self.terminal = terminal
        self.log_file = log_file
        self.lock = lock

    def write(self, text):
        with self.lock:
            written = self.terminal.write(text)
            self.log_file.write(text)
            self.log_file.flush()
            return written

    def flush(self):
        with self.lock:
            self.terminal.flush()
            self.log_file.flush()

    def __getattr__(self, name):
        return getattr(self.terminal, name)


class TerminalOutputLog:
    def __init__(self, path):
        self.path = Path(path).expanduser()
        self.lock = threading.Lock()

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.log_file = self.path.open(
            "a",
            encoding="utf-8",
        )
        self.original_stdout = sys.stdout
        self.original_stderr = sys.stderr
        sys.stdout = TeeTextStream(
            self.original_stdout,
            self.log_file,
            self.lock,
        )
        sys.stderr = TeeTextStream(
            self.original_stderr,
            self.log_file,
            self.lock,
        )
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            sys.stdout.flush()
            sys.stderr.flush()
        finally:
            sys.stdout = self.original_stdout
            sys.stderr = self.original_stderr
            self.log_file.close()
        return False


# ============================================================
# Configuration
# ============================================================

def arguments():
    parser = argparse.ArgumentParser(
        description="Benchmark four Tumult queries on PUBLIC dog data."
    )

    parser.add_argument("--env", default=".env")
    parser.add_argument("--plan", action="store_true")
    parser.add_argument("--public-data", action="store_true")

    parser.add_argument("--sizes")
    parser.add_argument("--epsilons")
    parser.add_argument("--repeats", type=int)
    parser.add_argument("--output")

    parser.add_argument(
        "--plot-only",
        metavar="RESULT_DIRECTORY",
        help="Regenerate metrics/charts from existing raw_results.csv.",
    )

    parser.add_argument("--show-charts", action="store_true")

    return parser.parse_args()


def configuration(args):
    load_dotenv(args.env, override=False)

    sizes_text = (
        args.sizes
        if args.sizes is not None
        else os.getenv(
            "SAMPLE_SIZES",
            "1000,2000,3000,4000,5000,6000,7000,8000,9000,9358",
        )
    )

    epsilon_text = (
        args.epsilons
        if args.epsilons is not None
        else os.getenv(
            "EPSILONS",
            "0.1,0.25,0.5,0.75,1.0,1.25,1.5,1.75,"
            "2.0,2.25,2.5,2.75,3.0",
        )
    )

    c = {
        "path": os.getenv(
            "CSV_PATH",
            "./data/nyc_dog_licenses.csv",
        ),
        "sep": os.getenv("CSV_SEP", ";"),
        "birth_column": os.getenv(
            "BIRTH_YEAR_COLUMN", "AnimalBirthYear"
        ),
        "extract_column": os.getenv(
            "EXTRACT_YEAR_COLUMN", "Extract Year"
        ),
        "gender_column": os.getenv(
            "GENDER_COLUMN", "AnimalGender"
        ),
        "sizes": sorted(
            set(int(x.strip()) for x in sizes_text.split(","))
        ),
        "epsilons": sorted(
            set(Decimal(x.strip()) for x in epsilon_text.split(","))
        ),
        "repeats": (
            args.repeats
            if args.repeats is not None
            else int(os.getenv("REPEATS", "30"))
        ),
        "seed": int(os.getenv("SAMPLE_SEED", "42")),
        "low": float(os.getenv("AGE_LOW", "0")),
        "high": float(os.getenv("AGE_HIGH", "25")),
        "master": os.getenv("SPARK_MASTER", "local[2]"),
        "output": Path(
            args.output
            if args.output is not None
            else os.getenv(
                "BENCH_OUTPUT", "./results/four_queries"
            )
        ),
    }

    if not c["sizes"] or min(c["sizes"]) < 1:
        raise ValueError("All sample sizes must be positive.")

    if c["repeats"] < 1:
        raise ValueError("REPEATS must be positive.")

    if (
        not math.isfinite(c["low"])
        or not math.isfinite(c["high"])
        or c["low"] >= c["high"]
    ):
        raise ValueError("Require finite AGE_LOW < AGE_HIGH.")

    for epsilon in c["epsilons"]:
        if (
            not epsilon.is_finite()
            or epsilon <= 0
            or not math.isfinite(float(epsilon))
            or float(epsilon) == 0
        ):
            raise ValueError("Epsilon values must be finite and positive.")

    return c


def print_plan(c):
    cells = (
        len(c["sizes"])
        * len(c["epsilons"])
        * len(QUERIES)
    )

    evaluations = cells * c["repeats"]

    composed_bound = (
        Decimal(len(c["sizes"]) * c["repeats"] * len(QUERIES))
        * sum(c["epsilons"], Decimal(0))
    )

    print("Queries:", ", ".join(QUERIES))
    print("Sample sizes:", c["sizes"])
    print("Epsilons:", [str(e) for e in c["epsilons"]])
    print("Repetitions per cell:", c["repeats"])
    print("Experiment cells:", cells)
    print("DP evaluations:", evaluations)

    print(
        "Conservative additive epsilon bound across the sweep:",
        composed_bound,
    )

    print(
        "Each epsilon is PER QUERY PER REPETITION, "
        "not the budget for the entire sweep."
    )

    print(
        "Public-data benchmark only: exact answers and errors "
        "are exported."
    )


# ============================================================
# Public dataset preparation
# ============================================================

def normalise_header(text):
    return re.sub(r"[^a-z0-9]", "", str(text).lower())


def load_public_data(c):
    header = pd.read_csv(
        c["path"],
        sep=c["sep"],
        nrows=0,
        encoding="utf-8-sig",
    )

    lookup = {}

    for column in header.columns:
        key = normalise_header(column)

        if key in lookup:
            raise ValueError(
                "Ambiguous columns after header normalisation."
            )

        lookup[key] = column

    requested = [
        c["birth_column"],
        c["extract_column"],
        c["gender_column"],
    ]

    columns = []

    for requested_column in requested:
        key = normalise_header(requested_column)

        if key not in lookup:
            raise ValueError(
                "Missing CSV column: " + requested_column
            )

        columns.append(lookup[key])

    if len(set(columns)) != 3:
        raise ValueError("Configure three distinct input columns.")

    source = pd.read_csv(
        c["path"],
        sep=c["sep"],
        usecols=columns,
        dtype=str,
        keep_default_na=False,
        encoding="utf-8-sig",
    )

    def parse_year(column):
        text = source[column].str.strip()

        # Reject decimal years, arbitrary text and invalid calendar years.
        result = pd.to_numeric(
            text.where(text.str.fullmatch(r"[0-9]{1,4}")),
            errors="coerce",
        )

        return result.where(result.between(1, 9999))

    birth = parse_year(columns[0])
    extract = parse_year(columns[1])

    gender = source[columns[2]].str.strip().str.upper()

    prepared = pd.DataFrame({
        "Age": (extract - birth).astype(float),
        "Gender": gender.where(
            gender.isin(["M", "F"]),
            "OTHER",
        ),
    })

    if len(prepared) < max(c["sizes"]):
        raise ValueError(
            "CSV has {} rows, but the largest requested sample is {}. "
            "Reduce SAMPLE_SIZES; rows will not be duplicated or padded."
            .format(len(prepared), max(c["sizes"]))
        )

    # One fixed shuffle; larger samples contain the smaller samples.
    order = np.random.default_rng(c["seed"]).permutation(
        len(prepared)
    )

    return prepared.iloc[order].reset_index(drop=True)


def exact_benchmarks(sample, c):
    age = sample["Age"].dropna()
    bounded = age.clip(lower=c["low"], upper=c["high"])

    if len(bounded) == 0:
        raise ValueError(
            "This sample has no usable ages; AVG cannot be benchmarked."
        )

    gender_counts = sample["Gender"].value_counts()

    truth = {
        "SUM": {"ALL": float(bounded.sum())},
        "AVG": {"ALL": float(bounded.mean())},
        "COUNT": {"ALL": float(len(sample))},
        "HISTOGRAM": {
            gender: float(gender_counts.get(gender, 0))
            for gender in GENDERS
        },
    }

    diagnostics = {
        "sample_size": len(sample),
        "usable_age_rows": len(age),
        "missing_age_rows": int(sample["Age"].isna().sum()),
        "age_rows_below_low": int((age < c["low"]).sum()),
        "age_rows_above_high": int((age > c["high"]).sum()),
        "np_sum_unclamped": float(age.sum()),
        "np_sum_clamped": truth["SUM"]["ALL"],
        "np_avg_unclamped": float(age.mean()),
        "np_avg_clamped": truth["AVG"]["ALL"],
    }

    return truth, diagnostics


# ============================================================
# Four Tumult queries only
# ============================================================

def build_queries(low, high):
    from tmlt.analytics import KeySet, QueryBuilder

    usable_age = (
        QueryBuilder("dogs")
        .filter("Age IS NOT NULL")
    )

    return {
        "SUM": usable_age.sum(
            "Age",
            low=low,
            high=high,
            name="value",
            mechanism="laplace",
        ),
        "AVG": usable_age.average(
            "Age",
            low=low,
            high=high,
            name="value",
            mechanism="laplace",
        ),
        "COUNT": QueryBuilder("dogs").count(
            name="value",
            mechanism="laplace",
        ),
        "HISTOGRAM": (
            QueryBuilder("dogs")
            .groupby(
                KeySet.from_dict({
                    "Gender": list(GENDERS)
                })
            )
            .count(
                name="value",
                mechanism="laplace",
            )
        ),
    }


# ============================================================
# Accuracy metrics
# ============================================================

def calculate_metrics(group):
    exact = group["np_value"].to_numpy(dtype=float)
    released = group["dp_value"].to_numpy(dtype=float)

    error = released - exact

    # RMSPE is undefined when the exact result is zero.
    eligible = exact != 0

    if eligible.any():
        relative_error = error[eligible] / exact[eligible]

        rmspe = (
            100.0
            * np.sqrt(np.mean(relative_error ** 2))
        )
    else:
        rmspe = np.nan

    return {
        "mae": float(np.mean(np.abs(error))),
        "rmspe_pct": float(rmspe),
        "completed_runs": int(group["run"].nunique()),
        "values_scored": int(len(group)),
        "relative_values_scored": int(eligible.sum()),
        "zero_reference_values": int((~eligible).sum()),
    }


def make_metric_tables(raw):
    summary_rows = []
    bin_rows = []

    keys = ["sample_size", "epsilon", "query"]

    for values, group in raw.groupby(keys, sort=True):
        row = dict(zip(keys, values))
        row.update(calculate_metrics(group))

        if row["query"] == "HISTOGRAM":
            reference = group.drop_duplicates("bin")

            row["zero_reference_bins"] = int(
                (reference["np_value"] == 0).sum()
            )
        else:
            row["zero_reference_bins"] = int(
                group["np_value"].iloc[0] == 0
            )

        summary_rows.append(row)

    bin_keys = keys + ["bin"]

    for values, group in raw.groupby(bin_keys, sort=True):
        row = dict(zip(bin_keys, values))
        row["np_value"] = float(group["np_value"].iloc[0])
        row.update(calculate_metrics(group))
        bin_rows.append(row)

    return (
        pd.DataFrame(summary_rows),
        pd.DataFrame(bin_rows),
    )


# ============================================================
# Charts generated from actual recorded results
# ============================================================

def write_charts(summary, output, show=False):
    import plotly.graph_objects as go
    import plotly.io as pio

    pio.templates.default = "plotly_white"

    units = {
        "SUM": "Record-years",
        "AVG": "Years",
        "COUNT": "Records",
        "HISTOGRAM": "Records per bin",
    }

    files = []

    for query in QUERIES:
        data = summary.loc[
            summary["query"] == query
        ]

        for metric in ("mae", "rmspe_pct"):
            grid = (
                data.pivot(
                    index="sample_size",
                    columns="epsilon",
                    values=metric,
                )
                .sort_index()
                .sort_index(axis=1)
            )

            label = "MAE" if metric == "mae" else "RMSPE (%)"

            colour_label = (
                units[query]
                if metric == "mae"
                else "Error (%)"
            )

            x_values = grid.columns.to_numpy(dtype=float)
            y_values = grid.index.to_numpy(dtype=float)
            values = grid.to_numpy(dtype=float)
            finite_values = values[np.isfinite(values)]
            colour_max = (
                max(float(finite_values.max()), 1e-12)
                if finite_values.size
                else 1.0
            )

            if grid.size == 1:
                figure = go.Figure()
                value = float(values[0, 0])
                subtitle = (
                    "One measured setting; no trend can be inferred"
                )

                if math.isfinite(value):
                    figure.add_trace(
                        go.Scatter(
                            x=x_values,
                            y=y_values,
                            mode="markers+text",
                            text=[f"{value:.4g}"],
                            textposition="top center",
                            marker={
                                "size": 28,
                                "color": [value],
                                "colorscale": "Blues_r",
                                "cmin": 0,
                                "cmax": colour_max,
                                "colorbar": {"title": colour_label},
                                "line": {"color": "#17365D", "width": 1},
                            },
                            hovertemplate=(
                                "Epsilon: %{x:g}<br>"
                                "Sample rows: %{y:g}<br>"
                                + label
                                + ": %{marker.color:.5g}<extra></extra>"
                            ),
                        )
                    )
                else:
                    subtitle = (
                        "RMSPE is undefined when the exact result is zero"
                    )
                    figure.add_trace(
                        go.Scatter(
                            x=x_values,
                            y=y_values,
                            mode="markers+text",
                            text=["Undefined"],
                            textposition="top center",
                            marker={
                                "size": 18,
                                "symbol": "circle-open",
                                "color": "#666666",
                            },
                            hovertemplate=(
                                "Epsilon: %{x:g}<br>"
                                "Sample rows: %{y:g}<br>"
                                "RMSPE is undefined when the exact result is zero"
                                "<extra></extra>"
                            ),
                        )
                    )

                x_margin = max(abs(x_values[0]) * 0.1, 0.5)
                y_margin = max(abs(y_values[0]) * 0.1, 1)
                figure.update_xaxes(
                    range=[
                        x_values[0] - x_margin,
                        x_values[0] + x_margin,
                    ]
                )
                figure.update_yaxes(
                    range=[
                        y_values[0] - y_margin,
                        y_values[0] + y_margin,
                    ]
                )
            else:
                subtitle = (
                    "Darker blue means lower error; hover for exact values"
                )
                figure = go.Figure(
                    go.Heatmap(
                        x=x_values,
                        y=y_values,
                        z=values,
                        zmin=0,
                        zmax=colour_max,
                        colorscale="Blues_r",
                        colorbar={"title": colour_label},
                        hovertemplate=(
                            "Epsilon: %{x:g}<br>"
                            "Sample rows: %{y:g}<br>"
                            + label
                            + ": %{z:.5g}<extra></extra>"
                        ),
                    )
                )

            figure.update_layout(
                title={
                    "text": (
                        query + ": " + label
                        + " by epsilon and sample size"
                        + "<br><span style='font-size:14px'>"
                        + subtitle
                        + "</span>"
                    )
                },
                width=850,
                height=600,
                margin={"l": 90, "r": 100, "t": 100, "b": 80},
                xaxis={"showgrid": True, "gridcolor": "#E5E7EB"},
                yaxis={"showgrid": True, "gridcolor": "#E5E7EB"},
            )

            figure.update_xaxes(
                title_text="Epsilon",
                tickmode="array",
                tickvals=x_values,
                ticktext=[f"{x:g}" for x in x_values],
            )

            figure.update_yaxes(
                title_text="Sample rows",
                tickmode="array",
                tickvals=y_values,
                ticktext=[f"{y:g}" for y in y_values],
            )

            path = output / (
                query.lower() + "_" + metric + ".png"
            )

            figure.write_image(str(path))

            metadata = {
                "caption": query + " " + label,
                "description": (
                    "Measured errors over repeated Tumult evaluations. "
                    "Lower error is better; blank cells indicate undefined "
                    "or unavailable metrics. Single-point charts show the "
                    "measured value directly."
                ),
            }

            Path(str(path) + ".meta.json").write_text(
                json.dumps(metadata, indent=2),
                encoding="utf-8",
            )

            files.append(path)

    if show:
        for path in files:
            webbrowser.open(path.resolve().as_uri())

    return files


def finish_outputs(output, show=False):
    raw = pd.read_csv(output / "raw_results.csv")

    if raw.empty:
        raise ValueError("No completed results are available.")

    summary, per_bin = make_metric_tables(raw)

    summary.to_csv(
        output / "summary.csv",
        index=False,
    )

    per_bin.to_csv(
        output / "per_bin_metrics.csv",
        index=False,
    )

    print("\nMEASURED RESULTS")
    print(
        summary[
            [
                "sample_size",
                "epsilon",
                "query",
                "mae",
                "rmspe_pct",
                "completed_runs",
                "zero_reference_bins",
            ]
        ].to_string(index=False)
    )

    try:
        chart_paths = write_charts(summary, output, show)

        print("\nPNG charts:")
        for path in chart_paths:
            print(" ", path)

    except Exception as exc:
        # Preserve numerical results if image export fails.
        print("\nMetrics were saved, but PNG export failed:")
        print(str(exc))

        print(
            "After fixing the chart dependencies, use "
            "--plot-only with this results directory."
        )


# ============================================================
# Benchmark execution
# ============================================================

def run_benchmark(c):
    if version("tmlt.analytics") != "0.21.0":
        raise RuntimeError(
            "This script targets tmlt.analytics==0.21.0."
        )

    from pyspark.sql import SparkSession

    from tmlt.analytics import (
        AddOneRow,
        PureDPBudget,
        Session,
    )

    # Public benchmark only: preprocessing and sample-size checks
    # are intentionally nonprivate.
    public_data = load_public_data(c)

    output = c["output"]

    # Never overwrite a previous experiment.
    output.mkdir(parents=True, exist_ok=False)

    manifest = {
        "queries": list(QUERIES),
        "csv_path": c["path"],
        "sample_sizes": c["sizes"],
        "epsilons": [str(e) for e in c["epsilons"]],
        "repeats": c["repeats"],
        "sample_seed": c["seed"],
        "age_bounds": [c["low"], c["high"]],
        "privacy_unit": "One input licence row",
        "data_status": "Already-public data required",
        "sampling": "Nested prefixes of one seeded shuffle",
        "dp_noise": "Generated by Tumult; not manually seeded",
        "negative_counts": "Retained without postprocessing",
        "versions": {
            name: version(name)
            for name in [
                "tmlt.analytics",
                "pyspark",
                "numpy",
                "pandas",
                "plotly",
            ]
        },
    }

    (output / "experiment.json").write_text(
        json.dumps(manifest, indent=2),
        encoding="utf-8",
    )

    spark = None
    diagnostics = []

    fields = [
        "sample_size",
        "epsilon",
        "query",
        "run",
        "bin",
        "np_value",
        "dp_value",
    ]

    try:
        spark = (
            SparkSession.builder
            .master(c["master"])
            .appName("dog-four-query-utility-benchmark")
            .config("spark.sql.shuffle.partitions", "4")
            .getOrCreate()
        )

        spark.sparkContext.setLogLevel("ERROR")

        queries = build_queries(c["low"], c["high"])

        allocations = [
            (epsilon, PureDPBudget(float(epsilon)))
            for epsilon in c["epsilons"]
        ]

        # Use Tumult budget arithmetic, avoiding rounded float totals.
        sample_budget = PureDPBudget(0)

        for _, allocation in allocations:
            sample_budget = sample_budget + (
                allocation * (c["repeats"] * len(QUERIES))
            )

        with (output / "raw_results.csv").open(
            "w",
            newline="",
            encoding="utf-8",
        ) as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=fields,
            )

            writer.writeheader()
            handle.flush()

            for size in c["sizes"]:
                sample = public_data.iloc[:size].copy()

                truth, diagnostic = exact_benchmarks(
                    sample, c
                )

                diagnostics.append(diagnostic)

                pd.DataFrame(diagnostics).to_csv(
                    output / "sample_diagnostics.csv",
                    index=False,
                )

                records = [
                    (
                        None if pd.isna(age) else float(age),
                        str(gender),
                    )
                    for age, gender in sample[
                        ["Age", "Gender"]
                    ].itertuples(index=False, name=None)
                ]

                dataframe = spark.createDataFrame(
                    records,
                    schema="Age double, Gender string",
                ).cache()

                session = None

                try:
                    dataframe.count()

                    session = Session.from_dataframe(
                        privacy_budget=sample_budget,
                        source_id="dogs",
                        dataframe=dataframe,
                        protected_change=AddOneRow(),
                    )

                    for epsilon, allocation in allocations:
                        for query_name in QUERIES:
                            cell_rows = []

                            for run in range(1, c["repeats"] + 1):
                                # Do not retry a failed evaluation
                                # automatically.
                                released = session.evaluate(
                                    queries[query_name],
                                    allocation,
                                ).collect()

                                result = {
                                    (
                                        str(row["Gender"])
                                        if query_name == "HISTOGRAM"
                                        else "ALL"
                                    ): float(row["value"])
                                    for row in released
                                }

                                if set(result) != set(truth[query_name]):
                                    raise RuntimeError(
                                        "DP and NP output domains differ."
                                    )

                                for bin_name, exact in truth[
                                    query_name
                                ].items():
                                    value = result[bin_name]

                                    if not math.isfinite(value):
                                        raise RuntimeError(
                                            "Nonfinite DP result."
                                        )

                                    row = {
                                        "sample_size": size,
                                        "epsilon": float(epsilon),
                                        "query": query_name,
                                        "run": run,
                                        "bin": bin_name,
                                        "np_value": exact,
                                        "dp_value": value,
                                    }

                                    writer.writerow(row)
                                    cell_rows.append(row)

                                # Preserve completed results if interrupted.
                                handle.flush()

                            measured = calculate_metrics(
                                pd.DataFrame(cell_rows)
                            )

                            print(
                                "n={} | epsilon={} | {} | "
                                "MAE={:.6g} | RMSPE={:.6g}%"
                                .format(
                                    size,
                                    epsilon,
                                    query_name,
                                    measured["mae"],
                                    measured["rmspe_pct"],
                                ),
                                flush=True,
                            )

                finally:
                    if session is not None:
                        session.stop()

                    dataframe.unpersist()

    finally:
        if spark is not None:
            spark.stop()


def main():
    args = arguments()
    load_dotenv(args.env, override=False)

    log_path = os.getenv("TERMINAL_LOG", "./logs/benchmark.log").strip()
    logger = TerminalOutputLog(log_path) if log_path else None

    if logger is None:
        run(args)
    else:
        with logger:
            try:
                run(args)
            except SystemExit as error:
                if isinstance(error.code, str):
                    print(error.code, file=sys.stderr)
                    raise SystemExit(1)
                raise
            except Exception:
                traceback.print_exc()
                raise SystemExit(1)


def run(args):
    if args.plot_only:
        finish_outputs(
            Path(args.plot_only),
            args.show_charts,
        )
        return

    c = configuration(args)
    print_plan(c)

    if args.plan:
        return

    if not args.public_data:
        raise SystemExit(
            "\nRefusing to export exact answers without "
            "--public-data. Use this benchmark only on "
            "already-public data."
        )

    run_benchmark(c)

    finish_outputs(
        c["output"],
        args.show_charts,
    )


if __name__ == "__main__":
    main()