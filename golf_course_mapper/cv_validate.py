"""
Offline validation harness for the CV segmenter.

Renders the synthetic demo course as a realistic pseudo-satellite raster
(rough-textured background, fairway/green/bunker/water colours, sensor
noise), runs CVSegmenter on the raster, and reports recovery rates of
each feature class against the known ground truth.

Run:  python -m golf_course_mapper.cv_validate
"""

import logging
import math
from pathlib import Path

import cv2
import numpy as np

from .demo_data import generate_demo_course
from .cv_segmenter import CVSegmenter
from .vectorizer import SVGVectorizer, ProjectionHelper

logger = logging.getLogger(__name__)

# BGR colours approximating mid-day aerial imagery
COLOURS = {
    "rough":   (40, 95, 70),     # dark yellow-green
    "fairway": (60, 160, 95),    # medium green
    "green":   (70, 200, 110),   # vivid green
    "bunker":  (150, 200, 225),  # sand beige
    "water":   (140, 90, 30),    # blue (BGR)
}


def render_pseudo_satellite(features: dict, bbox: dict, size: int = 2048,
                            noise_sigma: float = 6.0) -> np.ndarray:
    """Rasterize ground-truth features into a satellite-like BGR image."""
    proj = ProjectionHelper(bbox, size, margin=0)
    img = np.zeros((size, size, 3), dtype=np.uint8)
    img[:] = COLOURS["rough"]

    # Rough texture: per-pixel brightness jitter, then smooth slightly
    tex = np.random.default_rng(7).normal(0, 14, (size, size, 1)).astype(np.float32)
    img = np.clip(img.astype(np.float32) + tex, 0, 255).astype(np.uint8)

    def draw(layer: str, colour):
        for feat in features.get(layer, []):
            pts = proj.project_ring(feat["coords"])
            poly = np.array([[int(x), int(y)] for x, y in pts], dtype=np.int32)
            cv2.fillPoly(img, [poly], colour)

    # Paint order: fairway under green/bunker, water anywhere
    draw("fairway", COLOURS["fairway"])
    draw("green", COLOURS["green"])
    draw("bunker", COLOURS["bunker"])
    draw("water", COLOURS["water"])

    # Sensor noise + slight blur to mimic optics
    rng = np.random.default_rng(13)
    noise = rng.normal(0, noise_sigma, img.shape).astype(np.float32)
    img = np.clip(img.astype(np.float32) + noise, 0, 255).astype(np.uint8)
    img = cv2.GaussianBlur(img, (3, 3), 0)
    return img


def validate(output_dir: str = "./output") -> dict:
    """Run the full loop and return recovery stats per class."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    features, bbox, name = generate_demo_course(course_name="CV Validation Course")

    img = render_pseudo_satellite(features, bbox)
    img_path = out / "pseudo_satellite.png"
    cv2.imwrite(str(img_path), img)
    logger.info("Pseudo-satellite written: %s", img_path)

    seg = CVSegmenter()
    recovered = seg.segment(img_path, bbox)
    seg.save_debug_masks(img_path, bbox, out / "masks")

    stats = {}
    for cls in ("green", "fairway", "bunker", "water"):
        truth = len(features[cls])
        found = len(recovered[cls])
        stats[cls] = {"truth": truth, "found": found}
        logger.info("%-8s truth=%3d  recovered=%3d", cls, truth, found)

    # Render the recovered features as an SVG for visual inspection
    vect = SVGVectorizer(svg_size=2048)
    svg_path = vect.render(recovered, bbox, "CV Recovered Course", str(out))
    logger.info("Recovered-features SVG: %s", svg_path)

    return stats


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
    validate()
