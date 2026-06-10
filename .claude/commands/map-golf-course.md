# map-golf-course

Map a golf course from OpenStreetMap data into a layered SVG vector outline ready for Adobe Illustrator colorization.

## Usage

```
/map-golf-course <course name or GPS coords>
```

**Examples:**
- `/map-golf-course Pebble Beach Golf Links`
- `/map-golf-course Augusta National Golf Club, USA`
- `/map-golf-course 36.5685, -121.9497`
- `/map-golf-course batch courses.json`

---

## What You Should Do

You are a golf course mapping specialist. When this skill is invoked with `$ARGUMENTS`:

### 1 — Parse the input

Determine whether the argument is:
- A **GPS coordinate pair** — matches `[-\d.]+\s*,\s*[-\d.]+`
- A **batch file path** — ends with `.json`
- A **course name** (everything else)

### 2 — Run the pipeline

Execute the correct CLI command from the project root:

**By name:**
```bash
python -m golf_course_mapper.cli --name "$ARGUMENTS" --out ./output --size 4096 --verbose
```

**By GPS (lat, lon):**
```bash
python -m golf_course_mapper.cli --lat <LAT> --lon <LON> --out ./output --size 4096 --verbose
```

**Batch file:**
```bash
python -m golf_course_mapper.cli --batch "$ARGUMENTS" --out ./output --size 4096 --verbose
```

### 3 — Handle errors & retry

- If the Overpass API times out, retry up to **4 times** with exponential backoff (5, 10, 20, 40 s).
- If Nominatim returns no results, try the Overpass text search automatically (the pipeline does this internally — just re-run if there was a network error).
- If very few features are extracted (< 3 feature types), report which types are missing and offer to expand the search radius with `--radius 5000`.

### 4 — Report results

After success tell the user:
- The full path to the generated SVG file
- How many features were found per layer (course boundary, fairways, greens, tees, bunkers, water, rivers, ocean)
- SVG canvas size
- How to open it in Adobe Illustrator

### 5 — Adobe Illustrator import tip

Remind the user that each golf feature type is saved as a named `<g>` layer:
- `layer_course_boundary`
- `layer_fairway`
- `layer_green`
- `layer_tee`
- `layer_bunker`
- `layer_water`
- `layer_river`
- `layer_ocean`
- `layer_rough`
- `layer_path`
- `layer_building`

These can be targeted by layer name in Illustrator's Layers panel and coloured with Artistic Prints Studio's proprietary graphic styles.

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `LookupError: Could not find golf course` | Try a more specific name or add the country, e.g. `"Augusta National Golf Club, Georgia, USA"` |
| `RuntimeError: All Overpass endpoints exhausted` | Overpass is rate-limited; wait 60 s and retry |
| SVG is nearly empty | The course may lack detailed OSM mapping; try `--radius 5000` |
| Coordinate system looks squished | Normal for equirectangular at high latitudes — the SVG is correct for Illustrator use |
