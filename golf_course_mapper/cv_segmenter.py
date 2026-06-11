"""
Computer-vision segmentation of golf-course satellite imagery.

Takes a geo-referenced aerial/satellite image and recovers golf features
as polygons by colour + texture classification:

  green   – vivid bright green, very low texture (mowed putting surface)
  fairway – medium green, low texture, large connected area
  bunker  – bright sand/beige, high value, low saturation in the yellow band
  water   – dark blue / cyan hue, low luminance
  rough   – darker, yellower green with high texture variance (implied,
            not emitted as polygons by default — it is the background)

Pipeline per feature class:
  HSV threshold → morphological open/close → connected components
  → area filter → contour trace (cv2.findContours)
  → Douglas-Peucker simplify → pixel→lat/lon transform → closed ring

The output dict is identical in shape to OSMExtractor output, so the
existing SVGVectorizer renders it without modification.
"""

import logging
import math
from pathlib import Path

import cv2
import numpy as np

logger = logging.getLogger(__name__)


# ── HSV classification (OpenCV ranges: H 0-179, S 0-255, V 0-255) ────────────
#
# Vegetation (rough / fairway / green) is NOT separated by fixed bands —
# brightness varies hugely between imagery providers, seasons and irrigation.
# Instead we take all green-hue pixels and k-means-cluster their V channel
# into three groups; mowed fairways are brighter than rough, and putting
# greens are the brightest, smoothest cluster. This adapts per-image.
#
# Bunker and water keep fixed bands (sand and water hues are stable).

VEGETATION_HUE = ((25, 30, 40), (90, 255, 255))   # broad green-hue gate

HSV_BANDS = {
    "bunker": [
        # sand: yellow-beige hue, moderate-to-low saturation, high value
        ((10, 20, 140), (35, 140, 255)),
        # very pale / white sand
        ((0, 0, 170), (60, 50, 255)),
    ],
    "water": [
        # blue water
        ((85, 40, 30), (130, 255, 220)),
        # dark / shadowed water
        ((85, 20, 10), (140, 255, 90)),
    ],
}

# Minimum feature areas in square metres (converted to px² at runtime)
MIN_AREA_M2 = {
    "green": 150,
    "fairway": 1200,
    "bunker": 25,
    "water": 100,
}

# Morphology kernel sizes in metres (converted to px)
MORPH_M = {
    "green": 3,
    "fairway": 6,
    "bunker": 2,
    "water": 4,
}


class CVSegmenter:
    """Extract golf features from a geo-referenced satellite image."""

    def __init__(self, simplify_epsilon_m: float = 1.5):
        # Douglas-Peucker tolerance in metres on the ground
        self.simplify_epsilon_m = simplify_epsilon_m

    # ------------------------------------------------------------------ public

    def segment(self, image_path, bbox: dict) -> dict:
        """
        Segment an aerial image into golf feature polygons.

        image_path : PNG/JPG covering exactly the bbox extents
        bbox       : {min_lat, max_lat, min_lon, max_lon} of the image

        Returns the same structure as OSMExtractor.extract().
        """
        img = cv2.imread(str(image_path))
        if img is None:
            raise FileNotFoundError(f"Cannot read image: {image_path}")
        h, w = img.shape[:2]
        logger.info("Segmenting %s  (%d×%d px)", image_path, w, h)

        # Ground resolution (metres per pixel)
        m_per_px = self._metres_per_pixel(bbox, w, h)
        logger.info("Ground resolution: %.2f m/px", m_per_px)

        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        blur = cv2.GaussianBlur(hsv, (5, 5), 0)

        # Texture map — local std-dev of grayscale; greens/fairways are smooth
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32)
        mean = cv2.boxFilter(gray, -1, (15, 15))
        sq_mean = cv2.boxFilter(gray * gray, -1, (15, 15))
        texture = np.sqrt(np.maximum(sq_mean - mean * mean, 0))

        features = {t: [] for t in [
            "course_boundary", "hole_boundary", "fairway", "tee", "green",
            "bunker", "water", "river", "ocean", "rough", "path", "building",
        ]}

        masks = {}
        for cls in ("bunker", "water"):
            masks[cls] = self._band_mask(blur, texture, cls, m_per_px)

        veg_green, veg_fairway = self._vegetation_masks(blur, texture, m_per_px)
        masks["green"] = veg_green
        masks["fairway"] = veg_fairway

        # Remove water pixels misread as fairway shadow
        masks["fairway"] = cv2.bitwise_and(
            masks["fairway"], cv2.bitwise_not(masks["water"])
        )

        for cls in ("green", "fairway", "bunker", "water"):
            rings = self._mask_to_rings(masks[cls], cls, m_per_px, bbox, w, h)
            features[cls].extend(rings)

        totals = {k: len(v) for k, v in features.items() if v}
        logger.info("CV segmentation found: %s", totals)
        return features

    def save_debug_masks(self, image_path, bbox: dict,
                         out_dir) -> list:
        """Write per-class binary masks alongside the source for tuning."""
        img = cv2.imread(str(image_path))
        h, w = img.shape[:2]
        m_per_px = self._metres_per_pixel(bbox, w, h)
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        blur = cv2.GaussianBlur(hsv, (5, 5), 0)
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32)
        mean = cv2.boxFilter(gray, -1, (15, 15))
        sq_mean = cv2.boxFilter(gray * gray, -1, (15, 15))
        texture = np.sqrt(np.maximum(sq_mean - mean * mean, 0))

        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        green, fairway = self._vegetation_masks(blur, texture, m_per_px)
        masks = {
            "green": green,
            "fairway": fairway,
            "bunker": self._band_mask(blur, texture, "bunker", m_per_px),
            "water": self._band_mask(blur, texture, "water", m_per_px),
        }
        written = []
        for cls, mask in masks.items():
            p = out_dir / f"mask_{cls}.png"
            cv2.imwrite(str(p), mask)
            written.append(p)
        return written

    # ----------------------------------------------------------------- private

    @staticmethod
    def _metres_per_pixel(bbox: dict, w: int, h: int) -> float:
        R = 6_371_000
        mid_lat = (bbox["min_lat"] + bbox["max_lat"]) / 2
        width_m = math.radians(bbox["max_lon"] - bbox["min_lon"]) * R * math.cos(math.radians(mid_lat))
        height_m = math.radians(bbox["max_lat"] - bbox["min_lat"]) * R
        return ((width_m / w) + (height_m / h)) / 2

    def _band_mask(self, hsv_img, texture, cls: str, m_per_px: float) -> np.ndarray:
        """Fixed-band classes: bunker, water."""
        mask = np.zeros(hsv_img.shape[:2], dtype=np.uint8)
        for lower, upper in HSV_BANDS[cls]:
            band = cv2.inRange(hsv_img, np.array(lower), np.array(upper))
            mask = cv2.bitwise_or(mask, band)
        return self._morph_clean(mask, cls, m_per_px)

    def _vegetation_masks(self, hsv_img, texture, m_per_px: float):
        """
        Adaptive separation of vegetation into (green, fairway) masks.

        K-means (k=3) on the V channel of green-hue pixels splits the
        turf into rough (darkest), fairway (middle) and putting green
        (brightest). This adapts to any imagery exposure automatically.
        Texture gating removes trees/shrubs that share fairway brightness.
        """
        h, w = hsv_img.shape[:2]
        veg = cv2.inRange(hsv_img, np.array(VEGETATION_HUE[0]), np.array(VEGETATION_HUE[1]))
        veg_idx = veg > 0
        n_veg = int(veg_idx.sum())
        empty = np.zeros((h, w), dtype=np.uint8)
        if n_veg < 1000:
            logger.warning("Almost no vegetation pixels found (%d)", n_veg)
            return empty, empty

        v_vals = hsv_img[:, :, 2][veg_idx].astype(np.float32).reshape(-1, 1)

        # Subsample for speed on big images
        if len(v_vals) > 200_000:
            sel = np.random.default_rng(0).choice(len(v_vals), 200_000, replace=False)
            sample = v_vals[sel]
        else:
            sample = v_vals

        criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 50, 0.5)
        _, _, centers = cv2.kmeans(sample, 3, None, criteria, 5, cv2.KMEANS_PP_CENTERS)
        order = np.argsort(centers.flatten())          # dark → bright
        c_rough, c_fair, c_green = centers.flatten()[order]
        logger.info("Vegetation V clusters: rough=%.0f fairway=%.0f green=%.0f",
                    c_rough, c_fair, c_green)

        # Decision boundaries = midpoints between cluster centres
        t_fair = (c_rough + c_fair) / 2
        t_green = (c_fair + c_green) / 2

        v_chan = hsv_img[:, :, 2]
        fairway = ((v_chan > t_fair) & (v_chan <= t_green) & veg_idx).astype(np.uint8) * 255
        green = ((v_chan > t_green) & veg_idx).astype(np.uint8) * 255

        # Mowed turf is smooth; gate out high-texture canopy
        fairway = cv2.bitwise_and(fairway, (texture < 30).astype(np.uint8) * 255)
        green = cv2.bitwise_and(green, (texture < 20).astype(np.uint8) * 255)

        # If the clusters are too close together the course probably has
        # uniform turf — merge fairway+green into fairway and warn.
        if (c_green - c_fair) < 12:
            logger.warning("Green/fairway clusters indistinct (%.0f vs %.0f) — "
                           "merging into fairway", c_green, c_fair)
            fairway = cv2.bitwise_or(fairway, green)
            green = empty.copy()

        fairway = self._morph_clean(fairway, "fairway", m_per_px)
        green = self._morph_clean(green, "green", m_per_px)

        # A green inside the fairway mask is fine; remove green px from fairway
        fairway = cv2.bitwise_and(fairway, cv2.bitwise_not(green))
        return green, fairway

    def _morph_clean(self, mask: np.ndarray, cls: str, m_per_px: float) -> np.ndarray:
        k_px = max(3, int(MORPH_M[cls] / max(m_per_px, 0.01)))
        if k_px % 2 == 0:
            k_px += 1
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k_px, k_px))
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
        return mask

    def _mask_to_rings(self, mask: np.ndarray, cls: str, m_per_px: float,
                       bbox: dict, w: int, h: int) -> list:
        min_area_px = MIN_AREA_M2[cls] / (m_per_px ** 2)
        eps_px = max(1.0, self.simplify_epsilon_m / max(m_per_px, 0.01))

        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        rings = []
        idx = 0
        for cnt in contours:
            area = cv2.contourArea(cnt)
            if area < min_area_px:
                continue
            approx = cv2.approxPolyDP(cnt, eps_px, True)
            if len(approx) < 3:
                continue

            coords = []
            for pt in approx.reshape(-1, 2):
                px, py = float(pt[0]), float(pt[1])
                lon = bbox["min_lon"] + (px / w) * (bbox["max_lon"] - bbox["min_lon"])
                lat = bbox["max_lat"] - (py / h) * (bbox["max_lat"] - bbox["min_lat"])
                coords.append({"lat": lat, "lon": lon})
            coords.append(coords[0])  # close ring

            rings.append({
                "coords": coords,
                "tags": {"source": "cv_segmentation", "class": cls},
                "osm_id": 9_000_000 + hash((cls, idx)) % 900_000,
                "osm_type": "cv",
                "name": "",
                "ref": "",
            })
            idx += 1

        logger.debug("%s: %d contours kept (min_area=%.0f px², eps=%.1f px)",
                     cls, len(rings), min_area_px, eps_px)
        return rings
