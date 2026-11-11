"""VIIRS and MODIS hotspots from NASA FIRMS for the pre-fire and fire-season windows.

These are the hotspots SiPongi+ is built on, so they are the baseline for
Objective 1. Suomi NPP and MODIS are included because they were still feeding
SiPongi+ during 2026 (and 2019); leaving them out would make the miss rate look
worse than it was.

Needs a free FIRMS MAP_KEY (instant, by email):
    https://firms.modaps.eosdis.nasa.gov/api/map_key/
then, in the terminal:  export FIRMS_MAP_KEY=your_key

Run:  python 02_firms_hotspots.py            (2026)
      python 02_firms_hotspots.py --year 2019
Writes data/<year>/firms/hotspots_<year>.csv with every hotspot, day and night,
plus these columns:
  period      prefire, fire, or after (dates past the fire season)
  local_time  clock time where the hotspot is (WIB, or WITA in South/East Kalimantan)
  tz          WIB or WITA
  window      gap (03:00-06:00), night (19:00-03:00) or day (06:00-19:00)
  site        case-study site the hotspot falls in, if any
"""
import argparse
import csv
import io
import os
import re
import sys
import time
import urllib.request
from datetime import date, datetime, timedelta

from config import FIRE_BELT, OUT, PERIODS, SITES, utc_offset, window

BASE = "https://firms.modaps.eosdis.nasa.gov/api"
CHUNK_DAYS = 5  # days per request
# Standard (SP) products are reprocessed and preferred; near-real-time (NRT) fills recent dates
SOURCES = {
    "VIIRS_SNPP": ("VIIRS_SNPP_SP", "VIIRS_SNPP_NRT"),
    "VIIRS_NOAA20": ("VIIRS_NOAA20_SP", "VIIRS_NOAA20_NRT"),
    "VIIRS_NOAA21": ("VIIRS_NOAA21_SP", "VIIRS_NOAA21_NRT"),
    "MODIS": ("MODIS_SP", "MODIS_NRT"),
}


def get(url):
    with urllib.request.urlopen(url, timeout=120) as r:
        return r.read().decode("utf-8")


def availability(key):
    """{source: (first_date, last_date)} for every FIRMS source."""
    rows = csv.DictReader(io.StringIO(get(f"{BASE}/data_availability/csv/{key}/ALL")))
    return {r["data_id"]: (date.fromisoformat(r["min_date"]), date.fromisoformat(r["max_date"])) for r in rows}


def pick_source(options, avail, d0, d1):
    for src in options:  # SP first, then NRT
        if src in avail and avail[src][0] <= d0 and d1 <= avail[src][1]:
            return src
    return None


def which_site(lat, lon):
    for name, (w, s, e, n) in SITES.items():
        if w <= lon <= e and s <= lat <= n:
            return name
    return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, default=2026, choices=sorted(PERIODS))
    ap.add_argument("--start", type=date.fromisoformat, help="first day (YYYY-MM-DD); default: start of the pre-fire period")
    ap.add_argument("--end", type=date.fromisoformat, help="last day (YYYY-MM-DD); default: end of the fire season")
    args = ap.parse_args()
    key = (os.environ.get("FIRMS_MAP_KEY") or "").strip()
    if not re.fullmatch(r"[0-9a-fA-F]{32}", key):
        if not sys.stdin.isatty():
            sys.exit("Set FIRMS_MAP_KEY first (free key: https://firms.modaps.eosdis.nasa.gov/api/map_key/)")
        import getpass
        key = getpass.getpass("Paste your 32-character FIRMS map key (hidden), then press Enter: ").strip()

    per = PERIODS[args.year]
    out = OUT / str(args.year) / "firms"
    out.mkdir(parents=True, exist_ok=True)
    try:
        avail = availability(key)
    except urllib.error.HTTPError as err:
        if err.code in (400, 401, 403):
            sys.exit("FIRMS refused the map key. Check FIRMS_MAP_KEY is the 32-character key from the FIRMS email.")
        raise
    area = ",".join(str(v) for v in FIRE_BELT)
    start = args.start or date.fromisoformat(per["prefire"][0])
    end = args.end or date.fromisoformat(per["fire"][1])

    rows, fields, counts = [], None, {}
    for name, options in SOURCES.items():
        d = start
        # Stop at the newest day this satellite has, so a recent last chunk is cut short, not skipped
        last = max((avail[s][1] for s in options if s in avail), default=end)
        while d <= min(end, last):
            d1 = min(d + timedelta(days=CHUNK_DAYS - 1), end, last)
            src = pick_source(options, avail, d, d1)
            if src is None:
                if d == start:
                    print(f"  {name}: no data for {d} onwards (satellite not flying or not yet processed)")
                d = d1 + timedelta(days=1)
                continue
            text = get(f"{BASE}/area/csv/{key}/{src}/{area}/{(d1 - d).days + 1}/{d.isoformat()}")
            reader = csv.DictReader(io.StringIO(text))
            for r in reader:
                r["source"] = src
                rows.append(r)
                counts[src] = counts.get(src, 0) + 1
            fields = fields or (reader.fieldnames or [])
            d = d1 + timedelta(days=1)
            time.sleep(1)  # stay well inside the FIRMS transaction limit

    # Add period, local clock time, time window and site
    for r in rows:
        lat, lon = float(r["latitude"]), float(r["longitude"])
        utc = datetime.strptime(f"{r['acq_date']} {int(r['acq_time']):04d}", "%Y-%m-%d %H%M")
        off = utc_offset(lat, lon)
        local = utc + timedelta(hours=off)
        r["local_time"] = local.strftime("%Y-%m-%d %H:%M")
        r["tz"] = "WIB" if off == 7 else "WITA"
        r["window"] = window(local.hour + local.minute / 60)
        r["period"] = ("prefire" if utc.date() <= date.fromisoformat(per["prefire"][1]) else
                       "fire" if utc.date() <= date.fromisoformat(per["fire"][1]) else "after")
        r["site"] = which_site(lat, lon)

    cols = sorted({k for r in rows for k in r}, key=lambda k: (k not in (fields or []), k))
    # A custom date range goes to its own file, so the season file used by the other scripts is kept
    path = out / (f"hotspots_{start}_to_{end}.csv" if args.start or args.end else f"hotspots_{args.year}.csv")
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)

    print(f"\n{len(rows):,} hotspots -> {path}")
    for src, n in sorted(counts.items()):
        print(f"  {src:<18} {n:>8,}")
    print("\n  hotspots by time window (all sources):")
    for site in [*SITES, ""]:
        s = [r for r in rows if r["site"] == site]
        label = site or "rest of fire belt"
        print(f"  {label:<18} pre-fire {sum(r['period'] == 'prefire' for r in s):>6,}   "
              f"fire season {sum(r['period'] == 'fire' for r in s):>6,}   |   "
              f"day {sum(r['window'] == 'day' for r in s):>6,}   "
              f"night {sum(r['window'] == 'night' for r in s):>6,}   "
              f"03:00-06:00 {sum(r['window'] == 'gap' for r in s):>4,}")


if __name__ == "__main__":
    main()
