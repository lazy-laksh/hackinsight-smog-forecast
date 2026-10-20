# Business model: from a forecast to a decision service

## The problem we sell a solution to
Every smog season, Punjab and Khyber Pakhtunkhwa officials decide on school timings, outdoor-activity limits,
traffic and construction restrictions, and hospital preparation. Today those decisions are mostly made **after**
the air is already bad. Punjab's own Smog Action Plan 2025-26 asks for real-time monitoring and **early warnings
using AI-based forecasting** ([P&D Board](https://pnd.punjab.gov.pk/index.php/node/3956)), and the 2026 Pre-Smog
Action Plan tells schools to check the AQI before outdoor activity ([Dawn, Aug 2026](https://www.dawn.com/news/2023819)).
A decision the evening before needs a **forecast for tomorrow**, not today's reading.

## Value proposition
For **a district environment officer**, who needs to **decide the evening before whether to issue a smog advisory**,
our solution **forecasts tomorrow's PM2.5 with a calibrated range, the chance of an unhealthy day and a recommendation
at the officer's own risk threshold, in English and Urdu**, so that **schools, hospitals and enforcement teams can act
before people are exposed**, unlike **today's practice of reacting to the morning's AQI reading**.

| Question | Answer |
|---|---|
| User | District environment officer; school heads and hospital managers who receive the advisory |
| Customer / payer | Provincial environment department or district administration (public budget); private school networks and hospitals for their own alerts |
| Value | Earlier action (the evening before), fewer missed smog days, a public record of what was forecast and why |
| Sustains itself through | Free public dashboard + paid decision service (licence / subscription); could also run on a public budget or grant |
| Running cost | Small: one always-on machine, free data APIs (paid Open-Meteo plan for commercial use), language-model calls |

## What we deliver
| Layer | What the user gets |
|---|---|
| Forecast | Tomorrow's mean PM2.5 per city, with a calibrated 80% range, updated every hour |
| Decision | Chance of an unhealthy day and an advisory recommendation tuned to the user's own cost of a miss vs a false alarm (R slider) |
| Explanation | The main reasons (smog already in the air, weather, fires, season) and an Urdu advisory ready to forward |
| Trust | Public run history, data freshness, quality-gate results, a live "were we right?" report card |
| Questions | Insight Agent that answers with citations to gold rows and official documents, or says "I don't know" |

## Customers and how they pay (illustrative tiers; prices are assumptions to be tested)
| Segment | Need | Offer | Model |
|---|---|---|---|
| Citizens, media | Know tomorrow's risk | Public dashboard + Urdu bulletin | **Free** (public good; builds trust and reach) |
| Provincial / district government (EPA, PDMA, district administrations, school education departments) | Act the evening before; justify decisions | Alerts by SMS/WhatsApp/email at their own threshold, API, more cities, custom reports | **Annual licence** per province or division, with set-up and training |
| Private school networks, universities | Change timings / outdoor activities early | Per-campus alerts, parent message templates in Urdu | **Subscription** per campus |
| Hospitals and clinics | Staff smog counters, stock medicines | Next-day and 3-day risk for their district | **Subscription** per facility |
| Employers with outdoor workers (construction, logistics, delivery) | Plan shifts and masks | Site alerts, compliance log | **Subscription** per site |

Why a buyer pays when the forecast map is free: the paid value is **the decision service** (alerts at the user's
own threshold, Urdu messages, a record of what was forecast and decided), not the raw number.

## Costs (what it takes to run)
Estimates for one province (four to ten cities), per month. **These are assumptions to be checked with real quotes**,
not measured costs; the only measured facts are what our prototype uses today.

| Item | Today (prototype, measured) | At service scale (assumption) |
|---|---|---|
| Compute | One 2018 laptop runs the hourly pipeline; each run takes 6–10 minutes | One small cloud VM, roughly USD 10–20 |
| Dashboard hosting | Free static Hugging Face Space | Same (free) |
| Agent hosting | Render free instance (sleeps when idle) | A paid always-on instance, roughly USD 7–25 |
| Data | OpenAQ, NASA FIRMS, CAMS: free with attribution. Open-Meteo free API is **non-commercial only** ([pricing](https://open-meteo.com/en/pricing)) | Open-Meteo paid plan (Standard: 1M calls/month; price on request) or another licensed weather source |
| Language model | Groq and Gemini free tiers | Paid tier: depends on questions per day; the checker works with any provider |
| People | One developer | Part-time data/ML engineer for maintenance + onboarding/support |

**Net benefit = value created − running cost.** The value side is avoided harm and better-targeted action (earlier
school and hospital measures, fewer missed smog days). We have not priced health outcomes ourselves; a pilot should
measure what officers did differently and how many unhealthy days were warned in time (our report card already logs
forecasts against what happened).

### Key assumptions
* A district office would act on an evening forecast if it is reliable and explained (to be tested in a pilot).
* Running costs above are rough market ranges, not quotes.
* Sensor coverage stays at least at today's level; adding Punjab EPA reference stations would improve trust.

## Pakistan impact
* **Earlier action:** a forecast available at 20:00 lets schools and offices change plans before children and
  workers are exposed, instead of reacting to a morning reading.
* **Fewer missed smog days:** on the validation months our default rule caught far more unhealthy days than the
  "warn if today is unhealthy" rule (see `reports/decision_metrics.json` for the exact counts).
* **In Urdu:** forecasts and advisories that district officers and parents can read and forward.
* **Accountable:** every forecast, run and quality decision is public, so officials can show why they acted.

## Risks and how we handle them
| Risk | Mitigation |
|---|---|
| Low-cost sensors read high in humid air | Show sensor owners; plan humidity correction and comparison with Punjab EPA reference stations |
| Forecast wrong on a key day | Calibrated ranges, a cautious default rule, public report card; "information, not medical advice" |
| A data source stops | Freshness checks, stale sources shown on the dashboard, runs continue with existing data |
| Commercial licence terms of free data | Use paid Open-Meteo plan or alternative before charging customers |

## Next steps
1. Pilot with one district administration in Punjab for one smog season (Nov–Jan).
2. Add Punjab EPA reference stations as a validation and input source.
3. Add more cities (Faisalabad, Gujranwala, Multan) using the same pipeline.
4. SMS/WhatsApp alert channel at each user's chosen R.
