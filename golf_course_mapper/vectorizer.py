"""
Render extracted golf features to a layered, outline-only SVG.

Design rules:
  • NO fill colours — all shapes are black outlines only (fill="none")
  • Every area feature (fairway, green, tee, bunker, water, boundary …) is
    a CLOSED path ending with Z
  • River and cart-path features are open polylines (no Z)
  • Each feature type lives in its own named <g> layer so Adobe Illustrator
    can target them individually for Artistic Prints Studio colorization
  • Within each layer, individual paths carry data-ref="<hole number>" so
    per-hole selection is possible in Illustrator

Layer order (bottom → top):
  course_boundary → hole_boundary → ocean → water → river →
  rough → fairway → bunker → tee → green → path → building
"""

import math
import logging
import re
import xml.etree.ElementTree as ET
from xml.dom import minidom
from pathlib import Path

from shapely.geometry import LineString

logger = logging.getLogger(__name__)

SVG_NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG_NS)

# ── Stroke widths (pixels at 4096 px canvas).  No fill on any layer. ─────────
LAYER_STROKE = {
    "course_boundary": "4",
    "hole_boundary":   "2",
    "ocean":           "2",
    "water":           "1.5",
    "river":           "2",
    "rough":           "1",
    "fairway":         "1.5",
    "bunker":          "1.5",
    "tee":             "2",
    "green":           "2",
    "path":            "1",
    "building":        "1.5",
}

# These layers draw open polylines; everything else is a closed polygon (Z).
OPEN_LAYERS = {"river", "path"}

LAYER_ORDER = [
    "course_boundary",
    "hole_boundary",
    "ocean",
    "water",
    "river",
    "rough",
    "fairway",
    "bunker",
    "tee",
    "green",
    "path",
    "building",
]


def _safe_name(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", name.lower().strip()).strip("_")
    return s or "golf_course"


# ── Equirectangular projection ────────────────────────────────────────────────

class ProjectionHelper:
    def __init__(self, bbox: dict, svg_size: int = 4096, margin: int = 80):
        self.min_lat = bbox["min_lat"]
        self.max_lat = bbox["max_lat"]
        self.min_lon = bbox["min_lon"]
        self.max_lon = bbox["max_lon"]

        lat_span = self.max_lat - self.min_lat
        lon_span = self.max_lon - self.min_lon
        mid_lat  = (self.min_lat + self.max_lat) / 2
        self.lon_corr = math.cos(math.radians(mid_lat))

        draw = svg_size - 2 * margin
        geo_aspect = (lon_span * self.lon_corr) / max(lat_span, 1e-10)

        if geo_aspect > 1:          # wider than tall
            self.sx = draw / (lon_span * self.lon_corr)
            self.sy = self.sx
        else:
            self.sy = draw / lat_span
            self.sx = self.sy

        self.px_w = lon_span * self.lon_corr * self.sx
        self.px_h = lat_span * self.sy
        self.ox = margin + (draw - self.px_w) / 2
        self.oy = margin + (draw - self.px_h) / 2

    def project(self, lat: float, lon: float) -> tuple:
        x = self.ox + (lon - self.min_lon) * self.lon_corr * self.sx
        y = self.oy + (self.max_lat - lat) * self.sy   # Y flipped
        return round(x, 2), round(y, 2)

    def project_ring(self, coords: list) -> list:
        return [self.project(c["lat"], c["lon"]) for c in coords]


# ── Path simplification ───────────────────────────────────────────────────────

def _simplify(pts: list, tol: float) -> list:
    if len(pts) < 3 or tol <= 0:
        return pts
    try:
        return list(LineString(pts).simplify(tol, preserve_topology=True).coords)
    except Exception:
        return pts


# ── SVG helpers ───────────────────────────────────────────────────────────────

def _pts_to_d(pts: list, closed: bool) -> str:
    if not pts:
        return ""
    d = "M " + " L ".join(f"{x},{y}" for x, y in pts)
    return d + " Z" if closed else d


def _sub(parent, tag: str, **attrs) -> ET.Element:
    return ET.SubElement(
        parent,
        f"{{{SVG_NS}}}{tag}",
        attrib={k.replace("_", "-"): str(v) for k, v in attrs.items()},
    )


# ── Main vectorizer ───────────────────────────────────────────────────────────

class SVGVectorizer:
    def __init__(self, svg_size: int = 4096, simplify_tolerance: float = 0.4):
        self.svg_size = svg_size
        self.simplify_tolerance = simplify_tolerance

    def render(self, features: dict, bbox: dict, course_name: str,
               output_dir: str = ".") -> Path:
        """
        Write  golf_<safe_name>_outline.svg  to output_dir.
        All area features are black closed outlines; rivers/paths are open.
        Returns the Path to the saved file.
        """
        safe     = _safe_name(course_name)
        out_path = Path(output_dir) / f"golf_{safe}_outline.svg"
        out_path.parent.mkdir(parents=True, exist_ok=True)

        size = self.svg_size
        proj = ProjectionHelper(bbox, size)

        svg = ET.Element(f"{{{SVG_NS}}}svg", attrib={
            "width": str(size), "height": str(size),
            "viewBox": f"0 0 {size} {size}",
            "version": "1.1",
        })

        # Title
        title_el = ET.SubElement(svg, f"{{{SVG_NS}}}title")
        title_el.text = f"{course_name} — Golf Course Outline"

        # White background
        ET.SubElement(svg, f"{{{SVG_NS}}}rect", attrib={
            "id": "background", "x": "0", "y": "0",
            "width": str(size), "height": str(size), "fill": "#ffffff",
        })

        for layer_name in LAYER_ORDER:
            items = features.get(layer_name, [])
            if not items:
                continue

            sw     = LAYER_STROKE.get(layer_name, "1.5")
            closed = layer_name not in OPEN_LAYERS

            g = ET.SubElement(svg, f"{{{SVG_NS}}}g", attrib={
                "id":           f"layer_{layer_name}",
                "fill":         "none",
                "stroke":       "#000000",
                "stroke-width": sw,
                "data-layer":   layer_name,
                "data-label":   layer_name.replace("_", " ").title(),
            })

            for feat in items:
                pts = proj.project_ring(feat["coords"])
                pts = _simplify(pts, self.simplify_tolerance)
                if len(pts) < 2:
                    continue

                d = _pts_to_d(pts, closed)
                if not d:
                    continue

                path_attrs = {
                    "d":  d,
                    "id": f"{layer_name}_{feat['osm_id']}",
                }
                if feat.get("ref"):
                    path_attrs["data-ref"]  = feat["ref"]
                if feat.get("name"):
                    path_attrs["data-name"] = feat["name"]

                ET.SubElement(g, f"{{{SVG_NS}}}path", attrib=path_attrs)

        # Pretty-print via minidom
        raw = ET.tostring(svg, encoding="unicode")
        pretty = minidom.parseString(raw).toprettyxml(indent="  ", encoding="utf-8")
        out_path.write_bytes(pretty)

        totals = {k: len(v) for k, v in features.items() if v}
        logger.info("Saved: %s  |  features: %s", out_path, totals)
        return out_path
