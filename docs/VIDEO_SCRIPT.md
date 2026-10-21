# Demo video script (3:00 or less, follows the guide's plan)

Record in one go with OBS or the Windows Game Bar (Win + G), 1080p, microphone close, browser zoom 110%.
**Before recording:** open the dashboard and wait for "Assistant ready" in the Ask tab (the free server wakes in about a
minute), do not run `eval-agent` just before (it uses the model allowance), close anything showing `.env` or keys.
Read numbers **from the screen**; they change every hour.

Tabs to have open, in order:
1. Dashboard (top of page): https://huggingface.co/spaces/lazypanda21/smog-forecast-pakistan
2. Dashboard → Data quality tab
3. Prefect Runs page on the Dell (or the screenshot / `prefect_runs.csv`)
4. Dashboard → Ask a question tab
5. Dashboard → Were we right? tab (city table + back-test chart)

| Time | Screen | What you say |
|---|---|---|
| **0:00–0:30** Problem, user, decision | Dashboard top: headline "x of 4 cities need a smog advisory tomorrow" | "Every smog season, district officers in Punjab and KP decide on school activities, masks and hospital smog counters, usually after the air is already bad. Our user is the district environment officer. At 8 PM we give tomorrow's PM2.5 for Lahore, Islamabad–Rawalpindi, Peshawar and Karachi, the chance of an unhealthy day, and a clear advisory recommendation, in English and Urdu." |
| **0:30–1:15** Data and quality decisions | Data quality tab, then Prefect runs | "We use four independent sources: OpenAQ ground sensors (202 sensors, about 900,000 hourly readings), NASA FIRMS fire detections for crop burning, Open-Meteo weather and Copernicus CAMS. Every raw file has a SHA-256 fingerprint that is re-checked each run. Our Data Quality Agent flags stuck sensors and spikes, fills only one- or two-hour gaps, flags stale sources, and logs why. Here it flagged [read a row]. If a gate fails, nothing downstream is rebuilt. Prefect runs this every hour; here is the unedited run history." |
| **1:15–2:15** Live walk-through | Dashboard top → move R slider 3 → 2 → switch to اردو → Ask tab: one cited answer, one "I don't know" | "Each city sits on the official air-quality colour scale: the bar is our 80% range, the tick is the forecast, the circle is today. A number is not a decision, so we turn the range into a chance of an unhealthy day. The officer sets how much worse a missed smog day is than a false alarm; watch Karachi change when I move it. Here is the Urdu view. Now the assistant: 'Should Lahore issue a smog advisory tomorrow?' It answers with numbered citations to the exact gold row; our code checks every number. 'What is the forecast for Multan?' It says it doesn't know, because Multan isn't in our data. Our model beats the declared baseline, 'tomorrow equals today', by 17.7% on the locked October test month." |
| **2:15–3:00** Results by group, limits, Pakistan impact | Were we right? tab: city table, then back-test chart | "By city: Islamabad–Rawalpindi 40% better, Peshawar 20%, Lahore 9%, which is just below the 10% we declared in advance, and Karachi is worse than persistence because its October air is clean and steady. We show both instead of hiding them. Checked against Punjab EPA's own stations, our Lahore values are about 11% lower, so calibrating the sensors is our next step. Our ranges were calibrated on validation months only. Limits: low-cost sensors, one value per city, and the forecast is information, not medical advice. In a medical emergency call Rescue 1122. For Pakistan: Punjab's own smog plan asks for AI-based early warnings; this is a working, open version that runs every hour on a single old laptop, in Urdu as well as English." |

Checklist before uploading:
- [ ] 3:00 or less (trim pauses)
- [ ] One cited answer and one "I don't know" are both shown
- [ ] Results by city and the weakest city are mentioned
- [ ] Numbers spoken match the screen
- [ ] No API key, `.env` or token visible anywhere
- [ ] Uploaded so the link opens without a login (YouTube unlisted, or Google Drive "anyone with the link")
