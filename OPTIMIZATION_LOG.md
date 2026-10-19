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

Notes:
- Rows 3 and 4 were measured in our development workspace on generated test data with the same structure as the real data: row 3 used 200 sensors × 10 monthly files × 550 hours; row 4 used 180 sensors, hourly from June 2025 to October 2026, with about 45% of hours missing at random (1.2 M rows). We will re-measure both on the full real data and add the numbers here.
