"""Sentinel-2 maps of each site through the four stages of the 2026 fires: pre, co, post and recovery.

Uses the public Sentinel-2 L2A archive on AWS (Earth Search catalogue), so it
needs no login and no Earth Engine. For each stage it picks the date with the
least cloud over the site, then makes:
  - a true-colour image (shows smoke haze),
  - a short-wave infrared image, B12 / B8A / B4 (sees through most smoke:
    burn scars look dark red-brown and active fire fronts glow orange),
  - a dNBR map from the pre-fire date to the latest clear view after it,
    with Key and Benson (2006) severity classes and burned hectares.
Maps carry a scale bar, a north arrow and a legend.

Stages, as in the Tutorial 1 Eaton Fire assignment, set around the August burning (2026 dates):
  prefire   17 Jun - 31 Jul   before the fire season
  cofire    1 - 31 Aug        active burning and smoke
  postfire  1 Sep - 15 Oct    burn scars after the August fires (the haze peaked on 9 Oct)
  recovery  16 Oct - 2 Nov    early regrowth once the rains return, about two months on
Each stage is saved as true colour, SWIR, NBR and NDVI (NDVI from B8A and B4).

Run:  python 06_sentinel2_stages.py                       (all three sites, 40 m)
      python 06_sentinel2_stages.py --site kahayan_sebangau --res 60
Outputs in data/2026/stages/<site>/.
"""
import argparse
import json
import os
from collections import defaultdict

import matplotlib
import numpy as np
import requests
import rasterio
from affine import Affine
from rasterio.transform import from_origin
from rasterio.warp import Resampling, reproject, transform_bounds
from rasterio.windows import Window, from_bounds

from config import OUT, SITES

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.colors import BoundaryNorm, ListedColormap  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

os.environ.update(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", AWS_NO_SIGN_REQUEST="YES",
                  CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif", GDAL_HTTP_MULTIRANGE="YES",
                  GDAL_HTTP_MERGE_CONSECUTIVE_RANGES="YES")

STAC = "https://earth-search.aws.element84.com/v1/search"
STAGES = {
    "prefire": ("Pre-fire", "2026-06-17", "2026-07-31"),
    "cofire": ("Co-fire", "2026-08-01", "2026-08-31"),
    "postfire": ("Post-fire", "2026-09-01", "2026-11-02"),
}
MAX_DATES = 8                 # dates per stage composite
TARGET_CLEAR = 0.97           # stop adding dates once this share of the box is clear
UTM = "EPSG:32749"            # UTM zone 49S covers all three sites well enough for maps
CLEAR_SCL = (2, 4, 5, 7)      # dark areas (burned peat is dark), vegetation, bare, unclassified
GOOD_SCL = (2, 4, 5)          # preferred when filling: small clouds and their edges are often "unclassified"
DNBR_EDGES = [0.10, 0.27, 0.44, 0.66, 2.0]   # Key & Benson (2006)
DNBR_NAMES = ["Low", "Moderate-low", "Moderate-high", "High"]
DNBR_COLOURS = ["#f6d55c", "#f39c4a", "#d9482b", "#7a1022"]


def search(box, start, end):
    body = {"collections": ["sentinel-2-c1-l2a"], "bbox": list(box), "limit": 200,
            "datetime": f"{start}T00:00:00Z/{end}T23:59:59Z"}
    items = []
    while True:
        r = requests.post(STAC, json=body, timeout=120).json()
        items += r["features"]
        nxt = [l for l in r.get("links", []) if l.get("rel") == "next"]
        if not nxt:
            return items
        body = nxt[0].get("body", body)


def footprint_cover(items, box, n=40):
    """Share of the site box inside the union of the items' footprints (checked on an n x n grid)."""
    from matplotlib.path import Path as MPath
    w, s_, e, n_ = box
    xx, yy = np.meshgrid(np.linspace(w, e, n), np.linspace(s_, n_, n))
    pts = np.c_[xx.ravel(), yy.ravel()]
    inside = np.zeros(len(pts), bool)
    for it in items:
        g = it["geometry"]
        for poly in ([g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]):
            inside |= MPath(np.asarray(poly[0])).contains_points(pts)
    return inside.mean()


def rank_dates(items, box, res=600, latest_first=False):
    """Dates ordered by the clear share of the site box itself, from each date's scene classification
    read at a coarse pixel size (the tile-wide cloud figure says little about a small box).
    latest_first: newest date first instead, skipping dates under 15% clear, so a composite holds the
    latest clear view of each pixel (what a post-fire burn map needs)."""
    by = defaultdict(list)
    for it in items:
        by[it["properties"]["datetime"][:10]].append(it)
    tr, w, h = grid(box, res)
    def score(its):
        if footprint_cover(its, box) < 0.05:
            return 0.0
        return float(np.isin(read(its, "scl", tr, w, h, res, nearest=True)[0], CLEAR_SCL).mean())
    scored = [(d, its, score(its)) for d, its in by.items()]
    if latest_first:
        return [(d, its) for d, its, s in sorted(scored, key=lambda x: x[0], reverse=True) if s >= 0.15]
    return [(d, its) for d, its, s in sorted(scored, key=lambda x: (-x[2], x[0])) if s > 0]


MONTHS = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def grid(box, res):
    l, b, r, t = transform_bounds("EPSG:4326", UTM, *box)
    l, t = np.floor(l / res) * res, np.ceil(t / res) * res
    w, h = int(np.ceil((r - l) / res)), int(np.ceil((t - b) / res))
    return from_origin(l, t, res, res), w, h


def read(items, key, tr, w, h, res, bands=(1,), nearest=False):
    """Mosaic one asset from several tiles onto the common grid (0 = no data)."""
    out = np.zeros((len(bands), h, w), dtype="float32")
    dst_bounds = (tr.c, tr.f - h * res, tr.c + w * res, tr.f)
    rs = Resampling.nearest if nearest else Resampling.average
    for it in items:
        href = it["assets"][key]["href"]
        with rasterio.open(href) as src:
            try:
                win = from_bounds(*transform_bounds(UTM, src.crs, *dst_bounds), src.transform)
                win = win.intersection(Window(0, 0, src.width, src.height))
            except Exception:
                continue
            f = res / src.res[0]
            shape = (len(bands), max(1, int(win.height / f)), max(1, int(win.width / f)))
            data = src.read(list(bands), window=win, out_shape=shape, resampling=rs).astype("float32")
            st = src.window_transform(win) * Affine.scale(win.width / shape[2], win.height / shape[1])
            dest = np.zeros_like(out)
            reproject(data, dest, src_transform=st, src_crs=src.crs, dst_transform=tr, dst_crs=UTM,
                      resampling=Resampling.nearest if nearest else Resampling.bilinear, src_nodata=0, dst_nodata=0)
            fill = (out == 0) & (dest != 0)
            out[fill] = dest[fill]
    return out


def read_scl(items, tr, w, h, res, sub=3):
    """Scene classification on the output grid. The 20 m classes are read at res/sub, and an output
    pixel is marked cloud (9) if any of its sub-pixels is cloud, cloud shadow or cirrus, so cloud
    fringes that would tint the averaged bands are not taken as clear."""
    fine_tr = Affine(res / sub, 0, tr.c, 0, -res / sub, tr.f)
    fine = read(items, "scl", fine_tr, w * sub, h * sub, res / sub, nearest=True)[0]
    blocks = fine.reshape(h, sub, w, sub)
    out = blocks[:, sub // 2, :, sub // 2].copy()
    out[np.isin(blocks, (3, 8, 9, 10)).any(axis=(1, 3))] = 9
    return out


def refl(items, key, *g):
    rb = items[0]["assets"][key].get("raster:bands", [{}])[0]
    dn = read(items, key, *g)[0]
    r = dn * rb.get("scale", 1) + rb.get("offset", 0)
    r[dn == 0] = np.nan
    return r


def stretch(a, lo, hi):
    return np.clip((a - lo) / (hi - lo), 0, 1)


def furniture(ax, w, h, res, km=20):
    """Scale bar (bottom left) and north arrow (top right), in pixel coordinates."""
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
    ap.add_argument("--site", choices=SITES, action="append")
    ap.add_argument("--res", type=float, default=40.0, help="output pixel size in metres")
    args = ap.parse_args()

    for site in args.site or list(SITES):
        box, res = SITES[site], args.res
        out = OUT / "2026" / "stages" / site
        out.mkdir(parents=True, exist_ok=True)
        tr, w, h = grid(box, res)
        g = (tr, w, h, res)
        print(f"\n== {site}: {w} x {h} pixels at {res:.0f} m")

        stages = {}
        for key, (label, start, end) in STAGES.items():
            tc = np.zeros((3, h, w), "float32")
            b4, b8a, b12 = (np.full((h, w), np.nan, "float32") for _ in range(3))
            scl = np.zeros((h, w), "float32")
            used, sats = [], set()
            # Clearest date first (post-fire: newest date first). Each later date fills only pixels that are
            # still cloudy or not imaged with pixels it sees clearly: a cloud-free composite of up to MAX_DATES dates.
            for date, items in rank_dates(search(box, start, end), box, latest_first=key == "postfire")[:MAX_DATES]:
                sc = read_scl(items, *g)
                # Take a pixel if it upgrades what is there: nothing -> anything, cloud -> clear,
                # unclassified -> vegetation, bare or dark
                fill = (((scl == 0) & (sc > 0)) | (~np.isin(scl, CLEAR_SCL) & np.isin(sc, CLEAR_SCL))
                        | (~np.isin(scl, GOOD_SCL) & np.isin(sc, GOOD_SCL)))
                if fill.sum() < 0.005 * fill.size:
                    continue
                t = read(items, "visual", *g, bands=(1, 2, 3))
                r4, r8, r12 = (refl(items, k, *g) for k in ("red", "nir08", "swir22"))
                tc[:, fill] = t[:, fill]
                b4[fill], b8a[fill], b12[fill], scl[fill] = r4[fill], r8[fill], r12[fill], sc[fill]
                used.append(date)
                sats |= {i["properties"]["platform"].replace("sentinel-", "Sentinel-").upper().replace("SENTINEL", "Sentinel")
                         for i in items}
                if np.isin(scl, GOOD_SCL).mean() >= TARGET_CLEAR:
                    break
            clear = np.isin(scl, CLEAR_SCL)
            nbr = (b8a - b12) / (b8a + b12)
            ndvi = (b8a - b4) / (b8a + b4)
            dates = f"{int(used[0][8:])} {MONTHS[int(used[0][5:7])]}" if used else "none"
            if len(used) > 1:
                dates += f" 2026 + {len(used) - 1} more date{'s' if len(used) > 2 else ''}"
            stages[key] = dict(label=label, date=dates, dates=used, sats=sorted(sats), tc=tc, swir=(b12, b8a, b4),
                               nbr=nbr, ndvi=ndvi, clear=clear, clear_pct=100 * clear.sum() / max(1, (scl > 0).sum()),
                               cover_pct=100 * (tc.sum(0) > 0).mean())
            print(f"  {label:<12} {dates:<16} {', '.join(sorted(sats))}: covers {stages[key]['cover_pct']:.0f}% "
                  f"of the box, {stages[key]['clear_pct']:.0f}% of it clear, {100 * (scl == 7).mean():.1f}% unclassified")

        # dNBR: pre-fire NBR minus the clear post-fire NBR, else the co-fire NBR
        pre = stages["prefire"]
        post = np.full((h, w), np.nan, dtype="float32")
        post_date = np.zeros((h, w), dtype="uint8")
        for i, k in enumerate(("postfire", "cofire"), start=1):
            s = stages[k]
            take = np.isnan(post) & s["clear"] & np.isfinite(s["nbr"])
            post[take] = s["nbr"][take]
            post_date[take] = i
        dnbr = np.where(pre["clear"] & np.isfinite(pre["nbr"]), pre["nbr"] - post, np.nan)
        with rasterio.open(out / f"{site}_dnbr_2026.tif", "w", driver="GTiff", height=h, width=w, count=1,
                           dtype="float32", crs=UTM, transform=tr, nodata=np.nan, compress="deflate") as dst:
            dst.write(dnbr.astype("float32"), 1)
        pix_ha = res * res / 1e4
        valid = np.isfinite(dnbr)
        summary = {"site": site, "res_m": res, "dates": {k: s["dates"] for k, s in stages.items()},
                   "clear_pct": {k: round(s["clear_pct"], 1) for k, s in stages.items()},
                   "dnbr_valid_ha": round(valid.sum() * pix_ha), "burned_ha": {}}
        for lo, hi, name in zip(DNBR_EDGES[:-1], DNBR_EDGES[1:], DNBR_NAMES):
            summary["burned_ha"][name] = round(((dnbr >= lo) & (dnbr < hi)).sum() * pix_ha)
        summary["burned_ha_total"] = sum(summary["burned_ha"].values())
        summary["burned_ha_moderate_or_higher"] = summary["burned_ha_total"] - summary["burned_ha"]["Low"]
        json.dump(summary, open(out / f"{site}_summary_2026.json", "w"), indent=2)
        print(f"  dNBR checked over {summary['dnbr_valid_ha']:,} ha of clear land; burned (dNBR > 0.10): "
              f"{summary['burned_ha_total']:,} ha  {summary['burned_ha']}")

        # GeoTIFFs for QGIS (08_qgis_maps.py): each stage in true colour and SWIR, plus severity classes
        def save(arr, name, count):
            with rasterio.open(out / name, "w", driver="GTiff", height=h, width=w, count=count, dtype="uint8",
                               crs=UTM, transform=tr, nodata=0 if count == 3 else 255, compress="deflate") as d:
                d.write(arr)
        for k, st in stages.items():
            save(st["tc"].astype("uint8"), f"{site}_{k}_truecolour.tif", 3)
            b12, b8a, b4 = st["swir"]
            sw = np.stack([stretch(b12, 0, 0.35), stretch(b8a, 0, 0.45), stretch(b4, 0, 0.2)])
            save(np.where(np.isfinite(sw), 1 + sw * 254, 0).astype("uint8"), f"{site}_{k}_swir.tif", 3)
            for name, arr in (("nbr", st["nbr"]), ("ndvi", st["ndvi"])):
                with rasterio.open(out / f"{site}_{k}_{name}.tif", "w", driver="GTiff", height=h, width=w, count=1,
                                   dtype="float32", crs=UTM, transform=tr, nodata=np.nan, compress="deflate") as d:
                    d.write(np.where(st["clear"], arr, np.nan).astype("float32"), 1)
        cls = np.full((h, w), 255, "uint8")              # 255 = no clear view
        cls[valid] = np.digitize(dnbr[valid], DNBR_EDGES)  # 0 unburned, 1 low ... 4 high
        save(cls[None], f"{site}_dnbr_classes_2026.tif", 1)
        summary["stage_labels"] = {k: f"{st['label']}, {st['date']}" + ("" if "2026" in st["date"] else " 2026")
                                   for k, st in stages.items()}
        json.dump(summary, open(out / f"{site}_summary_2026.json", "w"), indent=2)

        # Figure 1: true colour and SWIR for the four stages
        fig, axes = plt.subplots(2, len(stages), figsize=(5 * len(stages), 10.6), dpi=110, facecolor="white")
        for c, (k, s) in enumerate(stages.items()):
            tc = np.dstack([stretch(s["tc"][i], 0, 255) for i in range(3)])
            b12, b8a, b4 = s["swir"]
            sw = np.nan_to_num(np.dstack([stretch(b12, 0, 0.35), stretch(b8a, 0, 0.45), stretch(b4, 0, 0.2)]))
            for r, (img, kind) in enumerate(((tc, "True colour"), (sw, "Short-wave infrared (B12, B8A, B4)"))):
                ax = axes[r, c]
                ax.imshow(img, interpolation="nearest")
                ax.set_xticks([]), ax.set_yticks([])
                if r == 0:
                    ax.set_title(f"{s['label']}\n{s['date']} 2026 · {'/'.join(s['sats'])}", fontsize=13, fontweight="bold")
                if c == 0:
                    ax.set_ylabel(kind, fontsize=12)
                furniture(ax, w, h, res)
        fig.suptitle(f"{site.replace('_', '–').title()}: three stages of the 2026 fires (Sentinel-2 L2A, {res:.0f} m)",
                     fontsize=16, fontweight="bold")
        fig.text(0.5, 0.01, "Short-wave infrared sees through most smoke: burn scars appear dark red-brown, active "
                 "fire fronts bright orange. Clouds appear white in both rows.", ha="center", fontsize=11)
        fig.tight_layout(rect=(0, 0.03, 1, 0.95))
        p1 = out / f"{site}_stages_2026.png"
        fig.savefig(p1)
        plt.close(fig)

        # Figure 2: dNBR severity
        fig, ax = plt.subplots(figsize=(9, 9), dpi=120, facecolor="white")
        base = np.nan_to_num(np.dstack([stretch(pre["tc"][i], 0, 255) for i in range(3)]))
        ax.imshow(base * 0.55 + 0.45 * base.mean(axis=2, keepdims=True), interpolation="nearest")
        cmap = ListedColormap(DNBR_COLOURS)
        ax.imshow(np.ma.masked_less(np.nan_to_num(dnbr, nan=-1), DNBR_EDGES[0]), cmap=cmap,
                  norm=BoundaryNorm(DNBR_EDGES, cmap.N), interpolation="nearest")
        ax.imshow(np.ma.masked_where(valid, np.ones_like(dnbr)), cmap=ListedColormap(["#bfc5cf"]), alpha=0.75,
                  interpolation="nearest")
        ax.set_xticks([]), ax.set_yticks([])
        furniture(ax, w, h, res)
        handles = [Patch(color=c, label=f"{n} ({lo:.2f}–{hi:.2f})" if hi < 2 else f"{n} (> {lo:.2f})")
                   for c, n, lo, hi in zip(DNBR_COLOURS, DNBR_NAMES, DNBR_EDGES[:-1], DNBR_EDGES[1:])]
        handles.append(Patch(color="#bfc5cf", label="No clear view (cloud or smoke)"))
        ax.legend(handles=handles, title="Burn severity, dNBR", loc="lower right", fontsize=9, title_fontsize=10,
                  framealpha=0.9)
        ax.set_title(f"{site.replace('_', '–').title()}: burned since {pre['date']}\n"
                     f"{summary['burned_ha_moderate_or_higher']:,} ha moderate or higher severity "
                     f"({summary['burned_ha_total']:,} ha including low)", fontsize=13, fontweight="bold")
        fig.text(0.5, 0.01, f"Pre-fire {pre['date']} against post-fire {stages['postfire']['date']}, else co-fire "
                 f"{stages['cofire']['date']}. All land, not yet "
                 "clipped to the peat map. Low severity may include leftover haze.", ha="center", fontsize=8.5, wrap=True)
        fig.tight_layout(rect=(0, 0.03, 1, 1))
        p2 = out / f"{site}_dnbr_2026.png"
        fig.savefig(p2)
        plt.close(fig)
        print(f"  -> {p1.name}, {p2.name}, {site}_dnbr_2026.tif, {site}_summary_2026.json")


if __name__ == "__main__":
    main()
