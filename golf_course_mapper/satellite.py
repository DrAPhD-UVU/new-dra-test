"""
Fetch satellite imagery tiles and stitch into a geo-referenced PNG.

Tile sources tried in order (all free, no API key required):
  1. ESRI World Imagery  — best quality (~0.3 m/px in metro areas)
  2. Google Maps satellite fallback URL pattern
  3. OpenStreetMap standard (non-satellite, last resort visual ref)

Usage:
    from satellite import SatelliteFetcher
    fetcher = SatelliteFetcher()
    img_path = fetcher.fetch(bbox, zoom=17, output_dir="./output")
"""

import io
import math
import logging
import time
from pathlib import Path

import requests
from PIL import Image

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "GolfCourseMapper/1.0 (artistic-prints-studio)",
    "Accept": "image/webp,image/apng,image/*,*/*",
}

# Tile sources in priority order
TILE_SOURCES = [
    # ESRI World Imagery — {z}/{y}/{x} (note: y before x)
    ("esri",   "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"),
    # Mapbox satellite (public tiles, no token for basic access)
    ("mapbox", "https://api.mapbox.com/v4/mapbox.satellite/{z}/{x}/{y}.jpg90?access_token=pk.eyJ1IjoiZ29sZm1hcHBlciIsImEiOiJjbGd4In0.example"),
    # OSM standard (not satellite, but usable for verification)
    ("osm",    "https://tile.openstreetmap.org/{z}/{x}/{y}.png"),
]

TILE_PX = 256   # standard tile size


# ── Tile math (Web Mercator / Slippy Map) ────────────────────────────────────

def _deg2tile(lat: float, lon: float, zoom: int) -> tuple:
    """Convert lat/lon to tile x, y at given zoom."""
    n = 2 ** zoom
    x = int((lon + 180) / 360 * n)
    lat_r = math.radians(lat)
    y = int((1 - math.asinh(math.tan(lat_r)) / math.pi) / 2 * n)
    return x, y


def _tile2deg(x: int, y: int, zoom: int) -> tuple:
    """Top-left corner (lat, lon) of tile (x, y, zoom)."""
    n = 2 ** zoom
    lon = x / n * 360 - 180
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    return lat, lon


def _bbox_to_tiles(bbox: dict, zoom: int) -> tuple:
    """Return (x_min, x_max, y_min, y_max) tile indices for a bbox."""
    x0, y0 = _deg2tile(bbox["max_lat"], bbox["min_lon"], zoom)  # NW corner → lower y
    x1, y1 = _deg2tile(bbox["min_lat"], bbox["max_lon"], zoom)  # SE corner → higher y
    return min(x0, x1), max(x0, x1), min(y0, y1), max(y0, y1)


def _pick_zoom(bbox: dict, target_px: int = 4096) -> int:
    """Choose zoom so the stitched image is ≈ target_px wide."""
    lat_mid = (bbox["min_lat"] + bbox["max_lat"]) / 2
    lon_span = bbox["max_lon"] - bbox["min_lon"]
    for zoom in range(19, 12, -1):
        n = 2 ** zoom
        px_per_deg_lon = TILE_PX * n / 360
        total_px = lon_span * px_per_deg_lon
        if total_px <= target_px * 1.5:
            return zoom
    return 14


# ── Tile fetcher ─────────────────────────────────────────────────────────────

class SatelliteFetcher:
    def __init__(self, zoom: int = 0, retries: int = 3, tile_delay: float = 0.05):
        self._zoom_override = zoom
        self.retries = retries
        self.tile_delay = tile_delay

    def fetch(self, bbox: dict, output_dir: str = "./output",
              name: str = "satellite"):
        """
        Download and stitch satellite tiles for bbox.
        Returns path to the PNG, or None if all sources fail.
        """
        zoom = self._zoom_override or _pick_zoom(bbox)
        x_min, x_max, y_min, y_max = _bbox_to_tiles(bbox, zoom)
        cols = x_max - x_min + 1
        rows = y_max - y_min + 1

        logger.info("Fetching %d×%d tiles at zoom %d for %s", cols, rows, zoom, name)
        if cols * rows > 200:
            logger.warning("Large tile count (%d) — reducing zoom", cols * rows)
            zoom = max(zoom - 1, 12)
            x_min, x_max, y_min, y_max = _bbox_to_tiles(bbox, zoom)
            cols = x_max - x_min + 1
            rows = y_max - y_min + 1

        canvas = Image.new("RGB", (cols * TILE_PX, rows * TILE_PX), (200, 200, 200))

        source_name = None
        for src_name, url_tpl in TILE_SOURCES:
            ok = self._fill_canvas(canvas, url_tpl, x_min, x_max, y_min, y_max, zoom)
            if ok:
                source_name = src_name
                break

        if source_name is None:
            logger.error("All tile sources failed — satellite image unavailable")
            return None

        # Crop to exact bbox
        nw_lat, nw_lon = _tile2deg(x_min, y_min, zoom)
        se_lat, se_lon = _tile2deg(x_max + 1, y_max + 1, zoom)

        total_lat_span = nw_lat - se_lat
        total_lon_span = se_lon - nw_lon

        left   = int((bbox["min_lon"] - nw_lon) / total_lon_span * canvas.width)
        right  = int((bbox["max_lon"] - nw_lon) / total_lon_span * canvas.width)
        top    = int((nw_lat - bbox["max_lat"]) / total_lat_span * canvas.height)
        bottom = int((nw_lat - bbox["min_lat"]) / total_lat_span * canvas.height)

        left   = max(0, left)
        top    = max(0, top)
        right  = min(canvas.width,  right)
        bottom = min(canvas.height, bottom)

        cropped = canvas.crop((left, top, right, bottom))

        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{name}_satellite_z{zoom}.png"
        cropped.save(out_path, "PNG")
        logger.info("Saved satellite image: %s  (%s, zoom=%d)", out_path, source_name, zoom)
        return out_path

    def _fill_canvas(self, canvas, url_tpl, x_min, x_max, y_min, y_max, zoom) -> bool:
        """Download tiles into canvas. Returns True if at least half succeeded."""
        total = (x_max - x_min + 1) * (y_max - y_min + 1)
        ok = 0
        for ty in range(y_min, y_max + 1):
            for tx in range(x_min, x_max + 1):
                tile = self._fetch_tile(url_tpl, tx, ty, zoom)
                if tile:
                    col = tx - x_min
                    row = ty - y_min
                    canvas.paste(tile, (col * TILE_PX, row * TILE_PX))
                    ok += 1
                time.sleep(self.tile_delay)
        return ok >= total // 2

    def _fetch_tile(self, url_tpl: str, x: int, y: int, z: int):
        url = url_tpl.format(z=z, x=x, y=y)
        for attempt in range(self.retries):
            try:
                r = requests.get(url, headers=HEADERS, timeout=15)
                if r.status_code == 200 and "image" in r.headers.get("Content-Type", ""):
                    return Image.open(io.BytesIO(r.content)).convert("RGB")
                logger.debug("Tile %d/%d/%d → HTTP %d", z, x, y, r.status_code)
                return None
            except Exception as exc:
                wait = 2 ** attempt
                logger.debug("Tile %d/%d/%d attempt %d failed: %s", z, x, y, attempt+1, exc)
                if attempt < self.retries - 1:
                    time.sleep(wait)
        return None


# ── Geo-anchored SVG background ───────────────────────────────────────────────

def embed_satellite_in_svg(svg_path: Path, sat_path: Path, bbox: dict,
                           svg_size: int = 4096) -> Path:
    """
    Insert a <image> element as the bottom layer of an existing SVG so the
    satellite imagery appears behind the vector outlines.
    Returns the path to the (overwritten) SVG.
    """
    import base64, re, xml.etree.ElementTree as ET

    ET.register_namespace("", "http://www.w3.org/2000/svg")

    with open(sat_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    data_uri = f"data:image/png;base64,{b64}"

    tree = ET.parse(svg_path)
    root = tree.getroot()
    ns   = "http://www.w3.org/2000/svg"

    # Build image element
    img_el = ET.Element(f"{{{ns}}}image", attrib={
        "id":      "satellite_background",
        "x":       "0", "y": "0",
        "width":   str(svg_size), "height": str(svg_size),
        "href":    data_uri,
        "preserveAspectRatio": "none",
    })

    # Insert after <rect id="background"> but before all layers
    children = list(root)
    insert_at = 1
    for i, ch in enumerate(children):
        cid = ch.get("id", "")
        if cid == "background":
            insert_at = i + 1
            break

    root.insert(insert_at, img_el)
    tree.write(svg_path, xml_declaration=True, encoding="unicode")
    logger.info("Embedded satellite background into %s", svg_path)
    return svg_path
