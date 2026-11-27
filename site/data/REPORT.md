# Evaluation report

Model `0.1.0+20261127` — data: Open-Meteo ERA5 reanalysis.
Test period (never used for training or tuning): **2022-01-01 → 2026-11-21**, 17,860 town-days.

## Next-day rain (≥ 1.0 mm)

| Forecast | Brier ↓ | Skill vs climatology ↑ | AUC ↑ | Accuracy | POD | FAR |
|---|---|---|---|---|---|---|
| model | 0.124 | 29.9% | 0.900 | 82.3% | 87.5% | 15.5% |
| climatology | 0.176 | 0.0% | 0.802 | 73.4% | 75.1% | 19.4% |
| markov | 0.144 | 18.6% | 0.863 | 80.2% | 84.7% | 16.4% |
| persistence | 0.202 | -14.2% | 0.786 | 79.8% | 83.7% | 16.3% |

## Versus professional forecasts (2024-02-05 → 2026-11-21, 10,170 town-days)

Same rows for every entry. NWP forecasts are day-ahead runs, scored as yes/no (≥ 1 mm forecast).

| Forecast | Type | Brier ↓ | Skill vs clim ↑ | Accuracy | CSI ↑ | POD | FAR |
|---|---|---|---|---|---|---|---|
| Weather Lab (LightGBM) | probability | 0.128 | 27.4% | 81.8% | 0.738 | 86.9% | 16.9% |
| Weather Lab, yes/no at 50% | yes/no | 0.182 | -3.9% | 81.8% | 0.738 | 86.9% | 16.9% |
| ECMWF IFS 0.25° | yes/no (≥1 mm forecast) | 0.185 | -5.3% | 81.5% | 0.746 | 91.6% | 19.9% |
| NCEP GFS | yes/no (≥1 mm forecast) | 0.197 | -12.2% | 80.3% | 0.704 | 79.2% | 13.6% |
| DWD ICON | yes/no (≥1 mm forecast) | 0.249 | -41.7% | 75.1% | 0.608 | 65.2% | 10.0% |
| Markov | baseline | 0.145 | 17.3% | 79.8% | 0.712 | 84.0% | 17.7% |
| Climatology | baseline | 0.176 | 0.0% | 73.8% | 0.634 | 76.8% | 21.5% |
| Persistence | baseline | 0.207 | -17.8% | 79.3% | 0.703 | 82.6% | 17.5% |

## Heavy rain (≥ 50.0 mm) — research only

Base rate 1.3%. Model Brier skill 9.6%, AUC 0.920, average precision 0.218 (climatology AP 0.074).

## By season

| Season | Model BSS | Markov BSS | Base rate |
|---|---|---|---|
| First inter-monsoon (Mar–Apr) | 26.5% | 16.6% | 58.3% |
| Northeast monsoon (Dec–Feb) | 30.9% | 18.7% | 54.9% |
| Second inter-monsoon (Oct–Nov) | 36.2% | 22.6% | 81.4% |
| Southwest monsoon (May–Sep) | 28.4% | 18.0% | 59.8% |

## By town

| Town | Model BSS | Markov BSS | AUC | Base rate |
|---|---|---|---|---|
| anuradhapura | 25.3% | 14.5% | 0.831 | 51.3% |
| batticaloa | 36.3% | 26.1% | 0.889 | 49.7% |
| colombo | 22.9% | 11.9% | 0.920 | 77.0% |
| galle | 21.8% | 13.3% | 0.899 | 85.2% |
| hambantota | 31.6% | 20.2% | 0.864 | 45.2% |
| jaffna | 37.0% | 23.0% | 0.880 | 41.1% |
| kandy | 30.5% | 18.4% | 0.842 | 61.1% |
| nuwara_eliya | 27.8% | 17.0% | 0.880 | 75.6% |
| ratnapura | 21.8% | 9.3% | 0.856 | 90.3% |
| trincomalee | 31.4% | 20.8% | 0.877 | 43.2% |

## Cross-check against NASA POWER precipitation

ERA5 and NASA POWER agree on rain/no-rain on 78.3% of test days. Scored against POWER instead of ERA5, the model's Brier skill is 22.8% (Markov 12.1%).

## Caveats

- Truth is ERA5 reanalysis grid-cell rainfall (~25 km), not a rain gauge. It smooths showers, overcounts drizzle days and underestimates extremes.
- The live model reads Open-Meteo's operational analysis for its inputs, not ERA5. The live ledger on the site measures how much that costs.
- NWP forecasts are single deterministic runs scored yes/no, which penalises them under Brier score; compare CSI/POD/FAR for a like-for-like view.

## Top features (share of gain)

- `precip` 42.6%
- `precip_hours` 10.8%
- `rain` 8.8%
- `wetdays30` 1.9%
- `et0` 1.7%
- `isl_trincomalee` 1.7%
- `wind_max` 1.6%
- `doy_cos` 1.6%
- `doy_sin` 1.3%
- `town_code` 1.3%
- `rh` 1.3%
- `isl_pmsl` 1.1%
