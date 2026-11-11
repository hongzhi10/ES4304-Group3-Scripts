"""Single-date Sentinel-2 views for the stage maps, one mostly visible date per site and stage.

The composites from 06_sentinel2_stages.py stitch up to eight dates together, which leaves seams and
smoke-tinted patches. For the stage maps we show one date per stage instead, the one with the largest
share of the box free of both cloud and smoke. A pixel counts as visible when the scene classification
is clear and its blue reflectance is under 0.10, since the Sentinel-2 cloud mask passes smoke as clear
but smoke brightens the blue band.

The dates below were picked by scoring every date from 1 Apr to 3 Nov 2026 this way:
  pre-fire   the most visible date from 17 Jun to 31 Jul, else from 1 Apr (East Jambi and Ogan
             Komering Ilir had no date at least 70% visible after mid-June)
  co-fire    the most visible date in August, else August and September
  third      Kubu Raya burned out in September, so it shows the most visible date since 20 Sep
             ("Post-fire"). The other three were still burning on 2 Nov, and October was almost all
             smoke and cloud, so they show the newest date since 1 Sep that is about half visible or more
             ("Latest clear").
The NBR maps hide only solid cloud, cloud shadow and water. Smoke and thin cirrus are left in, since
B8A and B12 see through most smoke and the cirrus flagged on 31 May at Ogan Komering Ilir leaves NBR
unchanged (0.66 inside and outside it).
The burn-severity (dNBR) analysis still uses the composites, which see more of the land.

Run:  python 06b_single_date_stages.py
Outputs in data/2026/stages/<site>/single/, read by 08_qgis_maps.py for the stage layout.
"""
import importlib.util
import json
from pathlib import Path

import numpy as np
import rasterio
from affine import Affine

from config import OUT, SITES

spec = importlib.util.spec_from_file_location("s2", Path(__file__).with_name("06_sentinel2_stages.py"))
s2 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(s2)

HAZE_BLUE = 0.10
# (stage label, date, share of the box visible in %)
DATES = {
    "east_jambi": {"prefire": ("Pre-fire", "2026-05-26", 88), "cofire": ("Co-fire", "2026-08-21", 88),
                   "postfire": ("Latest clear", "2026-09-03", 94)},
    "ogan_komering_ilir": {"prefire": ("Pre-fire", "2026-05-31", 94), "cofire": ("Co-fire", "2026-09-30", 39),
                           "postfire": ("Latest clear", "2026-10-20", 67)},
    "kubu_raya": {"prefire": ("Pre-fire", "2026-07-09", 87), "cofire": ("Co-fire", "2026-08-08", 87),
                  "postfire": ("Post-fire", "2026-11-01", 36)},
    "kahayan_sebangau": {"prefire": ("Pre-fire", "2026-07-13", 87), "cofire": ("Co-fire", "2026-08-12", 73),
                         "postfire": ("Latest clear", "2026-09-11", 50)},
}


def cloud_free(items, g, sub=3):
    """True where no 20 m sub-pixel is cloud or cloud shadow (as read_scl in 06, but cirrus is not counted)."""
    tr, w, h, res = g
    fine_tr = Affine(res / sub, 0, tr.c, 0, -res / sub, tr.f)
    fine = s2.read(items, "scl", fine_tr, w * sub, h * sub, res / sub, nearest=True)[0]
    return ~np.isin(fine.reshape(h, sub, w, sub), (3, 8, 9)).any(axis=(1, 3))


def main():
    for site, stages in DATES.items():
        box = SITES[site]
        folder = OUT / "2026" / "stages" / site
        res = json.load(open(folder / f"{site}_summary_2026.json"))["res_m"]
        out = folder / "single"
        out.mkdir(exist_ok=True)
        tr, w, h = s2.grid(box, res)
        g = (tr, w, h, res)
        labels = {}
        for key, (label, date, _) in stages.items():
            items = s2.search(box, date, date)
            scl = s2.read_scl(items, *g)
            tc = s2.read(items, "visual", *g, bands=(1, 2, 3))
            b4, b8a, b12, blue = (s2.refl(items, k, *g) for k in ("red", "nir08", "swir22", "blue"))
            visible = np.isin(scl, s2.CLEAR_SCL) & (np.nan_to_num(blue, nan=1) < HAZE_BLUE)
            # NBR is shown wherever there is no solid cloud: B8A and B12 see through most smoke, and thin cirrus
            # barely changes them, so the smoke test and the cirrus class only decide which date is used
            nbr_ok = cloud_free(items, g) & (scl != 6) & (scl > 0)
            nbr = (b8a - b12) / (b8a + b12)
            ndvi = (b8a - b4) / (b8a + b4)

            def save(arr, name, count, dtype="uint8", nodata=0):
                with rasterio.open(out / name, "w", driver="GTiff", height=h, width=w, count=count, dtype=dtype,
                                   crs=s2.UTM, transform=tr, nodata=nodata, compress="deflate") as d:
                    d.write(arr)
            save(tc.astype("uint8"), f"{site}_{key}_truecolour.tif", 3)
            sw = np.stack([s2.stretch(b12, 0, 0.35), s2.stretch(b8a, 0, 0.45), s2.stretch(b4, 0, 0.2)])
            save(np.where(np.isfinite(sw), 1 + sw * 254, 0).astype("uint8"), f"{site}_{key}_swir.tif", 3)
            for name, arr in (("nbr", nbr), ("ndvi", ndvi)):
                save(np.where(nbr_ok, arr, np.nan).astype("float32")[None], f"{site}_{key}_{name}.tif", 1,
                     "float32", np.nan)
            labels[key] = f"{label}, {int(date[8:])} {s2.MONTHS[int(date[5:7])]} 2026"
            print(f"{site:<20} {labels[key]:<28} {100 * visible.mean():5.1f}% of the box visible, "
                  f"NBR shown on {100 * nbr_ok.mean():5.1f}%", flush=True)
        json.dump({"site": site, "dates": {k: v[1] for k, v in stages.items()}, "stage_labels": labels},
                  open(out / f"{site}_single_dates.json", "w"), indent=2)


if __name__ == "__main__":
    main()
