"""List (and optionally download) NISAR and ALOS-2 radar data over the case-study sites.

- NISAR GUNW interferograms, 17 Jun - 31 Oct 2026 (Objective 3, pre-fire ground movement)
- ALOS-2 ScanSAR (free), 2018-2019, for Batanghari and Kubu Raya, where the
  water-table records are (used to link ground movement to the water table)

Searching needs no login. Downloading needs a NASA Earthdata login in ~/.netrc
(the same setup as Tutorial 3.3). NISAR GUNW files are about 2.1-2.3 GB each and
ALOS-2 ScanSAR scenes 28-56 GB, so the default is list only.

Run:  python 03_radar_search.py                     (write the lists)
      python 03_radar_search.py --download-nisar 2  (also download the first 2 pre-fire NISAR pairs)
"""
import argparse
import csv
import io
import re
import urllib.parse
import urllib.request

from datetime import datetime, timedelta

from config import OUT, PERIODS, SITES, site_centre, utc_offset, window

ASF = "https://api.daac.asf.alaska.edu/services/search/param"
WATER_TABLE_SITES = ("east_jambi", "kubu_raya")


def asf_search(**params):
    params["output"] = "csv"
    url = ASF + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=180) as r:
        return list(csv.DictReader(io.StringIO(r.read().decode("utf-8"))))


def box_wkt(box):
    w, s, e, n = box
    return f"POLYGON(({w} {s},{e} {s},{e} {n},{w} {n},{w} {s}))"


def nisar_pair_dates(name):
    """Reference and secondary dates from a GUNW granule name."""
    d = re.findall(r"_(\d{8})T\d{6}", name)
    return (d[0], d[2]) if len(d) >= 4 else ("", "")


def local_pass(utc_text, site):
    """Local clock time and window of a pass. Radar sees no heat, so this is for reference."""
    lat, lon = site_centre(site)
    utc = datetime.strptime(utc_text[:19].replace("T", " "), "%Y-%m-%d %H:%M:%S")
    local = utc + timedelta(hours=utc_offset(lat, lon))
    return local.strftime("%H:%M"), window(local.hour + local.minute / 60)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--download-nisar", type=int, default=0, metavar="N",
                    help="download the first N NISAR pairs that fall inside the pre-fire period")
    args = ap.parse_args()
    out = OUT / "radar"
    out.mkdir(parents=True, exist_ok=True)
    pre0, pre1 = (d.replace("-", "") for d in PERIODS[2026]["prefire"])

    to_download = []
    for site, box in SITES.items():
        rows = asf_search(dataset="NISAR", processingLevel="GUNW", intersectsWith=box_wkt(box),
                          start=PERIODS[2026]["prefire"][0] + "T00:00:00Z",
                          end=PERIODS[2026]["fire"][1] + "T23:59:59Z")
        path = out / f"{site}_nisar_gunw_2026.csv"
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["granule", "reference", "secondary", "track", "direction", "local_time", "window",
                        "size_mb", "prefire_pair", "url"])
            for r in sorted(rows, key=lambda r: r["Granule Name"]):
                ref, sec = nisar_pair_dates(r["Granule Name"])
                pre = pre0 <= ref and sec <= pre1
                t, win = local_pass(r["Start Time"], site)
                w.writerow([r["Granule Name"], ref, sec, r["Path Number"], r["Ascending or Descending?"],
                            t, win, r["Size (MB)"], int(pre), r["URL"]])
                if pre:
                    to_download.append(r["URL"])
        # A pair can come as several frame files, so count distinct (reference, secondary, direction)
        pairs = {(*nisar_pair_dates(r["Granule Name"]), r["Ascending or Descending?"]) for r in rows}
        n_pre = sum(pre0 <= a and b <= pre1 for a, b, _ in pairs)
        print(f"{site:<18} NISAR GUNW: {len(pairs):>3} pairs in {len(rows)} files ({n_pre} pairs fully pre-fire) -> {path.name}")

        if site in WATER_TABLE_SITES:
            a2 = asf_search(platform="ALOS-2", beamMode="WBD", intersectsWith=box_wkt(box),
                            start="2018-04-01T00:00:00Z", end="2019-12-31T23:59:59Z")
            path = out / f"{site}_alos2_scansar_2018_2019.csv"
            with open(path, "w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["granule", "date", "local_time", "window", "path", "beam", "size_mb", "url"])
                for r in sorted(a2, key=lambda r: r["Start Time"]):
                    t, win = local_pass(r["Start Time"], site)
                    w.writerow([r["Granule Name"], r["Start Time"][:10], t, win, r["Path Number"],
                                r["Beam Mode"], r["Size (MB)"], r["URL"]])
            print(f"{'':<18} ALOS-2 ScanSAR 2018-19: {len(a2)} scenes -> {path.name}")

    if args.download_nisar:
        import earthaccess  # in the course environment
        earthaccess.login(strategy="netrc")
        urls = list(dict.fromkeys(to_download))[: args.download_nisar]
        dest = out / "nisar_files"
        print(f"\nDownloading {len(urls)} NISAR pairs (about 2.2 GB each) to {dest}")
        earthaccess.download(urls, local_path=str(dest))


if __name__ == "__main__":
    main()
