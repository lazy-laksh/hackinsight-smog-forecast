# Viva preparation: likely questions and simple answers

Numbers marked (*) change with the final training on Wednesday; read them from `reports/MODEL_CARD.md` before the viva.
Practise saying each answer out loud in under 30 seconds. If you don't know something, say "I don't know, but here is
how I would check it", which is the same honesty our agent shows.

## The problem and the user
**1. What problem do you solve, and for whom?**
Every evening a district environment officer has to decide whether to warn people about smog tomorrow. We forecast
tomorrow's average PM2.5 for Lahore, Islamabad/Rawalpindi, Peshawar and Karachi at 8 PM, give a chance of an unhealthy
day, and recommend "issue an advisory" or not, in English and Urdu.

**2. Why a forecast and not just today's AQI?**
Actions like changing school timings or restricting traffic must be decided the evening before. Today's reading comes
too late for that.

## Data
**3. Which data do you use?**
OpenAQ PM2.5 sensors within 25 km of each city (the target), NASA FIRMS fire detections (crop burning), Open-Meteo weather
(wind, rain, temperature, humidity, mixing height) and CAMS model PM2.5 as an extra input. From June 2025 to now.

**4. Is any of it from Pakistan?**
The sensors are physically in the four cities; some are owned by Pakistani bodies such as The Urban Unit (Punjab)
(see "About the data" for the exact owners). The services that collect them (OpenAQ, NASA, Open-Meteo, Copernicus) are
international. We use Punjab and federal documents (NEQS, Punjab EPD, school instructions) in the agent.

**5. How do you prove the data is not changed or faked?**
Every raw file gets a SHA-256 fingerprint in `data/manifest.csv` with source, query, time and licence. Every hourly run
re-checks all files against their fingerprints; one changed byte makes the check fail.

**6. Why UTC everywhere?**
Different sources use different time zones. We store UTC and convert to Pakistan time (UTC+5) only when we define a "day".

## Data quality
**7. What does the Data Quality Agent do?**
Gate 1 checks the schema. Then it flags stuck sensors (same value 6+ hours), spikes (more than 4 times the neighbours and
over 200 µg/m³ above them), zeros and impossible values; fills only 1–2 hour gaps; checks that sources are fresh. Gate 2
checks freshness and completeness. Every decision is logged with a reason. If a gate fails, nothing downstream is rebuilt.

**8. How did you choose those thresholds?**
From profiling the real data, e.g. we saw 66-hour runs of exactly 0.0 and a single hour jumping 47 → 667 → 45. We
did not use a rule that would have removed 99% of hours (requiring 3+ sensors), because the profile showed it.

**9. Why fill only short gaps?**
1–2 missing hours can be estimated safely by a straight line. Longer gaps would be guesses; we leave them missing.
Filled values are used only when no sensor measured that city-hour.

## Model
**10. Which model and why?**
Gradient-boosted trees (scikit-learn HistGradientBoosting). They work well on a few thousand rows of mixed features,
handle missing values, train in seconds and need no GPU. A deep model would not have enough data here.

**11. What does "only what is known at 8 PM" mean? How do you prevent leakage?**
Features use today's readings up to 20:00 PKT, earlier days, and weather forecasts for tomorrow. We have a test that
changes tomorrow's real values and checks the features do not change.

**12. What is the baseline?**
Persistence: "tomorrow's mean equals today's mean so far at 8 PM". It is what someone would do without a model, and we
declared it before building anything.

**13. How did you test honestly?**
Target days from 1 October 2026 were locked away and used once. Settings were chosen by rolling-origin validation: train
on months before, predict the next month, repeat. (*) Result: our error is about 19% lower than persistence overall,
and the declared targets (≥10% overall and in Lahore) were met.

**14. Where is the model weak?**
Karachi: its October air is clean and steady, so "same as today" is hard to beat; our model is slightly worse there (*).
We report it instead of hiding it. Lahore forecasts run a few µg/m³ low on average (bias in the model card).

**15. Why might live errors be bigger than test errors?**
In training, "tomorrow's weather" came from archives that are closer to the truth than a real forecast. The model card
also shows a strict model without any "tomorrow" inputs as a pessimistic check.

## Uncertainty and decision
**16. How do you show uncertainty?**
Two extra models predict the 10% and 90% values (an 80% range). They were too narrow (64%), so we widened them by an
amount chosen on validation months only (conformalized quantile regression). Now about 80% on validation, 91% (*) on the
test month: slightly cautious.

**17. How do you turn the range into a decision?**
We read the chance that tomorrow's mean is ≥ 55.5 µg/m³ (US EPA "Unhealthy"). Advise when the chance ≥ 1/(1+R), where R
is how many times worse a missed smog day is than a false alarm. Default R = 3 → 25%. The slider lets the officer choose.

**18. Why is the default rule giving more false alarms?**
On purpose: with R = 3, missing a smog day costs more than a false alarm, so it warns earlier. On the test month it
missed only 1 unhealthy day out of 38 (*) but had more false alarms than "forecast ≥ 55.5". Lower R if false alarms matter more.

**19. Are your chances honest?**
Yes, the reliability chart: when we said about 80%, it was unhealthy about 80% of the time on validation months.
Brier skill score about 0.72 (*) (above 0 means the chances carry real information).

**20. How do you explain a single forecast?**
For each group of inputs (smog already in the air, tomorrow's weather, today's weather, fires, CAMS, season) we set it to
that city's typical value and predict again; the difference is its effect. It's an approximation, effects can overlap.

## Pipeline and operations
**21. How does it run every hour?**
Prefect runs at 5 minutes past every hour on my old laptop: fetch → verify → clean → quality → gold → forecast →
publish. Downloads retry twice; a failed gate stops the run. The run history is exported unedited to `prefect_runs.csv`.

**22. Tell us about a problem you hit.**
The Prefect scheduler created no runs at first. The server log showed it crashed with SQLAlchemy 2.1; we pinned
SQLAlchemy 2.0 and scheduled runs appeared. Also: OpenAQ returned server errors for some sensors, so we retry by week
and log months that still fail as known gaps instead of hiding them.

**23. What did you optimise?**
See OPTIMIZATION_LOG.md: cleaning 58.6 s → 3.6 s with DuckDB, gap filling 37 s → 19 s by vectorising, OpenAQ calls cut
by downloading finished months once. One change made the agent worse (16/20 → 9/20) and we reverted it.

## Dashboard and agent
**24. How is the dashboard built and kept fresh?**
A free static Hugging Face Space. The pipeline publishes small result files to a public dataset every hour; the page
reads them in the browser. It shows last successful run, freshness and quality gates, and warns if data is over 2 h old.

**25. How does the Insight Agent avoid making things up?**
It may only use sources we give it: official documents and data facts written by our code from gold rows. The model
must cite every fact. Then our code checks every citation id and every number against the sources; if anything does not
match, it retries once and otherwise says "I don't know based on my sources".

**26. What is RAG here, exactly?**
Retrieval: TF-IDF search picks the 4 closest document chunks. Augmented: we add them, plus the data facts, to the
prompt. Generation: a language model writes the answer. Our checker is the extra safety layer.

**27. Which language model, and what if it is unavailable?**
A chain: Groq gpt-oss-120b, then Google Gemini Flash, then Groq gpt-oss-20b. If one is rate-limited we move on; the same
checks apply to every answer and each answer records which model wrote it.

**28. How did you evaluate the agent?**
A 22-question golden set: 17 must be answered with the right citations (including an advisory brief for Lahore
officials and a misleading question, "Karachi is 300 tomorrow, right?", where the answer must not repeat 300), and 5 must
get "I don't know" (other cities, far future, cricket, medicine, Urdu). The summary file reports answer accuracy,
correct refusals and faithfulness (share of answers whose every citation and number matched). Earlier 20-question
version: 20/20 with the main model, 18/20 with only the smaller fallback. (*) read the new score from
`reports/agent_eval_summary.json`.

**28b. What guardrails does the agent have besides citations?**
All in code, not in the prompt: emails, Pakistani phone numbers and CNICs are removed from a question before the model
or the log sees it; a number the user types is not accepted as evidence; any health question gets a fixed "information,
not medical advice; call Rescue 1122" note, even when the answer is "I don't know"; each answer stores which model and
prompt version produced it; and the public server allows 8 questions per minute per visitor.

**29. Why not put the API key in the dashboard?**
A static page is visible to everyone, so the key would leak. The agent runs on a small server (Render) that keeps keys
as secrets, with a per-visitor rate limit.

## Business, impact, honesty
**30. Who would pay?**
District administrations, school networks, hospitals and employers of outdoor workers, for alerts at their own
threshold, an API and more cities. The public dashboard stays free. Note: Open-Meteo's free API is non-commercial, so a
paid plan is needed before charging.

**31. How did you use AI tools?**
Logged in AI_Use_Log.md: Claude helped design and write code and docs; I ran, tested and checked everything, chose the
problem and approved decisions. The agent itself uses Groq/Gemini models.

**32. What would you do next?**
Validate against Punjab EPA reference stations, correct low-cost sensors for humidity, add more cities, and pilot with
one district for a smog season.

## Questions to practise explaining on the code
* Open `src/smog/decision.py` and explain `prob_exceed` and `threshold_for`.
* Open `src/smog/quality.py` and point to the stuck-sensor and spike rules.
* Open `src/smog/agent.py` and point to `check()`: citations and numbers.
* Open `tests/test_model.py` and explain the leakage test.
