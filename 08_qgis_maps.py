"""Build a QGIS project with styled layers and print layouts, and export the maps as PNG.

Run with QGIS's own Python, after 06_sentinel2_stages.py and 07_ecostress_maps.py:
    /Applications/QGIS.app/Contents/MacOS/python 08_qgis_maps.py
It writes:
    peat_fire_2026.qgz                  open this in QGIS to edit any map or layout
    data/2026/qgis/<site>_stages.png    true colour and SWIR for pre-fire, fire, peak haze, easing
    data/2026/qgis/<site>_severity.png  dNBR burn severity with burned hectares
    data/2026/qgis/ecostress_gap.png    ECOSTRESS night LST, outside and inside the gap
Every layout has a title, scale bar, north arrow, legend or key, and a source line.
If 10_burns_vs_hotspots.py has run, its hotspots (pre-fire scene to 31 Oct) are
drawn on the burn-severity maps.
"""
import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Running headless, QGIS does not set these itself; without them coordinate transforms fail
_QGIS_RES = Path("/Applications/QGIS.app/Contents/Resources/qgis")
if _QGIS_RES.exists():
    os.environ.setdefault("PROJ_DATA", str(_QGIS_RES / "proj"))
    os.environ.setdefault("PROJ_LIB", str(_QGIS_RES / "proj"))
    os.environ.setdefault("GDAL_DATA", str(_QGIS_RES / "gdal"))

from qgis.core import (  # noqa: E402
    Qgis, QgsApplication, QgsColorRampShader, QgsContrastEnhancement, QgsCoordinateReferenceSystem,
    QgsFillSymbol, QgsLayoutExporter, QgsLayoutItemLabel, QgsLayoutItemLegend, QgsLayoutItemMap,
    QgsLayoutItemPicture, QgsLayoutItemScaleBar, QgsLayoutItemShape, QgsLayoutPoint, QgsLayoutSize,
    QgsMapLayerLegendUtils,
    QgsMarkerSymbol, QgsMultiBandColorRenderer, QgsPalettedRasterRenderer, QgsPrintLayout, QgsProject,
    QgsRasterLayer, QgsRasterShader, QgsSingleBandPseudoColorRenderer, QgsSymbolLayerUtils, QgsTextFormat,
    QgsVectorLayer, QgsPalLayerSettings, QgsVectorLayerSimpleLabeling, QgsTextBufferSettings, QgsRectangle,
    QgsCategorizedSymbolRenderer, QgsRendererCategory, QgsCoordinateTransform,
)
from qgis.PyQt.QtCore import Qt  # noqa: E402
from qgis.PyQt.QtGui import QColor, QFont  # noqa: E402

HERE = Path(__file__).resolve().parent
DATA = HERE / "data" / "2026"
OUTDIR = DATA / "qgis"
SITES = ("east_jambi", "ogan_komering_ilir", "kubu_raya", "kahayan_sebangau")
NAMES = {"east_jambi": "East Jambi, Jambi", "ogan_komering_ilir": "Ogan Komering Ilir, South Sumatra",
         "kubu_raya": "Kubu Raya, West Kalimantan",
         "kahayan_sebangau": "Kahayan–Sebangau, Central Kalimantan"}
STAGES = ("prefire", "cofire", "postfire")
SEVERITY = [(1, "#f6d55c", "Low (0.10–0.27), may include haze"), (2, "#f39c4a", "Moderate-low (0.27–0.44)"),
            (3, "#d9482b", "Moderate-high (0.44–0.66)"), (4, "#7a1022", "High (above 0.66)"),
            (255, "#bfc5cf", "No clear view (cloud or smoke)")]
MM = Qgis.LayoutUnit.Millimeters
UTM = QgsCoordinateReferenceSystem("EPSG:32749")
WORLD = "/Applications/QGIS.app/Contents/Resources/qgis/resources/data/world_map.gpkg"  # Natural Earth, ships with QGIS
SITE_LABELS = {"east_jambi": "East Jambi", "ogan_komering_ilir": "Ogan Komering Ilir", "kubu_raya": "Kubu Raya", "kahayan_sebangau": "Kahayan–Sebangau"}
# Provincial capitals near the sites (decimal degrees)
PLACES = [("Palangka Raya", -2.21, 113.92), ("Jambi", -1.61, 103.61), ("Pontianak", -0.03, 109.34),
          ("Palembang", -2.98, 104.78), ("Pekanbaru", 0.51, 101.45), ("Banjarmasin", -3.32, 114.59)]
FIRE_PROVINCES = ("Riau", "Jambi", "Sumatera Selatan", "Kalimantan Barat", "Kalimantan Tengah", "Kalimantan Selatan")

app = QgsApplication([], False)
app.initQgis()
project = QgsProject.instance()
project.setCrs(UTM)
root = project.layerTreeRoot()


# ---------- layers ----------
def add_to(group, layer):
    project.addMapLayer(layer, False)
    group.addLayer(layer)
    return layer


def rgb(path, name):
    lyr = QgsRasterLayer(str(path), name)
    ren = QgsMultiBandColorRenderer(lyr.dataProvider(), 1, 2, 3)
    for setter in (ren.setRedContrastEnhancement, ren.setGreenContrastEnhancement, ren.setBlueContrastEnhancement):
        ce = QgsContrastEnhancement(lyr.dataProvider().dataType(1))
        ce.setMinimumValue(1)
        ce.setMaximumValue(255)
        ce.setContrastEnhancementAlgorithm(QgsContrastEnhancement.ContrastEnhancementAlgorithm.StretchToMinimumMaximum)
        setter(ce)
    lyr.setRenderer(ren)
    return lyr


def severity(path, name):
    lyr = QgsRasterLayer(str(path), name)
    lyr.dataProvider().setUseSourceNoDataValue(1, False)  # 255 means "no clear view" and should be drawn
    classes = [QgsPalettedRasterRenderer.Class(v, QColor(c), label) for v, c, label in SEVERITY]
    lyr.setRenderer(QgsPalettedRasterRenderer(lyr.dataProvider(), 1, classes))
    lyr.renderer().setOpacity(0.9)
    return lyr


def nbr(path, name):
    """NBR in labelled classes: low values are burned or bare, high values are healthy vegetation."""
    lyr = QgsRasterLayer(str(path), name)
    ramp = QgsColorRampShader(-1, 1)
    ramp.setColorRampType(QgsColorRampShader.Type.Discrete)
    ramp.setColorRampItemList([
        QgsColorRampShader.ColorRampItem(-0.1, QColor("#7f3b08"), "Below −0.10: burned or bare"),
        QgsColorRampShader.ColorRampItem(0.1, QColor("#e08214"), "−0.10 to 0.10"),
        QgsColorRampShader.ColorRampItem(0.3, QColor("#fee0b6"), "0.10 to 0.30"),
        QgsColorRampShader.ColorRampItem(0.5, QColor("#b8e186"), "0.30 to 0.50"),
        QgsColorRampShader.ColorRampItem(float("inf"), QColor("#4d9221"), "Above 0.50: healthy vegetation"),
    ])
    shader = QgsRasterShader(-1, 1)
    shader.setRasterShaderFunction(ramp)
    lyr.setRenderer(QgsSingleBandPseudoColorRenderer(lyr.dataProvider(), 1, shader))
    return lyr


def lst(path, name):
    lyr = QgsRasterLayer(str(path), name)
    # Discrete classes (each value is a class's upper bound) so the legend lists labelled temperature bands
    ramp = QgsColorRampShader(250, 450)
    ramp.setColorRampType(QgsColorRampShader.Type.Discrete)
    ramp.setColorRampItemList([
        QgsColorRampShader.ColorRampItem(292, QColor("#000004"), "Below 292 K"),
        QgsColorRampShader.ColorRampItem(295, QColor("#3b0f70"), "292–295 K"),
        QgsColorRampShader.ColorRampItem(298, QColor("#8c2981"), "295–298 K"),
        QgsColorRampShader.ColorRampItem(301, QColor("#de4968"), "298–301 K"),
        QgsColorRampShader.ColorRampItem(304, QColor("#fe9f6d"), "301–304 K"),
        QgsColorRampShader.ColorRampItem(310, QColor("#fcfdbf"), "304–310 K (warm water)"),
        QgsColorRampShader.ColorRampItem(float("inf"), QColor("#29d3ff"), "Above 310 K: fire-hot"),
    ])
    shader = QgsRasterShader(250, 450)
    shader.setRasterShaderFunction(ramp)
    lyr.setRenderer(QgsSingleBandPseudoColorRenderer(lyr.dataProvider(), 1, shader))
    return lyr


def site_boxes():
    """Site boxes from config.py, written once as GeoJSON so the project can reload them."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("config", HERE / "config.py")
    cfg = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cfg)
    feats = [{"type": "Feature", "properties": {"site": k, "label": SITE_LABELS[k]},
              "geometry": {"type": "Polygon", "coordinates": [[[w, s], [e, s], [e, n], [w, n], [w, s]]]}}
             for k, (w, s, e, n) in cfg.SITES.items()]
    path = DATA / "site_boxes.geojson"
    path.write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
    lyr = QgsVectorLayer(str(path), "Study boxes", "ogr")
    lyr.renderer().setSymbol(QgsFillSymbol.createSimple(
        {"color": "0,0,0,0", "outline_color": "#ffffff", "outline_width": "0.5", "outline_style": "dash"}))
    return lyr


def label(layer, field, size=9, colour="#ffffff", buffer="#14213D", italic=False, bold=False, polygon=False,
          dist=1.5, around=False):
    s = QgsPalLayerSettings()
    s.fieldName = field
    fmt = QgsTextFormat()
    f = QFont("Arial")
    f.setItalic(italic)
    f.setBold(bold)
    fmt.setFont(f)
    fmt.setSize(size)
    fmt.setColor(QColor(colour))
    b = QgsTextBufferSettings()
    b.setEnabled(True)
    b.setSize(0.8)
    b.setColor(QColor(buffer))
    fmt.setBuffer(b)
    s.setFormat(fmt)
    if around:  # polygons labelled beside their centre, horizontally (site boxes)
        s.placement = Qgis.LabelPlacement.AroundPoint
        s.dist = dist
    elif polygon:
        s.placement = Qgis.LabelPlacement.Horizontal
        s.centroidInside = True
        s.centroidWhole = False   # label the part of the province that is on the map
    else:
        s.placement = Qgis.LabelPlacement.OrderedPositionsAroundPoint
        s.dist = dist
    layer.setLabeling(QgsVectorLayerSimpleLabeling(s))
    layer.setLabelsEnabled(True)
    return layer


def provinces():
    lyr = QgsVectorLayer(f"{WORLD}|layername=states_provinces", "Provinces (Natural Earth)", "ogr")
    lyr.setSubsetString("\"adm0_a3\" = 'IDN'")
    lyr.renderer().setSymbol(QgsFillSymbol.createSimple(
        {"color": "0,0,0,0", "outline_color": "255,255,255,170", "outline_width": "0.35", "outline_style": "dot"}))
    return label(lyr, "name", 9, italic=True, polygon=True)


def places():
    feats = [{"type": "Feature", "properties": {"name": n}, "geometry": {"type": "Point", "coordinates": [lon, lat]}}
             for n, lat, lon in PLACES]
    path = DATA / "places.geojson"
    path.write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
    lyr = QgsVectorLayer(str(path), "Provincial capitals", "ogr")
    lyr.renderer().setSymbol(QgsMarkerSymbol.createSimple(
        {"name": "square", "color": "#ffffff", "outline_color": "#14213D", "outline_width": "0.4", "size": "2"}))
    return label(lyr, "name", 10, bold=True)


def overview_layers():
    """Locator and overview base: Indonesia with the six peat-fire provinces shaded, plus labelled site boxes."""
    land = QgsVectorLayer(f"{WORLD}|layername=countries", "Countries (Natural Earth)", "ogr")
    land.renderer().setSymbol(QgsFillSymbol.createSimple(
        {"color": "#e6e1d6", "outline_color": "#9a9384", "outline_width": "0.2"}))
    prov = QgsVectorLayer(f"{WORLD}|layername=states_provinces", "Six western peat-fire provinces", "ogr")
    names = ", ".join(f"'{n}'" for n in FIRE_PROVINCES)
    prov.setSubsetString(f"\"adm0_a3\" = 'IDN' AND \"name\" IN ({names})")
    prov.renderer().setSymbol(QgsFillSymbol.createSimple(
        {"color": "#f6c79a", "outline_color": "#ffffff", "outline_width": "0.25"}))
    names_lyr = QgsVectorLayer(f"{WORLD}|layername=states_provinces", "Province names", "ogr")
    names_lyr.setSubsetString("\"adm0_a3\" = 'IDN'")
    names_lyr.renderer().setSymbol(QgsFillSymbol.createSimple(
        {"color": "0,0,0,0", "outline_color": "#ffffff", "outline_width": "0.25"}))
    label(names_lyr, "name", 8, colour="#5b4a33", buffer="#ffffff", italic=True, polygon=True)
    sites = QgsVectorLayer(str(DATA / "site_boxes.geojson"), "Case-study sites", "ogr")
    sites.renderer().setSymbol(QgsFillSymbol.createSimple(
        {"color": "200,30,30,90", "outline_color": "#c81e1e", "outline_width": "0.6"}))
    label(sites, "label", 10, colour="#c81e1e", buffer="#ffffff", bold=True, dist=3, around=True)
    return land, prov, names_lyr, sites


def site_highlight(site):
    lyr = QgsVectorLayer(str(DATA / "site_boxes.geojson"), f"Locator: {SITE_LABELS[site]}", "ogr")
    lyr.setSubsetString(f"\"site\" = '{site}'")
    lyr.renderer().setSymbol(QgsFillSymbol.createSimple(
        {"color": "200,30,30,160", "outline_color": "#c81e1e", "outline_width": "0.8"}))
    return add_to(locator, lyr)  # must belong to the project, or the layout map drops it


def footprint_highlight(raster, name):
    """Locator box for a raster's footprint (e.g. an ECOSTRESS tile), in lon/lat."""
    tr = QgsCoordinateTransform(raster.crs(), QgsCoordinateReferenceSystem("EPSG:4326"), project)
    e = tr.transformBoundingBox(raster.extent())
    path = DATA / f"{name}_footprint.geojson"
    path.write_text(json.dumps({"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {},
        "geometry": {"type": "Polygon", "coordinates": [[[e.xMinimum(), e.yMinimum()], [e.xMaximum(), e.yMinimum()],
        [e.xMaximum(), e.yMaximum()], [e.xMinimum(), e.yMaximum()], [e.xMinimum(), e.yMinimum()]]]}}]}))
    lyr = QgsVectorLayer(str(path), f"Locator: {name}", "ogr")
    lyr.renderer().setSymbol(QgsFillSymbol.createSimple(
        {"color": "200,30,30,160", "outline_color": "#c81e1e", "outline_width": "0.8"}))
    return add_to(locator, lyr)


def geo_extent(w, s, e, n, crs):
    tr = QgsCoordinateTransform(QgsCoordinateReferenceSystem("EPSG:4326"), crs, project)
    return tr.transformBoundingBox(QgsRectangle(w, s, e, n))


def add_inset(lay, highlight, land, x, y, w=62, h=26):
    """Small locator map of western Indonesia with the site highlighted."""
    crs = QgsCoordinateReferenceSystem("EPSG:3857")
    m = add_map(lay, [highlight, land], geo_extent(95, -8, 120, 6, crs), x, y, w, h, bg="#cfe3f0", crs=crs)
    add_label(lay, "Location", x + 1, y + 1, 16, 4, 7, True, box="#ffffff")
    return m


def hotspots():
    path = DATA / "firms" / "hotspots_sites_fire_2026.csv"  # written by 10_burns_vs_hotspots.py
    if not path.exists():
        return None
    uri = f"file://{path}?delimiter=,&xField=longitude&yField=latitude&crs=EPSG:4326"
    lyr = QgsVectorLayer(uri, "FIRMS hotspots, pre-fire scene to 31 Oct 2026", "delimitedtext")
    lyr.renderer().setSymbol(QgsMarkerSymbol.createSimple(
        {"name": "circle", "color": "#29d3ff", "outline_color": "#06323f", "outline_width": "0.1", "size": "0.7"}))
    return lyr


# ---------- layout helpers ----------
def new_layout(name, w=400, h=225):
    old = project.layoutManager().layoutByName(name)
    if old:
        project.layoutManager().removeLayout(old)
    lay = QgsPrintLayout(project)
    lay.initializeDefaults()
    lay.setName(name)
    lay.pageCollection().page(0).setPageSize(QgsLayoutSize(w, h, MM))
    project.layoutManager().addLayout(lay)
    return lay


def place(item, x, y, w, h):
    item.attemptMove(QgsLayoutPoint(x, y, MM))
    item.attemptResize(QgsLayoutSize(w, h, MM))


def add_map(lay, layers, extent, x, y, w, h, bg="#000000", crs=None):
    m = QgsLayoutItemMap(lay)
    m.setCrs(crs or layers[-1].crs())  # the bottom layer (usually the raster) sets the map projection
    lay.addLayoutItem(m)
    place(m, x, y, w, h)
    m.setLayers(layers)
    m.setKeepLayerSet(True)
    m.zoomToExtent(extent)
    m.setFrameEnabled(True)
    m.setBackgroundColor(QColor(bg))
    return m


def add_label(lay, text, x, y, w, h, size=10, bold=False, colour="#14213D", align=Qt.AlignmentFlag.AlignLeft,
              box=None):
    lb = QgsLayoutItemLabel(lay)
    lb.setText(text)
    fmt = QgsTextFormat()
    f = QFont("Arial")
    f.setBold(bold)
    fmt.setFont(f)
    fmt.setSize(size)
    fmt.setColor(QColor(colour))
    lb.setTextFormat(fmt)
    lb.setHAlign(align)
    lb.setVAlign(Qt.AlignmentFlag.AlignVCenter)
    if box:
        lb.setBackgroundEnabled(True)
        lb.setBackgroundColor(QColor(box))
        lb.setMarginX(1.5)
    lay.addLayoutItem(lb)
    place(lb, x, y, w, h)
    return lb


def add_scalebar(lay, m, x, y, km=10):
    sb = QgsLayoutItemScaleBar(lay)
    sb.setStyle("Single Box")
    sb.setLinkedMap(m)
    sb.setUnits(Qgis.DistanceUnit.Kilometers)
    sb.setUnitLabel("km")
    sb.setUnitsPerSegment(km)
    sb.setNumberOfSegments(2)
    sb.setNumberOfSegmentsLeft(0)
    fmt = QgsTextFormat()
    fmt.setSize(8)
    sb.setTextFormat(fmt)
    lay.addLayoutItem(sb)
    sb.attemptMove(QgsLayoutPoint(x, y, MM))
    return sb


def add_north(lay, m, x, y, size=12):
    na = QgsLayoutItemPicture(lay)
    na.setPicturePath(QgsSymbolLayerUtils.svgSymbolNameToPath("arrows/NorthArrow_02.svg", project.pathResolver()))
    na.setLinkedMap(m)
    lay.addLayoutItem(na)
    place(na, x, y, size, size)
    return na


def add_legend(lay, m, layers, title, x, y, w=95):
    lg = QgsLayoutItemLegend(lay)
    lg.setLinkedMap(m)
    lg.setAutoUpdateModel(False)
    lg.setTitle(title)
    group = lg.model().rootGroup()
    group.clear()
    for lyr in layers:
        node = group.addLayer(lyr)
        if isinstance(lyr.renderer(), (QgsSingleBandPseudoColorRenderer, QgsPalettedRasterRenderer)):
            # drop the "Band 1 (Gray)" header node, keep the labelled classes
            n = len(lg.model().layerLegendNodes(node))
            QgsMapLayerLegendUtils.setLegendNodeOrder(node, list(range(1, n)))
            lg.model().refreshLayerLegend(node)
    lay.addLayoutItem(lg)
    lg.attemptMove(QgsLayoutPoint(x, y, MM))
    lg.setResizeToContents(True)
    lg.setFrameEnabled(True)
    lg.setBackgroundColor(QColor("#ffffff"))
    return lg


def export(lay, path, dpi=200):
    s = QgsLayoutExporter.ImageExportSettings()
    s.dpi = dpi
    res = QgsLayoutExporter(lay).exportToImage(str(path), s)
    print(f"  {'ok ' if res == QgsLayoutExporter.ExportResult.Success else 'FAILED'} {path.name}")


# ---------- build ----------
OUTDIR.mkdir(parents=True, exist_ok=True)
boxes = add_to(root.addGroup("Study boxes"), site_boxes())
hot = hotspots()
if hot:
    add_to(root.addGroup("FIRMS"), hot)
lbl = root.addGroup("Labels")
towns = add_to(lbl, places())
provs = add_to(lbl, provinces())
ov = root.addGroup("Overview")
locator = root.addGroup("Locator boxes")
land, fireprov, provnames, sites_ov = (add_to(ov, l) for l in overview_layers())

# Layout 0: study-area overview (for the scope slide)
lay = new_layout("Study area")
merc = QgsCoordinateReferenceSystem("EPSG:3857")
m = add_map(lay, [towns, sites_ov, provnames, fireprov, land], geo_extent(97, -6.5, 118, 4, merc), 8, 8, 300, 209,
            bg="#cfe3f0", crs=merc)
add_label(lay, "Case-study sites", 316, 8, 80, 10, 16, True)
add_label(lay, "Four sites in the six western peat-fire provinces (shaded). Objectives 1 and 2 cover all of "
          "Indonesia; the radar part of Objective 3 uses these four sites.", 316, 20, 80, 30, 9, colour="#4A5568")
add_legend(lay, m, [sites_ov, towns, fireprov], "Legend", 316, 54, 80)
add_scalebar(lay, m, 316, 160, km=200)
add_north(lay, m, 316, 175, 14)
add_label(lay, "Boundaries and coastlines: Natural Earth (bundled with QGIS). Pseudo-Mercator. Site boxes are "
          "approximate.", 316, 195, 80, 20, 8, colour="#4A5568")
export(lay, OUTDIR / "study_area.png")

for site in SITES:
    folder = DATA / "stages" / site
    summary_path = folder / f"{site}_summary_2026.json"
    if not summary_path.exists():
        print(f"skip {site}: run 06_sentinel2_stages.py first")
        continue
    summary = json.loads(summary_path.read_text())
    labels = summary.get("stage_labels", {k: k for k in STAGES})
    # Stage maps show one mostly visible date per stage (06b_single_date_stages.py) when it has run;
    # the severity map below still uses the composites
    single = folder / "single" / f"{site}_single_dates.json"
    stage_dir = single.parent if single.exists() else folder
    if single.exists():
        labels = json.loads(single.read_text())["stage_labels"]
    grp = root.addGroup(NAMES[site])
    tc = {k: add_to(grp, rgb(stage_dir / f"{site}_{k}_truecolour.tif", f"{labels[k]} · true colour")) for k in STAGES}
    sw = {k: add_to(grp, rgb(stage_dir / f"{site}_{k}_swir.tif", f"{labels[k]} · SWIR")) for k in STAGES}
    nb = {k: add_to(grp, nbr(stage_dir / f"{site}_{k}_nbr.tif", f"{labels[k]} · NBR")) for k in STAGES}
    pre_tc = add_to(grp, rgb(folder / f"{site}_prefire_truecolour.tif", "Pre-fire composite · true colour"))
    sev = add_to(grp, severity(folder / f"{site}_dnbr_classes_2026.tif", "Burn severity, dNBR"))
    ext = sev.extent()

    # Layout 1: the stages of the fires (as in the Tutorial 1 Eaton Fire assignment)
    w, gap = 58, 3
    right = 8 + len(STAGES) * (w + gap) + 6
    lay = new_layout(f"{NAMES[site]} · three stages", w=right + 136)
    add_label(lay, f"{NAMES[site]}: three stages of the 2026 fires", 8, 4, right + 120, 10, 17, True)
    add_label(lay, "Sentinel-2 L2A, 60 m, " + ("one date per stage" if single.exists() else "set around the August burning")
              + ". Rows: true colour (smoke shows), short-wave infrared B12/B8A/B4 (sees through most smoke), and NBR.",
              8, 13, 245, 9, 8.5, colour="#4A5568")
    w, gap, top = 58, 3, 30
    first = nbr_map = None
    for c, k in enumerate(STAGES):
        x = 8 + c * (w + gap)
        add_label(lay, labels[k].replace(", ", "\n", 1), x, top - 9, w, 9, 9.5, True,
                  align=Qt.AlignmentFlag.AlignHCenter)
        extra = []  # hotspots go on the severity map, where they do not hide the burn scars
        rows = ([towns, boxes, tc[k]], [towns, boxes] + extra + [sw[k]], [towns, boxes, nb[k]])
        for r, layers in enumerate(rows):
            m = add_map(lay, layers, ext, x, top + r * (w + gap), w, w, bg="#c9ced6" if r == 2 else "#000000")
            first = first or m
            nbr_map = nbr_map or (m if r == 2 else None)
        if c == 0:
            for r, txt in enumerate(("True colour", "Short-wave infrared", "NBR")):
                add_label(lay, txt, x + 1.5, top + r * (w + gap) + 1.5, 32, 5, 7.5, True, box="#ffffff")
    add_legend(lay, nbr_map, [nb["prefire"]], "NBR (Normalised Burn Ratio)", right, 30, 80)
    add_inset(lay, site_highlight(site), land, right, 92, 75, 32)
    add_scalebar(lay, first, right, 130, km=10)
    add_north(lay, first, right + 60, 128, 10)
    add_label(lay, "Short-wave infrared: burn scars dark red-brown, active fire fronts orange, healthy forest green. "
              "NBR = (B8A − B12) / (B8A + B12); " + ("grey = cloud or water; NBR is shown through smoke. "
              if single.exists() else "grey = cloud or smoke. ") + "Data: Copernicus Sentinel-2 L2A via Earth "
              "Search (AWS). " + ("Each stage is a single date, the one with most of the box free of cloud and smoke. "
              if single.exists() else "Each stage is a composite: cloudy pixels filled from the next-clearest date in "
              "the same window. ") + "Dashed: study box. UTM 49S.",
              right, 146, 128, 40, 8, colour="#4A5568")
    export(lay, OUTDIR / f"{site}_stages.png")

    # Layout 2: burn severity
    lay = new_layout(f"{NAMES[site]} · burn severity")
    m = add_map(lay, [towns, provs, boxes] + ([hot] if hot else []) + [sev, pre_tc], ext, 8, 8, 209, 209)
    pre_day = datetime.strptime(min(summary["dates"]["prefire"]), "%Y-%m-%d")
    add_label(lay, f"{NAMES[site]}: burned since {pre_day.day} {pre_day:%b %Y}", 225, 8, 170, 12, 16, True)
    mod = summary.get("burned_ha_moderate_or_higher", 0)
    tot = summary.get("burned_ha_total", 0)
    add_label(lay, f"{mod:,} ha moderate or higher severity", 225, 24, 170, 10, 14, True, colour="#7a1022")
    add_label(lay, f"{tot:,} ha including low severity, of {summary['dnbr_valid_ha']:,} ha seen clearly. All land, "
              "not yet clipped to the peat map.", 225, 33, 170, 10, 9, colour="#4A5568")
    bvh = DATA / "burns_vs_hotspots_2026.json"
    if hot and bvh.exists():
        miss = json.loads(bvh.read_text())[site]["moderate_or_higher_missed_pct"]
        add_label(lay, f"{miss:.0f}% of it had no FIRMS hotspot over it (blue dots: VIIRS and MODIS, "
                  f"{pre_day.day} {pre_day:%b} – 31 Oct)", 225, 44, 170, 8, 10, True, colour="#14213D")
    add_legend(lay, m, [sev] + ([hot] if hot else []), "Burn severity (dNBR, Key & Benson 2006)", 225, 54)
    add_scalebar(lay, m, 225, 120, km=10)
    add_north(lay, m, 225, 135, 14)
    stage_dates = ", ".join(f"{k} {min(v)} to {max(v)} ({len(v)} dates)" for k, v in summary["dates"].items())
    add_label(lay, f"dNBR = NBR before minus NBR after (Sentinel-2 B8A and B12, 60 m). Pre-fire composite against "
              f"the post-fire composite, else co-fire. Dates: {stage_dates}. Background: pre-fire true colour.",
              225, 155, 170, 30, 8, colour="#4A5568")
    add_inset(lay, site_highlight(site), land, 225, 188, 75, 30)
    export(lay, OUTDIR / f"{site}_severity.png")

# Layout 3: ECOSTRESS at the site with the most fire inside the gap: its best gap pass against its
# best night pass outside the gap (both from 09_ecostress_fires.py), clipped to the study box
import csv
fire_csv = DATA / "ecostress" / "fire_nights_2026.csv"
nights = list(csv.DictReader(open(fire_csv))) if fire_csv.exists() else []
site_box = {f["properties"]["site"]: f["geometry"]["coordinates"][0]
            for f in json.loads((DATA / "site_boxes.geojson").read_text())["features"]}
px = lambda r: int(r["fire_hot_pixels"])
gaps = [r for r in nights if r["window"] == "gap" and px(r) > 0]
if gaps:
    g = max(gaps, key=px)
    site = g["site"]
    n = max((r for r in nights if r["site"] == site and r["window"] == "night"), key=px, default=None)
    grp = root.addGroup("ECOSTRESS night LST")
    lay = new_layout("ECOSTRESS · night and gap")
    add_label(lay, f"Fires burn inside the 03:00–06:00 gap: ECOSTRESS night land surface temperature, 70 m, "
              f"{NAMES[site]}", 8, 4, 384, 10, 16, True)
    xs = [q[0] for q in site_box[site]]
    ys = [q[1] for q in site_box[site]]
    maps, eco_layers = [], []
    for i, r in enumerate([q for q in (n, g) if q]):
        local = datetime.strptime(r["local_time"], "%Y-%m-%d %H:%M")
        layer = add_to(grp, lst(DATA / "ecostress" / "files" / f"{r['granule']}_LST.tif",
                                f"LST {local:%d %b %H:%M} WIB"))
        eco_layers.append(layer)
        x = 8 + i * 158
        where = "inside the gap" if r["window"] == "gap" else "night, outside the gap"
        add_label(lay, f"{local:%d %b %Y, %H:%M} WIB · {where}\n{px(r):,} fire-hot pixels",
                  x, 15, 150, 10, 11, True, align=Qt.AlignmentFlag.AlignHCenter)
        ext = geo_extent(min(xs), min(ys), max(xs), max(ys), layer.crs())
        maps.append(add_map(lay, [towns, provs, boxes, layer], ext, x, 27, 150, 150, bg="#c9ced6"))
    add_legend(lay, maps[0], eco_layers[-1:], "Land surface temperature", 326, 27, 70)
    add_scalebar(lay, maps[0], 8, 182, km=10)
    add_north(lay, maps[0], 300, 180, 12)
    add_inset(lay, site_highlight(site), land, 326, 120, 70, 32)
    add_label(lay, "ECOSTRESS L2T LSTE v002, clipped to the study box (dashed). Two different nights. Fire-hot = above "
              "310 K; at night the sea and rivers stay below about 304 K. Grey: no data or cloud.", 8, 197, 384, 10, 8,
              colour="#4A5568")
    export(lay, OUTDIR / "ecostress_gap.png")

# Layout 4: fires ECOSTRESS saw at night at each site, outside and inside the gap (09_ecostress_fires.py)
if nights:
    picks = []
    for site in SITES:
        for win in ("night", "gap"):
            c = [r for r in nights if r["site"] == site and r["window"] == win and int(r["fire_hot_pixels"]) > 0]
            if c:
                picks.append(max(c, key=lambda r: int(r["fire_hot_pixels"])))
    picks = sorted(picks, key=lambda r: -int(r["fire_hot_pixels"]))[:3]
    if picks:
        grp = root.addGroup("ECOSTRESS fire nights")
        lay = new_layout("ECOSTRESS · fire nights")
        add_label(lay, "2026 fires seen by ECOSTRESS at night, inside and outside the 03:00–06:00 gap (70 m)",
                  8, 4, 384, 10, 16, True)
        w, gap_mm = 118, 7
        first = None
        for i, r in enumerate(picks):
            site = r["site"]
            local = datetime.strptime(r["local_time"], "%Y-%m-%d %H:%M")
            layer = add_to(grp, lst(DATA / "ecostress" / "files" / f"{r['granule']}_LST.tif",
                                    f"LST {SITE_LABELS[site]} {local:%d %b %H:%M} WIB"))
            xs = [p[0] for p in site_box[site]]
            ys = [p[1] for p in site_box[site]]
            ext = geo_extent(min(xs), min(ys), max(xs), max(ys), layer.crs())
            x = 8 + i * (w + gap_mm)
            where = "inside the gap" if r["window"] == "gap" else "night, outside the gap"
            add_label(lay, f"{NAMES[site]}\n{local.day} {local:%b}, {local:%H:%M} WIB · {where}\n"
                      f"{int(r['fire_hot_pixels']):,} fire-hot pixels", x, 15, w, 15, 9.5, True,
                      align=Qt.AlignmentFlag.AlignHCenter)
            m = add_map(lay, [towns, boxes, layer], ext, x, 32, w, w, bg="#c9ced6")
            first = first or m
        lg = add_legend(lay, first, [layer], "Land surface temperature", 8, 156, 250)
        lg.setSplitLayer(True)  # let one layer's classes run across columns
        lg.setColumnCount(4)
        lg.adjustBoxSize()
        add_scalebar(lay, first, 270, 158, km=10)
        add_north(lay, first, 370, 156, 12)
        add_label(lay, "ECOSTRESS L2T LSTE v002, tile over each site centre, clipped to the study box (dashed). "
                  "Fire-hot = above 310 K; at night the sea and rivers stay below about 304 K. Grey: no data or cloud. "
                  "Best night at each site from all August–October 2026 passes (09_ecostress_fires.py).",
                  8, 210, 384, 12, 8, colour="#4A5568")
        export(lay, OUTDIR / "ecostress_fire_nights.png")

project.write(str(HERE / "peat_fire_2026.qgz"))
print(f"\nProject saved: {HERE / 'peat_fire_2026.qgz'}")
app.exitQgis()
