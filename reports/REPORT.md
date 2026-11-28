# Evaluation report

Model `0.1.0+20261128` — data: Open-Meteo ERA5 reanalysis.
Test period (never used for training or tuning): **2022-01-01 → 2026-11-22**, 44,675 town-days.

## Next-day rain (≥ 1.0 mm)

| Forecast | Brier ↓ | Skill vs climatology ↑ | AUC ↑ | Accuracy | POD | FAR |
|---|---|---|---|---|---|---|
| model | 0.127 | 29.8% | 0.897 | 81.9% | 86.7% | 16.2% |
| climatology | 0.181 | 0.0% | 0.796 | 72.7% | 73.7% | 20.7% |
| markov | 0.147 | 18.6% | 0.859 | 79.7% | 83.9% | 17.4% |
| persistence | 0.206 | -14.1% | 0.785 | 79.3% | 82.8% | 17.2% |

## Versus professional forecasts (2024-02-05 → 2026-11-22, 25,450 town-days)

Same rows for every entry. NWP forecasts are day-ahead runs, scored as yes/no (≥ 1 mm forecast).

| Forecast | Type | Brier ↓ | Skill vs clim ↑ | Accuracy | CSI ↑ | POD | FAR |
|---|---|---|---|---|---|---|---|
| Weather Lab (LightGBM) | probability | 0.130 | 27.5% | 81.6% | 0.729 | 86.2% | 17.4% |
| Weather Lab, yes/no at 50% | yes/no | 0.184 | -3.2% | 81.6% | 0.729 | 86.2% | 17.4% |
| ECMWF IFS 0.25° | yes/no (≥1 mm forecast) | 0.178 | 0.2% | 82.2% | 0.747 | 91.3% | 19.6% |
| NCEP GFS | yes/no (≥1 mm forecast) | 0.206 | -15.5% | 79.4% | 0.683 | 77.2% | 14.5% |
| DWD ICON | yes/no (≥1 mm forecast) | 0.242 | -35.5% | 75.8% | 0.609 | 65.4% | 10.2% |
| Markov | baseline | 0.148 | 17.3% | 79.5% | 0.701 | 83.4% | 18.5% |
| Climatology | baseline | 0.178 | 0.0% | 73.4% | 0.622 | 75.9% | 22.6% |
| Persistence | baseline | 0.210 | -17.9% | 79.0% | 0.691 | 81.8% | 18.4% |

## Heavy rain (≥ 50.0 mm) — research only

Base rate 1.1%. Model Brier skill 8.2%, AUC 0.930, average precision 0.191 (climatology AP 0.055).

## By season

| Season | Model BSS | Markov BSS | Base rate |
|---|---|---|---|
| First inter-monsoon (Mar–Apr) | 25.0% | 15.2% | 55.6% |
| Northeast monsoon (Dec–Feb) | 31.0% | 18.6% | 51.4% |
| Second inter-monsoon (Oct–Nov) | 37.9% | 24.8% | 80.6% |
| Southwest monsoon (May–Sep) | 28.2% | 17.9% | 58.6% |

## By town

| Town | Model BSS | Markov BSS | AUC | Base rate |
|---|---|---|---|---|
| ampara | 30.3% | 20.1% | 0.850 | 56.9% |
| anuradhapura | 25.4% | 14.6% | 0.831 | 51.3% |
| badulla | 39.8% | 26.9% | 0.886 | 56.8% |
| batticaloa | 37.0% | 26.0% | 0.891 | 49.7% |
| colombo | 24.7% | 11.7% | 0.922 | 76.9% |
| galle | 23.1% | 13.3% | 0.902 | 85.2% |
| gampaha | 24.7% | 11.7% | 0.922 | 76.9% |
| hambantota | 32.2% | 20.1% | 0.866 | 45.2% |
| jaffna | 37.0% | 23.0% | 0.879 | 41.1% |
| kalutara | 24.4% | 11.1% | 0.912 | 80.9% |
| kandy | 30.0% | 18.3% | 0.840 | 61.1% |
| kegalle | 26.9% | 17.5% | 0.921 | 70.8% |
| kilinochchi | 35.5% | 23.4% | 0.896 | 33.9% |
| kurunegala | 24.2% | 14.8% | 0.888 | 67.3% |
| mannar | 33.5% | 19.5% | 0.873 | 41.6% |
| matale | 23.9% | 14.4% | 0.836 | 65.6% |
| matara | 20.2% | 11.6% | 0.841 | 81.5% |
| monaragala | 31.8% | 21.8% | 0.853 | 63.6% |
| mullaitivu | 36.6% | 24.2% | 0.894 | 38.8% |
| nuwara_eliya | 27.9% | 16.9% | 0.882 | 75.5% |
| polonnaruwa | 30.5% | 19.4% | 0.869 | 42.0% |
| puttalam | 23.2% | 15.2% | 0.848 | 60.1% |
| ratnapura | 21.1% | 9.3% | 0.854 | 90.3% |
| trincomalee | 32.2% | 20.7% | 0.880 | 43.1% |
| vavuniya | 27.6% | 17.3% | 0.851 | 44.7% |

## Cross-check against NASA POWER precipitation

ERA5 and NASA POWER agree on rain/no-rain on 78.4% of test days. Scored against POWER instead of ERA5, the model's Brier skill is 24.3% (Markov 12.6%).

## Caveats

- Truth is ERA5 reanalysis grid-cell rainfall (~25 km), not a rain gauge. It smooths showers, overcounts drizzle days and underestimates extremes.
- The live model reads Open-Meteo's operational analysis for its inputs, not ERA5. The live ledger on the site measures how much that costs.
- NWP forecasts are single deterministic runs scored yes/no, which penalises them under Brier score; compare CSI/POD/FAR for a like-for-like view.
- Home advantage: the model learned from ERA5 and is graded against ERA5, so it has absorbed ERA5's quirks (e.g. its drizzle days); ECMWF, GFS and ICON have not. Treat the head-to-head as indicative until it is repeated against rain-gauge data.

## Top features (share of gain)

- `precip` 35.9%
- `precip_hours` 14.7%
- `rain` 10.8%
- `town_code` 3.4%
- `rh` 2.0%
- `wetdays30` 1.8%
- `doy_cos` 1.7%
- `run_wet` 1.6%
- `isl_trincomalee` 1.2%
- `doy_sin` 1.1%
- `et0` 1.0%
- `isl_pmsl` 1.0%
