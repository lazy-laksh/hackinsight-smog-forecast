# AI Use Log

We used AI assistants during the event. Every material use is listed here.
For each entry: the tool, what it helped with, what we checked or changed,
and which team member can explain that part.

| # | Date (PKT) | Tool and version | Task | What we kept / changed / checked | Team member who can explain it |
|---|---|---|---|---|---|
| 1 | 2026-10-19 | Claude (Anthropic), Opus 5.5 | Brainstormed problem ideas; checked source licences and access (OpenAQ, NASA FIRMS, Open-Meteo, Punjab EPA, AirNow) | We chose the smog problem ourselves; we checked the OpenAQ stations and AirNow archive by hand in the browser | Laksh |
| 2 | 2026-10-19 | Claude (Anthropic), Opus 5.5 | Drafted DATASET_DECLARATION.md | We filled in the station counts from our own checks and reviewed the baseline and test dates before committing | Laksh |
| 3 | 2026-10-19 | Claude (Anthropic), Opus 5.5 | Wrote the first version of the project skeleton: Makefile, config files, `src/smog/config.py`, `manifest.py`, `cli.py`, tests | Ran the tests and `python -m smog data` on our own laptop; kept the layout (raw / cleaned / gold, UTC, SHA-256 manifest) after checking it against the guide | Laksh |
| 4 | 2026-10-19 | Claude (Anthropic), Opus 5.5 | Data fetchers for OpenAQ, NASA FIRMS and Open-Meteo/CAMS, and the shared polite API client | Ran the full download ourselves; when OpenAQ returned HTTP 500 for some sensor-months we asked for retries by week and a known-gaps log instead of hiding them; checked `verify` passes | Laksh |
| 5 | 2026-10-19 | Claude (Anthropic), Opus 5.5 | Cleaning layer and the Data Quality Agent (stuck / spike / zero / impossible flags, 1–2 h gap filling, gates 1 and 2) | Thresholds were chosen from our own profile of the real data (66-hour runs of 0.0, a 47 → 667 → 45 spike); we rejected a rule that would have removed 99% of hours; reviewed the quality log | Laksh |
| 6 | 2026-10-19 | Claude (Anthropic), Opus 5.5 | Gold tables, forecast model, persistence baseline, leakage test, rolling-origin validation, model card | Checked the test dates match DATASET_DECLARATION.md; all numbers in the model card come from our own `train` runs, never typed by hand | Laksh |
| 7 | 2026-10-20 | Claude (Anthropic), Opus 5.5 | Prefect hourly flow and debugging when no scheduled runs appeared | We found the empty schedule in the Prefect UI and shared the server log; the fix (pin SQLAlchemy < 2.1) was tested on the Dell, which then ran 24/24 runs | Laksh |
| 8 | 2026-10-20 | Claude (Anthropic), Opus 5.5 | 80% range calibration (conformalized quantile regression) and the decision layer (chance of an unhealthy day, cost ratio R, advisory replay, reliability) | Checked coverage before/after (66% → 91% on test, 59% → 80% on validation) and that the widening uses validation months only; chose the default R = 3 | Laksh |
| 9 | 2026-10-20 | Claude (Anthropic), Opus 5.5 | Publish step and the static Hugging Face dashboard (English/Urdu, haze scale, report card) | Deployed it ourselves, reviewed it in screenshots and asked for fixes (missing chance shown as "no advisory", markers invisible in dark mode, 33% vs 33.3%) | Laksh |
| 10 | 2026-10-20 | Claude (Anthropic), Opus 5.5 | Insight Agent: retrieval over cited documents, data facts from gold rows, citation and number checker, golden question set | Checked every knowledge file against its source link; ran the golden set (16/20 → 9/20 after a "faster" setting → reverted → 20/20) | Laksh |
| 11 | 2026-10-20 | Claude (Anthropic), Opus 5.5 | Gemini fallback, hosting the agent on Render, Ask tab | Keys only in `.env` and Render secrets, never in code or chat; tested `/health` and live answers | Laksh |
| 12 | 2026-10-20 | Claude (Anthropic), Opus 5.5 | Documents: README, business model, problem analysis, datasheet, video script, viva preparation | Read and corrected them; every result number comes from the generated reports | Laksh |
| 13 | 2026-10-20 | Claude (Anthropic), Opus 5.5 | Agent guardrails: removing emails/phone numbers/CNICs, "Rescue 1122" note on health questions, prompt version, misleading-number test, evaluation summary | Ran all tests; golden set 22/22 with the new hard questions | Laksh |
| 14 | 2026-10-20 | Claude (Anthropic), Opus 5.5 | Drift monitor and local validation against Punjab EPA reference stations | Checked the EPA station readings against the source articles; read the drift result on the dashboard | Laksh |
| 15 | 2026-10-21 | Claude (Anthropic), Opus 5.5 | Fixes for two bugs found by our fresh-clone reproducibility test (empty Open-Meteo answer crashed a stage; manifest listed files that cannot be downloaded again) | We ran the fresh clone ourselves until all 8 stages passed and recorded the timings in the README | Laksh |
| 16 | 2026-10-21 | Claude (Anthropic), Opus 5.5 | Final checks: drift window fix, honest limitations in the Summary (Lahore target not met, smog-season weakness) | We decided not to retune after seeing the test month; Summary and model card show "Lahore NOT met" | Laksh |
| 17 | 2026-10-20 → now | Groq (gpt-oss-120b, gpt-oss-20b), Google Gemini Flash | Inside the product: the Insight Agent writes answers with these models | Every answer is checked by our code (citations and numbers) or replaced by "I don't know"; each answer records which model wrote it | Laksh |

How we worked: Claude wrote most of the code and documents from our instructions. We chose the problem, user,
baseline and test period, ran every command ourselves, read the outputs, reported errors back, and decided what
to keep. The tests (111) and the 24-hour Prefect run were run on our own laptops.
