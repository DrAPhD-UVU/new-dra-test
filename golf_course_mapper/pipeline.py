"""
Main pipeline: locate → extract → vectorize → save SVG.
Optional: fetch satellite tiles and embed as background layer.
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
        simplify_tolerance: float = 0.4,
        satellite: bool = False,
        sat_zoom: int = 0,
    ):
        self.output_dir = output_dir
        self.satellite = satellite
        self.sat_zoom = sat_zoom
        self.locator = CourseLocator()
        self.extractor = OSMExtractor()
        self.vectorizer = SVGVectorizer(svg_size=svg_size, simplify_tolerance=simplify_tolerance)

    # ------------------------------------------------------------------ public

    def run_by_name(self, name: str, country: str = "") -> Path:
        logger.info("=== Pipeline: name='%s' country='%s' ===", name, country)
        t0 = time.time()
        course = self.locator.find_by_name(name, country)
        logger.info("Located: %s  bbox=%s", course["name"], course["bbox"])
        features = self._extract_features(course)
        out = self.vectorizer.render(features, course["bbox"], course["name"], self.output_dir)
        if self.satellite:
            out = self._add_satellite(out, course["bbox"], course["name"])
        logger.info("Done in %.1f s → %s", time.time() - t0, out)
        return out

    def run_by_coords(self, lat: float, lon: float, radius_m: int = 2000) -> Path:
        logger.info("=== Pipeline: lat=%.6f lon=%.6f radius=%dm ===", lat, lon, radius_m)
        t0 = time.time()
        course = self.locator.find_by_coords(lat, lon, radius_m)
        logger.info("Located: %s  bbox=%s", course["name"], course["bbox"])
        features = self._extract_features(course)
        out = self.vectorizer.render(features, course["bbox"], course["name"], self.output_dir)
        if self.satellite:
            out = self._add_satellite(out, course["bbox"], course["name"])
        logger.info("Done in %.1f s → %s", time.time() - t0, out)
        return out

    def run_batch(self, courses: list) -> list:
        results = []
        for i, spec in enumerate(courses, 1):
            logger.info("--- Batch %d/%d: %s ---", i, len(courses), spec)
            try:
                if "name" in spec:
                    path = self.run_by_name(spec["name"], spec.get("country", ""))
                elif "lat" in spec:
                    path = self.run_by_coords(spec["lat"], spec["lon"], spec.get("radius_m", 2000))
                else:
                    logger.error("Unknown spec: %s", spec); continue
                results.append(path)
            except Exception as exc:
                logger.error("Failed for %s: %s", spec, exc)
        return results

    # ----------------------------------------------------------------- private

    def _extract_features(self, course: dict) -> dict:
        if course.get("elements"):
            features = self.extractor.extract_from_elements(course["elements"])
        else:
            features = self.extractor.extract(course["bbox"])
        non_boundary = sum(len(v) for k, v in features.items() if k != "course_boundary")
        if non_boundary == 0:
            logger.info("No detail features — re-querying full bbox")
            features = self.extractor.extract(course["bbox"])
        return features

    def _add_satellite(self, svg_path: Path, bbox: dict, name: str) -> Path:
        try:
            from .satellite import SatelliteFetcher, embed_satellite_in_svg
            fetcher = SatelliteFetcher(zoom=self.sat_zoom)
            safe_name = name.lower().replace(" ", "_")
            sat_path = fetcher.fetch(bbox, output_dir=str(self.output_dir), name=safe_name)
            if sat_path:
                svg_size = self.vectorizer.svg_size
                embed_satellite_in_svg(svg_path, sat_path, bbox, svg_size)
                logger.info("Satellite background embedded")
        except Exception as exc:
            logger.warning("Satellite fetch failed (non-fatal): %s", exc)
        return svg_path

