"""Find the 2026 fire-season nights when ECOSTRESS saw fire at each site, inside and outside the gap.

For each site it searches NASA's catalogue for ECOSTRESS LST tiles that contain the
site centre (1 Aug - 31 Oct 2026), keeps night passes (19:00-06:00 WIB, which
includes the 03:00-06:00 gap), downloads their LST and cloud GeoTIFFs, and counts
fire-hot pixels (above 310 K) inside the site box. Needs an Earthdata login in ~/.netrc.

Writes data/2026/ecostress/fire_nights_2026.csv: one row per pass with its local
time, window (night or gap), cloud share and fire-hot pixel count. 08_qgis_maps.py
maps the night with the most fire at each site.

Run:  python 09_ecostress_fires.py
"""
import csv
import json
import re
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

import numpy as np
import rasterio
from rasterio.warp import transform_bounds
from rasterio.windows import from_bounds

from config import OUT, SITES, site_centre, utc_offset, window

CMR = "https://cmr.earthdata.nasa.gov/search/granules.json"
FIRE_K = 310.0
START, END = "2026-08-01", "2026-10-31"


def search_point(lat, lon):
    q = urllib.parse.urlencode({"short_name": "ECO_L2T_LSTE", "version": "002", "page_size": 2000,
                                "point": f"{lon},{lat}", "temporal": f"{START}T00:00:00Z,{END}T23:59:59Z"})
    with urllib.request.urlopen(f"{CMR}?{q}", timeout=120) as r:
        return json.load(r)["feed"]["entry"]


def main():
    import earthaccess  # in the course environment
    earthaccess.login(strategy="netrc")
    folder = OUT / "2026" / "ecostress" / "files"
    folder.mkdir(parents=True, exist_ok=True)
    rows = []
    for site, box in SITES.items():
        lat, lon = site_centre(site)
        off = utc_offset(lat, lon)
        picks = []
        for e in search_point(lat, lon):
            utc = datetime.strptime(re.search(r"_(\d{8}T\d{6})_", e["title"]).group(1), "%Y%m%dT%H%M%S")
            local = utc + timedelta(hours=off)
            win = window(local.hour + local.minute / 60)
            if win == "day":
                continue
            urls = [l["href"] for l in e.get("links", []) if l["href"].startswith("https")
                    and l["href"].endswith(("_LST.tif", "_cloud.tif"))]
            picks.append((e["title"], local, win, urls))
        print(f"{site}: {len(picks)} night tiles over the centre, Aug-Oct 2026")
        for u in (u for p in picks for u in p[3]):
            if (folder / u.split("/")[-1]).exists():
                continue
            try:  # one bad file should not stop the rest
                earthaccess.download([u], local_path=str(folder), show_progress=False)
            except Exception as err:
                print(f"  skipped {u.split('/')[-1]}: {type(err).__name__}")
        for title, local, win, urls in picks:
            lst_path = folder / f"{title}_LST.tif"
            cloud_path = folder / f"{title}_cloud.tif"
            if not lst_path.exists():
                continue
            with rasterio.open(lst_path) as src:
                w = from_bounds(*transform_bounds("EPSG:4326", src.crs, *box), src.transform)
                lst = src.read(1, window=w, boundless=True, fill_value=np.nan).astype("float32")
            cloud = np.zeros_like(lst, bool)
            if cloud_path.exists():
                with rasterio.open(cloud_path) as c:
                    cloud = c.read(1, window=w, boundless=True, fill_value=0) == 1
            imaged = np.isfinite(lst) | cloud
            clear = np.isfinite(lst) & ~cloud
            rows.append({"site": site, "granule": title, "local_time": local.strftime("%Y-%m-%d %H:%M"),
                         "window": win, "imaged_pct": round(100 * imaged.mean(), 1),
                         "clear_pct_of_imaged": round(100 * clear.sum() / max(1, imaged.sum()), 1),
                         "fire_hot_pixels": int((clear & (lst > FIRE_K)).sum()),
                         "max_lst_k": round(float(np.nanmax(lst)), 1) if clear.any() else ""})
    if not rows:
        print("\nNo tiles could be read. If every download was skipped, NASA LP DAAC may be refusing requests for a "
              "while (HTTP 400); try again later.")
        return
    path = OUT / "2026" / "ecostress" / "fire_nights_2026.csv"
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(sorted(rows, key=lambda r: (r["site"], r["local_time"])))
    print(f"\n-> {path}")
    for site in SITES:
        s = [r for r in rows if r["site"] == site]
        for win in ("night", "gap"):
            best = max((r for r in s if r["window"] == win), key=lambda r: r["fire_hot_pixels"], default=None)
            if best:
                print(f"  {site:<18} best {win:<5} {best['local_time']} WIB: {best['fire_hot_pixels']:>5} fire-hot "
                      f"pixels, {best['clear_pct_of_imaged']:.0f}% clear, max {best['max_lst_k']} K")


if __name__ == "__main__":
    main()
