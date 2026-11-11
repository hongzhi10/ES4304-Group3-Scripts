"""ECOSTRESS 70 m land surface temperature over the case-study sites (Objective 2).

Searches NASA's catalogue (CMR) for ECO_L2T_LSTE tiles and labels each pass by
local clock time (WIB at all three sites):
  gap    03:00-06:00
  night  19:00-03:00 (night passes outside the gap, for comparison)
  day    06:00-19:00
Searching needs no login. Downloading needs an Earthdata login in ~/.netrc and
fetches only the LST and cloud-mask GeoTIFFs.

Run:  python 04_ecostress_lst.py                        (list only)
      python 04_ecostress_lst.py --download             (gap + night passes, the default)
      python 04_ecostress_lst.py --download --which gap (gap passes only)
      python 04_ecostress_lst.py --download --which all (day passes too)
"""
import argparse
import csv
import json
import re
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timedelta

from config import OUT, PERIODS, SITES, site_centre, utc_offset, window

CMR = "https://cmr.earthdata.nasa.gov/search/granules.json"
SHORT_NAME, VERSION = "ECO_L2T_LSTE", "002"
WHICH = {"gap": {"gap"}, "night": {"gap", "night"}, "all": {"gap", "night", "day"}}


def search(box, start, end):
    entries, page = [], 1
    while True:
        q = urllib.parse.urlencode({
            "short_name": SHORT_NAME, "version": VERSION, "page_size": 2000, "page_num": page,
            "bounding_box": ",".join(str(v) for v in box), "temporal": f"{start}T00:00:00Z,{end}T23:59:59Z"})
        with urllib.request.urlopen(f"{CMR}?{q}", timeout=120) as r:
            batch = json.load(r)["feed"]["entry"]
        entries += batch
        if len(batch) < 2000:
            return entries
        page += 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--download", action="store_true")
    ap.add_argument("--which", choices=WHICH, default="night",
                    help="gap = 03:00-06:00 only; night = gap plus other night passes (default); all = day too")
    args = ap.parse_args()
    out = OUT / "2026" / "ecostress"
    out.mkdir(parents=True, exist_ok=True)
    start, end = PERIODS[2026]["prefire"][0], PERIODS[2026]["fire"][1]

    urls = []
    for site, box in SITES.items():
        lat, lon = site_centre(site)
        off = utc_offset(lat, lon)
        rows = []
        for e in search(box, start, end):
            utc = datetime.strptime(re.search(r"_(\d{8}T\d{6})_", e["title"]).group(1), "%Y%m%dT%H%M%S")
            local = utc + timedelta(hours=off)
            win = window(local.hour + local.minute / 60)
            tifs = [l["href"] for l in e.get("links", []) if l["href"].startswith("https") and l["href"].endswith(".tif")]
            keep = [u for u in tifs if u.endswith(("_LST.tif", "_cloud.tif"))]
            rows.append([e["title"], utc.isoformat(), local.strftime("%Y-%m-%d %H:%M"), win, " ".join(keep)])
            if win in WHICH[args.which]:
                urls += keep
        path = out / f"{site}_ecostress_2026.csv"
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["granule", "utc", "local_time", "window", "lst_and_cloud_urls"])
            w.writerows(sorted(rows, key=lambda r: r[1]))
        # One overpass covers several map tiles, so count distinct orbits, not rows
        orbits = {(r[0].split("_")[3], r[3]) for r in rows}
        n = Counter(win for _, win in orbits)
        print(f"{site:<18} {len(orbits):>3} overpasses ({len(rows)} tiles): {n['gap']:>2} in 03:00-06:00, "
              f"{n['night']:>3} other night, {n['day']:>3} day -> {path.name}")

    if args.download:
        import earthaccess  # in the course environment
        earthaccess.login(strategy="netrc")
        urls = list(dict.fromkeys(urls))
        print(f"\nDownloading {len(urls)} files ({args.which} passes, LST + cloud) to {out / 'files'}")
        earthaccess.download(urls, local_path=str(out / "files"))


if __name__ == "__main__":
    main()
