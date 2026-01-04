# TempMap Renderer

TempMap Renderer is a FastAPI service plus a lightweight browser editor for building multi-floor temperature maps and rendering live heatmap images for Home Assistant dashboards.

It runs as a single service, stores floorplans on disk, polls Home Assistant for sensor states, and renders PNGs (and timelapses) on demand.

---

## What it does (in plain English)

1. **You draw your floorplan** in a simple canvas editor (`/editor`).
2. **You bind sensors** to spots on the floorplan.
3. **The backend polls Home Assistant** for those sensor values.
4. **The renderer solves a heatmap** and serves it as an image (`/render/live/{floor_id}.png`).
5. **Home Assistant displays the image** on your dashboard.

Everything is stored on disk (defaults to `/data`) so you can restart the container without losing your work.

---

## Quick start (local)

```bash
cp backend/config.example.yaml backend/config.yaml
python -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
```

Open the editor at:

```
http://localhost:8000/editor
```

Floorplans are stored under:

```
/data/floorplans/{floor_id}.json
```

---

## Docker (recommended)

```bash
docker build -f docker/Dockerfile -t tempmap-renderer .
docker run --rm -p 8000:8000 -v $(pwd)/data:/data tempmap-renderer
```

Then visit:

```
http://localhost:8000/editor
```

> **Note:** In the container, the config path is `/app/backend/config.yaml` and the data path is `/data`.

---

## Unraid

Import the template in **Unraid → Docker → Add Container → Template**.

If you keep `config.yaml` in Unraid appdata, bind-mount it to:

```
/app/backend/config.yaml
```

---

## How to use the editor (step by step)

1. **Open the editor** at `/editor`.
2. **Create a floor** (top-left menu).
3. **Draw walls and doors** using the toolbar.
4. **Drop sensors** where your physical sensors live.
5. **Name each sensor** and assign a Home Assistant entity ID.
6. **Save the floorplan**.

When saved, the floorplan becomes a JSON file at `/data/floorplans/{floor_id}.json`.

---

## Configure Home Assistant access

Copy the example config and edit the Home Assistant section:

```yaml
home_assistant:
  base_url: http://homeassistant.local:8123
  token: YOUR_LONG_LIVED_TOKEN
  refresh_seconds: 15
```

**Tips:**
- `base_url` must be reachable from the renderer (container → HA).
- Use a **long‑lived access token** from your Home Assistant profile.
- `refresh_seconds` controls how often sensor states are pulled.

---

## Render a live heatmap

Once you have a floorplan, the live image URL is:

```
GET /render/live/{floor_id}.png
```

Example (floor id = `floor1`):

```
http://YOUR_HOST:8000/render/live/floor1.png
```

The renderer recalculates the heatmap each time the image is requested (using cached sensor values).

---

## Home Assistant usage examples

### Picture entity

```yaml
type: picture-entity
entity: sensor.living_room_temperature
image: http://YOUR_HOST:8000/render/live/floor1.png
name: Floor 1 Heatmap
```

### Markdown card (force refresh)

```yaml
type: markdown
content: >-
  ![Heatmap](http://YOUR_HOST:8000/render/live/floor1.png?ts={{ now().timestamp() }})
```

### Timelapse GIF

```yaml
type: markdown
content: >-
  ![Timelapse](http://YOUR_HOST:8000/render/timelapse.gif?floor=floor1&window=86400&step=900&width=800)
```

---

## Configuration reference (backend/config.yaml)

The backend reads config once at startup. Use `backend/config.example.yaml` as a base.

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

### `data`

- `path`: base directory for floorplans, frames, and timelapses. Defaults to `/data`.
- Override with `TEMP_MAP_DATA_PATH` if you need a different path.

### `render`

Defaults used for **new** floorplans created in the editor.

- `default_grid`: solver grid size (lower = faster, higher = smoother).
- `default_legend`: default min/max temperature range for the legend.

### `timelapse`

Controls rolling timelapse generation and on-demand timelapses.

- `frame_retention_hours`: how long to keep cached PNG frames.
- `window_hours`: rolling timelapse time window.
- `sampling_seconds`: base sampling cadence for frames.
- `target_duration_seconds`: target output video length.
- `fps`: frames per second for the output MP4.
- `output_path`: directory for rendered MP4s.
- `rolling_enabled`: enable periodic rolling generation.
- `rolling_interval_seconds`: how often to regenerate rolling timelapses.
- `stitch_multi_floor`: generate a combined `all/rolling.mp4`.
- `border_px`: padding between floors in stitched outputs.
- `label_font_size`: font size for floor labels.

---

## Timelapse API

Generate a timelapse on demand:

```
GET /api/timelapse/{floor_id}?window=48h&sampling_seconds=120&target_duration_seconds=60&fps=10&stitch=true
```

Parameters:
- `window`: duration string (`30m`, `12h`, `2d`) or hours as a number.
- `sampling_seconds`: base sampling interval in seconds.
- `target_duration_seconds`: target length of the output video.
- `fps`: frames per second.
- `stitch`: set `true` to stitch all floors when `floor_id=all`.

---

## API endpoints (quick list)

- `GET /editor`
- `GET /api/floorplans`
- `GET /api/floorplans/{floor_id}`
- `PUT /api/floorplans/{floor_id}`
- `POST /api/floorplans/{floor_id}/validate`
- `POST /api/ha/test`
- `GET /render/live/{floor_id}.png`
- `GET /render/live/{floor_id}.json`
- `GET /render/timelapse.gif?floor=&window=&step=&width=`

---

## FAQ

### Where are floorplans stored?

They are stored as JSON files at:

```
/data/floorplans/{floor_id}.json
```

### Does it work without Home Assistant?

The editor works without Home Assistant, but live rendering requires sensor values. You can still render if your floorplan defines fallback values.

### Why is my heatmap cropped?

Auto-cropping trims blank space around your floorplan. You can disable it or adjust padding in each floorplan’s `render` section.

---

## Repository layout

- `backend/` — FastAPI app, rendering logic, configuration
- `frontend/` — Vanilla JS canvas editor
- `docker/` — Dockerfile for container builds
- `unraid/` — Unraid template
