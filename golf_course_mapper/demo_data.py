"""
Synthetic golf course data for offline testing and demos.

Generates a realistic 18-hole course layout at a specified location
without requiring network access to Overpass/Nominatim.
"""

import math
import random


def _polar_to_latlon(center_lat, center_lon, distance_m, bearing_deg):
    """Move from center by distance_m in direction bearing_deg → (lat, lon)."""
    R = 6_371_000  # Earth radius metres
    d = distance_m / R
    b = math.radians(bearing_deg)
    lat1 = math.radians(center_lat)
    lon1 = math.radians(center_lon)
    lat2 = math.asin(math.sin(lat1) * math.cos(d) + math.cos(lat1) * math.sin(d) * math.cos(b))
    lon2 = lon1 + math.atan2(
        math.sin(b) * math.sin(d) * math.cos(lat1),
        math.cos(d) - math.sin(lat1) * math.sin(lat2),
    )
    return math.degrees(lat2), math.degrees(lon2)


def _ellipse_ring(center_lat, center_lon, width_m, height_m, bearing_deg=0, n_pts=24):
    """Generate a closed polygon ring as an ellipse."""
    ring = []
    for i in range(n_pts + 1):
        angle = 2 * math.pi * i / n_pts
        # local metres
        dx = (width_m / 2) * math.cos(angle)
        dy = (height_m / 2) * math.sin(angle)
        # rotate by bearing
        b = math.radians(bearing_deg)
        rx = dx * math.cos(b) - dy * math.sin(b)
        ry = dx * math.sin(b) + dy * math.cos(b)
        dist = math.hypot(rx, ry)
        bear = math.degrees(math.atan2(rx, ry))
        lat, lon = _polar_to_latlon(center_lat, center_lon, dist, bear)
        ring.append({"lat": lat, "lon": lon})
    return ring


def _rect_ring(center_lat, center_lon, width_m, height_m, bearing_deg=0):
    """Generate a rotated rectangle ring."""
    corners = [
        (-width_m / 2, -height_m / 2),
        (width_m / 2, -height_m / 2),
        (width_m / 2, height_m / 2),
        (-width_m / 2, height_m / 2),
        (-width_m / 2, -height_m / 2),
    ]
    b = math.radians(bearing_deg)
    ring = []
    for cx, cy in corners:
        rx = cx * math.cos(b) - cy * math.sin(b)
        ry = cx * math.sin(b) + cy * math.cos(b)
        dist = math.hypot(rx, ry)
        bear = math.degrees(math.atan2(rx, ry))
        lat, lon = _polar_to_latlon(center_lat, center_lon, dist, bear)
        ring.append({"lat": lat, "lon": lon})
    return ring


def generate_demo_course(
    center_lat=32.8998,
    center_lon=-117.2498,
    course_name="Demo Golf Course",
    seed=42,
) -> tuple[dict, dict, str]:
    """
    Return (features_dict, bbox_dict, course_name) for a synthetic 18-hole course
    centred at (center_lat, center_lon).
    """
    rng = random.Random(seed)

    features = {
        "course_boundary": [],
        "fairway": [],
        "tee": [],
        "green": [],
        "bunker": [],
        "water": [],
        "river": [],
        "ocean": [],
        "rough": [],
        "path": [],
        "building": [],
    }

    # Overall boundary — large polygon ~1 km × 1.4 km
    boundary_ring = _ellipse_ring(center_lat, center_lon, 1000, 1400, n_pts=64)
    features["course_boundary"].append({
        "coords": boundary_ring,
        "tags": {"leisure": "golf_course", "name": course_name},
        "osm_id": 1000000,
        "osm_type": "way",
        "name": course_name,
        "ref": "",
    })

    # Clubhouse
    clubhouse_ring = _rect_ring(center_lat, center_lon, 40, 30)
    features["building"].append({
        "coords": clubhouse_ring,
        "tags": {"building": "clubhouse"},
        "osm_id": 1000001,
        "osm_type": "way",
        "name": "Clubhouse",
        "ref": "",
    })

    # Cart path — snaking line through the course
    path_pts = []
    for step in range(20):
        angle = step * (2 * math.pi / 18)
        dist = 500 + rng.uniform(-80, 80)
        bear = math.degrees(angle)
        lat, lon = _polar_to_latlon(center_lat, center_lon, dist, bear)
        path_pts.append({"lat": lat, "lon": lon})
    features["path"].append({
        "coords": path_pts,
        "tags": {"golf": "path"},
        "osm_id": 1000002,
        "osm_type": "way",
        "name": "",
        "ref": "",
    })

    # Water features — 2 ponds
    for i, (bear, dist) in enumerate([(30, 350), (200, 280)]):
        lat, lon = _polar_to_latlon(center_lat, center_lon, dist, bear)
        pond_ring = _ellipse_ring(lat, lon, rng.uniform(40, 80), rng.uniform(30, 60), n_pts=20)
        features["water"].append({
            "coords": pond_ring,
            "tags": {"natural": "water"},
            "osm_id": 1000010 + i,
            "osm_type": "way",
            "name": f"Pond {i + 1}",
            "ref": "",
        })

    # Stream
    stream_pts = []
    for step in range(8):
        bear = 270 + step * 10 + rng.uniform(-5, 5)
        dist = 200 + step * 40
        lat, lon = _polar_to_latlon(center_lat, center_lon, dist, bear)
        stream_pts.append({"lat": lat, "lon": lon})
    features["river"].append({
        "coords": stream_pts,
        "tags": {"waterway": "stream"},
        "osm_id": 1000020,
        "osm_type": "way",
        "name": "Creek",
        "ref": "",
    })

    # 18 holes: each has tee, fairway, green, optional bunker
    for hole in range(1, 19):
        angle = (hole - 1) * (2 * math.pi / 18)
        tee_bear = math.degrees(angle)
        tee_dist = 580 + rng.uniform(-100, 100)
        tee_lat, tee_lon = _polar_to_latlon(center_lat, center_lon, tee_dist, tee_bear)
        green_bear = tee_bear + rng.uniform(-15, 15)
        green_dist = tee_dist - rng.uniform(250, 380)
        green_lat, green_lon = _polar_to_latlon(center_lat, center_lon, green_dist, green_bear)
        fairway_lat = (tee_lat + green_lat) / 2
        fairway_lon = (tee_lon + green_lon) / 2

        tee_ring = _rect_ring(tee_lat, tee_lon, 8, 12, tee_bear)
        features["tee"].append({
            "coords": tee_ring,
            "tags": {"golf": "tee"},
            "osm_id": 1001000 + hole,
            "osm_type": "way",
            "name": f"Hole {hole} tee",
            "ref": str(hole),
        })

        fairway_width = rng.uniform(28, 48)
        fairway_length = rng.uniform(200, 330)
        fw_ring = _rect_ring(fairway_lat, fairway_lon, fairway_width, fairway_length, tee_bear)
        features["fairway"].append({
            "coords": fw_ring,
            "tags": {"golf": "fairway"},
            "osm_id": 1002000 + hole,
            "osm_type": "way",
            "name": f"Hole {hole}",
            "ref": str(hole),
        })

        green_ring = _ellipse_ring(green_lat, green_lon, rng.uniform(14, 22), rng.uniform(16, 28), n_pts=20)
        features["green"].append({
            "coords": green_ring,
            "tags": {"golf": "green"},
            "osm_id": 1003000 + hole,
            "osm_type": "way",
            "name": f"Hole {hole} green",
            "ref": str(hole),
        })

        # 0–2 bunkers per hole
        n_bunkers = rng.randint(0, 2)
        for b in range(n_bunkers):
            boff_bear = tee_bear + rng.choice([-1, 1]) * rng.uniform(15, 45)
            boff_dist = tee_dist - rng.uniform(120, 220)
            blat, blon = _polar_to_latlon(center_lat, center_lon, boff_dist, boff_bear)
            b_ring = _ellipse_ring(blat, blon, rng.uniform(10, 20), rng.uniform(8, 14), n_pts=16)
            features["bunker"].append({
                "coords": b_ring,
                "tags": {"golf": "bunker"},
                "osm_id": 1004000 + hole * 10 + b,
                "osm_type": "way",
                "name": "",
                "ref": str(hole),
            })

    # Compute bounding box
    all_lats = [c["lat"] for layer in features.values() for feat in layer for c in feat["coords"]]
    all_lons = [c["lon"] for layer in features.values() for feat in layer for c in feat["coords"]]
    pad = 0.001
    bbox = {
        "min_lat": min(all_lats) - pad,
        "max_lat": max(all_lats) + pad,
        "min_lon": min(all_lons) - pad,
        "max_lon": max(all_lons) + pad,
    }
    return features, bbox, course_name
