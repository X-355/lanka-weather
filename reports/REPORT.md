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

### Error bars (95%, resampling whole days)

Model skill 29.8% (27.4% to 32.1%); Markov 18.7% (16.7% to 20.5%). Model minus Markov: 11.1% (9.9% to 12.3%), 1,787 days.

## Fair comparison with professional forecasts (2025-06-30 → 2026-11-22)

ECMWF is turned into a probability, and the hybrid is fitted, on 2024-02-05 → 2025-06-29 only; every entry is scored on the same 12,775 later district-days.

| Forecast | Type | Brier ↓ | Skill vs clim (95% range) ↑ | AUC ↑ |
|---|---|---|---|---|
| Hybrid: Weather Lab + ECMWF/GFS/ICON | probability | 0.100 | 43.5% (39.7% to 47.1%) | 0.937 |
| Weather Lab (LightGBM) | probability | 0.128 | 27.7% (23.2% to 32.3%) | 0.898 |
| ECMWF IFS 0.25°, as a calibrated probability | probability | 0.129 | 27.1% (22.1% to 32.0%) | 0.900 |
| ECMWF IFS 0.25°, yes/no (≥ 1 mm forecast) | yes/no | 0.196 | -10.0% | 0.786 |
| NCEP GFS, yes/no (≥ 1 mm forecast) | yes/no | 0.212 | -19.4% | 0.790 |
| DWD ICON, yes/no (≥ 1 mm forecast) | yes/no | 0.235 | -32.1% | 0.779 |
| Markov baseline | baseline | 0.147 | 17.2% (13.4% to 20.9%) | 0.863 |
| Climatology | baseline | 0.178 | 0.0% | 0.807 |

- hybrid - ecmwf_cal: 16.3% (13.5% to 19.2%); better in 100% of resamples

- model - ecmwf_cal: 0.6% (-4.5% to 5.6%); better in 60% of resamples

- hybrid - model: 15.8% (12.7% to 19.1%); better in 100% of resamples

## Heavy-rain scorecard vs professional forecasts (≥ 50.0 mm)

185 heavy-rain district-days out of 12,775.

| Warning rule | Caught | Missed | False alarms | POD ↑ | FAR ↓ | CSI ↑ | Avg precision ↑ |
|---|---|---|---|---|---|---|---|
| ECMWF forecast ≥ 50 mm | 63 | 122 | 61 | 34.1% | 49.2% | 0.256 | 0.386 |
| ECMWF calibrated, warn at ≥ 30% | 53 | 132 | 28 | 28.6% | 34.6% | 0.249 | 0.385 |
| Weather Lab, warn at ≥ 30% | 22 | 163 | 16 | 11.9% | 42.1% | 0.109 | 0.313 |
| Hybrid, warn at ≥ 30% | 70 | 115 | 63 | 37.8% | 47.4% | 0.282 | 0.490 |
| Hybrid, warn at ≥ 20% | 85 | 100 | 138 | 45.9% | 61.9% | 0.263 | 0.490 |

## Heavy-rain scorecard, all test years (≥ 50.0 mm)

488 heavy-rain district-days out of 44,675.

| Warning rule | Caught | Missed | False alarms | POD ↑ | FAR ↓ | CSI ↑ | Avg precision ↑ |
|---|---|---|---|---|---|---|---|
| Weather Lab, warn at ≥ 20% | 84 | 404 | 145 | 17.2% | 63.3% | 0.133 | 0.212 |
| Weather Lab, warn at ≥ 30% | 38 | 450 | 35 | 7.8% | 47.9% | 0.073 | 0.212 |
| Climatology, warn at ≥ 20% | 0 | 488 | 0 | 0.0% | – | 0.000 | 0.055 |

## Against real rain gauges (NOAA GSOD/GHCN)

20 gauges in 17 districts; day shift 1 (agreement with ERA5 by shift on pre-test years: {'0': 0.673, '1': 0.687}).

**Rain gauges, 2024+ (with professional forecasts)**: 2024-02-14 → 2026-11-09, 2,138 rows on 501 days, identical for every forecast. Rain (≥ 1 mm) on 42.6% of rows here vs 75.6% in ERA5.

| Forecast | Ranking AUC (95% range) ↑ | Adjusted skill, 2025-04 → 2026-11 (95% range) ↑ |
|---|---|---|
| hybrid | 0.652 (0.622–0.678) | -20.1% (-30.9% to -10.8%) |
| model | 0.613 (0.582–0.641) | -16.8% (-25.7% to -8.9%) |
| ecmwf_cal | 0.648 (0.618–0.678) | -22.9% (-35.0% to -12.9%) |
| markov | 0.589 (0.560–0.617) | -19.8% (-29.1% to -11.4%) |

- hybrid - ecmwf_cal: AUC +0.004 (-0.009 to +0.017); adjusted skill 2.8% (0.4% to 5.3%)

- model - ecmwf_cal: AUC -0.035 (-0.068 to -0.006); adjusted skill 6.1% (1.4% to 11.7%)

- model - markov: AUC +0.024 (+0.005 to +0.042); adjusted skill 3.0% (0.7% to 5.4%)

**Rain gauges, all test years**: 2022-01-01 → 2026-11-09, 15,898 rows on 1,253 days, identical for every forecast. Rain (≥ 1 mm) on 37.0% of rows here vs 66.7% in ERA5.

| Forecast | Ranking AUC (95% range) ↑ | Adjusted skill, 2023-09 → 2026-11 (95% range) ↑ |
|---|---|---|
| model | 0.760 (0.746–0.773) | 3.9% (0.0% to 7.7%) |
| markov | 0.710 (0.696–0.723) | -3.9% (-6.6% to -1.4%) |

- model - markov: AUC +0.050 (+0.042 to +0.057); adjusted skill 7.9% (4.6% to 11.1%)

## Against NASA IMERG satellite rainfall


**NASA IMERG, 2024+ (with professional forecasts)**: 2025-07-19 → 2026-11-22, 12,300 rows on 492 days, identical for every forecast. Rain (≥ 1 mm) on 48.8% of rows here vs 56.8% in ERA5.

| Forecast | Ranking AUC (95% range) ↑ | Adjusted skill, 2026-03 → 2026-11 (95% range) ↑ |
|---|---|---|
| hybrid | 0.850 (0.837–0.864) | 30.8% (25.2% to 36.0%) |
| model | 0.791 (0.772–0.809) | 17.1% (10.6% to 22.7%) |
| ecmwf_cal | 0.832 (0.816–0.847) | 25.1% (19.1% to 30.7%) |
| markov | 0.743 (0.726–0.761) | 7.8% (1.9% to 13.1%) |

- hybrid - ecmwf_cal: AUC +0.018 (+0.012 to +0.024); adjusted skill 5.7% (3.7% to 7.6%)

- model - ecmwf_cal: AUC -0.041 (-0.058 to -0.026); adjusted skill -8.1% (-11.7% to -4.6%)

- model - markov: AUC +0.048 (+0.038 to +0.057); adjusted skill 9.3% (7.0% to 11.5%)

**NASA IMERG, all test years**: 2025-07-19 → 2026-11-22, 12,300 rows on 492 days, identical for every forecast. Rain (≥ 1 mm) on 48.8% of rows here vs 56.8% in ERA5.

| Forecast | Ranking AUC (95% range) ↑ | Adjusted skill, 2026-03 → 2026-11 (95% range) ↑ |
|---|---|---|
| model | 0.791 (0.772–0.809) | 17.1% (10.6% to 22.7%) |
| markov | 0.743 (0.726–0.761) | 7.8% (1.9% to 13.1%) |

- model - markov: AUC +0.048 (+0.038 to +0.057); adjusted skill 9.3% (7.0% to 11.5%)

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

66 towns in 41 ERA5 cells that contain no district point; each cell counted once (73,267 cell-days). Baselines from each town's own 2014–2021 data. Coverage: 166 of 166 towns scored.

| Forecast | Brier ↓ | Skill vs climatology ↑ | AUC ↑ |
|---|---|---|---|
| model | 0.144 | 27.4% | 0.874 |
| markov | 0.162 | 18.0% | 0.835 |
| climatology | 0.198 | 0.0% | 0.761 |

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
