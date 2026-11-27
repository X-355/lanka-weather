# 🌧️ Sri Lanka Weather Lab

An open experiment that tests whether a gradient-boosted tree model can predict **tomorrow's rain** for
all 25 districts of Sri Lanka (one point per district capital). It is scored on years it never saw, against simple baselines and against professional
weather models.

Everything runs free: **GitHub Actions** does the fetching, training and nightly forecasts. The model is
published to **Hugging Face**, and the results site is a static page on **GitHub Pages** (optionally also
a static HF Space).

| | |
|---|---|
| Site | `https://x-355.github.io/lanka-weather/` |
| Model | [`xi1514r/lanka-weather`](https://huggingface.co/xi1514r/lanka-weather) on Hugging Face |
| Coverage | The 25 district capitals: Ampara, Anuradhapura, Badulla, Batticaloa, Colombo, Galle, Gampaha, Hambantota, Jaffna, Kalutara, Kandy, Kegalle, Kilinochchi, Kurunegala, Mannar, Matale, Matara, Monaragala, Mullaitivu, Nuwara Eliya, Polonnaruwa, Puttalam, Ratnapura, Trincomalee, Vavuniya |

## What's on the site

1. **Forecast checker**: an island map and tomorrow's rain chance per district and a live, append-only track record. It also
   shows the backtest scoreboard against baselines and ECMWF / GFS / ICON day-ahead forecasts, a
   reliability diagram, skill by town, and a tool to look up what every forecast said on any test day.
2. **Monsoon & rainfall dashboard**: monthly rainfall vs the 1991–2020 normal (with P10–P90 range),
   season totals by year for NEM / FIM / SWM / SIM, and the last 90 days.
3. **Rooftop solar estimator**: monthly and yearly kWh, value and payback, from NASA POWER irradiance.
4. **Heavy-rain risk tracker** (research only): chance of ≥ 50 mm per town vs calendar odds, how
   heavy-rain days are spread across the year, and recent extreme days. It is clearly marked as not a
   warning service.

## How the testing stays honest

- **Split by date, never shuffled.** Train 1980–2018, validation 2019–2021 (early stopping only), and
  **test 2022 → latest**, which is untouched during development.
- **No look-ahead.** Each feature for day *t* uses only data up to the evening of *t*. A unit test changes
  all data after a date and checks that no earlier feature changes (`tests/test_pipeline.py`).
- **Same rows for everyone.** Model, climatology, a Markov-chain baseline (rain today × season),
  persistence and NWP are all scored on identical town-days.
- **Professional forecasts:** day-ahead runs from the Open-Meteo *Previous Runs* archive (from Jan 2024),
  summed to local days. They are scored yes/no at ≥ 1 mm, and the site explains why Brier is harsh on
  yes/no forecasts and shows CSI / POD / FAR too.
- **Second truth:** results are cross-checked against NASA POWER precipitation as well as ERA5.
- **Live ledger** (`predictions/ledger.csv`): each night's forecast is locked before the day happens and
  scored about 6 days later, once ERA5 publishes that day.
- **Synthetic data can't leak out.** CI runs on synthetic data, and the publish step refuses anything
  trained on it.

**Known limitations**
- "Truth" is ERA5 grid-cell rain (~25 km), not a gauge. It smooths out showers and underestimates extremes.
- The live inputs come from Open-Meteo's operational forecast API, because ERA5 is published ~5 days
  late. The training data is ERA5, so the two differ slightly; the ledger measures what that costs.
- Heavy rain is rare (~1% of town-days), so skill there is modest.

## Repository layout

```
config/config.yaml        towns, thresholds, split dates, model settings
lankaweather/
  fetch.py                Open-Meteo ERA5 archive, Previous Runs (NWP), forecast API, NASA POWER
  features.py             leak-free features + next-day targets
  train.py                LightGBM, baselines, evaluation, production refit, REPORT.md
  predict.py              nightly forecast + append-only ledger + scoring
  build_site.py           site/data/*.json
  publish_hf.py           HF model repo + optional static Space
  synthetic.py            fake data with the real schema (CI only)
site/                     static site (vanilla JS, no build step)
.github/workflows/        ci, train (monthly), daily (nightly), pages
data/raw/                 cached data (committed; makes runs incremental)
models/ reports/ predictions/
```

## Setup (once)

1. Create the GitHub repo `X-355/lanka-weather` and push this code.
2. **Settings → Pages → Source: GitHub Actions.**
3. **Settings → Actions → General → Workflow permissions: Read and write.**
4. Hugging Face: create a **write** token, then add it as the repository secret `HF_TOKEN`.
   - Optional variables: `HF_MODEL_REPO` (defaults to `xi1514r/lanka-weather`; set it if your HF username
     is different) and `HF_SPACE_REPO` (set it to also publish the site as a static Space).
5. Run **Actions → Train model → Run workflow**.
   - The first full download is about 47 years × 25 districts. If Open-Meteo's free-tier limit stops it, the
     data so far is committed and the job ends without training. The nightly job keeps filling gaps, so
     run *Train model* again a day later. Each run stays within `max_weight_per_run` in the config.
6. After the first training, **Daily forecast** runs every night at 23:05 Sri Lanka time and redeploys
   the site.

## Run locally

```bash
pip install -r requirements.txt pytest
pytest -q
python -m lankaweather fetch        # real data (resumable)
python -m lankaweather train        # writes models/, reports/REPORT.md
python -m lankaweather predict      # tomorrow's forecast -> ledger + site/data/forecast.json
python -m lankaweather site         # site/data/*.json
cd site && python -m http.server    # open http://localhost:8000
```

To try it with no network, run `python -m lankaweather synthetic` before `train`, and `predict --offline`.
Delete `data/raw` before fetching real data.

## Data & credits

Weather data by [Open-Meteo.com](https://open-meteo.com/) (CC BY 4.0), including Copernicus ERA5 and
archived ECMWF, NOAA GFS and DWD ICON forecasts. Solar irradiance from the
[NASA POWER](https://power.larc.nasa.gov/) project. Not an official forecast: use the
[Department of Meteorology, Sri Lanka](https://www.meteo.gov.lk) and the
[Disaster Management Centre](https://www.dmc.gov.lk) for warnings.

MIT licence.
