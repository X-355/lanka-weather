# Evaluation report

Model `0.1.0+20261128` — data: Open-Meteo ERA5 reanalysis.
Test period (never used for training or tuning): **2022-01-01 → 2026-11-22**, 44,675 town-days.

## Next-day rain (≥ 1.0 mm)

| Forecast | Brier ↓ | Skill vs climatology ↑ | AUC ↑ | Accuracy | POD | FAR |
|---|---|---|---|---|---|---|
| model | 0.127 | 29.8% | 0.897 | 81.9% | 86.5% | 16.2% |
| climatology | 0.181 | 0.0% | 0.797 | 72.8% | 73.7% | 20.6% |
| markov | 0.147 | 18.7% | 0.860 | 79.8% | 83.9% | 17.4% |
| persistence | 0.206 | -13.9% | 0.785 | 79.4% | 82.9% | 17.2% |

## Versus professional forecasts (2024-02-05 → 2026-11-22, 25,450 town-days)

Same rows for every entry. NWP forecasts are day-ahead runs, scored as yes/no (≥ 1 mm forecast).

| Forecast | Type | Brier ↓ | Skill vs clim ↑ | Accuracy | CSI ↑ | POD | FAR |
|---|---|---|---|---|---|---|---|
| Weather Lab (LightGBM) | probability | 0.129 | 27.6% | 81.6% | 0.729 | 86.1% | 17.4% |
| Weather Lab, yes/no at 50% | yes/no | 0.184 | -3.1% | 81.6% | 0.729 | 86.1% | 17.4% |
| ECMWF IFS 0.25° | yes/no (≥1 mm forecast) | 0.178 | 0.2% | 82.2% | 0.747 | 91.3% | 19.6% |
| NCEP GFS | yes/no (≥1 mm forecast) | 0.207 | -16.1% | 79.3% | 0.682 | 77.0% | 14.4% |
| DWD ICON | yes/no (≥1 mm forecast) | 0.242 | -35.9% | 75.8% | 0.608 | 65.4% | 10.3% |
| Markov | baseline | 0.147 | 17.4% | 79.6% | 0.702 | 83.4% | 18.5% |
| Climatology | baseline | 0.178 | 0.0% | 73.5% | 0.622 | 75.9% | 22.5% |
| Persistence | baseline | 0.210 | -17.6% | 79.0% | 0.692 | 81.9% | 18.3% |

## Heavy rain (≥ 50.0 mm) — research only

Base rate 1.1%. Model Brier skill 9.4%, AUC 0.930, average precision 0.212 (climatology AP 0.055).

## By season

| Season | Model BSS | Markov BSS | Base rate |
|---|---|---|---|
| First inter-monsoon (Mar–Apr) | 24.5% | 15.5% | 55.7% |
| Northeast monsoon (Dec–Feb) | 31.1% | 18.7% | 51.1% |
| Second inter-monsoon (Oct–Nov) | 38.4% | 24.9% | 80.6% |
| Southwest monsoon (May–Sep) | 28.2% | 17.9% | 58.5% |

## By town

| Town | Model BSS | Markov BSS | AUC | Base rate |
|---|---|---|---|---|
| ampara | 30.3% | 20.1% | 0.850 | 56.9% |
| anuradhapura | 25.3% | 14.6% | 0.831 | 51.3% |
| badulla | 39.7% | 26.9% | 0.886 | 56.8% |
| batticaloa | 37.0% | 26.0% | 0.891 | 49.7% |
| colombo | 24.3% | 11.7% | 0.922 | 76.9% |
| galle | 23.4% | 13.3% | 0.904 | 85.2% |
| gampaha | 26.0% | 15.4% | 0.935 | 75.4% |
| hambantota | 32.2% | 20.1% | 0.865 | 45.2% |
| jaffna | 37.1% | 23.0% | 0.880 | 41.1% |
| kalutara | 24.0% | 11.1% | 0.912 | 80.9% |
| kandy | 30.1% | 18.3% | 0.841 | 61.1% |
| kegalle | 27.1% | 17.5% | 0.922 | 70.8% |
| kilinochchi | 35.4% | 23.4% | 0.895 | 33.9% |
| kurunegala | 23.9% | 14.8% | 0.887 | 67.3% |
| mannar | 33.2% | 19.5% | 0.871 | 41.6% |
| matale | 23.8% | 14.4% | 0.836 | 65.6% |
| matara | 19.9% | 11.6% | 0.840 | 81.5% |
| monaragala | 31.4% | 21.8% | 0.851 | 63.6% |
| mullaitivu | 36.5% | 24.2% | 0.893 | 38.8% |
| nuwara_eliya | 27.6% | 16.9% | 0.883 | 75.5% |
| polonnaruwa | 30.1% | 19.4% | 0.868 | 42.0% |
| puttalam | 23.5% | 15.2% | 0.849 | 60.1% |
| ratnapura | 21.6% | 9.3% | 0.857 | 90.3% |
| trincomalee | 32.4% | 20.7% | 0.881 | 43.1% |
| vavuniya | 27.5% | 17.3% | 0.851 | 44.7% |

## Towns the model never trained on (2022-01-01 → 2026-11-22)

53 towns in 29 ERA5 cells that contain no district point; each cell counted once (51,823 cell-days). Baselines from each town's own 2014–2021 data. Coverage: 153 of 166 towns scored.

| Forecast | Brier ↓ | Skill vs climatology ↑ | AUC ↑ |
|---|---|---|---|
| model | 0.144 | 27.6% | 0.873 |
| markov | 0.163 | 18.0% | 0.834 |
| climatology | 0.199 | 0.0% | 0.760 |

Towns sharing a district point's cell (100 towns, 25 cells): model skill 30.3%, Markov 18.7%.

## Cross-check against NASA POWER precipitation

ERA5 and NASA POWER agree on rain/no-rain on 78.4% of test days. Scored against POWER instead of ERA5, the model's Brier skill is 24.0% (Markov 12.7%).

## Caveats

- Truth is ERA5 reanalysis grid-cell rainfall (~25 km), not a rain gauge. It smooths showers, overcounts drizzle days and underestimates extremes.
- The live model reads Open-Meteo's operational analysis for its inputs, not ERA5. The live ledger on the site measures how much that costs.
- NWP forecasts are single deterministic runs scored yes/no, which penalises them under Brier score; compare CSI/POD/FAR for a like-for-like view.
- Home advantage: the model learned from ERA5 and is graded against ERA5, so it has absorbed ERA5's quirks (e.g. its drizzle days); ECMWF, GFS and ICON have not. Treat the head-to-head as indicative until it is repeated against rain-gauge data.

## Top features (share of gain)

- `precip` 33.9%
- `precip_hours` 15.3%
- `rain` 8.1%
- `run_wet` 4.3%
- `town_code` 3.5%
- `rh` 1.8%
- `wetdays30` 1.8%
- `doy_cos` 1.7%
- `isl_trincomalee` 1.3%
- `doy_sin` 1.1%
- `isl_pmsl` 1.1%
- `dep_point` 1.0%
