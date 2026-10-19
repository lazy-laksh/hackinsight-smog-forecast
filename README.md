# hackinsight-smog-forecast

Next-day smog (PM2.5) forecast for Pakistani cities – HackInsight 2026.
Team XYZ. See [DATASET_DECLARATION.md](DATASET_DECLARATION.md) for our sources, licences and declared baseline.

## Setup (once per computer)

```bash
python -m venv .venv
# Windows PowerShell:  .venv\Scripts\Activate.ps1
# Linux / macOS / Git Bash:  source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
cp .env.example .env        # Windows: copy .env.example .env
# then open .env and paste your OpenAQ and NASA FIRMS keys
```

## Rebuild everything with one command

```bash
make data          # or, without make:  python -m smog data
make clean         # or:  python -m smog clean
make test          # or:  python -m pytest
```

`make data` fetches every raw file, checks each one against its SHA-256 in
`data/manifest.csv`, builds the cleaned and gold layers, and runs the
Data Quality Agent. Its output lists every stage and says plainly which
stages are not built yet.

## Folder layout

```
config/            cities, fire-detection box, source licences
data/raw/          files exactly as downloaded (never edited, not in Git)
data/cleaned/      types fixed, units unified, problems flagged (not in Git)
data/gold/         final tables for the model and dashboard (not in Git)
data/manifest.csv  source, query, fetch time, SHA-256 and licence of every raw file (in Git)
src/smog/          pipeline code
tests/             unit tests
```

## Data acknowledgements

- Air sensor data: OpenAQ and the owner of each location (licence recorded per location in `data/manifest.csv`).
- We acknowledge the use of data and/or imagery from NASA's Fire Information for Resource Management System (FIRMS), part of NASA's Earth Science Data and Information System (ESDIS).
- Weather data by [Open-Meteo.com](https://open-meteo.com/) (CC BY 4.0).
- Air quality model data: Copernicus Atmosphere Monitoring Service (CAMS), served by Open-Meteo.com (CC BY 4.0).
