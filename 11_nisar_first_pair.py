"""First look at one NISAR GUNW pair over a case-study site (Objective 3).

Crops one pre-fire pair to the site box and reports how much of it keeps a usable
signal. It then compares land that later burned with land that did not. One pair
cannot show drying or fire risk: this is a data check, not a Phase 2 result.

Line-of-sight movement = -(wavelength / 4 pi) x phase, so positive means towards the
satellite. The ionosphere and troposphere layers supplied with the product are removed,
then a plane is fitted and removed, so only local differences inside the box are kept.

Run:  python 11_nisar_first_pair.py GUNW.h5 ogan_komering_ilir OUTPUT_FOLDER
"""
import json
import sys
from pathlib import Path

import h5py
import numpy as np
import rasterio
from matplotlib import font_manager, pyplot as plt
from rasterio.transform import from_origin
from rasterio.warp import Resampling, reproject
from scipy.interpolate import RegularGridInterpolator

BOXES = {"ogan_komering_ilir": (105.2, -3.8, 105.8, -3.2)}
COH_MIN = 0.3
STEP = 0.00072                      # about 80 m
HERE = Path(__file__).parent


def layer_at_ground(meta, name, x, y, height=20.0):
    """Sample a 3-D correction cube at about ground height onto the product grid."""
    h = meta["heightAboveEllipsoid"][:]
    cube = meta[name][:2 + int(np.searchsorted(h, height))]
    k = int(np.searchsorted(h, height)) - 1
    w = (height - h[k]) / (h[k + 1] - h[k])
    plane = (1 - w) * cube[k] + w * cube[k + 1]
    f = RegularGridInterpolator((meta["yCoordinates"][:][::-1], meta["xCoordinates"][:]), plane[::-1],
                                bounds_error=False, fill_value=np.nan)
    yy, xx = np.meshgrid(y, x, indexing="ij")
    return f((yy, xx)).astype("float32")


def to_box(arr, src_transform, crs, box, nodata=np.nan, dtype="float32"):
    w, s, e, n = box
    width, height = round((e - w) / STEP), round((n - s) / STEP)
    out = np.full((height, width), nodata, dtype)
    reproject(arr, out, src_transform=src_transform, src_crs=crs, src_nodata=nodata,
              dst_transform=from_origin(w, n, STEP, STEP), dst_crs="EPSG:4326", dst_nodata=nodata,
              resampling=Resampling.nearest)
    return out


def main():
    h5, site, out = sys.argv[1], sys.argv[2], Path(sys.argv[3])
    box = BOXES[site]
    with h5py.File(h5, "r") as f:
        grid = f["science/LSAR/GUNW/grids/frequencyA/unwrappedInterferogram"]
        hh, meta = grid["HH"], f["science/LSAR/GUNW/metadata/radarGrid"]
        wavelength = 299792458.0 / f["science/LSAR/GUNW/grids/frequencyA/centerFrequency"][()]
        epsg = int(hh["projection"][()])
        x, y = hh["xCoordinates"][:], hh["yCoordinates"][:]
        dx, dy = float(x[1] - x[0]), float(y[0] - y[1])
        tr = from_origin(x[0] - dx / 2, y[0] + dy / 2, dx, dy)
        crs = f"EPSG:{epsg}"
        print("mask:", grid["mask"].attrs["description"].decode()[600:2200])
        m = grid["mask"][:].astype("uint32")
        raw = hh["unwrappedPhase"][:]
        iono = hh["ionospherePhaseScreen"][:]
        wet = layer_at_ground(meta, "wetTroposphericPhaseScreen", x, y)
        dry = layer_at_ground(meta, "hydrostaticTroposphericPhaseScreen", x, y)
        L = {"coh": hh["coherenceMagnitude"][:], "raw": raw, "iono": iono, "tropo": wet + dry,
             "conn": hh["connectedComponents"][:].astype("float32"),
             "low": (m & 255).astype("float32"), "flags": (m >> 8).astype("float32")}
    L["conn"][L["conn"] >= 65535] = np.nan
    B = {k: to_box(v, tr, crs, box) for k, v in L.items()}

    with rasterio.open(HERE / "data/2026/stages" / site / f"{site}_dnbr_classes_2026.tif") as src:
        burn = np.full(B["coh"].shape, 255, "uint8")
        reproject(rasterio.band(src, 1), burn, dst_transform=from_origin(box[0], box[3], STEP, STEP),
                  dst_crs="EPSG:4326", dst_nodata=255, resampling=Resampling.nearest)
        print("burn classes", dict(zip(*[a.tolist() for a in np.unique(burn, return_counts=True)])))

    seen = np.isfinite(B["coh"]) & (B["coh"] > 0)
    for k in ("low", "flags", "conn"):
        vals, cnt = np.unique(B[k][seen], return_counts=True)
        print(k, {int(a) if np.isfinite(a) else -1: int(b) for a, b in list(zip(vals, cnt))[:14]})
    water = (B["low"] >= 100) & seen
    land = seen & ~water
    good = land & (B["coh"] >= COH_MIN) & (B["conn"] > 0)

    def spread(p):
        yy, xx = np.nonzero(good)
        A = np.c_[xx, yy, np.ones(len(xx))]
        v = p[good]
        ok = np.isfinite(v)
        c = np.linalg.lstsq(A[ok], v[ok], rcond=None)[0]
        Y, X = np.indices(p.shape)
        q = p - (c[0] * X + c[1] * Y + c[2])
        return q, float(np.nanstd(q[good]))

    tests = {"raw": B["raw"], "minus iono": B["raw"] - B["iono"], "plus iono": B["raw"] + B["iono"],
             "minus iono minus tropo": B["raw"] - B["iono"] - B["tropo"],
             "minus iono plus tropo": B["raw"] - B["iono"] + B["tropo"]}
    sd = {k: round(spread(v)[1] * wavelength / (4 * np.pi) * 100, 2) for k, v in tests.items()}
    print("spread in cm after plane removal", sd)
    phase, _ = spread(tests["minus iono minus tropo"])
    los = -(wavelength / (4 * np.pi)) * phase * 100
    los -= np.nanmedian(los[good])
    los[~good] = np.nan

    later = land & (burn >= 2) & (burn != 255)
    unburned = land & (burn == 0)
    stats = {"wavelength_m": round(float(wavelength), 4), "box_with_data_pct": round(100 * seen.mean(), 1),
             "land_pct_of_seen": round(100 * land.sum() / seen.sum(), 1),
             "median_coherence_land": round(float(np.nanmedian(B["coh"][land])), 3),
             "coh_at_least_0.3_pct_land": round(100 * (B["coh"][land] >= COH_MIN).mean(), 1),
             "usable_pct_land": round(100 * good.sum() / land.sum(), 1),
             "spread_cm": sd, "los_p2_p98_cm": [round(float(v), 2) for v in np.nanpercentile(los, [2, 98])]}
    for name, m in (("later_burned", later), ("not_burned", unburned)):
        g = m & good
        stats[name] = {"land_pixels": int(m.sum()), "median_coherence": round(float(np.nanmedian(B["coh"][m])), 3),
                       "usable_pct": round(100 * g.sum() / max(1, m.sum()), 1),
                       "median_los_cm": round(float(np.nanmedian(los[g])), 2),
                       "iqr_los_cm": [round(float(v), 2) for v in np.nanpercentile(los[g], [25, 75])]}
    print(json.dumps(stats, indent=1))
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{site}_nisar_first_pair.json").write_text(json.dumps(stats, indent=1))
    np.savez_compressed(out / "arrays.npz", coh=B["coh"], los=los, land=land)

    F = "/Applications/Microsoft PowerPoint.app/Contents/Resources/DFonts/"
    for n in ("Aptos.ttf", "Aptos-Bold.ttf"):
        font_manager.fontManager.addfont(F + n)
    plt.rcParams.update({"font.family": "Aptos", "font.size": 15, "text.color": "#2D2A32",
                         "axes.labelcolor": "#2D2A32", "xtick.color": "#6B6475", "ytick.color": "#6B6475"})
    ext = [box[0], box[2], box[1], box[3]]
    fig, ax = plt.subplots(1, 2, figsize=(16, 7.4), constrained_layout=True)
    c = np.where(land, B["coh"], np.nan)
    cm = plt.get_cmap("magma").copy(); cm.set_bad("#D8D3DD")
    a = ax[0].imshow(c, extent=ext, vmin=0, vmax=0.8, cmap=cm, interpolation="nearest")
    ax[0].set_title(f"Coherence: {stats['coh_at_least_0.3_pct_land']:.0f}% of land at 0.3 or above", loc="left", fontweight="bold")
    fig.colorbar(a, ax=ax[0], shrink=.82, label="Coherence, 12 days")
    v = float(np.ceil(np.nanpercentile(np.abs(los), 98)))
    cm = plt.get_cmap("RdBu_r").copy(); cm.set_bad("#D8D3DD")
    b = ax[1].imshow(los, extent=ext, vmin=-v, vmax=v, cmap=cm, interpolation="nearest")
    ax[1].set_title("Line-of-sight change: likely mostly atmosphere", loc="left", fontweight="bold")
    fig.colorbar(b, ax=ax[1], shrink=.82, label="cm (positive = towards satellite)")
    for q in ax:
        q.set_xlabel("Longitude (°E)"); q.set_ylabel("Latitude (°)")
        q.set_xticks([105.2, 105.4, 105.6, 105.8]); q.set_yticks([-3.8, -3.6, -3.4, -3.2])
    fig.savefig(out / f"{site}_nisar_first_pair.png", dpi=120, facecolor="white")


main()
