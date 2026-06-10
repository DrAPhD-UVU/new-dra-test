#!/usr/bin/env python3
"""
Command-line interface for the Golf Course Mapper.

Examples:
    python -m golf_course_mapper.cli --name "Pebble Beach Golf Links"
    python -m golf_course_mapper.cli --lat 36.5685 --lon -121.9497
    python -m golf_course_mapper.cli --batch courses.json
    python -m golf_course_mapper.cli --name "Augusta National" --size 8192 --out ./svgs
"""

import argparse
import json
import logging
import sys
from pathlib import Path

from .pipeline import GolfCoursePipeline


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="golf-course-mapper",
        description="Map a golf course from OSM data → layered SVG vector outline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --name "Pebble Beach Golf Links"
  %(prog)s --lat 36.5685 --lon -121.9497 --radius 3000
  %(prog)s --name "Augusta National Golf Club" --size 8192 --out ./svgs
  %(prog)s --batch courses.json
        """,
    )
    parser.add_argument("--name", help="Golf course name to search")
    parser.add_argument("--country", default="", help="Optional country hint for name search")
    parser.add_argument("--lat", type=float, help="Latitude (decimal degrees)")
    parser.add_argument("--lon", type=float, help="Longitude (decimal degrees)")
    parser.add_argument("--radius", type=int, default=2000, help="Search radius in metres (default 2000)")
    parser.add_argument("--batch", help="JSON file with list of courses [{name: ...} or {lat:, lon:}]")
    parser.add_argument("--out", default="./output", help="Output directory (default ./output)")
    parser.add_argument("--size", type=int, default=4096, help="SVG canvas size in px (default 4096)")
    parser.add_argument("--simplify", type=float, default=0.5, help="Path simplification tolerance in px (default 0.5)")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    parser.add_argument("--demo", action="store_true", help="Generate a demo SVG using built-in synthetic data (no network required)")
    parser.add_argument("--demo-lat", type=float, default=32.8998, help="Demo course centre latitude (default San Diego)")
    parser.add_argument("--demo-lon", type=float, default=-117.2498, help="Demo course centre longitude")
    parser.add_argument("--demo-name", type=str, default="Demo Golf Course", help="Course name for demo SVG")

    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s  %(name)s  %(message)s",
    )

    pipeline = GolfCoursePipeline(
        output_dir=args.out,
        svg_size=args.size,
        simplify_tolerance=args.simplify,
    )

    if args.demo:
        from pathlib import Path
        from .demo_data import generate_demo_course
        from .vectorizer import SVGVectorizer
        features, bbox, name = generate_demo_course(
            center_lat=args.demo_lat,
            center_lon=args.demo_lon,
            course_name=args.demo_name,
        )
        vect = SVGVectorizer(svg_size=args.size, simplify_tolerance=args.simplify)
        path = vect.render(features, bbox, name, args.out)
        totals = {k: len(v) for k, v in features.items() if v}
        print(f"\nDemo SVG saved: {path}")
        print(f"Features: {totals}")
        return

    if args.batch:
        batch_path = Path(args.batch)
        courses = json.loads(batch_path.read_text())
        paths = pipeline.run_batch(courses)
        print(f"\nGenerated {len(paths)} SVG file(s):")
        for p in paths:
            print(f"  {p}")
        return

    if args.name:
        path = pipeline.run_by_name(args.name, args.country)
        print(f"\nSaved: {path}")
        return

    if args.lat is not None and args.lon is not None:
        path = pipeline.run_by_coords(args.lat, args.lon, args.radius)
        print(f"\nSaved: {path}")
        return

    parser.print_help()
    sys.exit(1)


if __name__ == "__main__":
    main()
