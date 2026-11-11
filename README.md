# ES4304 Group 3 Scripts

Scripts behind Section 3.1 (Phase 1: Data Assembly and Pre-processing) of the ES4304 Group 3 proposal for two night-time thermal satellites to detect smouldering peat fires in Indonesia. All data are free and from 2026. No data files are stored here, so each script downloads what it needs.

## Setup

Use the course environment (`conda activate eyes-on-earth`), or install the packages in `requirements.txt`.

| Login | Needed for | How |
|---|---|---|
| Google Earth Engine | `01_gee_burned_area.py` | `earthengine authenticate`, then set `GEE_PROJECT` in `config.py` to your own project |
| NASA FIRMS map key | `02_firms_hotspots.py` | Free key from https://firms.modaps.eosdis.nasa.gov/api/map_key/, then `export FIRMS_MAP_KEY=your_key` |
| NASA Earthdata | Downloads in `03`, `04` and `11` | Login saved in `~/.netrc` |

Sentinel-2 (Earth Search on AWS) and Himawari-9 (NOAA Open Data on AWS) need no login. `config.py` holds the study periods, the four site boxes and the time windows used by every script. Outputs go to `data/`, which git ignores.

## Scripts and the report

| Script | What it does | Used in the report |
|---|---|---|
| `01_gee_burned_area.py` | Sentinel-2 dNBR, burned mask, WorldCover and CHIRPS rainfall in Earth Engine | National burned-area method |
| `02_firms_hotspots.py` | All VIIRS and MODIS hotspots, labelled by local time window | Table 3, Figures B2, B4, B6, B8 |
| `03_radar_search.py` | Lists NISAR GUNW pairs and their local pass times | Table 2, Table 3 |
| `04_ecostress_lst.py` | Lists and downloads ECOSTRESS 70 m land surface temperature passes | Table 3 |
| `05_himawari_gap.py` | Himawari-9 infrared images inside and outside the 03:00–06:00 gap | Pre-dawn gap |
| `06_sentinel2_stages.py` | Cloud-free Sentinel-2 composites, NBR, dNBR, severity classes and burned hectares (run with `--res 60`) | Table 3, Figures B2, B4, B6, B8 |
| `06b_single_date_stages.py` | One mostly clear Sentinel-2 date per fire stage | Figures B1, B3, B5, B7 |
| `07_ecostress_maps.py` | Quick-look maps of ECOSTRESS night tiles | Checking only |
| `08_qgis_maps.py` | Lays out and exports the maps in QGIS (run with QGIS's own Python) | Figures A1, B1–B8, E1, E2 |
| `09_ecostress_fires.py` | Finds nights when ECOSTRESS saw fire at each site, inside and outside the gap | Figures E1, E2 |
| `10_burns_vs_hotspots.py` | Share of burned land with no hotspot over it | Table 3 |
| `11_nisar_first_pair.py` | Coherence and line-of-sight change for one NISAR pair at Ogan Komering Ilir | Figure C1 |

Run order for the burn-severity results: `02`, then `06 --res 60`, `06b`, `10`, and finally `08` with `/Applications/QGIS.app/Contents/MacOS/python 08_qgis_maps.py`.

## Orbit coverage simulation

| File | What it does | Used in the report |
|---|---|---|
| `simulation/index.html` | Interactive simulation of night-time looks by the two proposed satellites, opened in any browser | Figures F1, F2, Table F1 |
| `simulation/inclination_scan.py` | Same orbit model in Python, run for inclinations from 0° to 6° | Figure F3 |
| `simulation/inclination_chart.py` | Plots `scan.json` from the scan | Figure F3 |

The model uses circular orbits at 650 km with the J2 drift of each orbit plane, a 935 km swath and a 94-night run, without cloud. A look is a pass whose swath covers a province point between 19:00 and 05:00 local solar time. The simulation labels the Jambi point "Batanghari", an earlier site name.

## Notes

- Burned area covers all land in each box, not only peat.
- These scripts were written with the help of Claude (Anthropic), and the outputs were checked before use in the report.
