# Optimization log

Each change: what we changed, the numbers before and after, and whether we kept it.
Rule: change one thing at a time, measure before and after, and keep results identical
(the test suite must still pass).

| # | Date (PKT) | Change | Before | After | Same results? | Kept? |
|---|---|---|---|---|---|---|
| 1 | 2026-10-19 | OpenAQ download: a finished month is downloaded once, then skipped on later runs (only the current month is refreshed) | 1st full run: 2,470 API calls, ~105 min | 2nd run: 621 API calls (most of them retries of OpenAQ server errors) | Yes: `manifest verify` passes, same files | Kept |
| 2 | 2026-10-19 | OpenAQ server errors (HTTP 500): give up on a whole month after 2 tries instead of 5, then fall back to 7-day requests; months that still fail are logged in `data/logs/openaq_known_gaps.csv` and skipped on later runs | Each failing month cost ~30 s of retries on every run (93 months, ~45 min) | Failing months tried once more, then skipped (0 s on later runs) | Yes: no successful data changes; gaps are listed, not hidden | Kept |
| 3 | 2026-10-19 | Cleaning PM2.5: read the hourly JSON files with DuckDB `read_json` instead of a Python loop; convert file paths once per file instead of once per row | 58.6 s for 1.1 M rows | 3.6 s for 1.1 M rows | Yes: same 110,000 de-duplicated rows; all 33 tests pass | Kept |
| 4 | 2026-10-19 | Data Quality Agent gap filling: vectorised with pandas (one step per missing-hour position) instead of a Python loop over every gap; filled values used in a city-hour only when no sensor measured it | 37.1 s for the agent on 1.2 M rows | 19.3 s on the same 1.2 M rows | Same flags and fills; all 42 tests pass. The "measured first" rule is a quality change, not a speed change, and is tested separately | Kept |
| 5 | 2026-10-20 | 80% range: widen the 10%/90% models by an amount chosen on validation months only (conformalized quantile regression) | Range held the real value 59% (validation) / 66% (test) of the time | 80% (validation) / 91% (test); width +5.7 µg/m³ each side | Point forecast unchanged; test set not used to choose it | Kept |
| 6 | 2026-10-20 | Insight Agent: ask Groq gpt-oss for "low" reasoning effort to answer faster | Golden set 16/20 | 9/20: the model wrote citations in other brackets (【】) that our checker rejected | **No, worse** | **Reverted** for Groq |
| 7 | 2026-10-20 | Insight Agent: normalise citations in code (unicode dashes, wide brackets, bare ids) before checking | 9/20 with the faster setting; 16/20 before | 20/20 with gpt-oss-120b | Same checker rules; only formatting differences are forgiven | Kept |
| 8 | 2026-10-20 | Insight Agent: fallback chain when the main model is rate-limited (Groq 120b → Gemini Flash → Groq 20b), remembering busy models | Rate limit = waits of 5–25 min or no answer | Answer from the next model in seconds; golden set 18/20 using only the small fallback | Every answer still passes the same checks | Kept |
| 9 | 2026-10-21 | Re-measured rows 3–4 on the full real data (fresh clone, main laptop) | Rows 3–4 were measured on generated data | Build cleaned layer (all tables, 904,250 PM2.5 hours + fires + weather): 12.9 s; Data Quality Agent on 904,250 hours: 15.6 s; gold: 0.6 s | Yes: both gates passed | Kept |
| 10 | 2026-10-21 | Open-Meteo: retry "200 OK" answers with an empty body, and 1 request/s instead of every 0.3 s | Fresh-clone run crashed at stage 4 | 136 month files in 87 s, 0 failed | Yes: no data changed; broken answers are never saved | Kept |

Notes:
- Rows 3 and 4 were measured in our development workspace on generated test data with the same structure as the real data: row 3 used 200 sensors × 10 monthly files × 550 hours; row 4 used 180 sensors, hourly from June 2025 to October 2026, with about 45% of hours missing at random (1.2 M rows). Both were re-measured on the full real data in row 9.
