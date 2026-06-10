"""
Golf Course Mapper — locate courses via GPS/OSM, extract features, output SVG vector outlines.

Pipeline:
  1. Locate course via GPS coordinates, name search, or Overpass/Nominatim API
  2. Query all golf features (fairways, greens, tees, bunkers, water, coastline, rivers)
  3. Project geodata to a local coordinate system
  4. Emit a layered SVG named  golf_course_name_outline.svg  ready for Illustrator colorization
"""

from .pipeline import GolfCoursePipeline
from .locator import CourseLocator
from .osm_extractor import OSMExtractor
from .vectorizer import SVGVectorizer

__all__ = ["GolfCoursePipeline", "CourseLocator", "OSMExtractor", "SVGVectorizer"]
__version__ = "1.0.0"
