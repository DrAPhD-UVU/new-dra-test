"""
Main pipeline: locate → extract → vectorize → save SVG.

Usage (Python API):
    from golf_course_mapper import GolfCoursePipeline
    p = GolfCoursePipeline(output_dir="./output")
    path = p.run_by_name("Pebble Beach Golf Links")

Usage (GPS):
    path = p.run_by_coords(36.5685, -121.9497)
"""

import logging
import time
from pathlib import Path

from .locator import CourseLocator
from .osm_extractor import OSMExtractor
from .vectorizer import SVGVectorizer

logger = logging.getLogger(__name__)


class GolfCoursePipeline:
    def __init__(
        self,
        output_dir: str = "./output",
        svg_size: int = 4096,
        simplify_tolerance: float = 0.5,
    ):
        self.output_dir = output_dir
        self.locator = CourseLocator()
        self.extractor = OSMExtractor()
        self.vectorizer = SVGVectorizer(svg_size=svg_size, simplify_tolerance=simplify_tolerance)

    # ------------------------------------------------------------------ public

    def run_by_name(self, name: str, country: str = "") -> Path:
        """Full pipeline starting from a course name."""
        logger.info("=== Pipeline: name='%s' country='%s' ===", name, country)
        t0 = time.time()

        course = self.locator.find_by_name(name, country)
        logger.info("Located: %s  bbox=%s", course["name"], course["bbox"])

        features = self._extract_features(course)
        out = self.vectorizer.render(features, course["bbox"], course["name"], self.output_dir)

        logger.info("Done in %.1f s → %s", time.time() - t0, out)
        return out

    def run_by_coords(self, lat: float, lon: float, radius_m: int = 2000) -> Path:
        """Full pipeline starting from GPS coordinates."""
        logger.info("=== Pipeline: lat=%.6f lon=%.6f radius=%dm ===", lat, lon, radius_m)
        t0 = time.time()

        course = self.locator.find_by_coords(lat, lon, radius_m)
        logger.info("Located: %s  bbox=%s", course["name"], course["bbox"])

        features = self._extract_features(course)
        out = self.vectorizer.render(features, course["bbox"], course["name"], self.output_dir)

        logger.info("Done in %.1f s → %s", time.time() - t0, out)
        return out

    def run_batch(self, courses: list[dict]) -> list[Path]:
        """
        Process a list of courses.
        Each item: {"name": "..."} or {"lat": ..., "lon": ..., "radius_m": ...}
        Continues even if individual courses fail.
        """
        results = []
        for i, spec in enumerate(courses, 1):
            logger.info("--- Batch %d/%d: %s ---", i, len(courses), spec)
            try:
                if "name" in spec:
                    path = self.run_by_name(spec["name"], spec.get("country", ""))
                elif "lat" in spec:
                    path = self.run_by_coords(spec["lat"], spec["lon"], spec.get("radius_m", 2000))
                else:
                    logger.error("Unknown spec: %s", spec)
                    continue
                results.append(path)
            except Exception as exc:
                logger.error("Failed for %s: %s", spec, exc)
        return results

    # ----------------------------------------------------------------- private

    def _extract_features(self, course: dict) -> dict:
        """Extract features — reuse locator elements if present, else re-query bbox."""
        if course.get("elements"):
            features = self.extractor.extract_from_elements(course["elements"])
        else:
            features = self.extractor.extract(course["bbox"])

        # If only the boundary came back, do a wider bbox query to fill in detail
        non_boundary = sum(len(v) for k, v in features.items() if k != "course_boundary")
        if non_boundary == 0:
            logger.info("No detail features from locator elements — re-querying bbox")
            features = self.extractor.extract(course["bbox"])

        return features
