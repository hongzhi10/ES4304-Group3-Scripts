"""Map downloaded ECOSTRESS night LST tiles side by side, marking unusually hot pixels.

Run after 04_ecostress_lst.py --download (or with the files already in
data/2026/ecostress/files). Each LST tile is drawn with clouds greyed out, fire-hot pixels (above 310 K)
marked, a scale bar, a north arrow and the local pass time and window. At night
the sea and rivers reach about 304 K, so 310 K separates fire from warm water.

Run:  python 07_ecostress_maps.py                 (every LST tile in the folder)
      python 07_ecostress_maps.py --tile 49MHS    (one map tile only)
"""
import argparse
import re
from datetime import datetime, timedelta

import matplotlib
import numpy as np
import rasterio

from config import OUT, window

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.colors import ListedColormap  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

FIRE_K = 310.0  # night LST above this is treated as fire-hot (warm water peaks near 304 K)
WIB = 7


def furniture(ax, w, h, res, km=10):
    px = km * 1000 / res
    x0, y0 = w * 0.05, h * 0.93
    ax.plot([x0, x0 + px], [y0, y0], color="white", lw=4, solid_capstyle="butt")
    ax.plot([x0, x0 + px], [y0, y0], color="black", lw=1.5, solid_capstyle="butt")
    ax.text(x0 + px / 2, y0 - h * 0.025, f"{km} km", color="white", ha="center", va="bottom", fontsize=9,
            bbox=dict(facecolor="black", alpha=0.5, lw=0, pad=1.5))
    ax.annotate("N", xy=(w * 0.93, h * 0.06), xytext=(w * 0.93, h * 0.16), color="white", ha="center",
                fontsize=11, fontweight="bold", arrowprops=dict(facecolor="white", width=3, headwidth=10),
                bbox=dict(facecolor="black", alpha=0.5, lw=0, pad=1.5))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tile", help="MGRS tile, e.g. 49MHS")
    args = ap.parse_args()
    folder = OUT / "2026" / "ecostress" / "files"
    lsts = sorted(p for p in folder.glob("*_LST.tif") if not args.tile or f"_{args.tile}_" in p.name)
    if not lsts:
        raise SystemExit(f"No LST files in {folder}; run 04_ecostress_lst.py --download first")

    panels = []
    for p in lsts:
        utc = datetime.strptime(re.search(r"_(\d{8}T\d{6})_", p.name).group(1), "%Y%m%dT%H%M%S")
        local = utc + timedelta(hours=WIB)
        with rasterio.open(p) as src:
            lst = src.read(1).astype("float32")
            res = src.res[0]
            if src.nodata is not None:
                lst[lst == src.nodata] = np.nan
        cloud_path = p.with_name(p.name.replace("_LST.tif", "_cloud.tif"))
        cloud = np.zeros_like(lst, dtype=bool)
        if cloud_path.exists():
            with rasterio.open(cloud_path) as c:
                cloud = c.read(1) == 1
        lst[(lst < 250) | (lst > 400)] = np.nan
        clear = np.isfinite(lst) & ~cloud
        imaged = np.isfinite(lst) | cloud  # cloudy pixels carry no temperature
        med = float(np.nanmedian(lst[clear])) if clear.any() else np.nan
        hot = clear & (lst > FIRE_K)
        panels.append(dict(name=p.name, local=local, win=window(local.hour + local.minute / 60), lst=lst,
                           cloud=cloud, hot=hot, med=med, res=res,
                           clear_pct=100 * clear.sum() / max(1, imaged.sum())))
        print(f"{local:%Y-%m-%d %H:%M} WIB ({panels[-1]['win']}): clear {panels[-1]['clear_pct']:.0f}%, "
              f"median {med:.1f} K, {hot.sum()} pixels above {FIRE_K:.0f} K")

    fig, axes = plt.subplots(1, len(panels), figsize=(7.5 * len(panels), 8), dpi=120, facecolor="white", squeeze=False)
    for ax, s in zip(axes[0], panels):
        h, w = s["lst"].shape
        im = ax.imshow(s["lst"], cmap="magma", vmin=290, vmax=306, interpolation="nearest")
        ax.imshow(np.ma.masked_where(~s["cloud"], s["cloud"]), cmap=ListedColormap(["#c7ccd6"]), interpolation="nearest")
        ax.imshow(np.ma.masked_where(~s["hot"], s["hot"]), cmap=ListedColormap(["#29d3ff"]), interpolation="nearest")
        ax.set_xticks([]), ax.set_yticks([])
        furniture(ax, w, h, s["res"])
        label = {"gap": "inside the 03:00–06:00 gap", "night": "night, outside the gap", "day": "daytime"}[s["win"]]
        ax.set_title(f"{s['local']:%d %b %Y, %H:%M} WIB · {label}\n{s['hot'].sum():,} fire-hot pixels "
                     f"(70 m) · {100 - s['clear_pct']:.0f}% cloud", fontsize=12, fontweight="bold")
        fig.colorbar(im, ax=ax, shrink=0.7, label="Land surface temperature (K)")
        ax.legend(handles=[Patch(color="#29d3ff", label=f"Fire-hot, above {FIRE_K:.0f} K"),
                           Patch(color="#c7ccd6", label="Cloud"),
                           Patch(facecolor="white", edgecolor="#888", label="No data")],
                  loc="lower right", fontsize=9, framealpha=0.9)
    fig.suptitle("ECOSTRESS 70 m land surface temperature, tile " + panels[0]["name"].split("_")[5], fontsize=14,
                 fontweight="bold")
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    path = OUT / "2026" / "ecostress" / "ecostress_night_vs_gap.png"
    fig.savefig(path)
    print(f"-> {path}")


if __name__ == "__main__":
    main()
