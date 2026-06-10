"""
Extract all golf-course features from OpenStreetMap within a bounding box.

Feature taxonomy returned as a dict of lists:
  course_boundary  – outer boundary of the entire course
  fairway          – each fairway polygon
  tee              – tee-box polygons
  green            – putting-green polygons
  bunker           – sand-bunker polygons
  water            – ponds, lakes, water hazards
  river            – rivers, streams, canals
  ocean            – coastline segments
  rough            – labelled rough areas
  path             – cart paths, footpaths within course
  building         – clubhouse, maintenance buildings
"""

import logging
import time
import requests

logger = logging.getLogger(__name__)

OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.openstreetmap.ru/api/interpreter",
]
HEADERS = {"User-Agent": "GolfCourseMapper/1.0 (artistic-prints-studio)"}


def _post_overpass(query: str, retries: int = 4) -> dict:
    last_err = None
    for endpoint in OVERPASS_ENDPOINTS:
        for attempt in range(retries):
            try:
                r = requests.post(endpoint, data={"data": query}, headers=HEADERS, timeout=120)
                r.raise_for_status()
                return r.json()
            except Exception as exc:
                last_err = exc
                wait = 2 ** attempt
                logger.warning("Overpass attempt %d/%d failed: %s — retry in %ss", attempt + 1, retries, exc, wait)
                time.sleep(wait)
    raise RuntimeError(f"Overpass unavailable after all retries: {last_err}")


def _build_query(bbox: dict) -> str:
    s, n, w, e = bbox["min_lat"], bbox["max_lat"], bbox["min_lon"], bbox["max_lon"]
    bb = f"{s},{w},{n},{e}"
    return f"""
[out:json][timeout:120];
(
  /* Course boundary */
  way["leisure"="golf_course"]({bb});
  relation["leisure"="golf_course"]({bb});

  /* Golf-tagged features */
  way["golf"="fairway"]({bb});
  way["golf"="tee"]({bb});
  way["golf"="green"]({bb});
  way["golf"="bunker"]({bb});
  way["golf"="rough"]({bb});
  way["golf"="path"]({bb});
  way["golf"="water_hazard"]({bb});

  /* Generic land use / natural */
  way["natural"="water"]({bb});
  way["landuse"="reservoir"]({bb});
  way["water"="pond"]({bb});
  way["water"="lake"]({bb});

  /* Waterways */
  way["waterway"="river"]({bb});
  way["waterway"="stream"]({bb});
  way["waterway"="canal"]({bb});
  way["waterway"="ditch"]({bb});

  /* Coastline / beach */
  way["natural"="coastline"]({bb});
  way["natural"="beach"]({bb});

  /* Paths inside the course */
  way["highway"="path"]({bb});
  way["highway"="track"]({bb});
  way["highway"="footway"]({bb});

  /* Buildings */
  way["building"]({bb});
);
out body;
>;
out skel qt;
"""


# ──────────────────────────────────────────────────────────────────────────────
# Tag → layer mapping
# ──────────────────────────────────────────────────────────────────────────────

def _classify(tags: dict) -> str | None:
    leisure = tags.get("leisure", "")
    golf = tags.get("golf", "")
    natural = tags.get("natural", "")
    waterway = tags.get("waterway", "")
    landuse = tags.get("landuse", "")
    water = tags.get("water", "")
    highway = tags.get("highway", "")
    building = tags.get("building", "")

    if leisure == "golf_course":
        return "course_boundary"
    if golf == "fairway":
        return "fairway"
    if golf == "tee":
        return "tee"
    if golf == "green":
        return "green"
    if golf in ("bunker", "sand"):
        return "bunker"
    if golf in ("rough",):
        return "rough"
    if golf in ("path",):
        return "path"
    if golf == "water_hazard":
        return "water"
    if natural == "water" or water in ("pond", "lake", "reservoir") or landuse == "reservoir":
        return "water"
    if waterway in ("river", "stream", "canal", "ditch"):
        return "river"
    if natural == "coastline":
        return "ocean"
    if natural == "beach":
        return "ocean"
    if highway in ("path", "track", "footway"):
        return "path"
    if building:
        return "building"
    return None


# ──────────────────────────────────────────────────────────────────────────────

class OSMExtractor:
    """Download and parse OSM elements into geometry lists per feature type."""

    FEATURE_TYPES = [
        "course_boundary", "hole_boundary", "fairway", "tee", "green", "bunker",
        "water", "river", "ocean", "rough", "path", "building",
    ]

    def extract(self, bbox: dict) -> dict:
        """Return dict[feature_type] = list of coordinate rings [[{lat,lon},...],...]"""
        query = _build_query(bbox)
        logger.info("Querying Overpass for bbox %s", bbox)
        data = _post_overpass(query)
        return self._parse(data)

    def extract_from_elements(self, elements: list) -> dict:
        """Parse already-fetched OSM element list (reuse locator data)."""
        return self._parse({"elements": elements})

    # ----------------------------------------------------------------- private
    def _parse(self, data: dict) -> dict:
        elements = data.get("elements", [])
        node_map = {e["id"]: e for e in elements if e["type"] == "node"}

        features: dict = {t: [] for t in self.FEATURE_TYPES}

        for elem in elements:
            if elem["type"] not in ("way", "relation"):
                continue
            tags = elem.get("tags", {})
            layer = _classify(tags)
            if layer is None:
                continue

            rings = self._rings_of(elem, elements, node_map)
            for ring in rings:
                if len(ring) >= 2:
                    features[layer].append({
                        "coords": ring,
                        "tags": tags,
                        "osm_id": elem["id"],
                        "osm_type": elem["type"],
                        "name": tags.get("name", ""),
                        "ref": tags.get("ref", ""),
                    })

        totals = {k: len(v) for k, v in features.items() if v}
        logger.info("Extracted features: %s", totals)
        return features

    def _rings_of(self, element: dict, all_elements: list, node_map: dict) -> list:
        """Return a list of coordinate rings for a way or relation."""
        if element["type"] == "way":
            ring = self._way_to_coords(element, node_map)
            return [ring] if ring else []

        # relation: assemble member ways into outer/inner rings
        way_map = {e["id"]: e for e in all_elements if e["type"] == "way"}
        rings = []
        for member in element.get("members", []):
            if member.get("type") == "way":
                way = way_map.get(member["ref"])
                if way:
                    ring = self._way_to_coords(way, node_map)
                    if ring:
                        rings.append(ring)
        return rings

    @staticmethod
    def _way_to_coords(way: dict, node_map: dict) -> list:
        coords = []
        for nid in way.get("nodes", []):
            n = node_map.get(nid)
            if n:
                coords.append({"lat": n["lat"], "lon": n["lon"]})
        return coords
