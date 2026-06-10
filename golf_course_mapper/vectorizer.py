"""
Convert extracted OSM features into a layered SVG vector file.

Uses Python's xml.etree.ElementTree directly for maximum SVG compatibility
and to support Inkscape/Illustrator layer metadata without validator issues.

Each feature type becomes a named <g> layer — ideal for colorization
in Adobe Illustrator using Artistic Prints Studio proprietary styles.

Layer order (bottom → top):
  course_boundary → ocean → water → river → rough → fairway →
  bunker → tee → green → path → building
"""

import math
import logging
import re
import xml.etree.ElementTree as ET
from xml.dom import minidom
from pathlib import Path

from shapely.geometry import LineString

logger = logging.getLogger(__name__)

# SVG namespaces
SVG_NS = "http://www.w3.org/2000/svg"
INKSCAPE_NS = "http://www.inkscape.org/namespaces/inkscape"

ET.register_namespace("", SVG_NS)
ET.register_namespace("inkscape", INKSCAPE_NS)


LAYER_STYLE = {
    "course_boundary": {"fill": "none",     "stroke": "#1a4a1a", "stroke-width": "3"},
    "ocean":           {"fill": "#a8d5e5",   "stroke": "#5b9aba", "stroke-width": "1"},
    "water":           {"fill": "#b0d4e8",   "stroke": "#4a88a8", "stroke-width": "1"},
    "river":           {"fill": "none",      "stroke": "#4a88a8", "stroke-width": "2"},
    "rough":           {"fill": "#8fbc6a",   "stroke": "#6a9a45", "stroke-width": "0.5"},
    "fairway":         {"fill": "#5da832",   "stroke": "#3d7a1a", "stroke-width": "1"},
    "bunker":          {"fill": "#e8d89a",   "stroke": "#c4b060", "stroke-width": "1"},
    "tee":             {"fill": "#2e7d1a",   "stroke": "#1a5a0a", "stroke-width": "1"},
    "green":           {"fill": "#38a01a",   "stroke": "#1a7a00", "stroke-width": "1.5"},
    "path":            {"fill": "none",      "stroke": "#a0836a", "stroke-width": "1.5"},
    "building":        {"fill": "#d4c4b0",   "stroke": "#8a7a6a", "stroke-width": "1"},
}

LAYER_ORDER = [
    "course_boundary", "ocean", "water", "river", "rough",
    "fairway", "bunker", "tee", "green", "path", "building",
]

OPEN_LAYERS = {"river", "path", "ocean", "course_boundary"}


def _safe_name(name: str) -> str:
    s = name.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = s.strip("_")
    return s or "golf_course"


class ProjectionHelper:
    """Equirectangular projection: lat/lon → SVG x/y, preserving aspect ratio."""

    def __init__(self, bbox: dict, svg_width: int = 4096, svg_height: int = 4096, margin: int = 64):
        self.min_lat = bbox["min_lat"]
        self.max_lat = bbox["max_lat"]
        self.min_lon = bbox["min_lon"]
        self.max_lon = bbox["max_lon"]

        lat_span = self.max_lat - self.min_lat
        lon_span = self.max_lon - self.min_lon
        mid_lat = (self.min_lat + self.max_lat) / 2
        self.lon_correction = math.cos(math.radians(mid_lat))

        geo_aspect = (lon_span * self.lon_correction) / max(lat_span, 1e-10)
        draw_w = svg_width - 2 * margin
        draw_h = svg_height - 2 * margin

        if geo_aspect > draw_w / draw_h:
            self.scale_x = draw_w / (lon_span * self.lon_correction)
            self.scale_y = self.scale_x
        else:
            self.scale_y = draw_h / lat_span
            self.scale_x = self.scale_y

        self.px_w = lon_span * self.lon_correction * self.scale_x
        self.px_h = lat_span * self.scale_y
        self.offset_x = margin + (draw_w - self.px_w) / 2
        self.offset_y = margin + (draw_h - self.px_h) / 2

    def project(self, lat: float, lon: float) -> tuple:
        x = self.offset_x + (lon - self.min_lon) * self.lon_correction * self.scale_x
        y = self.offset_y + (self.max_lat - lat) * self.scale_y
        return round(x, 2), round(y, 2)

    def project_ring(self, coords: list) -> list:
        return [self.project(c["lat"], c["lon"]) for c in coords]


def _simplify_path(pts: list, tolerance: float) -> list:
    if len(pts) < 3 or tolerance <= 0:
        return pts
    try:
        line = LineString(pts)
        simplified = line.simplify(tolerance, preserve_topology=True)
        return list(simplified.coords)
    except Exception:
        return pts


def _pts_to_d(pts: list, closed: bool) -> str:
    if not pts:
        return ""
    d = "M " + " L ".join(f"{x},{y}" for x, y in pts)
    if closed:
        d += " Z"
    return d


def _el(tag: str, **attrs) -> ET.Element:
    return ET.Element(f"{{{SVG_NS}}}{tag}", attrib={k.replace("_", "-"): str(v) for k, v in attrs.items()})


def _sub(parent: ET.Element, tag: str, **attrs) -> ET.Element:
    child = ET.SubElement(parent, f"{{{SVG_NS}}}{tag}", attrib={k.replace("_", "-"): str(v) for k, v in attrs.items()})
    return child


class SVGVectorizer:
    """Render extracted golf features to a layered SVG file."""

    def __init__(self, svg_size: int = 4096, simplify_tolerance: float = 0.5):
        self.svg_size = svg_size
        self.simplify_tolerance = simplify_tolerance

    def render(self, features: dict, bbox: dict, course_name: str, output_dir: str = ".") -> Path:
        """
        Produce  <output_dir>/golf_<safe_name>_outline.svg
        Returns the path to the written file.
        """
        safe = _safe_name(course_name)
        filename = f"golf_{safe}_outline.svg"
        out_path = Path(output_dir) / filename
        out_path.parent.mkdir(parents=True, exist_ok=True)

        size = self.svg_size
        proj = ProjectionHelper(bbox, size, size)

        svg = ET.Element(f"{{{SVG_NS}}}svg", attrib={
            "width": f"{size}",
            "height": f"{size}",
            "viewBox": f"0 0 {size} {size}",
            "version": "1.1",
        })

        # Title
        title = ET.SubElement(svg, f"{{{SVG_NS}}}title")
        title.text = f"{course_name} — Golf Course Outline"

        # Background
        bg = ET.SubElement(svg, f"{{{SVG_NS}}}rect", attrib={
            "id": "background", "x": "0", "y": "0",
            "width": str(size), "height": str(size), "fill": "#f5f0e8",
        })

        # Layers
        for layer_name in LAYER_ORDER:
            items = features.get(layer_name, [])
            if not items:
                continue

            style = LAYER_STYLE[layer_name]
            g_attrs = {
                "id": f"layer_{layer_name}",
                "fill": style["fill"],
                "stroke": style["stroke"],
                "stroke-width": style["stroke-width"],
                "data-layer": layer_name,
                "data-label": layer_name.replace("_", " ").title(),
            }
            g = ET.SubElement(svg, f"{{{SVG_NS}}}g", attrib=g_attrs)

            for feat in items:
                pts = proj.project_ring(feat["coords"])
                pts = _simplify_path(pts, self.simplify_tolerance)
                if len(pts) < 2:
                    continue

                is_closed = layer_name not in OPEN_LAYERS
                path_d = _pts_to_d(pts, is_closed)
                if not path_d:
                    continue

                p_attrs = {
                    "d": path_d,
                    "id": f"{layer_name}_{feat['osm_id']}",
                }
                if feat.get("name"):
                    p_attrs["data-name"] = feat["name"]
                if feat.get("ref"):
                    p_attrs["data-ref"] = feat["ref"]
                ET.SubElement(g, f"{{{SVG_NS}}}path", attrib=p_attrs)

        # Pretty-print via minidom
        rough_xml = ET.tostring(svg, encoding="unicode")
        dom = minidom.parseString(rough_xml)
        pretty = dom.toprettyxml(indent="  ", encoding="utf-8")

        out_path.write_bytes(pretty)

        totals = {k: len(v) for k, v in features.items() if v}
        logger.info("Saved SVG: %s  features=%s", out_path, totals)
        return out_path
