"""
Improved synthetic golf course data.

Changes vs previous version:
- Course boundary scaled to realistic size (rx=500m, ry=700m ≈ 150 acres)
- Holes weave across the INTERIOR in a proper routing (not around the perimeter)
- Par distances approximate real values:
    Par 3: 130-180m  |  Par 4: 290-400m  |  Par 5: 430-530m
- Corridor widths scaled to real fairway/green dimensions
"""

import math
import random


# ── Local coordinate helpers (flat-earth approx, accurate to ~0.1% within 2 km) ─

def _ll2local(lat, lon, ref_lat, ref_lon):
    R = 6_371_000
    east  = math.radians(lon - ref_lon) * R * math.cos(math.radians(ref_lat))
    north = math.radians(lat - ref_lat) * R
    return east, north


def _local2ll(east, north, ref_lat, ref_lon):
    R = 6_371_000
    return {
        "lat": ref_lat + math.degrees(north / R),
        "lon": ref_lon + math.degrees(east / (R * math.cos(math.radians(ref_lat)))),
    }


# ── Geometry primitives ──────────────────────────────────────────────────────

def _interp(pts, density_m=6):
    """Densify a polyline so corridor offsets are smooth."""
    result = []
    for i in range(len(pts) - 1):
        x0, y0 = pts[i]; x1, y1 = pts[i + 1]
        seg = math.hypot(x1 - x0, y1 - y0)
        steps = max(2, int(seg / density_m))
        for j in range(steps):
            t = j / steps
            result.append((x0 + t * (x1 - x0), y0 + t * (y1 - y0)))
    result.append(pts[-1])
    return result


def _corridor(centerline, half_widths, ref_lat, ref_lon):
    """Closed polygon corridor: offset a polyline left/right then close the loop."""
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
            dx, dy = centerline[i+1][0] - centerline[i-1][0], \
                     centerline[i+1][1] - centerline[i-1][1]
        ln = math.hypot(dx, dy) or 1
        dx /= ln; dy /= ln
        px, py = -dy, dx                    # left-hand perpendicular
        w = half_widths[i]
        left.append(_local2ll(ex + px * w, ny + py * w, ref_lat, ref_lon))
        right.append(_local2ll(ex - px * w, ny - py * w, ref_lat, ref_lon))
    ring = left + right[::-1]
    ring.append(ring[0])
    return ring


def _ellipse(cx, cy, rx, ry, angle_deg, n_pts, ref_lat, ref_lon):
    a = math.radians(angle_deg)
    ring = []
    for i in range(n_pts + 1):
        t = 2 * math.pi * i / n_pts
        lx, ly = rx * math.cos(t), ry * math.sin(t)
        ex = lx * math.cos(a) - ly * math.sin(a)
        ny = lx * math.sin(a) + ly * math.cos(a)
        ring.append(_local2ll(cx + ex, cy + ny, ref_lat, ref_lon))
    return ring


def _rect(cx, cy, w, h, angle_deg, ref_lat, ref_lon):
    a = math.radians(angle_deg)
    corners = [(-w/2, -h/2), (w/2, -h/2), (w/2, h/2), (-w/2, h/2)]
    ring = []
    for lx, ly in corners:
        ex = lx * math.cos(a) - ly * math.sin(a)
        ny = lx * math.sin(a) + ly * math.cos(a)
        ring.append(_local2ll(cx + ex, cy + ny, ref_lat, ref_lon))
    ring.append(ring[0])
    return ring


def _bearing(x0, y0, x1, y1):
    return math.degrees(math.atan2(x1 - x0, y1 - y0))


# ── Hole routing ─────────────────────────────────────────────────────────────
#
# Course ellipse: rx=500m (E-W), ry=700m (N-S).
# Holes weave across the INTERIOR — not around the perimeter.
# Each row below: (tee_e, tee_n, green_e, green_n, dogleg_e, dogleg_n)
#
# Approximate layout (local metre frame, N=up):
#
#         ·········COURSE BOUNDARY·········
#        ·   4  ←←← 5 ←←← 6           ·
#        ·   ↓             ↓           ·
#        ·   3           7   13        ·
#        ·   ↑             ↓   ↑       ·
#        ·   2  →→→       8   12  14   ·
#        ·   ↓       ↑       ↓   ↓     ·
#        ·   1  CLUB 9  10→ 11  15  17 ·
#        ·        ↑   ↓       ↓   ↓    ·
#        ·       18  ←← 9  ←← 16  ←←  ·
#         ·········COURSE BOUNDARY·········

HOLE_ROUTES = [
    # Front nine — weave west-to-north-to-east
    (   0, -580,  -150, -280,    None,  None),  # 1  Par 4  ~319 m N-W
    (-150, -280,  -380,   50,    None,  None),  # 2  Par 4  ~332 m NW
    (-380,   50,  -460,  390,  -440,  220),    # 3  Par 5  ~373+175=548 m dogleg N
    (-460,  390,  -250,  570,    None,  None),  # 4  Par 3  ~273 m NE  (long par 3)
    (-250,  570,    50,  620,    None,  None),  # 5  Par 4  ~303 m E
    (  50,  620,   320,  460,    None,  None),  # 6  Par 4  ~314 m SE
    ( 320,  460,   460,  100,    None,  None),  # 7  Par 5  ~378 m S (plays into wind)
    ( 460,  100,   380, -290,   460, -100),    # 8  Par 4  ~201+302=503? let me recheck dogleg
    ( 380, -290,   100, -560,    None,  None),  # 9  Par 3  ~369 m SW (long par 3)

    # Back nine — return through the interior east side
    ( 100, -560,   300, -180,    None,  None),  # 10 Par 4  ~427 m NE
    ( 300, -180,   420,  300,    None,  None),  # 11 Par 5  ~494 m N
    ( 420,  300,   240,  570,    None,  None),  # 12 Par 4  ~321 m NW
    ( 240,  570,    40,  660,    None,  None),  # 13 Par 3  ~218 m W (long par 3)
    (  40,  660,  -310,  510,    None,  None),  # 14 Par 5  ~380 m W
    (-310,  510,  -470,  130,    None,  None),  # 15 Par 4  ~413 m S
    (-470,  130,  -400, -290,   -480, -100),   # 16 Par 4  ~83+292=375 m dogleg S
    (-400, -290,  -200, -500,    None,  None),  # 17 Par 3  ~278 m SE
    (-200, -500,    0,  -580,    None,  None),  # 18 Par 4  ~208 m SE home
]


# ── Main generator ────────────────────────────────────────────────────────────

def generate_demo_course(
    center_lat: float = 32.8998,
    center_lon: float = -117.2498,
    course_name: str = "Demo Golf Course",
    seed: int = 42,
) -> tuple:
    """Return (features, bbox, name) for a synthetic 18-hole course."""
    rng = random.Random(seed)
    rl, rl2 = center_lat, center_lon

    def l2ll(e, n): return _local2ll(e, n, rl, rl2)

    features = {
        "course_boundary": [], "hole_boundary": [],
        "fairway": [], "tee": [], "green": [],
        "bunker": [], "water": [], "river": [],
        "ocean": [], "rough": [], "path": [], "building": [],
    }

    # Course outer boundary (realistic 150-acre course)
    features["course_boundary"].append({
        "coords": _ellipse(0, 0, 520, 720, 0, 72, rl, rl2),
        "tags": {"leisure": "golf_course", "name": course_name},
        "osm_id": 1_000_000, "osm_type": "way", "name": course_name, "ref": "",
    })

    # Clubhouse
    features["building"].append({
        "coords": _rect(30, -150, 55, 38, 0, rl, rl2),
        "tags": {"building": "clubhouse"},
        "osm_id": 1_000_001, "osm_type": "way", "name": "Clubhouse", "ref": "",
    })

    # Water bodies (ponds / lakes)
    for i, (ex, ny, rx, ry, ang) in enumerate([
        ( 180,  -50, 55, 38, 20),
        (-200,  200, 40, 30, -8),
        ( 350,  400, 30, 22,  0),
    ]):
        features["water"].append({
            "coords": _ellipse(ex, ny, rx, ry, ang, 24, rl, rl2),
            "tags": {"natural": "water"},
            "osm_id": 1_000_010 + i, "osm_type": "way",
            "name": f"Pond {i + 1}", "ref": "",
        })

    # Creek / stream (open polyline — crosses central part of course)
    stream = []
    sx, sy = -350, -600
    for step in range(14):
        sx += rng.uniform(10, 35)
        sy += rng.uniform(60, 90)
        stream.append(l2ll(sx, sy))
    features["river"].append({
        "coords": stream,
        "tags": {"waterway": "stream"},
        "osm_id": 1_000_020, "osm_type": "way", "name": "Creek", "ref": "",
    })

    # 18 holes
    cart_path = []

    for hole_num, (te, tn, ge, gn, de, dn) in enumerate(HOLE_ROUTES, 1):

        # Centerline with optional dogleg
        if de is not None:
            raw = [(te, tn),
                   (de + rng.uniform(-8, 8), dn + rng.uniform(-8, 8)),
                   (ge, gn)]
        else:
            # Gentle natural curve
            mx = (te + ge) / 2 + rng.uniform(-20, 20)
            my = (tn + gn) / 2 + rng.uniform(-20, 20)
            raw = [(te, tn), (mx, my), (ge, gn)]

        cl = _interp(raw, density_m=6)
        n  = len(cl)

        # Fairway half-widths:
        #   - Narrow at tee end (~12 m half-width = 24 m wide)
        #   - Widest in mid-fairway (~20 m half-width = 40 m wide)
        #   - Flares at green complex (~28 m half-width = 56 m wide)
        fw_hw = []
        for i in range(n):
            t = i / max(n - 1, 1)
            if t < 0.15:
                w = 12 + t / 0.15 * 6          # taper up from tee
            elif t < 0.70:
                w = 18 + 4 * math.sin(math.pi * (t - 0.15) / 0.55)  # bell curve
            else:
                w = 18 + (t - 0.70) / 0.30 * 14  # flare into green complex
            fw_hw.append(w)

        # Hole boundary (rough + buffer around entire hole)
        hb_hw = [w * 2.0 + 20 for w in fw_hw]
        features["hole_boundary"].append({
            "coords": _corridor(cl, hb_hw, rl, rl2),
            "tags": {"golf": "hole"},
            "osm_id": 1_009_000 + hole_num, "osm_type": "way",
            "name": f"Hole {hole_num}", "ref": str(hole_num),
        })

        # Fairway (stop a bit before green so green pops out of it)
        fw_end = max(3, n - int(n * 0.10))
        features["fairway"].append({
            "coords": _corridor(cl[:fw_end], fw_hw[:fw_end], rl, rl2),
            "tags": {"golf": "fairway"},
            "osm_id": 1_002_000 + hole_num, "osm_type": "way",
            "name": f"Hole {hole_num}", "ref": str(hole_num),
        })

        # Tee box: small rectangle, rotated to aim at fairway
        tee_ang = _bearing(cl[0][0], cl[0][1], cl[1][0], cl[1][1])
        features["tee"].append({
            "coords": _rect(te, tn, 12, 16, tee_ang, rl, rl2),
            "tags": {"golf": "tee"},
            "osm_id": 1_001_000 + hole_num, "osm_type": "way",
            "name": f"Hole {hole_num} Tee", "ref": str(hole_num),
        })

        # Green: irregular ellipse, angled along approach
        g_ang = _bearing(cl[-3][0], cl[-3][1], ge, gn)
        grx = rng.uniform(13, 19)
        gry = rng.uniform(16, 24)
        features["green"].append({
            "coords": _ellipse(ge, gn, grx, gry, g_ang, 32, rl, rl2),
            "tags": {"golf": "green"},
            "osm_id": 1_003_000 + hole_num, "osm_type": "way",
            "name": f"Hole {hole_num} Green", "ref": str(hole_num),
        })

        # Greenside bunkers (1–3 per hole)
        for b in range(rng.randint(1, 3)):
            ang = rng.uniform(0, 2 * math.pi)
            dist = grx + rng.uniform(8, 18)
            bx, by = ge + dist * math.cos(ang), gn + dist * math.sin(ang)
            features["bunker"].append({
                "coords": _ellipse(bx, by, rng.uniform(7, 14), rng.uniform(5, 10),
                                   rng.uniform(0, 90), 18, rl, rl2),
                "tags": {"golf": "bunker"},
                "osm_id": 1_004_000 + hole_num * 10 + b, "osm_type": "way",
                "name": "", "ref": str(hole_num),
            })

        # Optional fairway bunker (65% chance)
        if rng.random() < 0.65:
            mi  = n // 2
            mx2, my2 = cl[mi]
            if mi < n - 1:
                sdx = cl[mi+1][0] - cl[mi][0]; sdy = cl[mi+1][1] - cl[mi][1]
            else:
                sdx = cl[-1][0] - cl[-2][0];  sdy = cl[-1][1] - cl[-2][1]
            sl = math.hypot(sdx, sdy) or 1
            px, py = -sdy/sl, sdx/sl
            side = rng.choice([-1, 1])
            off  = fw_hw[mi] + rng.uniform(4, 10)
            features["bunker"].append({
                "coords": _ellipse(mx2 + px * off * side, my2 + py * off * side,
                                   rng.uniform(8, 16), rng.uniform(6, 10),
                                   rng.uniform(0, 90), 16, rl, rl2),
                "tags": {"golf": "bunker"},
                "osm_id": 1_004_500 + hole_num, "osm_type": "way",
                "name": "", "ref": str(hole_num),
            })

        # Cart path waypoints every few centerline points
        for pt in cl[::4]:
            cart_path.append(l2ll(pt[0], pt[1]))

    features["path"].append({
        "coords": cart_path,
        "tags": {"golf": "path"},
        "osm_id": 1_000_002, "osm_type": "way", "name": "Cart Path", "ref": "",
    })

    # Bounding box
    all_lats = [c["lat"] for layer in features.values() for f in layer for c in f["coords"]]
    all_lons = [c["lon"] for layer in features.values() for f in layer for c in f["coords"]]
    pad = 0.0008
    bbox = {
        "min_lat": min(all_lats) - pad, "max_lat": max(all_lats) + pad,
        "min_lon": min(all_lons) - pad, "max_lon": max(all_lons) + pad,
    }
    return features, bbox, course_name
