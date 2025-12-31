# TempMap Renderer

A FastAPI service and lightweight canvas editor for building multi-floor temperature maps and rendering live heatmap PNGs for Home Assistant dashboards.

## Highlights

- **Live heatmap rendering** from Home Assistant sensor states.
- **Floorplan editor** served at `/editor` (no frontend framework).
- **Unraid-friendly Docker image** with `/data` volume persistence.
- **Configurable scaling** (absolute vs. relative min/max) and **auto-cropping** to reduce blank space around the floorplan.

## Repository layout

- `backend/` — FastAPI app, rendering logic, configuration.
- `frontend/` — Vanilla JS canvas editor.
- `docker/` — Dockerfile for container builds.
- `unraid/` — Unraid template.

## Quick start (local)

```bash
cp backend/config.example.yaml backend/config.yaml
python -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
```

Open the editor at http://localhost:8000/editor.

Floorplans are stored under `/data/floorplans/{floor_id}.json`.

## Docker

```bash
docker build -f docker/Dockerfile -t tempmap-renderer .
docker run --rm -p 8000:8000 -v $(pwd)/data:/data tempmap-renderer
```

Then visit http://localhost:8000/editor.

## Unraid template

Import the template in Unraid via **Unraid > Docker > Add Container > Template**.

> **Note:** The container reads config from `/app/backend/config.yaml` at runtime. If you keep your config under Unraid appdata, bind-mount that file directly to `/app/backend/config.yaml`.

---

# Configuration file (`backend/config.yaml`)

The backend reads its configuration once at startup from `backend/config.yaml` (in the container: `/app/backend/config.yaml`). Use `backend/config.example.yaml` as a starting point.

```yaml
server:
  host: 0.0.0.0
  port: 8000

data:
  path: /data

home_assistant:
  base_url: http://homeassistant.local:8123
  token: YOUR_LONG_LIVED_TOKEN
  refresh_seconds: 15

render:
  default_grid:
    width: 400
    height: 250
  default_legend:
    min_f: 60
    max_f: 80

timelapse:
  frame_retention_hours: 48
  window_hours: 48
  sampling_seconds: 120
  target_duration_seconds: 60
  fps: 10
  output_path: /data/timelapses
  rolling_enabled: true
  rolling_interval_seconds: 900
  stitch_multi_floor: true
  border_px: 12
  label_font_size: 18
```

## `server`

- **host**: informational only; the app is started via `uvicorn` (CLI).  
- **port**: informational only; the app is started via `uvicorn` (CLI).

## `data`

- **path**: base directory for persisted data (floorplans, frames).  
  - Default: `/data`
  - Override with `TEMP_MAP_DATA_PATH` environment variable if needed.

## `home_assistant`

- **base_url**: Home Assistant base URL (e.g., `http://192.168.1.199:8123`).
- **token**: long‑lived access token from Home Assistant.
- **refresh_seconds**: polling interval for HA sensor states and frame caching.

## `render`

This section controls **defaults** used when creating new floorplans in the editor.

- **default_grid**: default solver grid size used for new floorplans.
- **default_legend**: default min/max values for the legend in new floorplans.

## `timelapse`

Configure rolling timelapse generation and on-demand timelapses. The backend caches frames under
`/data/frames/{floor_id}` and writes MP4s to `output_path`.

- **frame_retention_hours**: how long to keep cached PNG frames.
- **window_hours**: rolling timelapse window length used by background generation.
- **sampling_seconds**: base sampling cadence for frames before adaptive downsampling.
- **target_duration_seconds**: target output duration; long sequences are downsampled to fit.
- **fps**: frames per second for the generated MP4 (H.264).
- **output_path**: directory to store rendered MP4s.
- **rolling_enabled**: enable periodic rolling generation.
- **rolling_interval_seconds**: how often to regenerate rolling timelapses.
- **stitch_multi_floor**: build a stitched `all/rolling.mp4` when multiple floors exist.
- **border_px**: border size between stitched floors.
- **label_font_size**: font size for floor labels in stitched timelapses.

### Timelapse API

Generate a timelapse on demand:

```
GET /api/timelapse/{floor_id}?window=48h&sampling_seconds=120&target_duration_seconds=60&fps=10&stitch=true
```

- **window**: duration string (`30m`, `12h`, `2d`) or hours as a number.
- **sampling_seconds**: base sampling interval in seconds.
- **target_duration_seconds**: target length of the output video.
- **fps**: frames per second.
- **stitch**: set `true` to stitch all floors when `floor_id=all`.

---

# Floorplan schema

Floorplans live at `/data/floorplans/{floor_id}.json`.

```json
{
  "version": 1,
  "floor_id": "floor1",
  "canvas": {"width": 1600, "height": 1000},
  "scale": {
    "mode": "calibrated",
    "px_per_meter": 100,
    "calibration": {"p1": [0, 0], "p2": [100, 0], "distance_m": 1}
  },
  "walls": [
    {"id": "wall_1", "points": [[20, 20], [200, 20]]}
  ],
  "doors": [
    {
      "id": "door_1",
      "segment": [[200, 20], [240, 20]],
      "entity_id": "binary_sensor.front_door",
      "mapping": {
        "open_values": ["on", "open"],
        "closed_values": ["off", "closed"],
        "unknown_as": "closed"
      },
      "open": false,
      "open_resistance": 2,
      "closed_resistance": 500
    }
  ],
  "sensors": [
    {
      "id": "sensor_1",
      "entity": "sensor.living_room_temperature",
      "pos": [120, 80],
      "label": "Living",
      "weight": 1
    }
  ],
  "thermostats": [
    {
      "id": "thermo_1",
      "pos": [300, 140],
      "temperature_entity": "sensor.living_room_temperature",
      "setpoint_entity": "input_number.living_setpoint",
      "mode_entity": "climate.living_room",
      "device_label": "Living Room"
    }
  ],
  "stairwell": {
    "id": "stair_1",
    "polygon": [[500, 400], [600, 400], [600, 520], [500, 520]],
    "link_to_floor_id": "floor2",
    "coupling": 0.05
  },
  "render": {
    "temp_range_f": {"min": 60, "max": 80},
    "overlay_alpha": 0.6,
    "scale_min_mode": "absolute",
    "scale_max_mode": "absolute",
    "auto_crop": true,
    "crop_padding": 30,
    "exterior_margin": 20,
    "show_walls": true,
    "show_labels": true,
    "show_legend": true,
    "show_timestamp": true
  },
  "solver": {
    "grid_w": 400,
    "grid_h": 250,
    "iterations": 200,
    "sensor_pull": 0.15,
    "wall_resistance": 5000,
    "default_passage_resistance": 2
  }
}
```

### Render settings (`render`)

- **temp_range_f.min / temp_range_f.max**: absolute temperature range (F).  
- **scale_min_mode / scale_max_mode**:
  - `absolute`: use the values from `temp_range_f`.
  - `relative`: use the live grid’s min/max values.
  - You can mix modes to clamp just one end of the scale.
- **overlay_alpha**: transparency of the heatmap overlay (0–1).
- **auto_crop**: trims extra blank space around the floorplan when rendering.
- **crop_padding**: extra pixels to keep around the geometry when auto-cropping.
- **exterior_margin**: padding outside the cropped floorplan used to draw the legend and timestamp.
- **show_walls / show_labels / show_legend / show_timestamp**: toggles for overlay elements.

> The heatmap is masked to the floorplan hull so areas outside the exterior walls are not colorized.
> The solver clamps the final grid so values do not fall below the coldest sensor reading on the floor.

### Solver settings (`solver`)

- **grid_w / grid_h**: solver grid resolution. Lower values render faster.
- **iterations**: diffusion iterations. Higher values look smoother but render slower.
- **sensor_pull**: strength of sensor influence.
- **wall_resistance / default_passage_resistance**: resistance values for walls and openings.

---

# API endpoints

- `GET /editor` (static frontend)
- `GET /api/floorplans`
- `GET /api/floorplans/{floor_id}`
- `PUT /api/floorplans/{floor_id}`
- `POST /api/floorplans/{floor_id}/validate`
- `POST /api/ha/test`
- `GET /render/live/{floor_id}.png`
- `GET /render/live/{floor_id}.json`
- `GET /render/timelapse.gif?floor=&window=&step=&width=`

---

# Home Assistant card examples

### Picture entity

```yaml
type: picture-entity
entity: sensor.living_room_temperature
image: http://YOUR_HOST:8000/render/live/floor1.png
name: Floor 1 Heatmap
```

### Markdown (PNG refresh)

```yaml
type: markdown
content: >-
  ![Heatmap](http://YOUR_HOST:8000/render/live/floor1.png?ts={{ now().timestamp() }})
```

### Timelapse

```yaml
type: markdown
content: >-
  ![Timelapse](http://YOUR_HOST:8000/render/timelapse.gif?floor=floor1&window=86400&step=900&width=800)
```
