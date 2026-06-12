"""
Find golf courses by name, GPS coordinates, or address using
Nominatim + Overpass API (no API key required).
"""

import re
import time
import logging
import requests

logger = logging.getLogger(__name__)

OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.openstreetmap.ru/api/interpreter",
]
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
HEADERS = {"User-Agent": "GolfCourseMapper/1.0 (artistic-prints-studio)"}


def _post_overpass(query: str, retries: int = 3) -> dict:
    last_err = None
    for endpoint in OVERPASS_ENDPOINTS:
        for attempt in range(retries):
            try:
                r = requests.post(
                    endpoint, data={"data": query}, headers=HEADERS, timeout=90
                )
                r.raise_for_status()
                return r.json()
            except Exception as exc:
                last_err = exc
                wait = 2 ** attempt
                logger.warning("Overpass %s attempt %d failed: %s — retry in %ss", endpoint, attempt + 1, exc, wait)
                time.sleep(wait)
    raise RuntimeError(f"All Overpass endpoints exhausted: {last_err}")


class CourseLocator:
    """Locate a golf course and return its OSM element (way or relation) + bounding box."""

    # ------------------------------------------------------------------ public
    def find_by_name(self, name: str, country: str = "") -> dict:
        """Search by human-readable course name. Returns best match dict."""
        results = self._nominatim_search(name, country)
        if results:
            best = results[0]
            logger.info("Nominatim hit: %s  osm_type=%s  osm_id=%s",
                        best.get("display_name"), best.get("osm_type"), best.get("osm_id"))
            return self._resolve_osm_element(best["osm_type"], best["osm_id"], best["display_name"])

        logger.info("Nominatim found nothing — falling back to Overpass text search")
        return self._overpass_name_search(name)

    def find_by_coords(self, lat: float, lon: float, radius_m: int = 2000) -> dict:
        """Find a golf course centred on (lat, lon) within radius_m metres."""
        return self._find_course_near(lat, lon, name="", radius_m=radius_m)

    # ----------------------------------------------------------------- private

    def _find_course_near(self, lat: float, lon: float, name: str,
                          radius_m: int = 3000) -> dict:
        """Query Overpass for golf_course ways/relations within radius_m of a point."""
        query = f"""
[out:json][timeout:60];
(
  way["leisure"="golf_course"](around:{radius_m},{lat},{lon});
  relation["leisure"="golf_course"](around:{radius_m},{lat},{lon});
);
out body;
>;
out skel qt;
"""
        data = _post_overpass(query)
        elements = data.get("elements", [])
        courses = [e for e in elements if e.get("tags", {}).get("leisure") == "golf_course"]
        if not courses:
            if not name:
                raise LookupError(f"No golf course found within {radius_m} m of ({lat}, {lon})")
            # Last resort: use a generous bbox around the point with no feature elements
            pad = 0.025   # ~2.5 km
            logger.warning("No course polygon found near (%s, %s) — using %dm bbox", lat, lon, radius_m)
            return {
                "name": name,
                "osm_type": "node", "osm_id": 0,
                "bbox": {"min_lat": lat - pad, "max_lat": lat + pad,
                         "min_lon": lon - pad, "max_lon": lon + pad},
                "elements": [],
            }
        best = courses[0]
        course_name = best.get("tags", {}).get("name", None) or name or f"golf_course_{best['id']}"
        bbox = self._bbox_of_element(best, elements)
        return {"name": course_name, "osm_type": best["type"], "osm_id": best["id"],
                "bbox": bbox, "elements": elements}

    def _nominatim_search(self, name: str, country: str) -> list:
        q = name + (f", {country}" if country else "")
        params = {
            "q": q,
            "format": "jsonv2",
            "limit": 5,
            "addressdetails": 0,
            "extratags": 1,
        }
        try:
            r = requests.get(NOMINATIM_URL, params=params, headers=HEADERS, timeout=30)
            r.raise_for_status()
            hits = r.json()
            hits = [h for h in hits
                    if "golf" in h.get("type", "")
                    or "golf" in h.get("display_name", "").lower()
                    or "golf" in str(h.get("extratags", {}))]
            # Prefer way/relation (polygon data) over node (single point)
            hits.sort(key=lambda h: 0 if h.get("osm_type") in ("way", "relation") else 1)
            return hits
        except Exception as exc:
            logger.warning("Nominatim search failed: %s", exc)
            return []

    def _overpass_name_search(self, name: str) -> dict:
        safe = name.replace('"', '\\"')
        query = f"""
[out:json][timeout:90];
(
  way["leisure"="golf_course"]["name"~"{safe}",i];
  relation["leisure"="golf_course"]["name"~"{safe}",i];
);
out body;
>;
out skel qt;
"""
        data = _post_overpass(query)
        elements = data.get("elements", [])
        courses = [e for e in elements if e.get("tags", {}).get("leisure") == "golf_course"]
        if not courses:
            raise LookupError(f"Could not find golf course matching '{name}' in Overpass either.")
        best = courses[0]
        course_name = best.get("tags", {}).get("name", name)
        bbox = self._bbox_of_element(best, elements)
        return {"name": course_name, "osm_type": best["type"], "osm_id": best["id"],
                "bbox": bbox, "elements": elements}

    def _resolve_osm_element(self, osm_type: str, osm_id: str, display_name: str) -> dict:
        typ = osm_type.lower()  # way / node / relation
        query = f"""
[out:json][timeout:60];
{typ}({osm_id});
out body;
>;
out skel qt;
"""
        data = _post_overpass(query)
        elements = data.get("elements", [])
        if not elements:
            raise LookupError(f"OSM element {osm_type}/{osm_id} not found")
        top = elements[0]
        name = top.get("tags", {}).get("name", display_name)

        # Nominatim often returns a node (entrance/POI) rather than the actual
        # course polygon. When that happens, search around the node for the real
        # golf_course way or relation — exactly like find_by_coords does.
        if top["type"] == "node" and "lat" in top:
            logger.info("Nominatim returned a node — searching for course polygon nearby")
            return self._find_course_near(top["lat"], top["lon"], name=name, radius_m=3000)

        bbox = self._bbox_of_element(top, elements)
        return {"name": name, "osm_type": top["type"], "osm_id": top["id"],
                "bbox": bbox, "elements": elements}

    @staticmethod
    def _bbox_of_element(element: dict, all_elements: list) -> dict:
        """Compute bounding box from element node refs, member ways, or direct bounds."""
        # Overpass sometimes includes a pre-computed bounds object
        if "bounds" in element:
            b = element["bounds"]
            return {"min_lat": b["minlat"], "max_lat": b["maxlat"],
                    "min_lon": b["minlon"], "max_lon": b["maxlon"]}

        node_map = {e["id"]: e for e in all_elements if e["type"] == "node"}
        way_map  = {e["id"]: e for e in all_elements if e["type"] == "way"}

        lats, lons = [], []

        # Way: direct node list
        for nid in element.get("nodes", []):
            n = node_map.get(nid)
            if n:
                lats.append(n["lat"])
                lons.append(n["lon"])

        # Relation: walk member ways → their nodes
        if not lats:
            for member in element.get("members", []):
                if member.get("type") == "way":
                    way = way_map.get(member["ref"])
                    if way:
                        for nid in way.get("nodes", []):
                            n = node_map.get(nid)
                            if n:
                                lats.append(n["lat"])
                                lons.append(n["lon"])

        if not lats:
            # Node fallback — use a generous radius so the bbox query finds features
            if "lat" in element:
                pad = 0.025  # ~2.5 km
                return {"min_lat": element["lat"] - pad, "max_lat": element["lat"] + pad,
                        "min_lon": element["lon"] - pad, "max_lon": element["lon"] + pad}
            raise ValueError(f"Cannot derive bounding box for element {element.get('id')}")

        pad = 0.001  # ~100 m padding around polygon
        return {
            "min_lat": min(lats) - pad,
            "max_lat": max(lats) + pad,
            "min_lon": min(lons) - pad,
            "max_lon": max(lons) + pad,
        }
