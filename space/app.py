"""Smog forecast dashboard (Hugging Face Space).

Reads the small files that the hourly pipeline publishes to a Hugging Face dataset
(see src/smog/publish.py) and shows:
  - pipeline health: last successful run, data freshness, quality gate results;
  - tomorrow's forecast per city with a chance of an unhealthy day and an advisory
    recommendation that follows the officer's own cost ratio (slider);
  - why each forecast is high or low; a live report card; back-test results; the quality log.

Every number shown comes from those published files (gold rows / reports); nothing is typed in here.
Run locally:  LOCAL_DATA_DIR=path/to/data/publish python app.py
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import gradio as gr
import pandas as pd
import plotly.graph_objects as go

DATA_REPO = os.getenv("DATA_REPO", "")
LOCAL_DATA_DIR = os.getenv("LOCAL_DATA_DIR", "")
UNHEALTHY = 55.5
PKT = "Asia/Karachi"

CITIES = {
    "lahore": {"en": "Lahore", "ur": "لاہور", "color": "#c2410c"},
    "islamabad_rawalpindi": {"en": "Islamabad / Rawalpindi", "ur": "اسلام آباد / راولپنڈی", "color": "#0e7490"},
    "peshawar": {"en": "Peshawar", "ur": "پشاور", "color": "#a16207"},
    "karachi": {"en": "Karachi", "ur": "کراچی", "color": "#4338ca"},
}
DRIVERS_UR = {
    "smog already in the air (today and recent days)": "فضا میں پہلے سے موجود اسموگ (آج اور پچھلے دن)",
    "tomorrow's weather (wind, rain, mixing height)": "کل کا موسم (ہوا، بارش، فضا کی ملاوٹ کی اونچائی)",
    "today's weather": "آج کا موسم",
    "fires nearby (crop burning)": "قریبی آگ (فصلوں کی باقیات جلانا)",
    "CAMS air-quality model": "CAMS فضائی معیار ماڈل",
    "season": "موسم کا وقت (سیزن)",
}

# --- Loading the published files ------------------------------------------------------------------

_cache: dict = {"at": 0.0, "dir": None}


def data_dir() -> Path | None:
    if LOCAL_DATA_DIR:
        return Path(LOCAL_DATA_DIR)
    if not DATA_REPO:
        return None
    if time.time() - _cache["at"] > 60 or _cache["dir"] is None:     # at most one download check per minute
        from huggingface_hub import snapshot_download
        _cache["dir"] = Path(snapshot_download(DATA_REPO, repo_type="dataset"))
        _cache["at"] = time.time()
    return _cache["dir"]


def read_csv(name: str) -> pd.DataFrame:
    d = data_dir()
    try:
        return pd.read_csv(d / name) if d else pd.DataFrame()
    except Exception:
        return pd.DataFrame()


def read_json(name: str) -> dict:
    d = data_dir()
    try:
        return json.loads((d / name).read_text(encoding="utf-8")) if d else {}
    except Exception:
        return {}


def read_text(name: str) -> str:
    d = data_dir()
    try:
        return (d / name).read_text(encoding="utf-8") if d else ""
    except Exception:
        return ""


def to_pkt(series) -> pd.Series:
    return pd.to_datetime(series, utc=True, format="mixed").dt.tz_convert(PKT)


def ago(ts) -> str:
    if ts is None or pd.isna(ts):
        return "unknown"
    minutes = (datetime.now(timezone.utc) - pd.Timestamp(ts).tz_convert("UTC")).total_seconds() / 60
    if minutes < 90:
        return f"{minutes:.0f} min ago"
    return f"{minutes / 60:.1f} h ago"


def threshold_for(r: float) -> float:
    return 1.0 / (1.0 + r)


# --- Pipeline health ------------------------------------------------------------------------------

def status_html() -> str:
    status = read_json("pipeline_status.json")
    published = read_json("published.json")
    fresh = read_csv("freshness.csv")
    gates = read_csv("quality_gates.csv")
    if not status and not published:
        return "<div class='banner bad'>No published data found yet. The pipeline has not uploaded anything.</div>"

    last_ok = (status.get("last_success") or {}).get("finished_utc")
    last = status.get("last_run") or {}
    last_state = last.get("status", "unknown")
    pub = published.get("published_utc")
    pub_age_h = ((datetime.now(timezone.utc) - pd.Timestamp(pub).tz_convert("UTC")).total_seconds() / 3600) if pub else 99
    gate_ok = bool(len(gates)) and bool(gates.iloc[0].get("gate1_passed")) and bool(gates.iloc[0].get("gate2_passed"))
    stale = fresh[fresh.get("status", pd.Series(dtype=str)).astype(str).str.upper() == "STALE"] if len(fresh) else fresh

    def chip(ok: bool, text: str) -> str:
        return f"<span class='chip {'ok' if ok else 'bad'}'>{text}</span>"

    when = lambda t: pd.Timestamp(t).tz_convert(PKT).strftime("%a %d %b, %H:%M PKT") if t else "never"
    chips = [
        chip(pub_age_h < 2, f"Dashboard data updated {ago(pub) if pub else 'never'}"),
        chip(last_state == "success", f"Last run: {last_state}"),
        chip(bool(last_ok), f"Last successful run: {when(last_ok)} ({ago(last_ok) if last_ok else '-'})"),
        chip(gate_ok, "Quality gates: PASS" if gate_ok else "Quality gates: FAILED"),
        chip(len(stale) == 0, "All sources fresh" if len(stale) == 0 else f"Stale: {', '.join(stale['source'])}"),
    ]
    warn = ""
    if pub_age_h >= 2:
        warn = ("<div class='banner bad'>The hourly pipeline has not published for over 2 hours. "
                "Forecasts below may be out of date.</div>")
    elif last_state != "success":
        warn = (f"<div class='banner bad'>The last run failed ({(last.get('steps') or {}).get('error', '')}). "
                "Showing results from the last successful run.</div>")
    return warn + "<div class='chips'>" + "".join(chips) + "</div>"


# --- Tomorrow's forecast ---------------------------------------------------------------------------

def latest_predictions() -> pd.DataFrame:
    p = read_csv("predictions.csv")
    if p.empty or "issued_utc" not in p:
        return p
    p["issued"] = to_pkt(p["issued_utc"])
    latest = p["issued"].max()
    return p[p["issued"] == latest].copy()


def cards_html(cost_ratio: float, lang: str) -> str:
    p = latest_predictions()
    if p.empty:
        return "<p>No forecast published yet.</p>"
    ur = lang == "اردو"
    thr = threshold_for(cost_ratio)
    issued = p["issued"].iloc[0]
    target = pd.Timestamp(p["target_day_pkt"].iloc[0])
    cards = []
    order = [c for c in CITIES if c in set(p["city_id"])]
    for city in order:
        r = p[p["city_id"] == city].iloc[0]
        prob = float(r.get("p_unhealthy", float("nan")))
        warn = prob >= thr
        info = CITIES[city]
        level = "vhigh" if r["pm25_p10"] >= UNHEALTHY else "high" if r["pm25_pred"] >= UNHEALTHY else \
            "mid" if r["pm25_p90"] >= UNHEALTHY else "low"
        drivers = []
        try:
            for d in json.loads(r.get("drivers_json") or "[]")[:3]:
                eff = d["effect_ugm3"]
                if ur:
                    verb = "بڑھاتا ہے" if eff > 0 else "کم کرتا ہے"
                    drivers.append(f"<li>{DRIVERS_UR.get(d['driver'], d['driver'])}: {abs(eff):.0f} µg/m³ {verb}</li>")
                else:
                    drivers.append(f"<li><b>{'+' if eff > 0 else '−'}{abs(eff):.0f}</b> {d['driver']}</li>")
        except Exception:
            pass
        if ur:
            advice = "اسموگ ایڈوائزری جاری کرنے کی سفارش" if warn else "ایڈوائزری کی ضرورت نہیں"
            body = (f"<div class='num'>{r['pm25_pred']:.0f}<span> µg/m³</span></div>"
                    f"<div class='range'>اندازاً {r['pm25_p10']:.0f} سے {r['pm25_p90']:.0f} تک (80٪ امکان)</div>"
                    f"<div class='prob'>غیر صحت مند دن کا امکان: <b>{prob:.0%}</b></div>"
                    f"<div class='advice {'warn' if warn else 'calm'}'>{advice}</div>"
                    f"<div class='why'>وجوہات:<ul>{''.join(drivers)}</ul></div>")
            name = info["ur"]
        else:
            advice = "Recommend: issue smog advisory" if warn else "No advisory needed"
            body = (f"<div class='num'>{r['pm25_pred']:.0f}<span> µg/m³</span></div>"
                    f"<div class='range'>80% range: {r['pm25_p10']:.0f} – {r['pm25_p90']:.0f}</div>"
                    f"<div class='prob'>Chance of an unhealthy day (≥ {UNHEALTHY}): <b>{prob:.0%}</b></div>"
                    f"<div class='advice {'warn' if warn else 'calm'}'>{advice}</div>"
                    f"<div class='why'>Why (vs a typical day):<ul>{''.join(drivers)}</ul></div>")
            name = info["en"]
        cards.append(f"<div class='card {level}' style='border-top-color:{info['color']}' "
                     f"{'dir=rtl' if ur else ''}><div class='city'>{name}</div>{body}"
                     f"<div class='src'>row {r['row_id']}</div></div>")
    head = (f"<p {'dir=rtl' if ur else ''}><b>{'کل' if ur else 'Tomorrow'}: {target:%A %d %B %Y}</b> · "
            f"{'جاری کیا گیا' if ur else 'issued'} {issued:%H:%M PKT %d %b} · "
            f"{'ایڈوائزری کی حد' if ur else 'advisory when chance ≥'} {thr:.0%} (R = {cost_ratio:g})</p>")
    return head + "<div class='cards'>" + "".join(cards) + "</div>"


def urdu_bulletin(cost_ratio: float) -> str:
    """A short Urdu advisory an officer could forward (numbers straight from the latest gold rows)."""
    p = latest_predictions()
    if p.empty:
        return ""
    thr = threshold_for(cost_ratio)
    target = pd.Timestamp(p["target_day_pkt"].iloc[0])
    lines = [f"اسموگ پیش گوئی برائے {target:%d-%m-%Y} (اوسط PM2.5، µg/m³)", ""]
    for city in [c for c in CITIES if c in set(p["city_id"])]:
        r = p[p["city_id"] == city].iloc[0]
        flag = "⚠️ ایڈوائزری کی سفارش" if r["p_unhealthy"] >= thr else "✅ معمول"
        lines.append(f"• {CITIES[city]['ur']}: {r['pm25_pred']:.0f} ({r['pm25_p10']:.0f}–{r['pm25_p90']:.0f})، "
                     f"غیر صحت مند دن کا امکان {r['p_unhealthy']:.0%} — {flag}")
    lines += ["", "احتیاط: باہر کم وقت گزاریں، ماسک (N95) استعمال کریں، بچوں اور بزرگوں کا خاص خیال رکھیں۔",
              "یہ ایک کمپیوٹر ماڈل کا اندازہ ہے؛ سرکاری ہدایات (پنجاب EPA / PDMA) پر عمل کریں۔"]
    return "\n".join(lines)


def evolution_plot() -> go.Figure:
    p = read_csv("predictions.csv")
    fig = go.Figure()
    if p.empty:
        return fig
    p["issued"] = to_pkt(p["issued_utc"])
    target = p.loc[p["issued"].idxmax(), "target_day_pkt"]
    p = p[p["target_day_pkt"] == target]
    for city, info in CITIES.items():
        c = p[p["city_id"] == city].sort_values("issued")
        if c.empty:
            continue
        fig.add_trace(go.Scatter(x=list(c["issued"]) + list(c["issued"][::-1]),
                                 y=list(c["pm25_p90"]) + list(c["pm25_p10"][::-1]), fill="toself",
                                 fillcolor=info["color"], opacity=0.12, line=dict(width=0),
                                 hoverinfo="skip", showlegend=False))
        fig.add_trace(go.Scatter(x=c["issued"], y=c["pm25_pred"], name=info["en"], mode="lines+markers",
                                 line=dict(color=info["color"], width=2)))
    fig.add_hline(y=UNHEALTHY, line_dash="dot", line_color="#991b1b", annotation_text="unhealthy (55.5)")
    fig.update_layout(title=f"How the forecast for {target} changed with each hourly run",
                      xaxis_title="issued (PKT)", yaxis_title="PM2.5 µg/m³", height=380,
                      margin=dict(l=40, r=20, t=50, b=40), legend=dict(orientation="h", y=-0.25))
    return fig


# --- History and back-test --------------------------------------------------------------------------

def history_plot(city: str) -> go.Figure:
    d = read_csv("city_daily.csv")
    fig = go.Figure()
    if d.empty:
        return fig
    d["day_pkt"] = pd.to_datetime(d["day_pkt"])
    d = d[(d["city_id"] == city) & (d["day_pkt"] >= d["day_pkt"].max() - pd.Timedelta(days=60))]
    info = CITIES.get(city, {"en": city, "color": "#333"})
    fig.add_trace(go.Scatter(x=d["day_pkt"], y=d["pm25_mean"], name="measured daily mean",
                             mode="lines+markers", line=dict(color=info["color"])))
    s = read_csv("scorecard.csv")
    if len(s):
        s = s[s["city_id"] == city]
        fig.add_trace(go.Scatter(x=pd.to_datetime(s["target_day_pkt"]), y=s["pm25_pred"], name="our forecast (evening before)",
                                 mode="markers", marker=dict(symbol="diamond", size=11, color="#111827"),
                                 error_y=dict(type="data", symmetric=False, array=s["pm25_p90"] - s["pm25_pred"],
                                              arrayminus=s["pm25_pred"] - s["pm25_p10"])))
    fig.add_hline(y=UNHEALTHY, line_dash="dot", line_color="#991b1b")
    fig.update_layout(title=f"{info['en']}: last 60 days (measured) and our live forecasts", height=380,
                      yaxis_title="PM2.5 µg/m³", margin=dict(l=40, r=20, t=50, b=40), legend=dict(orientation="h", y=-0.2))
    return fig


def backtest_plot(city: str) -> go.Figure:
    t = read_csv("reports/test_predictions.csv")
    fig = go.Figure()
    if t.empty:
        return fig
    t = t[t["city_id"] == city].sort_values("target_day")
    info = CITIES.get(city, {"en": city, "color": "#333"})
    fig.add_trace(go.Scatter(x=list(t["target_day"]) + list(t["target_day"][::-1]),
                             y=list(t["p90"]) + list(t["p10"][::-1]), fill="toself", fillcolor=info["color"],
                             opacity=0.15, line=dict(width=0), name="80% range"))
    fig.add_trace(go.Scatter(x=t["target_day"], y=t["prediction"], name="forecast", line=dict(color=info["color"])))
    fig.add_trace(go.Scatter(x=t["target_day"], y=t["target"], name="what happened", mode="markers",
                             marker=dict(color="#111827")))
    fig.add_trace(go.Scatter(x=t["target_day"], y=t["baseline_same_info"], name="baseline: same as today",
                             line=dict(color="#9ca3af", dash="dash")))
    fig.add_hline(y=UNHEALTHY, line_dash="dot", line_color="#991b1b")
    fig.update_layout(title=f"{info['en']}: back-test on the locked test month (never used for tuning)",
                      height=380, yaxis_title="PM2.5 µg/m³", margin=dict(l=40, r=20, t=50, b=40),
                      legend=dict(orientation="h", y=-0.2))
    return fig


def scorecard_table() -> pd.DataFrame:
    s = read_csv("scorecard.csv")
    if s.empty:
        return pd.DataFrame({"note": ["The first live forecasts are scored once their target day has data."]})
    s["city"] = s["city_id"].map(lambda c: CITIES.get(c, {}).get("en", c))
    cols = {"target_day_pkt": "day", "city": "city", "pm25_pred": "forecast", "pm25_p10": "low", "pm25_p90": "high",
            "p_unhealthy": "P(unhealthy)", "pm25_mean": "measured", "status": "status", "error": "our error",
            "persistence_error": "baseline error", "issued_pkt": "issued (PKT)"}
    return s[[c for c in cols if c in s.columns]].rename(columns=cols)


def replay(cost_ratio: float) -> pd.DataFrame:
    """Hits / misses / false alarms of three warning rules on past days the model never trained on."""
    thr = threshold_for(cost_ratio)
    rows = []
    for period, name in (("validation months", "reports/validation_predictions.csv"),
                         ("test month (October)", "reports/test_predictions.csv")):
        df = read_csv(name)
        if df.empty or "p_unhealthy" not in df:
            continue
        bad = df["target"] >= UNHEALTHY
        for label, warn in ((f"Our rule: chance ≥ {thr:.0%}", df["p_unhealthy"] >= thr),
                            ("Warn if today is unhealthy", df["baseline_same_info"] >= UNHEALTHY),
                            ("Warn if forecast number ≥ 55.5", df["prediction"] >= UNHEALTHY)):
            hits, misses = int((warn & bad).sum()), int((~warn & bad).sum())
            fa = int((warn & ~bad).sum())
            rows.append({"period": period, "rule": label, "unhealthy days": int(bad.sum()), "caught": hits,
                         "missed": misses, "false alarms": fa,
                         "cost per 100 days": round(100 * (fa + cost_ratio * misses) / max(len(df), 1), 1)})
    return pd.DataFrame(rows)


def reliability_plot() -> go.Figure:
    dm = read_json("reports/decision_metrics.json")
    rel = pd.DataFrame(dm.get("reliability_validation", []))
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=[0, 1], y=[0, 1], mode="lines", line=dict(dash="dash", color="#9ca3af"),
                             name="perfectly honest"))
    if len(rel):
        fig.add_trace(go.Scatter(x=rel["said"], y=rel["happened"], mode="markers+lines", name="our chances",
                                 marker=dict(size=[max(6, min(30, n / 15)) for n in rel["days"]], color="#0e7490"),
                                 text=[f"{n} days" for n in rel["days"]]))
    fig.update_layout(title="When we said X% chance, how often was it really unhealthy? (validation months)",
                      xaxis_title="chance we gave", yaxis_title="share of days that were unhealthy",
                      xaxis_tickformat=".0%", yaxis_tickformat=".0%", height=360, margin=dict(l=40, r=20, t=50, b=40))
    return fig


def errors_table() -> pd.DataFrame:
    e = read_csv("reports/test_errors_by_city.csv")
    if len(e):
        e["city"] = e["city"].map(lambda c: CITIES.get(c, {}).get("en", c))
    return e


def headline_md() -> str:
    m = read_json("reports/model_metrics.json")
    if not m:
        return ""
    t = m["test"]
    return (f"**Back-test (locked test month, {m['test_rows']} city-days):** our error {t['model']['mae']} µg/m³ vs "
            f"{t['baseline_same_info']['mae']} for *same as today* → **{t['improvement_vs_baseline_pct']}% better**. "
            f"80% range held the real value {t['interval_80_coverage']:.0%} of the time "
            f"(calibrated on validation months only).")


# --- Quality ------------------------------------------------------------------------------------------

def quality_tables():
    gates = read_csv("quality_gates.csv").head(24)
    dec = read_csv("quality_decisions.csv")
    keep = [c for c in ("check", "action", "rule", "rows_affected", "reason") if c in dec.columns]
    fresh = read_csv("freshness.csv")
    counts = read_csv("qc_status_counts.csv")
    return gates, dec[keep] if keep else dec, fresh, counts


# --- Page ---------------------------------------------------------------------------------------------

CSS = """
.chips{display:flex;flex-wrap:wrap;gap:8px;margin:4px 0 8px}
.chip{padding:6px 12px;border-radius:999px;font-size:14px;font-weight:600}
.chip.ok{background:#dcfce7;color:#14532d}.chip.bad{background:#fee2e2;color:#7f1d1d}
.banner{padding:10px 14px;border-radius:10px;margin-bottom:8px;font-weight:600}
.banner.bad{background:#fee2e2;color:#7f1d1d}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:14px}
.card{border:1px solid #e5e7eb;border-top:6px solid;border-radius:14px;padding:14px;background:var(--block-background-fill)}
.card .city{font-size:18px;font-weight:700;margin-bottom:6px}
.card .num{font-size:40px;font-weight:800;line-height:1}.card .num span{font-size:14px;font-weight:500}
.card .range,.card .prob{margin-top:6px;font-size:14px}
.card .advice{margin-top:10px;padding:8px 10px;border-radius:8px;font-weight:700;text-align:center}
.advice.warn{background:#fee2e2;color:#7f1d1d}.advice.calm{background:#dcfce7;color:#14532d}
.card .why{margin-top:10px;font-size:13px}.card .why ul{margin:4px 0 0 18px;padding:0}
.card .src{margin-top:8px;font-size:11px;opacity:.6;word-break:break-all}
"""

INTRO = """# Next-day smog forecast for Pakistan
Tomorrow's average PM2.5 for **Lahore, Islamabad/Rawalpindi, Peshawar and Karachi**, updated every hour from
OpenAQ sensors, NASA FIRMS fires, Open-Meteo weather and CAMS. Built for an environment officer deciding
**the evening before** whether to issue a smog advisory. *Information only, not medical advice: follow official
advisories (Punjab EPA, PDMA, PMD).*"""


def refresh(cost_ratio, lang, city):
    gates, dec, fresh, counts = quality_tables()
    return (status_html(), cards_html(cost_ratio, lang), urdu_bulletin(cost_ratio), evolution_plot(),
            history_plot(city), scorecard_table(), backtest_plot(city), errors_table(), headline_md(),
            replay(cost_ratio), reliability_plot(), gates, dec, fresh, counts,
            read_text("reports/MODEL_CARD.md") or "Model card not published yet.")


with gr.Blocks(title="Pakistan smog forecast") as demo:
    gr.Markdown(INTRO)
    status = gr.HTML()
    with gr.Row():
        cost = gr.Slider(1, 10, value=3, step=0.5, label="How many times worse is a MISSED smog day than a false alarm? (R)",
                         info="Advisory when the chance of an unhealthy day ≥ 1/(1+R). Default R = 3 → 25%.")
        lang = gr.Radio(["English", "اردو"], value="English", label="Language / زبان")
        refresh_btn = gr.Button("Refresh", variant="secondary", scale=0)
    with gr.Tab("Tomorrow"):
        cards = gr.HTML()
        evo = gr.Plot()
        with gr.Accordion("Urdu advisory text (copy and forward)", open=False):
            bulletin = gr.Textbox(lines=9, show_label=False, rtl=True)
    with gr.Tab("Report card"):
        gr.Markdown("### Live: our evening forecast vs what really happened\n"
                    "Each day is scored with the forecast issued around **20:05 PKT the evening before**. "
                    "*so far today* = the day is not finished yet.")
        city = gr.Dropdown([(v["en"], k) for k, v in CITIES.items()], value="lahore", label="City")
        hist = gr.Plot()
        score = gr.Dataframe(wrap=True)
        gr.Markdown("### Back-test on the locked test month")
        headline = gr.Markdown()
        back = gr.Plot()
        errs = gr.Dataframe()
    with gr.Tab("Advisory rule"):
        gr.Markdown("### Would the advisory rule have worked?\nReplayed on past days the model never trained on. "
                    "Move the R slider at the top to see the trade-off between missed smog days and false alarms.")
        rep = gr.Dataframe()
        rel = gr.Plot()
    with gr.Tab("Data quality"):
        gr.Markdown("### Quality gates (latest runs)\nGate 1 = schema checks, Gate 2 = freshness and completeness. "
                    "If a gate fails, gold tables and forecasts are not rebuilt.")
        gates_df = gr.Dataframe()
        gr.Markdown("### What the Data Quality Agent did in the latest run (and why)")
        dec_df = gr.Dataframe(wrap=True)
        gr.Markdown("### Source freshness")
        fresh_df = gr.Dataframe()
        gr.Markdown("### Hourly sensor readings by quality label")
        counts_df = gr.Dataframe()
    with gr.Tab("Model card"):
        card_md = gr.Markdown()

    outputs = [status, cards, bulletin, evo, hist, score, back, errs, headline, rep, rel, gates_df, dec_df,
               fresh_df, counts_df, card_md]
    inputs = [cost, lang, city]
    demo.load(refresh, inputs, outputs)
    refresh_btn.click(refresh, inputs, outputs)
    cost.change(refresh, inputs, outputs)
    lang.change(refresh, inputs, outputs)
    city.change(refresh, inputs, outputs)
    gr.Timer(300).tick(refresh, inputs, outputs)          # re-check for new data every 5 minutes

if __name__ == "__main__":
    demo.launch(css=CSS, theme=gr.themes.Soft())
