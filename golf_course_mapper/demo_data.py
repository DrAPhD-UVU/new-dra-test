"""
Improved synthetic golf course — proper corridor-based fairway shapes,
closed polygons for every area feature, realistic hole geometry.

Coordinate math uses a local East/North metre frame referenced to the
course centre so the corridor-offset calculations work in flat space.
"""

import math
import random


# ── Local coordinate helpers ────────────────────────────────────────────────

def _ll2local(lat, lon, ref_lat, ref_lon):
    R = 6_371_000
    east  = math.radians(lon - ref_lon) * R * math.cos(math.radians(ref_lat))
    north = math.radians(lat - ref_lat) * R
    return east, north


def _local2ll(east, north, ref_lat, ref_lon):
    R = 6_371_000
    lat = ref_lat + math.degrees(north / R)
    lon = ref_lon + math.degrees(east / (R * math.cos(math.radians(ref_lat))))
    return {"lat": lat, "lon": lon}


# ── Geometry primitives ──────────────────────────────────────────────────────

def _interp_centerline(pts, density=8):
    """Linear interpolation to make a smoother, denser polyline."""
    result = []
    for i in range(len(pts) - 1):
        x0, y0 = pts[i]
        x1, y1 = pts[i + 1]
        seg_len = math.hypot(x1 - x0, y1 - y0)
        steps = max(2, int(seg_len / density))
        for j in range(steps):
            t = j / steps
            result.append((x0 + t * (x1 - x0), y0 + t * (y1 - y0)))
    result.append(pts[-1])
    return result


def _corridor_ring(centerline, half_widths, ref_lat, ref_lon):
    """
    Build a closed polygon by offsetting a polyline on both sides.
    centerline  : list of (east_m, north_m)
    half_widths : list of half-width values, same length as centerline
    """
    n = len(centerline)
    if n < 2:
        return []

    left, right = [], []
    for i, (ex, ny) in enumerate(centerline):
        if i == 0:
            dx, dy = centerline[1][0] - ex, centerline[1][1] - ny
        elif i == n - 1:
            dx, dy = ex - centerline[-2][0], ny - centerline[-2][1]
        else:
            dx, dy = centerline[i + 1][0] - centerline[i - 1][0], \
                     centerline[i + 1][1] - centerline[i - 1][1]

        ln = math.hypot(dx, dy) or 1
        px, py = -dy / ln, dx / ln          # perpendicular (left side)

        w = half_widths[i]
        left.append(_local2ll(ex + px * w, ny + py * w, ref_lat, ref_lon))
        right.append(_local2ll(ex - px * w, ny - py * w, ref_lat, ref_lon))

    # closed ring: left forward → right reversed → back to start
    ring = left + right[::-1]
    ring.append(ring[0])
    return ring


def _ellipse_local(cx, cy, rx, ry, angle_deg, n_pts, ref_lat, ref_lon):
    a = math.radians(angle_deg)
    ring = []
    for i in range(n_pts + 1):
        t = 2 * math.pi * i / n_pts
        lx = rx * math.cos(t)
        ly = ry * math.sin(t)
        ex = lx * math.cos(a) - ly * math.sin(a)
        ny = lx * math.sin(a) + ly * math.cos(a)
        ring.append(_local2ll(cx + ex, cy + ny, ref_lat, ref_lon))
    return ring


def _rect_local(cx, cy, w, h, angle_deg, ref_lat, ref_lon):
    a = math.radians(angle_deg)
    corners = [(-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)]
    ring = []
    for lx, ly in corners:
        ex = lx * math.cos(a) - ly * math.sin(a)
        ny = lx * math.sin(a) + ly * math.cos(a)
        ring.append(_local2ll(cx + ex, cy + ny, ref_lat, ref_lon))
    ring.append(ring[0])
    return ring


def _seg_angle(x0, y0, x1, y1):
    return math.degrees(math.atan2(x1 - x0, y1 - y0))


# ── Hole routes ──────────────────────────────────────────────────────────────
# Each entry: (tee_e, tee_n, green_e, green_n, dogleg_e, dogleg_n)
# All units in local metres from course centre

HOLE_ROUTES = [
    ( -50, -800,  -50, -380,   None, None ),   #  1  Par 4, straight
    ( -50, -380,  370, -320,   None, None ),   #  2  Par 4, slight angle
    (  370, -320,  620,  150,  500,  -80 ),    #  3  Par 5, dogleg right
    (  620,  150,  720,  550,   None, None ),   #  4  Par 4, straight
    (  720,  550,  420,  820,   None, None ),   #  5  Par 3, straight
    (  420,  820,   20,  980,  200,  960 ),    #  6  Par 4, dogleg left
    (   20,  980, -340,  870, -160, 1040 ),    #  7  Par 5, dogleg left
    ( -340,  870, -680,  560,   None, None ),   #  8  Par 4, straight
    ( -680,  560, -800,  160,   None, None ),   #  9  Par 4, straight
    ( -800,  160, -740, -220,   None, None ),   # 10  Par 4, slight angle
    ( -740, -220, -540, -480, -700, -340 ),    # 11  Par 4, dogleg right
    ( -540, -480, -180, -660,   None, None ),   # 12  Par 3, straight
    ( -180, -660,  200, -700,   None, None ),   # 13  Par 4, straight
    (  200, -700,  520, -560,  400, -680 ),    # 14  Par 4, dogleg right
    (  520, -560,  710, -260,   None, None ),   # 15  Par 5, straight
    (  710, -260,  760,  140,   None, None ),   # 16  Par 4, straight
    (  760,  140,  500,  380,  720,  280 ),    # 17  Par 3, dogleg left
    (  500,  380,  100,  140,   None, None ),   # 18  Par 4, straight, back to clubhouse
]


# ── Main generator ────────────────────────────────────────────────────────────

def generate_demo_course(
    center_lat: float = 32.8998,
    center_lon: float = -117.2498,
    course_name: str = "Demo Golf Course",
    seed: int = 42,
) -> tuple:
    """
    Return (features_dict, bbox_dict, course_name) for a synthetic 18-hole course.
    All area features are closed polygons.  River/path are open polylines.
    """
    rng = random.Random(seed)
    rl, rl2 = center_lat, center_lon

    def l2ll(e, n): return _local2ll(e, n, rl, rl2)

    features = {
        "course_boundary": [],
        "hole_boundary": [],
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

    # ── Course outer boundary ────────────────────────────────────────────────
    features["course_boundary"].append({
        "coords": _ellipse_local(0, 0, 960, 1320, 0, 72, rl, rl2),
        "tags": {"leisure": "golf_course", "name": course_name},
        "osm_id": 1_000_000, "osm_type": "way", "name": course_name, "ref": "",
    })

    # ── Clubhouse ────────────────────────────────────────────────────────────
    features["building"].append({
        "coords": _rect_local(0, -230, 60, 40, 0, rl, rl2),
        "tags": {"building": "clubhouse"},
        "osm_id": 1_000_001, "osm_type": "way", "name": "Clubhouse", "ref": "",
    })

    # ── Water bodies ─────────────────────────────────────────────────────────
    for i, (ex, ny, rx, ry, ang) in enumerate([
        ( 310,  420, 75, 52, 15),
        (-260, -110, 48, 36, -10),
        ( 500,  640, 35, 28,  5),
    ]):
        features["water"].append({
            "coords": _ellipse_local(ex, ny, rx, ry, ang, 24, rl, rl2),
            "tags": {"natural": "water"}, "osm_id": 1_000_010 + i,
            "osm_type": "way", "name": f"Pond {i + 1}", "ref": "",
        })

    # ── Stream ───────────────────────────────────────────────────────────────
    stream = []
    ex = -440
    for step in range(14):
        ny = -700 + step * 105 + rng.uniform(-20, 20)
        ex += rng.uniform(-20, 35)
        stream.append(l2ll(ex, ny))
    features["river"].append({
        "coords": stream,
        "tags": {"waterway": "stream"}, "osm_id": 1_000_020,
        "osm_type": "way", "name": "Creek", "ref": "",
    })

    # ── 18 holes ─────────────────────────────────────────────────────────────
    cart_path = []

    for hole_num, (te, tn, ge, gn, de, dn) in enumerate(HOLE_ROUTES, 1):

        # --- Centerline ---
        if de is not None:
            raw_cl = [(te, tn),
                      (de + rng.uniform(-15, 15), dn + rng.uniform(-15, 15)),
                      (ge, gn)]
        else:
            mx = (te + ge) / 2 + rng.uniform(-25, 25)
            my = (tn + gn) / 2 + rng.uniform(-25, 25)
            raw_cl = [(te, tn), (mx, my), (ge, gn)]

        cl = _interp_centerline(raw_cl, density=8)
        n = len(cl)

        # --- Width profiles ---
        fw_base = rng.uniform(16, 24)      # half-width of fairway at widest
        fw_widths = []
        hole_widths = []
        for i in range(n):
            t = i / max(n - 1, 1)
            # Fairway: tapers at tee end, widens near green complex
            bell = math.sin(math.pi * min(t * 1.4, 1.0)) ** 0.6
            fw_w = fw_base * (0.55 + 0.45 * bell)
            # Wider near green to wrap the green complex
            if t > 0.75:
                fw_w *= 1.0 + (t - 0.75) * 1.8
            fw_widths.append(fw_w)
            hole_widths.append(fw_w * 2.4 + 18)   # rough + hole corridor

        # --- Hole boundary (rough corridor around entire hole) ---
        hb_ring = _corridor_ring(cl, hole_widths, rl, rl2)
        features["hole_boundary"].append({
            "coords": hb_ring,
            "tags": {"golf": "hole"}, "osm_id": 1_009_000 + hole_num,
            "osm_type": "way", "name": f"Hole {hole_num}", "ref": str(hole_num),
        })

        # --- Fairway (corridor ending ~20 m before green centre) ---
        fw_stop = max(2, n - int(n * 0.12))
        fw_ring = _corridor_ring(cl[:fw_stop], fw_widths[:fw_stop], rl, rl2)
        features["fairway"].append({
            "coords": fw_ring,
            "tags": {"golf": "fairway"}, "osm_id": 1_002_000 + hole_num,
            "osm_type": "way", "name": f"Hole {hole_num}", "ref": str(hole_num),
        })

        # --- Tee box ---
        tee_angle = _seg_angle(cl[0][0], cl[0][1], cl[1][0], cl[1][1])
        tee_ring = _rect_local(te, tn, 10, 14, tee_angle, rl, rl2)
        features["tee"].append({
            "coords": tee_ring,
            "tags": {"golf": "tee"}, "osm_id": 1_001_000 + hole_num,
            "osm_type": "way", "name": f"Hole {hole_num} tee", "ref": str(hole_num),
        })

        # --- Green ---
        green_angle = _seg_angle(cl[-3][0], cl[-3][1], ge, gn)
        grx = rng.uniform(11, 17)
        gry = rng.uniform(13, 21)
        green_ring = _ellipse_local(ge, gn, grx, gry, green_angle, 28, rl, rl2)
        features["green"].append({
            "coords": green_ring,
            "tags": {"golf": "green"}, "osm_id": 1_003_000 + hole_num,
            "osm_type": "way", "name": f"Hole {hole_num} green", "ref": str(hole_num),
        })

        # --- Green-side bunkers (1–3) ---
        n_bunkers = rng.randint(1, 3)
        for b in range(n_bunkers):
            ang = rng.uniform(0, 2 * math.pi)
            dist = grx + rng.uniform(6, 16)
            bx = ge + dist * math.cos(ang)
            by = gn + dist * math.sin(ang)
            b_ring = _ellipse_local(
                bx, by, rng.uniform(6, 13), rng.uniform(4, 9),
                rng.uniform(0, 90), 16, rl, rl2
            )
            features["bunker"].append({
                "coords": b_ring,
                "tags": {"golf": "bunker"}, "osm_id": 1_004_000 + hole_num * 10 + b,
                "osm_type": "way", "name": "", "ref": str(hole_num),
            })

        # --- Optional fairway bunker ---
        if rng.random() < 0.6:
            mid_i = n // 2
            mx2, my2 = cl[mid_i]
            if mid_i < n - 1:
                sdx = cl[mid_i + 1][0] - cl[mid_i][0]
                sdy = cl[mid_i + 1][1] - cl[mid_i][1]
            else:
                sdx = cl[-1][0] - cl[-2][0]
                sdy = cl[-1][1] - cl[-2][1]
            sln = math.hypot(sdx, sdy) or 1
            px, py = -sdy / sln, sdx / sln
            side = rng.choice([-1, 1])
            off = fw_widths[mid_i] + rng.uniform(3, 10)
            fbx = mx2 + px * off * side
            fby = my2 + py * off * side
            fb_ring = _ellipse_local(
                fbx, fby, rng.uniform(7, 14), rng.uniform(5, 9),
                rng.uniform(0, 90), 14, rl, rl2
            )
            features["bunker"].append({
                "coords": fb_ring,
                "tags": {"golf": "bunker"}, "osm_id": 1_004_500 + hole_num,
                "osm_type": "way", "name": "", "ref": str(hole_num),
            })

        # Collect cart path waypoints every few CL points
        for pt in cl[::4]:
            cart_path.append(l2ll(pt[0], pt[1]))

    # ── Cart path ─────────────────────────────────────────────────────────────
    features["path"].append({
        "coords": cart_path,
        "tags": {"golf": "path"}, "osm_id": 1_000_002,
        "osm_type": "way", "name": "Cart Path", "ref": "",
    })

    # ── Bounding box ──────────────────────────────────────────────────────────
    all_lats = [c["lat"] for layer in features.values() for feat in layer for c in feat["coords"]]
    all_lons = [c["lon"] for layer in features.values() for feat in layer for c in feat["coords"]]
    pad = 0.001
    bbox = {
        "min_lat": min(all_lats) - pad, "max_lat": max(all_lats) + pad,
        "min_lon": min(all_lons) - pad, "max_lon": max(all_lons) + pad,
    }
    return features, bbox, course_name
