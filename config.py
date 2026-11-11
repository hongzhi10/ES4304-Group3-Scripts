"""Shared settings for the ES4304 peat-fire data pulls.

Edit values here, not inside the scripts. Every script imports this file,
so keep it in the same folder as them.
"""
from pathlib import Path

# Google Earth Engine project used in the tutorials
GEE_PROJECT = "es4304-512405"

# Everything downloaded or written goes under ./data/
OUT = Path(__file__).resolve().parent / "data"

# Study periods (inclusive dates). Same windows as Section 3.1.
PERIODS = {
    2026: {
        "prefire": ("2026-06-17", "2026-07-31"),   # NISAR public data starts 17 Jun
        "fire": ("2026-08-01", "2026-10-31"),
        "postfire": ("2026-10-01", "2026-10-31"),  # Sentinel-2 composite for dNBR
    },
    2019: {  # reference year with the Gaveau et al. (2021) burned-area map
        "prefire": ("2019-06-17", "2019-07-31"),
        "fire": ("2019-08-01", "2019-10-31"),
        "postfire": ("2019-10-15", "2019-11-30"),
    },
}

# Case-study sites as approximate boxes: (west, south, east, north) in degrees.
# Replace them with your study-area polygons when you have them.
SITES = {
    "east_jambi": (103.9, -1.9, 104.5, -1.3),        # Muaro Jambi and Tanjung Jabung Timur, Jambi
    "ogan_komering_ilir": (105.2, -3.8, 105.8, -3.2),  # South Sumatra: most 2026 fire power in Sumatra
    "kubu_raya": (109.1, -0.7, 109.7, -0.1),         # West Kalimantan
    "kahayan_sebangau": (113.5, -2.9, 114.4, -2.0),  # Central Kalimantan
}

# Wider box for hotspots: the six western peat-fire provinces
# (Riau, Jambi, South Sumatra, West, Central and South Kalimantan)
FIRE_BELT = (98.0, -5.0, 117.0, 3.0)

# Time windows, in local clock time (WIB or WITA, see utc_offset below)
GAP_HOURS = (3, 6)       # the 03:00-06:00 gap
NIGHT_START = 19         # night = 19:00-06:00; "night" below means night outside the gap


def site_centre(site):
    w, s, e, n = SITES[site]
    return (s + n) / 2, (w + e) / 2


def utc_offset(lat, lon):
    """Hours ahead of UTC for a point in the western fire belt.

    WIB (UTC+7): Sumatra, West and Central Kalimantan, so all three case-study sites.
    WITA (UTC+8): South and East Kalimantan. The border is approximated by longitude
    and latitude, which is accurate enough for hotspot timing.
    """
    if lon >= 115.6 or (lon >= 114.4 and lat < -1.3):
        return 8
    return 7


def window(hour):
    """Label a local clock hour (0-24, decimals allowed): 'gap', 'night' or 'day'."""
    if GAP_HOURS[0] <= hour < GAP_HOURS[1]:
        return "gap"
    if hour >= NIGHT_START or hour < GAP_HOURS[0]:
        return "night"
    return "day"
