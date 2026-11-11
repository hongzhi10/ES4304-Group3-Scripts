"""How much 2026 burned area did the hotspots miss? (Objective 1)

Compares the Sentinel-2 burn map from 06_sentinel2_stages.py with the FIRMS
hotspots from 02_firms_hotspots.py. Each hotspot from the site's pre-fire scene
(the start of the dNBR) to 31 Oct is drawn as the square of its pixel, 375 m for VIIRS and 1 km for MODIS, on the 60 m
burn grid. A burned pixel counts as "seen" if any hotspot square covers it.

Run:  python 10_burns_vs_hotspots.py
Writes data/2026/burns_vs_hotspots_2026.json and prints a table.
"""
import csv
import json

import numpy as np
import rasterio
from pyproj import Transformer

from config import OUT, SITES

STAGES = OUT / "2026" / "stages"
HOTSPOTS = OUT / "2026" / "firms" / "hotspots_2026.csv"
MODERATE = 0.27  # Key & Benson (2006): moderate-low severity and above
BURNED = 0.10


def main():
    rows = list(csv.DictReader(open(HOTSPOTS)))
    result, keep = {}, []
    print(f"{'site':<18}{'burned ha':>11}{'missed':>9}{'moderate+ ha':>14}{'missed':>9}   hotspots (day / night / 03-06)")
    for site, (w, s, e, n) in SITES.items():
        with rasterio.open(STAGES / site / f"{site}_dnbr_2026.tif") as src:
            dnbr = src.read(1)
            tr, crs, res = src.transform, src.crs, src.res[0]
        to_utm = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
        covered = np.zeros(dnbr.shape, bool)
        # Start at the earliest pre-fire date in the composite, so earlier burns are not counted as missed
        start = min(json.load(open(STAGES / site / f"{site}_summary_2026.json"))["dates"]["prefire"])
        here = [r for r in rows if r["acq_date"] >= start
                and w <= float(r["longitude"]) <= e and s <= float(r["latitude"]) <= n]
        keep += [dict(r, site=site) for r in here]
        for r in here:
            x, y = to_utm.transform(float(r["longitude"]), float(r["latitude"]))
            half = (375 if r.get("instrument", "VIIRS").upper().startswith("VIIRS") else 1000) / 2
            c0, r0 = ~tr * (x - half, y + half)
            c1, r1 = ~tr * (x + half, y - half)
            c0, c1 = max(0, int(np.floor(c0))), min(dnbr.shape[1], int(np.ceil(c1)))
            r0, r1 = max(0, int(np.floor(r0))), min(dnbr.shape[0], int(np.ceil(r1)))
            if c0 < c1 and r0 < r1:
                covered[r0:r1, c0:c1] = True
        ha = res * res / 1e4
        out = {"hotspots_from": start, "hotspots": len(here),
               "hotspots_by_window": {k: sum(r["window"] == k for r in here) for k in ("day", "night", "gap")}}
        for label, thr in (("burned", BURNED), ("moderate_or_higher", MODERATE)):
            b = np.isfinite(dnbr) & (dnbr >= thr)
            out[f"{label}_ha"] = round(b.sum() * ha)
            out[f"{label}_missed_ha"] = round((b & ~covered).sum() * ha)
            out[f"{label}_missed_pct"] = round(100 * (b & ~covered).sum() / max(1, b.sum()), 1)
        result[site] = out
        hw = out["hotspots_by_window"]
        print(f"{site:<18}{out['burned_ha']:>11,}{out['burned_missed_pct']:>8.1f}%{out['moderate_or_higher_ha']:>14,}"
              f"{out['moderate_or_higher_missed_pct']:>8.1f}%   {out['hotspots']:,} ({hw['day']:,} / {hw['night']:,} / {hw['gap']:,})")
    # Small copy of the hotspots used above, for the QGIS maps
    with open(OUT / "2026" / "firms" / "hotspots_sites_fire_2026.csv", "w", newline="") as f:
        cols = ["latitude", "longitude", "acq_date", "acq_time", "instrument", "source", "local_time", "window", "site"]
        wr = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        wr.writeheader()
        wr.writerows(keep)
    path = OUT / "2026" / "burns_vs_hotspots_2026.json"
    json.dump(result, open(path, "w"), indent=2)
    print(f"\n-> {path}\nBurned area is all land at 60 m, not yet clipped to the peat map.")


if __name__ == "__main__":
    main()
