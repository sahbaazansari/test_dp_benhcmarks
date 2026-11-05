### Run commands:

Terminal output from the benchmark (stdout and stderr) is also appended to
`./logs/benchmark.log` and written immediately, while still appearing in the
terminal. Change `TERMINAL_LOG` in `.env` to choose another path; set it blank
to disable terminal logging.

- Inspect the experiment without reading the CSV:
```bash
python main.py --plan
```
- Run a small integration test first:
```bash
python main.py \
  --public-data \
  --sizes 1000 \
  --epsilons 1.0 \
  --repeats 3 \
  --output results/smoke
```
- Run the complete grid:
```bash
python main.py \
  --public-data \
  --output results/full
```
- Regenerate the charts without spending more query budget:
```bash
python main.py --plot-only results/full
```
- Open the charts on a local desktop:
```bash
python main.py --plot-only results/full --show-charts
```

The charts show measured error for each sample size and privacy budget
(epsilon). Darker blue means lower error; hover over a cell to see its exact
value. A run with only one sample size and one epsilon has just one measured
point, so its chart shows that point rather than suggesting a trend. Use several
sizes and epsilon values to compare settings.

Run Docker commands from the project directory. Rebuild after changing
`main.py`, then run a small integration test first. The `.env` file is passed
into the container, and `logs/` is mounted so the terminal log persists on the
host:
```bash
docker build --platform linux/amd64 -t nyc-dog-dp .

mkdir -p results logs

docker run --rm --platform linux/amd64 \
  --env-file .env \
  -e TERMINAL_LOG=/app/logs/benchmark.log \
  -v "$PWD/data/nyc_dog_licenses.csv:/app/data/nyc_dog_licenses.csv:ro" \
  -v "$PWD/results:/app/results" \
  -v "$PWD/logs:/app/logs" \
  nyc-dog-dp python main.py \
  --public-data \
  --sizes 1000 \
  --epsilons 1.0 \
  --repeats 3 \
  --output /app/results/docker-smoke
```

For the full Docker experiment, use a new output directory each time. This
grid runs 15,600 query evaluations and can take a long time:
```bash
docker run --rm --platform linux/amd64 \
  --env-file .env \
  -e TERMINAL_LOG=/app/logs/benchmark.log \
  -v "$PWD/data/nyc_dog_licenses.csv:/app/data/nyc_dog_licenses.csv:ro" \
  -v "$PWD/results:/app/results" \
  -v "$PWD/logs:/app/logs" \
  nyc-dog-dp python main.py \
  --public-data \
  --sizes 1000,2000,3000,4000,5000,6000,7000,8000,9000,9358 \
  --epsilons 0.1,0.25,0.5,0.75,1.0,1.25,1.5,1.75,2.0,2.25,2.5,2.75,3.0 \
  --repeats 30 \
  --output /app/results/docker-full
```

After either run, regenerate charts from existing results without repeating
the DP queries:
```bash
docker run --rm --platform linux/amd64 \
  -v "$PWD/results:/app/results" \
  nyc-dog-dp python main.py --plot-only /app/results/docker-smoke
```

Open the results folder on a local desktop with `open results/docker-smoke` (or
`open results/docker-full` after the full run).

docker build --platform linux/amd64 -t nyc-dog-dp .

mkdir -p results logs

docker run --rm --platform linux/amd64 \
  --env-file .env \
  -e TERMINAL_LOG=/app/logs/benchmark.log \
  -v "$PWD/data/nyc_dog_licenses.csv:/app/data/nyc_dog_licenses.csv:ro" \
  -v "$PWD/results:/app/results" \
  -v "$PWD/logs:/app/logs" \
  nyc-dog-dp python main.py \
  --public-data \
  --sizes 1000 \
  --epsilons 1.0 \
  --repeats 3 \
  --output /app/results/docker-smoke