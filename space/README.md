---
title: Pakistan Smog Forecast
emoji: 🌫️
colorFrom: gray
colorTo: red
sdk: gradio
sdk_version: 6.29.1
python_version: "3.12"
app_file: app.py
pinned: false
license: mit
short_description: Next-day PM2.5 forecast and smog advisory for 4 Pakistani cities
---

# Next-day smog forecast for Pakistan

Live dashboard for the HackInsight 2026 project. It reads the files that the hourly pipeline
publishes to a Hugging Face dataset (Space variable `DATA_REPO`) and shows pipeline health
(last successful run, data freshness, quality gates), tomorrow's forecast with a chance of an
unhealthy day and an advisory recommendation, the reasons behind each forecast, a live report card,
back-test results and the data quality log. Urdu view included.

Information only, not medical advice. Follow official advisories (Punjab EPA, PDMA, PMD).
