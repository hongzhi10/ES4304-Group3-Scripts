"""Himawari-9 infrared images inside and outside the 03:00-06:00 gap, from NOAA's public AWS copy.

No account is needed: the files come from the open bucket noaa-himawari9.
For each chosen night it downloads band 7 (3.9 um, fire-sensitive) and band 14
(11.2 um, background temperature) for the two full-disk segments that cover
Indonesia (S05, S06), and decompresses them. Default hours, in WIB:
  22:00 and 01:00  night, outside the gap (01:00 is close to a VIIRS pass)
  03:00, 04:00, 05:00  inside the gap
  13:00  day, close to the VIIRS day pass
Times are WIB, the clock time at all three case-study sites.

With --plot it also uses satpy (pip install satpy) to save, for each site and
hour, a PNG of B07 - B14, where hot spots stand out, labelled gap / night / day.

Run:  python 05_himawari_gap.py                           (default night 2026-09-04)
      python 05_himawari_gap.py --night 2026-09-04 --night 2026-10-10 --plot
      python 05_himawari_gap.py --hours 1 3 4 5             (choose your own WIB hours)
"""
import argparse
import bz2
import urllib.request
from datetime import datetime, timedelta

from config import OUT, SITES, window

BUCKET = "https://noaa-himawari9.s3.amazonaws.com"
BANDS = (7, 14)
SEGMENTS = (5, 6)               # full disk is split into 10 strips; 5 and 6 straddle the equator
DEFAULT_HOURS = (22, 1, 3, 4, 5, 13)
WIB = 7


def files_for(night, hour):
    """S3 keys for one WIB hour. Hours from 19 to 23 fall on the evening date, the rest on the next day."""
    day = datetime.fromisoformat(night) + timedelta(days=0 if hour >= 19 else 1)
    local = day + timedelta(hours=hour)
    utc = local - timedelta(hours=WIB)
    stamp, folder = utc.strftime("%Y%m%d_%H%M"), utc.strftime("%Y/%m/%d/%H%M")
    return local, [f"AHI-L1b-FLDK/{folder}/HS_H09_{stamp}_B{b:02d}_FLDK_R20_S{s:02d}10.DAT.bz2"
                   for b in BANDS for s in SEGMENTS]


def fetch(key, dest):
    dat = dest / key.split("/")[-1].removesuffix(".bz2")
    if not dat.exists():
        with urllib.request.urlopen(f"{BUCKET}/{key}", timeout=300) as r:
            dat.write_bytes(bz2.decompress(r.read()))
    return dat


def plot(files, local, label, dest):
    from satpy import Scene  # optional: pip install satpy
    import matplotlib.pyplot as plt

    scn = Scene(reader="ahi_hsd", filenames=[str(f) for f in files])
    scn.load(["B07", "B14"])
    for site, (w, s, e, n) in SITES.items():
        sub = scn.crop(ll_bbox=(w - 1, s - 1, e + 1, n + 1))
        diff = sub["B07"] - sub["B14"]
        fig, ax = plt.subplots(figsize=(5, 5))
        im = ax.imshow(diff.values, cmap="inferno", vmin=0, vmax=15)
        ax.set_title(f"{site}  {local:%Y-%m-%d %H:%M} WIB ({label})\nHimawari-9 B07 - B14 (K)")
        ax.axis("off")
        fig.colorbar(im, ax=ax, shrink=0.8)
        png = dest / f"{site}_{local:%Y%m%d_%H%M}_{label}_B07-B14.png"
        fig.savefig(png, dpi=120, bbox_inches="tight")
        plt.close(fig)
        print(f"    {png.name}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--night", action="append", help="evening date, YYYY-MM-DD (repeatable)")
    ap.add_argument("--hours", type=int, nargs="+", default=list(DEFAULT_HOURS), help="WIB hours, 0-23")
    ap.add_argument("--plot", action="store_true", help="make B07 - B14 PNGs with satpy")
    args = ap.parse_args()
    nights = args.night or ["2026-09-04"]  # first unhealthy PSI in Singapore this year

    for night in nights:
        dest = OUT / "2026" / "himawari" / night
        dest.mkdir(parents=True, exist_ok=True)
        for hour in sorted(args.hours, key=lambda h: (h < 19, h)):  # evening first, then morning
            local, keys = files_for(night, hour)
            label = window(hour)
            print(f"{local:%Y-%m-%d %H:%M} WIB ({label}): downloading {len(keys)} files")
            files = [fetch(k, dest) for k in keys]
            if args.plot:
                plot(files, local, label, dest)
        print(f"  -> {dest}")


if __name__ == "__main__":
    main()
