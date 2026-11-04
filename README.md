### Run commands:
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

When running in Docker, rebuild after changing `main.py`, then regenerate charts
from an existing run without repeating the DP queries:
```bash
docker build --platform linux/amd64 -t nyc-dog-dp .
docker run --rm --platform linux/amd64 \
  -v "$PWD/results:/app/results" \
  nyc-dog-dp python main.py --plot-only /app/results/smoke
open results/smoke
```

Run the full Docker experiment grid from the project directory. It uses 10
sample sizes, 13 epsilon values, four query types, and 30 repeats (15,600
query evaluations). This can take a long time. Use a new output directory for
each run:
```bash
docker run --rm --platform linux/amd64 \
  -v "$PWD/data/nyc_dog_licenses.csv:/app/data/nyc_dog_licenses.csv:ro" \
  -v "$PWD/results:/app/results" \
  nyc-dog-dp python main.py \
  --public-data \
  --sizes 1000,2000,3000,4000,5000,6000,7000,8000,9000,9358 \
  --epsilons 0.1,0.25,0.5,0.75,1.0,1.25,1.5,1.75,2.0,2.25,2.5,2.75,3.0 \
  --repeats 30 \
  --output /app/results/four_queries
```

When it completes, open the charts on your Mac:
```bash
open results/four_queries
```

Validation limitation: this replacement has not been executed end-to-end against your CSV. Do not start with the full grid before checking the small run.


#### Run ASAP
CMD 1
```bash
docker build --platform linux/amd64 -t nyc-dog-dp .
```

CMD2
```bash
docker run --rm --platform linux/amd64 \
  -v "$PWD/data/nyc_dog_licenses.csv:/app/data/nyc_dog_licenses.csv:ro" \
  -v "$PWD/results:/app/results" \
  nyc-dog-dp python main.py \
  --public-data \
  --sizes 1000,2000,3000,4000,5000,6000,7000,8000,9000,9358 \
  --epsilons 0.1,0.25,0.5,0.75,1.0,1.25,1.5,1.75,2.0,2.25,2.5,2.75,3.0 \
  --repeats 30 \
  --output /app/results/four_queries
```