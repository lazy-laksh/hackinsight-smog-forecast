"""Build the one-page summary (reports/Summary.html) from the generated reports.

Every number is read from reports/*.json / *.csv, data/manifest.csv and the warehouse, never typed by hand.
Make the PDF: open reports/Summary.html in Chrome or Edge -> Ctrl+P -> "Save as PDF" (A4, margins: default).
"""
from __future__ import annotations

import json

import pandas as pd

from smog import config, warehouse

SPACE = "https://huggingface.co/spaces/lazypanda21/smog-forecast-pakistan"
REPO = "https://github.com/lazy-laksh/hackinsight-smog-forecast"
CITY = {"lahore": "Lahore", "islamabad_rawalpindi": "Islamabad/Rawalpindi", "peshawar": "Peshawar", "karachi": "Karachi"}


def _load(name: str):
    path = config.ROOT / "reports" / name
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8")) if name.endswith(".json") else pd.read_csv(path)


def _volumes() -> dict:
    out = {}
    try:
        out["raw_files"] = len(pd.read_csv(config.ROOT / "data/manifest.csv"))
    except Exception:
        pass
    queries = {"sensors": "SELECT count(*) FROM cleaned.sensors",
               "pm25_hours": "SELECT count(*) FROM cleaned.pm25_hourly",
               "fires": "SELECT count(*) FROM cleaned.fires",
               "city_days": "SELECT count(*) FROM gold.city_daily WHERE complete",
               "flagged": "SELECT count(*) FROM qc.pm25_hourly WHERE qc_status LIKE 'flag_%'"}
    try:
        with warehouse.connect(read_only=True) as con:
            for key, sql in queries.items():
                try:
                    out[key] = con.execute(sql).fetchone()[0]
                except Exception:
                    pass
    except Exception:
        pass
    return out


def build() -> str:
    m, d = _load("model_metrics.json"), _load("decision_metrics.json")
    if not m:
        return "FAILED: run `python -m smog train` first"
    by_city, ev, v = _load("test_errors_by_city.csv"), _load("agent_eval.csv"), _volumes()
    t, cal = m["test"], m["interval_calibration"]
    fmt = lambda n: f"{n:,}" if isinstance(n, (int, float)) else "–"
    rows = "".join(
        f"<tr><td>{CITY.get(r.city, r.city)}</td><td>{r.model_mae}</td><td>{r.baseline_mae}</td>"
        f"<td>{r.improvement_pct:+.1f}%</td><td>{r.interval_coverage:.0%}</td></tr>" for r in by_city.itertuples()
    ) if by_city is not None else ""
    rule = ""
    if d:
        r = str(d["default_cost_ratio"])
        for period, label in (("validation", "validation months"), ("test", "test month")):
            ours, pers = d[period][r]["our_rule"], d[period][r]["warn_if_today_unhealthy"]
            rule += (f"<li>{label}: caught <b>{ours['hits']}/{ours['unhealthy_days']}</b> unhealthy days with "
                     f"{ours['false_alarms']} false alarms (persistence rule: {pers['hits']}/{pers['unhealthy_days']}, "
                     f"{pers['false_alarms']} false alarms)</li>")
        brier = d["validation"][r]["probability_brier_skill_vs_base_rate"]
    agent = f"{int(ev['passed'].sum())}/{len(ev)}" if ev is not None else "–"
    losers = [CITY.get(c, c) for c in by_city.loc[by_city["improvement_pct"] < 0, "city"]] if by_city is not None else []
    weak = (f"{', '.join(losers)} {'does' if len(losers) == 1 else 'do'} not beat persistence on the test month"
            if losers else "every city beats persistence on the test month")
    success = m["declared_success"]
    lv = _load("local_validation.csv")
    local = ""
    if lv is not None and lv["difference"].notna().any():
        ok = lv.dropna(subset=["difference"])
        diff = ok["difference"].mean()
        local = (f" Checked against Punjab EPA reference stations on {len(ok)} hours: our Lahore value was on average "
                 f"{abs(diff):.0f} µg/m³ {'higher' if diff > 0 else 'lower'} than the EPA station mean "
                 f"(ratio {ok['ratio'].mean():.2f}).")
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Summary - Next-day smog forecast for Pakistan</title>
<style>
@page {{ size: A4; margin: 12mm 13mm; }}
body {{ font: 9.6pt/1.36 "Segoe UI", system-ui, sans-serif; color: #111; margin: 0; }}
h1 {{ font-size: 15pt; margin: 0 0 2px; }} h2 {{ font-size: 10.5pt; margin: 7px 0 2px; color: #0e7490; }}
p, ul {{ margin: 2px 0; }} ul {{ padding-left: 16px; }} li {{ margin: 1px 0; }}
.cols {{ display: grid; grid-template-columns: 1fr 1fr; gap: 0 14px; }}
table {{ border-collapse: collapse; width: 100%; font-size: 8.8pt; margin-top: 2px; }}
th, td {{ border-bottom: 1px solid #ddd; padding: 1.5px 4px; text-align: left; }}
.kpi {{ display: flex; gap: 8px; margin: 5px 0; }} .kpi div {{ flex: 1; border: 1px solid #ddd; border-radius: 6px; padding: 4px 6px; }}
.kpi b {{ font-size: 13pt; display: block; }} .muted {{ color: #555; font-size: 8.4pt; }}
</style></head><body>
<h1>Next-day smog forecast and advisory for Pakistan</h1>
<p class="muted">HackInsight 2026 (Data-to-Decision) · Team XYZ (Laksh Kumar) · Dashboard: {SPACE} · Code: {REPO}</p>
<div class="kpi">
 <div><b>{t['improvement_vs_baseline_pct']}%</b>lower error than persistence (test month, {m['test_rows']} city-days)</div>
 <div><b>{t['interval_80_coverage']:.0%}</b>of real values inside the calibrated 80% range</div>
 <div><b>24/7</b>hourly pipeline, quality gates, public run history</div>
 <div><b>{agent}</b>golden questions passed by the cited Insight Agent</div>
</div>
<div class="cols"><div>
<h2>Problem and user</h2>
<p>Smog decisions in Punjab and KP (school timings, outdoor activity, traffic and construction limits, hospital
readiness) are made too late. A district environment officer needs, <b>at 20:00</b>, tomorrow's PM2.5 and a clear
recommendation. We forecast tomorrow's mean PM2.5 for Lahore, Islamabad/Rawalpindi, Peshawar and Karachi.</p>
<h2>Data and pipeline</h2>
<ul><li>OpenAQ sensors ({fmt(v.get('sensors'))} sensors, {fmt(v.get('pm25_hours'))} hourly readings since June 2025),
NASA FIRMS fires ({fmt(v.get('fires'))} detections), Open-Meteo weather and CAMS.</li>
<li>{fmt(v.get('raw_files'))} raw files, each with a SHA-256 fingerprint in <code>data/manifest.csv</code>, re-verified every run.</li>
<li>Prefect flow every hour: fetch → verify → clean → Data Quality Agent (gate 1 schema, gate 2 freshness and
completeness) → gold → forecast → publish. A failed gate stops the run; every quality decision is logged
({fmt(v.get('flagged'))} sensor-hours flagged: stuck, spikes, zeros, impossible values).</li></ul>
<h2>Model</h2>
<p>Gradient-boosted trees on what is known at 20:00 PKT (today so far, recent days, weather forecast, fires, CAMS).
Settings chosen by month-by-month rolling-origin validation; target days from {m['test_target_days'][0]} were
locked as the test set. Declared baseline: persistence (today so far).</p>
<table><tr><th>City (test month)</th><th>Our MAE</th><th>Persistence</th><th>Better</th><th>80% range</th></tr>{rows}
<tr><td><b>All</b></td><td><b>{t['model']['mae']}</b></td><td><b>{t['baseline_same_info']['mae']}</b></td>
<td><b>{t['improvement_vs_baseline_pct']:+.1f}%</b></td><td><b>{t['interval_80_coverage']:.0%}</b></td></tr></table>
<p class="muted">Declared success (≥10% better overall and in Lahore): overall {'met' if success['overall_met'] else 'NOT met'},
Lahore {'met' if success['lahore_met'] else 'NOT met'}. Strict model without any "tomorrow" inputs:
{t['improvement_without_tomorrow_inputs_pct']}% better. Ranges widened by {cal['widening_ugm3']} µg/m³, chosen on
validation months only (coverage {cal['validation_coverage_before']:.0%} → {cal['validation_coverage_after']:.0%}).</p>
</div><div>
<h2>From forecast to decision</h2>
<p>The calibrated range gives a <b>chance of an unhealthy day</b> (≥ 55.5 µg/m³). The dashboard recommends an
advisory when that chance ≥ 1/(1+R), where R (slider, default 3) is how much worse a missed smog day is than a
false alarm. Replayed on days the model never trained on:</p><ul>{rule}</ul>
<p class="muted">Probability skill vs the base rate (Brier, validation): {brier if d else '–'}. Each forecast also shows its
main reasons (smog already in the air, weather, fires, season) and an Urdu advisory to forward.</p>
<h2>Insight Agent (RAG)</h2>
<p>Retrieves from cited official sources (WHO 2021, US EPA AQI 2024, Pakistan NEQS 2010, Punjab EPD and P&amp;D,
school instructions 2026) and from data facts built by code from gold rows. An LLM (Groq gpt-oss, Gemini fallback)
answers; our code then checks every citation id and every number against the sources, retries once, and otherwise
answers <i>"I don't know based on my sources."</i> Personal details are removed before the model sees a question;
health questions always get a "call Rescue 1122" note. English and Urdu. Golden set: {agent} passed.</p>
<h2>Dashboard and operations</h2>
<p>Free static Hugging Face Space reading the hourly published dataset: last successful run, data freshness,
quality gates, forecasts, live "were we right?" report card, drift monitor (inputs vs the same weeks last year,
live error vs test error, every run), back-test, advisory replay, model card.{local}</p>
<h2>Business model and impact</h2>
<p>Free public dashboard and Urdu bulletin; paid decision service (alerts at the user's own threshold, API, more
cities) for district administrations, school networks, hospitals and employers of outdoor workers. Running cost:
one small machine; a commercial Open-Meteo plan is needed before charging.</p>
<h2>Limitations</h2>
<p>Low-cost sensors (no humidity correction); one value per city; archived weather used for "tomorrow" in training;
{weak}; the default rule trades more false alarms for fewer misses.
Information, not medical advice.</p>
</div></div></body></html>"""
    out = config.ROOT / "reports/Summary.html"
    out.write_text(page, encoding="utf-8")
    return f"wrote {out.relative_to(config.ROOT)}: open it in Chrome/Edge, Ctrl+P, Save as PDF (A4) -> Summary.pdf"
