"""The Data Quality Agent.

It does four things, every run:
1. RUN TOOLS  - validation checks on the cleaned tables (pandera for schema,
                DuckDB queries for statistical checks).
2. DECIDE     - for each problem found, choose one action:
                  fix  : a safe, certain correction
                  fill : a short estimate, clearly marked as filled
                  flag : leave the value, mark it, and keep it out of averages
3. EXPLAIN    - write the rule, the reason and how many rows were affected.
4. LOG        - store every decision with a timestamp (table qc.decisions and
                data/logs/quality_log.csv, which is committed to Git).

It also runs two gates (see the methodology diagram):
  Gate 1  cleaned data must have the right schema, types and no duplicates.
  Gate 2  the checked data must be fresh and complete enough to publish.
If a gate fails, the gold layer is NOT rebuilt: the dashboard keeps the last
good version and shows the alert.

Every threshold below was chosen after looking at the real data
(`python -m smog profile`); the reason is written next to it.
"""
from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

import pandas as pd
import pandera.pandas as pa

from smog import config, manifest, warehouse

# --- Thresholds (each one explained) -------------------------------------------------
MAX_PLAUSIBLE_PM25 = 2000.0  # highest hourly PM2.5 ever reported in Lahore smog is well under this
STUCK_HOURS = 6              # the same exact value for 6+ consecutive hours is not real air (profile: 66 h runs of 0.0)
SPIKE_RATIO = 4.0            # one hour more than 4x its neighbours...
SPIKE_MIN_JUMP = 200.0       # ...and more than 200 µg/m³ above them (profile: 47 -> 667 -> 45 in May)
ISOLATED_EXTREME = 500.0     # a value above 500 with no neighbour within 3 h cannot be checked
MAX_FILL_HOURS = 2           # gaps of 1-2 hours are filled by straight-line estimate; longer gaps stay missing
STALE_SENSOR_HOURS = 6       # a sensor silent for 6 h is shown as stale
SOURCE_FRESHNESS_HOURS = {"openaq": 3, "firms": 24, "openmeteo_weather": 24, "openmeteo_forecast": 6}
FIRE_GAP_SHARE = 0.10        # a satellite with <10% of the other satellites' detections that month is a product gap
GATE2_MIN_USABLE_SHARE = 0.80   # at least 80% of the last 24 h of PM2.5 rows must be usable
GATE2_MAX_AGE_HOURS = 6         # the newest usable PM2.5 hour must be at most 6 h old

LOG_PATH_NAME = "data/logs/quality_log.csv"


@dataclass
class Decision:
    check: str
    table: str
    action: str            # fix | fill | flag | info
    rule: str
    reason: str
    rows_affected: int
    examples: list = field(default_factory=list)
    gate: str = ""


# --- Gate 1: schema (pandera) ----------------------------------------------------------

PM25_SCHEMA = pa.DataFrameSchema(
    {
        "sensor_id": pa.Column("int64"),
        "ts_utc": pa.Column("datetime64[us, UTC]", coerce=True),
        "pm25": pa.Column(float, nullable=True),
        "source_file": pa.Column(str),
    },
    unique=["sensor_id", "ts_utc"],
    strict=False,
)


def gate1_schema(pm25: pd.DataFrame) -> tuple[bool, list[Decision]]:
    """Types and uniqueness must be right; otherwise nothing downstream can be trusted."""
    try:
        PM25_SCHEMA.validate(pm25, lazy=True)
        return True, [Decision("schema", "cleaned.pm25_hourly", "info",
                               "pandera schema: sensor_id int, ts_utc UTC timestamp, pm25 float, unique (sensor_id, ts_utc)",
                               f"All {len(pm25):,} rows passed the schema check.", 0, gate="gate1")]
    except pa.errors.SchemaErrors as errors:
        cases = errors.failure_cases
        summary = cases.groupby("check").size().to_dict()
        return False, [Decision("schema", "cleaned.pm25_hourly", "flag", "pandera schema",
                                f"Schema check failed: {summary}. Gold will not be rebuilt.",
                                int(len(cases)), cases.head(5).astype(str).to_dict("records"), gate="gate1")]


# --- The tools: each finds one kind of problem ---------------------------------------------

def _load_pm25(con) -> pd.DataFrame:
    return con.execute("""
        SELECT h.sensor_id, s.city_id, h.ts_utc, h.pm25, h.source_file
        FROM cleaned.pm25_hourly h JOIN cleaned.sensors s USING (sensor_id)
        ORDER BY h.sensor_id, h.ts_utc
    """).df()


def label_pm25(con) -> pd.DataFrame:
    """Give every PM2.5 hour a qc_status, using one SQL pass over ~1 million rows.

    Order of precedence (first match wins): out_of_range, zero, stuck, spike, ok.
    """
    return con.execute(f"""
        WITH base AS (
            SELECT h.sensor_id, s.city_id, h.ts_utc, h.pm25, h.source_file,
                   lag(h.pm25)   OVER w AS prev_v, lag(h.ts_utc)  OVER w AS prev_t,
                   lead(h.pm25)  OVER w AS next_v, lead(h.ts_utc) OVER w AS next_t
            FROM cleaned.pm25_hourly h JOIN cleaned.sensors s USING (sensor_id)
            WINDOW w AS (PARTITION BY h.sensor_id ORDER BY h.ts_utc)
        ),
        runs AS (   -- consecutive hours with exactly the same value form one "run"
            SELECT *, sum(CASE WHEN prev_v = pm25 AND ts_utc - prev_t = INTERVAL 1 HOUR THEN 0 ELSE 1 END)
                      OVER (PARTITION BY sensor_id ORDER BY ts_utc) AS run_no
            FROM base
        ),
        sized AS (
            SELECT *, count(*) OVER (PARTITION BY sensor_id, run_no) AS run_len,
                   (ts_utc - prev_t = INTERVAL 1 HOUR) AS prev_near,
                   (next_t - ts_utc = INTERVAL 1 HOUR) AS next_near,
                   coalesce(ts_utc - prev_t <= INTERVAL 3 HOUR, false)
                     OR coalesce(next_t - ts_utc <= INTERVAL 3 HOUR, false) AS any_neighbour_3h
            FROM runs
        ),
        scored AS (
            SELECT *,
                   CASE WHEN prev_near AND next_near THEN (prev_v + next_v) / 2
                        WHEN prev_near THEN prev_v WHEN next_near THEN next_v END AS neighbour_ref
            FROM sized
        )
        SELECT sensor_id, city_id, ts_utc, pm25, source_file, run_len, neighbour_ref,
               CASE
                 WHEN pm25 < 0 OR pm25 > {MAX_PLAUSIBLE_PM25} THEN 'flag_out_of_range'
                 WHEN pm25 = 0 THEN 'flag_zero'
                 WHEN run_len >= {STUCK_HOURS} THEN 'flag_stuck'
                 WHEN neighbour_ref IS NOT NULL AND pm25 > {SPIKE_RATIO} * neighbour_ref
                      AND pm25 - neighbour_ref > {SPIKE_MIN_JUMP} THEN 'flag_spike'
                 WHEN NOT any_neighbour_3h AND pm25 > {ISOLATED_EXTREME} THEN 'flag_spike'
                 ELSE 'ok'
               END AS qc_status
        FROM scored
        ORDER BY sensor_id, ts_utc
    """).df()


def fill_short_gaps(labelled: pd.DataFrame) -> pd.DataFrame:
    """Rows for 1-2 missing hours between two usable hours, by straight-line estimate.

    Vectorised (no Python loop over rows): for k = 1..MAX_FILL_HOURS, every gap at
    least k hours long gets the estimate for its k-th missing hour.
    """
    ok = labelled.loc[labelled["qc_status"] == "ok", ["sensor_id", "city_id", "ts_utc", "pm25"]]
    ok = ok.sort_values(["sensor_id", "ts_utc"])
    prev_t = ok.groupby("sensor_id")["ts_utc"].shift()
    prev_v = ok.groupby("sensor_id")["pm25"].shift()
    span = (ok["ts_utc"] - prev_t) / pd.Timedelta(hours=1)          # 2 = one missing hour, 3 = two
    gaps = ok.assign(prev_t=prev_t, prev_v=prev_v, span=span)
    gaps = gaps[(gaps["span"] >= 2) & (gaps["span"] <= MAX_FILL_HOURS + 1)]

    parts = []
    for k in range(1, MAX_FILL_HOURS + 1):
        g = gaps[gaps["span"] > k]
        parts.append(pd.DataFrame({
            "sensor_id": g["sensor_id"], "city_id": g["city_id"],
            "ts_utc": g["prev_t"] + pd.Timedelta(hours=k),
            "pm25": (g["prev_v"] + (g["pm25"] - g["prev_v"]) * k / g["span"]).round(2),
            "source_file": "filled: straight line between " + g["prev_t"].dt.strftime("%Y-%m-%d %H")
                           + "h and " + g["ts_utc"].dt.strftime("%Y-%m-%d %H") + "h UTC",
        }))
    filled = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    if filled.empty:
        return filled
    # Never fill an hour that exists (even if it was flagged): only truly absent hours.
    present = labelled[["sensor_id", "ts_utc"]].assign(_present=True)
    filled = filled.merge(present, on=["sensor_id", "ts_utc"], how="left")
    filled = filled[filled["_present"].isna()].drop(columns="_present")
    return filled.assign(qc_status="filled").reset_index(drop=True)


def long_gap_stats(labelled: pd.DataFrame) -> tuple[int, int]:
    """How many gaps longer than MAX_FILL_HOURS exist, and how many hours they hide."""
    d = labelled.sort_values(["sensor_id", "ts_utc"])
    gap_h = d.groupby("sensor_id")["ts_utc"].diff() / pd.Timedelta(hours=1) - 1
    long = gap_h[gap_h > MAX_FILL_HOURS]
    return int(len(long)), int(long.sum())


def stale_sensors(labelled: pd.DataFrame, now: datetime) -> pd.DataFrame:
    last = labelled[labelled["qc_status"] == "ok"].groupby(["sensor_id", "city_id"])["ts_utc"].max().reset_index()
    last["silent_hours"] = (pd.Timestamp(now) - last["ts_utc"]) / pd.Timedelta(hours=1)
    # Only sensors that were alive in the last 30 days: long-dead sensors are not "stale", just retired.
    recent = last[(last["silent_hours"] > STALE_SENSOR_HOURS) & (last["silent_hours"] <= 30 * 24)]
    return recent.sort_values("silent_hours")


def weather_observed(con) -> tuple[pd.DataFrame, int]:
    """Past weather minus any hour that was still in the future when the file was fetched."""
    weather = con.execute("SELECT * FROM cleaned.weather_hourly").df()
    fetched = pd.DataFrame(manifest.load())
    if weather.empty or fetched.empty:
        return weather, 0
    fetched = fetched.rename(columns={"file_path": "source_file"})[["source_file", "fetched_at_utc"]]
    fetched["fetched_at_utc"] = pd.to_datetime(fetched["fetched_at_utc"], utc=True)
    w = weather.merge(fetched, on="source_file", how="left")
    future = w["fetched_at_utc"].notna() & (w["ts_utc"] > w["fetched_at_utc"])
    return w.loc[~future].drop(columns="fetched_at_utc"), int(future.sum())


def fire_product_gaps(con) -> pd.DataFrame:
    """Satellite-months with far fewer detections than the other satellites (or none at all)."""
    counts = con.execute("""
        SELECT date_trunc('month', acq_ts_utc)::DATE AS month, family, count(*) AS n
        FROM cleaned.fires GROUP BY 1, 2
    """).df()
    if counts.empty:
        return counts
    grid = pd.MultiIndex.from_product([counts["month"].unique(), counts["family"].unique()],
                                      names=["month", "family"]).to_frame(index=False)
    full = grid.merge(counts, how="left").fillna({"n": 0})
    full["best_other"] = full.apply(
        lambda r: full[(full["month"] == r["month"]) & (full["family"] != r["family"])]["n"].max(), axis=1)
    return full[(full["best_other"] >= 100) & (full["n"] < FIRE_GAP_SHARE * full["best_other"])]


def source_freshness(con, now: datetime) -> pd.DataFrame:
    rows = []
    newest = {
        "openaq": "SELECT max(ts_utc) FROM cleaned.pm25_hourly",
        "firms": "SELECT max(acq_ts_utc) FROM cleaned.fires",
        "openmeteo_forecast": "SELECT max(issued_utc) FROM cleaned.weather_forecast",
    }
    for source, sql in newest.items():
        # .df() instead of .fetchone(): fetchone on a UTC timestamp needs the extra 'pytz' package.
        ts = con.execute(sql).df().iloc[0, 0]
        ts = None if pd.isna(ts) else pd.Timestamp(ts)
        age = (pd.Timestamp(now) - ts).total_seconds() / 3600 if ts is not None else None
        limit = SOURCE_FRESHNESS_HOURS[source]
        rows.append({"source": source, "newest_utc": ts, "age_hours": None if age is None else round(age, 1),
                     "limit_hours": limit, "status": "fresh" if age is not None and age <= limit else "STALE"})
    # Weather archive: judged by when we last fetched it, because reanalysis lags by design.
    weather_fetches = [r["fetched_at_utc"] for r in manifest.load() if r["source"] == "openmeteo_weather"]
    if weather_fetches:
        ts = max(pd.to_datetime(weather_fetches, utc=True))
        age = (pd.Timestamp(now) - ts).total_seconds() / 3600
        limit = SOURCE_FRESHNESS_HOURS["openmeteo_weather"]
        rows.append({"source": "openmeteo_weather (last fetch)", "newest_utc": ts, "age_hours": round(age, 1),
                     "limit_hours": limit, "status": "fresh" if age <= limit else "STALE"})
    return pd.DataFrame(rows)


# --- Gate 2: is the checked data good enough to publish? ------------------------------------

def gate2(qc_pm25: pd.DataFrame, now: datetime) -> tuple[bool, Decision]:
    recent = qc_pm25[qc_pm25["ts_utc"] >= pd.Timestamp(now) - pd.Timedelta(hours=24)]
    usable = recent["usable"].mean() if len(recent) else 0.0
    newest = qc_pm25.loc[qc_pm25["usable"], "ts_utc"].max()
    age = (pd.Timestamp(now) - newest) / pd.Timedelta(hours=1) if pd.notna(newest) else float("inf")
    passed = usable >= GATE2_MIN_USABLE_SHARE and age <= GATE2_MAX_AGE_HOURS
    reason = (f"Last 24 h: {len(recent):,} PM2.5 rows, {usable:.0%} usable (need ≥ {GATE2_MIN_USABLE_SHARE:.0%}); "
              f"newest usable hour is {age:.1f} h old (limit {GATE2_MAX_AGE_HOURS} h). "
              + ("Gold will be rebuilt." if passed else "Gold is NOT rebuilt; the last good version stays published."))
    return passed, Decision("publish_gate", "qc.pm25_hourly", "info" if passed else "flag",
                            "completeness + freshness", reason, 0, gate="gate2")


# --- The agent ------------------------------------------------------------------------------

class DataQualityAgent:
    def __init__(self, now: Optional[datetime] = None):
        self.now = now or datetime.now(timezone.utc)
        self.run_id = self.now.strftime("%Y%m%dT%H%M%SZ")
        self.decisions: list[Decision] = []

    def decide(self, decision: Decision) -> None:
        self.decisions.append(decision)

    def run(self) -> dict:
        with warehouse.connect() as con:
            pm25_raw = con.execute("SELECT * FROM cleaned.pm25_hourly").df()
            gate1_ok, notes = gate1_schema(pm25_raw)
            for d in notes:
                self.decide(d)
            if not gate1_ok:
                return self._finish(con, gate1_ok, False, None, None)

            labelled = label_pm25(con)
            self._explain_flags(labelled)

            filled = fill_short_gaps(labelled)
            self.decide(Decision(
                "short_gap", "qc.pm25_hourly", "fill",
                f"gap of 1-{MAX_FILL_HOURS} missing hours between two usable hours",
                f"Filled {len(filled):,} missing hours with a straight line between the hours on each side. "
                "Filled rows are marked qc_status='filled' so they are never mistaken for measurements, "
                "and a city's hourly value uses them only when no real sensor reported that hour.",
                len(filled), filled.head(3)[["sensor_id", "ts_utc", "pm25"]].astype(str).to_dict("records")))

            n_long, hidden = long_gap_stats(labelled)
            self.decide(Decision(
                "long_gap", "cleaned.pm25_hourly", "flag", f"gap longer than {MAX_FILL_HOURS} hours",
                f"{n_long:,} gaps hide {hidden:,} missing hours. Too long to estimate safely, so they stay "
                "missing; city averages simply use the sensors that did report.", n_long))

            stale = stale_sensors(labelled, self.now)
            self.decide(Decision(
                "stale_sensor", "cleaned.sensors", "flag", f"no usable reading for more than {STALE_SENSOR_HOURS} h",
                f"{len(stale)} sensors that reported in the last 30 days have gone silent; shown as stale "
                "on the dashboard.", len(stale), stale.head(5).astype(str).to_dict("records")))

            weather, n_future = weather_observed(con)
            self.decide(Decision(
                "weather_future_rows", "cleaned.weather_hourly", "fix",
                "an hour later than the time the file was fetched is a forecast, not an observation",
                f"Removed {n_future:,} weather hours that were still in the future when fetched, so the model "
                "never learns from weather it could not have known.", n_future))

            gaps = fire_product_gaps(con)
            self.decide(Decision(
                "fire_product_gap", "cleaned.fires", "flag",
                f"a satellite with < {FIRE_GAP_SHARE:.0%} of the best other satellite's detections in a month",
                (f"{len(gaps)} satellite-month(s) look incomplete: "
                 + ", ".join(f"{r.family} {r.month:%Y-%m} ({int(r.n)} vs {int(r.best_other)})" for r in gaps.itertuples())
                 + ". Fire features therefore use VIIRS_NOAA20 only, which is complete every month.")
                if len(gaps) else "Every satellite has a consistent number of detections every month.",
                len(gaps)))

            freshness = source_freshness(con, self.now)
            stale_sources = freshness[freshness["status"] == "STALE"]
            self.decide(Decision(
                "source_freshness", "all sources", "flag" if len(stale_sources) else "info",
                "newest data per source within its limit " + json.dumps(SOURCE_FRESHNESS_HOURS),
                "All sources are fresh." if stale_sources.empty else
                "Stale: " + ", ".join(f"{r.source} ({r.age_hours} h old, limit {r.limit_hours} h)"
                                      for r in stale_sources.itertuples()) + ". Shown on the dashboard.",
                len(stale_sources)))

            qc = pd.concat([labelled.drop(columns=["run_len", "neighbour_ref"]), filled], ignore_index=True)
            qc["usable"] = qc["qc_status"].isin(["ok", "filled"])
            qc = qc.sort_values(["sensor_id", "ts_utc"]).reset_index(drop=True)
            qc["row_id"] = "pm|" + qc["sensor_id"].astype(str) + "|" + qc["ts_utc"].dt.strftime("%Y%m%dT%H")

            gate2_ok, decision = gate2(qc, self.now)
            self.decide(decision)

            con.execute("CREATE SCHEMA IF NOT EXISTS qc")
            con.register("qc_df", qc)
            con.execute("CREATE OR REPLACE TABLE qc.pm25_hourly AS SELECT * FROM qc_df")
            con.register("weather_df", weather)
            con.execute("CREATE OR REPLACE TABLE qc.weather_observed AS SELECT * FROM weather_df")
            con.register("fresh_df", freshness)
            con.execute("CREATE OR REPLACE TABLE qc.source_freshness AS SELECT * FROM fresh_df")
            stale = stale.assign(run_id=self.run_id)
            con.register("stale_df", stale)
            con.execute("CREATE OR REPLACE TABLE qc.stale_sensors AS SELECT * FROM stale_df")
            return self._finish(con, gate1_ok, gate2_ok, qc, freshness)

    def _explain_flags(self, labelled: pd.DataFrame) -> None:
        texts = {
            "flag_out_of_range": ("PM2.5 below 0 or above 2000 µg/m³",
                                  "Physically impossible for outdoor air; flagged and kept out of averages."),
            "flag_zero": ("PM2.5 exactly 0.0",
                          "Outdoor PM2.5 in these cities is never truly zero; a 0 from a low-cost optical sensor "
                          "means a reset or fault. Flagged and kept out of averages."),
            "flag_stuck": (f"same exact value for {STUCK_HOURS}+ consecutive hours",
                           "Real air changes from hour to hour; a frozen number means the sensor is stuck. "
                           "Flagged and kept out of averages."),
            "flag_spike": (f"one hour > {SPIKE_RATIO:g}x its neighbours and > {SPIKE_MIN_JUMP:g} µg/m³ above them, "
                           f"or > {ISOLATED_EXTREME:g} with no neighbour within 3 h",
                           "A single-hour jump that disappears the next hour is a sensor glitch, not smog "
                           "(real smog builds over several hours). Flagged, not deleted, so it stays visible."),
        }
        for status, (rule, why) in texts.items():
            hits = labelled[labelled["qc_status"] == status]
            self.decide(Decision(
                status.removeprefix("flag_"), "cleaned.pm25_hourly", "flag", rule,
                f"{len(hits):,} hours from {hits['sensor_id'].nunique()} sensors. {why}", len(hits),
                hits.head(3)[["sensor_id", "ts_utc", "pm25"]].astype(str).to_dict("records")))

    def _finish(self, con, gate1_ok, gate2_ok, qc, freshness) -> dict:
        decided_at = self.now.isoformat(timespec="seconds")
        log = pd.DataFrame([{**asdict(d), "examples": json.dumps(d.examples, default=str),
                             "run_id": self.run_id, "decided_at_utc": decided_at} for d in self.decisions])
        con.execute("CREATE SCHEMA IF NOT EXISTS qc")
        con.register("log_df", log)
        con.execute("CREATE TABLE IF NOT EXISTS qc.decisions AS SELECT * FROM log_df WHERE false")
        con.execute("INSERT INTO qc.decisions BY NAME SELECT * FROM log_df")
        gates = pd.DataFrame([{"run_id": self.run_id, "decided_at_utc": decided_at,
                               "gate1_passed": gate1_ok, "gate2_passed": gate2_ok}])
        con.register("gates_df", gates)
        con.execute("CREATE TABLE IF NOT EXISTS qc.gate_results AS SELECT * FROM gates_df WHERE false")
        con.execute("INSERT INTO qc.gate_results BY NAME SELECT * FROM gates_df")
        self._append_csv(log)
        measured = int((qc["qc_status"] != "filled").sum()) if qc is not None else 0
        return {"gate1": gate1_ok, "gate2": gate2_ok, "decisions": len(self.decisions), "run_id": self.run_id,
                "measured": measured,
                "measured_ok": int((qc["qc_status"] == "ok").sum()) if qc is not None else 0,
                "filled": int((qc["qc_status"] == "filled").sum()) if qc is not None else 0}

    @staticmethod
    def _append_csv(log: pd.DataFrame) -> None:
        path = config.ROOT / LOG_PATH_NAME
        path.parent.mkdir(parents=True, exist_ok=True)
        columns = ["run_id", "decided_at_utc", "gate", "check", "table", "action", "rule", "reason",
                   "rows_affected", "examples"]
        new_file = not path.exists()
        with path.open("a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
            if new_file:
                writer.writeheader()
            writer.writerows(log.to_dict("records"))


class QualityGateFailed(RuntimeError):
    pass


def run(now: Optional[datetime] = None) -> str:
    result = DataQualityAgent(now).run()
    summary = (f"{result['decisions']} decisions logged (run {result['run_id']}); "
               f"{result['measured_ok']:,} of {result['measured']:,} measured PM2.5 hours passed, "
               f"{result['measured'] - result['measured_ok']:,} flagged, {result['filled']:,} short gaps filled; "
               f"gate 1 {'PASS' if result['gate1'] else 'FAIL'}, gate 2 {'PASS' if result['gate2'] else 'FAIL'}")
    if not (result["gate1"] and result["gate2"]):
        raise QualityGateFailed(summary + " -> gold not rebuilt (see qc.decisions)")
    return summary
