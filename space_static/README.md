---
title: Pakistan Smog Forecast
emoji: 🌫️
colorFrom: gray
colorTo: red
sdk: static
app_file: index.html
pinned: false
license: mit
short_description: Next-day smog forecast for 4 Pakistani cities
---

# Next-day smog forecast for Pakistan

Live dashboard for the HackInsight 2026 project. A static page that reads the files the hourly
pipeline publishes to a public Hugging Face dataset, and shows pipeline health (last successful run,
data freshness, quality gates), tomorrow's forecast with the chance of an unhealthy day and an
advisory recommendation, the reasons behind each forecast, a live report card, back-test results,
the advisory-rule replay and the data quality log. Urdu view included.

Bundled libraries (lib/): PapaParse 5.4.1, Plotly.js 2.35.2, marked 12.0.2 (all MIT licence).

Information only, not medical advice. Follow official advisories (Punjab EPA, PDMA, PMD).
