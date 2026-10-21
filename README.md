# Next-day smog forecast for Pakistan (HackInsight 2026)

Every evening, forecast **tomorrow's average PM2.5** for **Lahore, Islamabad/Rawalpindi, Peshawar and Karachi**,
turn it into a **smog-advisory recommendation** an environment officer can act on, and explain it in English
and Urdu. Team XYZ (see Team and responsibilities below).

| | Link |
|---|---|
| **Live dashboard (deployed, Hugging Face Space)** | https://huggingface.co/spaces/lazypanda21/smog-forecast-pakistan |
| **Code (this repository, tag `v1.0-final`)** | https://github.com/lazy-laksh/hackinsight-smog-forecast |
| **Demo video (≤ 3 min)** | https://youtu.be/1tIjeWMbzng |
| Insight Agent (deployed on Render, used by the dashboard's "Ask a question" tab) | open the dashboard → Ask a question |
| Hourly published results (dataset) | https://huggingface.co/datasets/lazypanda21/smog-forecast-live |
| One-page summary | [reports/Summary.pdf](reports/Summary.pdf) |
| Dataset proposal (sources, credibility, how they are used) | [docs/DatasetProposal.pdf](docs/DatasetProposal.pdf) |
| Prefect run history (24 h window, unedited) | [prefect_runs.csv](prefect_runs.csv) · [screenshot](docs/prefect_runs.png) |
| Dataset declaration | [DATASET_DECLARATION.md](DATASET_DECLARATION.md) |
| Model card (generated) | [reports/MODEL_CARD.md](reports/MODEL_CARD.md) |
| AI use log / optimisation log | [AI_Use_Log.md](AI_Use_Log.md) · [OPTIMIZATION_LOG.md](OPTIMIZATION_LOG.md) |
| Business model | [docs/BUSINESS_MODEL.md](docs/BUSINESS_MODEL.md) |

## Team and responsibilities

**Team XYZ**: four members. Laksh Kumar built the system; the other three members joined on 21 Oct 2026 and present and defend the parts listed below.

| Member | Role | Responsibilities |
|---|---|---|
| Laksh Kumar (BS Computer Science, Data Science, NED University of Engineering & Technology, Karachi) | Team lead and developer | Problem choice and user research; dataset declaration (baseline, metric, test period); data pipeline (OpenAQ, NASA FIRMS, Open-Meteo/CAMS; raw → cleaned → gold; SHA-256 manifest); Data Quality Agent; forecast model, validation and calibration; decision layer; Prefect hourly automation on a dedicated laptop; dashboard and deployment (Hugging Face); Insight Agent and its deployment (Render); drift monitor and Punjab EPA validation; tests and reproducibility test; documentation, AI use log, optimization log, demo video; presents data, pipeline, quality and automation |
| Syed Mustehsan Akhtar Kazmi | Problem and impact | Presents and answers questions on the problem, the user, the live dashboard, the business model and Pakistan impact |
| Noor Fatima | Model and results | Presents and answers questions on the model, baseline, results by city, uncertainty, the advisory rule and limitations |
| Amna Iqbal | Agent and responsible AI | Presents and answers questions on the Insight Agent and its guardrails, the drift monitor and the Punjab EPA validation |

AI assistance (Claude) was used for code and documents; every use, and what was checked, is in [AI_Use_Log.md](AI_Use_Log.md).

## Project details in brief

* **Problem:** smog decisions in Punjab and KP (schools, hospitals, traffic) must be made the evening before, but only today's reading is usually available.
* **User and decision:** a district environment officer decides at 20:00 whether to issue a smog advisory for tomorrow.
* **What we deliver:** tomorrow's mean PM2.5 for Lahore, Islamabad/Rawalpindi, Peshawar and Karachi with a calibrated 80% range, the chance of an unhealthy day (≥ 55.5 µg/m³), an advisory recommendation at the officer's own risk threshold, the main reasons, and an Urdu message; updated every hour.
* **Result:** 17.7% lower error than the declared baseline "tomorrow = today" on the locked October 2026 test month (80 city-days); 80% range held the real value 91% of the time; the advisory rule caught 39 of 40 unhealthy test days. Lahore (8.7%) is below our declared 10% target and Karachi does not beat the baseline; both are reported in the model card.
* **Deployed:** live dashboard (Hugging Face Space), hourly pipeline (Prefect, 24/24 runs in the judged window), Insight Agent with cited answers (Render).

## How it works

```
OpenAQ sensors ─┐
NASA FIRMS fires ├─► raw files ─► SHA-256 manifest ─► cleaned ─► Data Quality Agent ─► gold tables
Open-Meteo + CAMS┘     (never edited)   (verified every run)          (gate 1 + gate 2, logged)      │
                                                                                                   ▼
             Insight Agent ◄── published gold rows ◄── publish ◄── forecast + chance + advisory + "why"
        (cited answers or "I don't know")      (HF dataset)  ──►  static dashboard (HF Space)
```

* **Hourly pipeline** (Prefect, `5 * * * *`): download → verify fingerprints → clean → quality gates → gold →
  forecast → publish. A failed download is retried twice and the run continues (the source shows as stale);
  a failed quality gate stops the run so nothing is rebuilt from bad data.
* **Data Quality Agent** (`src/smog/quality.py`): flags stuck sensors (same value 6+ h), spikes, zeros and
  impossible values; fills only 1–2 h gaps; checks freshness and completeness; logs every decision.
* **Model** (`src/smog/model.py`): gradient-boosted trees using only what is known at 20:00 PKT; settings chosen
  by month-by-month rolling-origin validation; test period (target days from 1 Oct 2026) never used for tuning;
  80% range calibrated on validation months (conformalized quantile regression).
* **Decision layer** (`src/smog/decision.py`): chance of an unhealthy day (≥ 55.5 µg/m³) → advisory when the chance
  ≥ 1/(1+R); R = how many times worse a missed smog day is than a false alarm (default 3, slider on the dashboard).
* **Insight Agent** (`src/smog/agent.py`): TF-IDF retrieval over cited official sources (`knowledge/`) plus data facts
  built by code from gold rows; an LLM (Groq gpt-oss, with Gemini Flash as fallback) answers with citations; code then
  checks every citation id and every number against the sources (numbers typed by the user do not count) and answers
  "I don't know" otherwise. English and Urdu. Guardrails in code: emails, phone numbers and CNICs are removed before
  the model or the log sees a question; health questions always end with a "not medical advice, Rescue 1122" note;
  every answer stores the model and prompt version. Golden set of 22 questions including an advisory brief and a
  misleading question: `config/agent_golden.yaml` (`python -m smog eval-agent` → `reports/agent_eval.csv` and
  `reports/agent_eval_summary.json` with answer accuracy, correct refusals and faithfulness).

* **Drift monitor** (`src/smog/drift.py`, every hourly publish): inputs of the last 14 days vs the same weeks last year
  (PSI minus its small-sample noise), and live error vs test error; dashboard panel + `data/logs/drift_log.csv`.
* **Local validation** (`src/smog/local_validation.py`): Lahore value vs Punjab EPA reference stations for the same hour
  (`config/punjab_epa_reference.csv`, sources linked per row; add rows from aqi.punjab.gov.pk and re-run).

## Hosting

* **Dashboard:** free static Hugging Face Space (`python -m smog deploy-space`). It reads the public dataset in the
  visitor's browser, so it needs no server and no secrets.
* **Insight Agent (Ask tab):** a small Python server (`python -m smog.agent_ui --host 0.0.0.0`) hosted on Render's
  free plan from `render.yaml` (keys stored as Render secrets). The dashboard calls it at `AGENT_URL`. Free instances
  sleep after 15 minutes idle, so the first question can take about a minute. Language models are tried in the order in
  `config/agent.yaml` (Groq gpt-oss-120b, Google Gemini Flash, Groq gpt-oss-20b); the same checks apply to every answer.

## Results

All numbers are generated by `python -m smog train` into [reports/model_metrics.json](reports/model_metrics.json)
and [reports/MODEL_CARD.md](reports/MODEL_CARD.md) (never typed by hand). The model card is the source of truth.

## Setup (once per computer)

```bash
python -m venv .venv
# Windows PowerShell:  .venv\Scripts\Activate.ps1      Linux/macOS:  source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
cp .env.example .env        # Windows: copy .env.example .env
# put your keys in .env (never commit it): OPENAQ_API_KEY, FIRMS_MAP_KEY (both free); HF_TOKEN and
# GROQ_API_KEY / GEMINI_API_KEY are only needed for publishing and the Insight Agent, not for `make data`
python -m smog data         # or: make data
```

## Reproducibility test (fresh clone, empty data folder)

Done on 20–21 Oct 2026 on a Windows laptop (Python 3.14) that had never run the project: `git clone` into a new
folder, the setup above, then `python -m smog data`.

| Run | Result | Time |
|---|---|---|
| 1, from empty | OpenAQ 2,018 sensor-month files (2,086 API calls, 55 min) and NASA FIRMS 307 windows (5 min) downloaded; **stopped at Open-Meteo**: one answer came back "200 OK" with an empty body and crashed the stage | 61 min |
| 2, after fix 1 | Open-Meteo 136 month files in 87 s, 0 failed; **stopped at verify**: the committed manifest lists 56 files from the original machine (hourly forecast snapshots) that can never be downloaded again | 7 min |
| 3, after fix 2 | **all 8 stages OK**: 904,250 PM2.5 sensor-hours (1,552 flagged, 27,881 short gaps filled), 319,616 fire detections, 48,768 weather hours → 2,013 city-days; both quality gates passed | 6 min |

So a stranger needs about **65 minutes** from an empty folder (almost all of it is waiting for the OpenAQ speed limit);
cleaning, quality checks and gold take about 30 seconds. Fixes made because of this test:
1. `src/smog/api.py`: a "200 OK" whose body is empty or not JSON is retried and, if it keeps failing, logged for that
   one month instead of crashing the stage (tests in `tests/test_api.py`); Open-Meteo is asked at most once a second.
2. `src/smog/manifest.py`: a file listed in the manifest but not on this machine is reported in
   `data/logs/manifest_missing.csv`, not a failure; an edited or unrecorded file on disk still fails the check.

Expected and logged, not errors: about a dozen OpenAQ sensor-months return HTTP 500/408 from OpenAQ's server
(`data/logs/openaq_known_gaps.csv`). The exact row counts grow every hour because the data is live.

## Commands

| Command | What it does |
|---|---|
| `make data` / `python -m smog data` | fetch raw files, verify SHA-256, build cleaned + gold, run quality gates |
| `python -m smog train` | train, score on the locked test month, write `reports/` and the model card |
| `python -m smog predict` | forecast tomorrow for each city (appends to `gold.predictions`) |
| `python -m smog.flow` | start the hourly Prefect schedule (needs `prefect server start`) |
| `python -m smog publish` | export dashboard files and upload them to the HF dataset |
| `python -m smog deploy-space` | create/update the static dashboard Space |
| `python -m smog ask "..."` | ask the Insight Agent |
| `python -m smog agent-ui` | local web page for the agent (http://127.0.0.1:8000) |
| `python -m smog validate-local` | compare our Lahore values with Punjab EPA reference stations → `reports/LOCAL_VALIDATION.md` |
| `python -m smog eval-agent` | run the 22-question golden set → `reports/agent_eval.csv` + `agent_eval_summary.json` |
| `python -m smog export-runs` | Prefect run history for the evaluation window → `prefect_runs.csv` |
| `make test` / `python -m pytest -q` | unit tests (no network, no keys needed) |

## Folder layout

```
config/            cities, fire box, source licences, agent settings, agent golden questions
knowledge/         short cited summaries of official sources used by the Insight Agent
data/raw/          files exactly as downloaded (never edited; not in Git)
data/cleaned/      typed, de-duplicated, unit-checked tables (not in Git)
data/gold/         final tables for the model, dashboard and agent (not in Git)
data/manifest.csv  source, query, fetch time, SHA-256 and licence of every raw file (in Git)
data/logs/         quality decisions, known source gaps, pipeline status, agent answers (in Git)
reports/           generated metrics, model card, predictions, agent evaluation
space_static/      the dashboard (static HTML + JS; libraries bundled in lib/)
src/smog/          pipeline, model, decision layer, publish, agent
tests/             unit tests
```

## Honest limitations

* Mostly low-cost sensors without humidity correction; one value per city hides neighbourhood hot spots.
* Training used archived weather for "tomorrow", closer to the truth than a real forecast; live errors may be larger
  (the model card also reports a strict model without any "tomorrow" inputs).
* Karachi: the model does not beat persistence on the test month (clean, stable October air); reported, not hidden.
* The advisory rule is cautious by design (default R = 3): it rarely misses a smog day but gives more false alarms.
* Information, not medical advice: follow official advisories (Punjab EPA, PDMA, PMD).

## Data acknowledgements

- Air sensor data: OpenAQ and the owner of each location (licence recorded per location in `data/manifest.csv`).
- We acknowledge the use of data and/or imagery from NASA's Fire Information for Resource Management System (FIRMS),
  part of NASA's Earth Science Data and Information System (ESDIS).
- Weather data by [Open-Meteo.com](https://open-meteo.com/) (CC BY 4.0).
- Air quality model data: Copernicus Atmosphere Monitoring Service (CAMS), served by Open-Meteo.com (CC BY 4.0).
- Official documents summarised in `knowledge/` (WHO, US EPA, Government of Pakistan, Government of Punjab, Dawn,
  The Express Tribune): each file lists its source URL.
- Dashboard libraries: PapaParse, Plotly.js, marked (MIT).
