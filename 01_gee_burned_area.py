"""Sentinel-2 burned area (dNBR), land cover and rainfall for each case-study site.

Uses Google Earth Engine, as in Tutorial 5.1. It:
  1. builds cloud-masked Sentinel-2 composites (Cloud Score+ >= 0.6) for the
     pre-fire and post-fire windows,
  2. computes NBR from B8A and B12 and dNBR = NBR(pre) - NBR(post),
  3. marks burned pixels (dNBR > 0.10, Key & Benson), drops water and
     built-up land (ESA WorldCover) and patches smaller than 0.36 ha,
  4. prints burned hectares per site, saves a quick-look PNG locally,
  5. writes daily CHIRPS rainfall per site to CSV,
  6. starts full-resolution GeoTIFF exports to Google Drive (folder ES4304_peat).

Run:  python 01_gee_burned_area.py            (2026 haze)
      python 01_gee_burned_area.py --year 2019 (reference year)
      add --no-export to skip the Drive exports
First time only:  earthengine authenticate
"""
import argparse
import csv
import urllib.request

import ee

from config import GEE_PROJECT, OUT, PERIODS, SITES

CS_CLEAR = 0.6      # Cloud Score+ threshold used in Section 3.1
DNBR_BURNED = 0.10  # Key & Benson (2006) lower bound for "burned"
MIN_PATCH_PX = 9    # 3 x 3 pixels at 20 m = 0.36 ha


def end_exclusive(date):
    return ee.Date(date).advance(1, "day")


def s2_nbr(region, start, end):
    """Median NBR from cloud-masked Sentinel-2, plus the number of scenes used."""
    cs = ee.ImageCollection("GOOGLE/CLOUD_SCORE_PLUS/V1/S2_HARMONIZED")
    s2 = (ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
          .filterBounds(region)
          .filterDate(start, end_exclusive(end))
          .linkCollection(cs, ["cs_cdf"])
          .map(lambda im: im.updateMask(im.select("cs_cdf").gte(CS_CLEAR))))
    nbr = s2.median().normalizedDifference(["B8A", "B12"]).rename("NBR")
    return nbr, s2.size()


def chirps_csv(site, region, start, end, path):
    """Daily mean rainfall over the site box, saved as CSV."""
    col = ee.ImageCollection("UCSB-CHG/CHIRPS/DAILY").filterDate(start, end_exclusive(end))

    def to_feature(im):
        mean = im.reduceRegion(ee.Reducer.mean(), region, 5566).get("precipitation")
        return ee.Feature(None, {"date": im.date().format("YYYY-MM-dd"), "rain_mm": mean})

    rows = col.map(to_feature).getInfo()["features"]
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "rain_mm"])
        for r in rows:
            w.writerow([r["properties"]["date"], r["properties"]["rain_mm"]])
    print(f"  rainfall: {len(rows)} days -> {path.name}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, default=2026, choices=sorted(PERIODS))
    ap.add_argument("--no-export", action="store_true", help="skip Google Drive exports")
    args = ap.parse_args()

    ee.Initialize(project=GEE_PROJECT)
    per = PERIODS[args.year]
    out = OUT / str(args.year) / "sentinel2"
    out.mkdir(parents=True, exist_ok=True)
    wc = ee.ImageCollection("ESA/WorldCover/v200").first()
    land = wc.neq(80).And(wc.neq(50))  # drop permanent water (80) and built-up (50)

    for site, box in SITES.items():
        print(f"\n== {site} ({args.year})")
        region = ee.Geometry.Rectangle(list(box))
        pre, n_pre = s2_nbr(region, *per["prefire"])
        post, n_post = s2_nbr(region, *per["postfire"])
        dnbr = pre.subtract(post).rename("dNBR").clip(region)
        burned = (dnbr.gt(DNBR_BURNED).And(land).selfMask())
        burned = burned.updateMask(burned.connectedPixelCount(MIN_PATCH_PX + 1, True).gte(MIN_PATCH_PX))
        burned = burned.rename("burned").toByte()

        ha = (burned.multiply(ee.Image.pixelArea()).divide(1e4)
              .reduceRegion(ee.Reducer.sum(), region, 20, maxPixels=1e10).get("burned"))
        print(f"  scenes used: pre-fire {n_pre.getInfo()}, post-fire {n_post.getInfo()}")
        print(f"  burned area (dNBR > {DNBR_BURNED}): {ee.Number(ha).getInfo() or 0:,.0f} ha (all land, before peat mask)")

        # Quick-look PNG: dNBR from green (unburned) to dark red (severe)
        url = dnbr.getThumbURL({"region": region, "dimensions": 1024, "min": -0.1, "max": 0.66,
                                "palette": ["1a9850", "fee08b", "fc8d59", "d73027", "67001f"]})
        png = out / f"{site}_dnbr_{args.year}.png"
        urllib.request.urlretrieve(url, png)
        print(f"  quick-look -> {png.name}")

        chirps_csv(site, region, per["prefire"][0], per["fire"][1], out / f"{site}_chirps_{args.year}.csv")

        if not args.no_export:
            for img, name in ((dnbr.toFloat(), "dnbr"), (burned, "burned"), (wc.clip(region).toByte(), "worldcover")):
                task = ee.batch.Export.image.toDrive(
                    image=img, description=f"{site}_{name}_{args.year}", folder="ES4304_peat",
                    fileNamePrefix=f"{site}_{name}_{args.year}", region=region, scale=20,
                    crs="EPSG:4326", maxPixels=1e10)
                task.start()
            print("  Drive exports started (dNBR, burned mask, WorldCover); check the Tasks tab")

    print(f"\nOutputs in {out}")


if __name__ == "__main__":
    main()
