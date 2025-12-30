# TempMap Renderer

A lightweight FastAPI service plus a vanilla JS canvas editor for building multi-floor temperature maps.

## Stack
- **Backend:** FastAPI (Python)
- **Frontend:** Vanilla JS canvas editor (no framework)
- **Container:** Unraid-friendly Dockerfile
- **Data:** stored under `/data` (mounted volume)

## Quick start

```bash
cp backend/config.example.yaml backend/config.yaml
python -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
```

Open the editor at http://localhost:8000/editor.

Floorplans are saved under `/data/floorplans/{floor_id}.json`.

## Docker

```bash
docker build -f docker/Dockerfile -t tempmap-renderer .
docker run --rm -p 8000:8000 -v $(pwd)/data:/data tempmap-renderer
```

Then visit http://localhost:8000/editor.

## Unraid template

Import the template in Unraid via **Unraid > Docker > Add Container > Template** dropdown, then select the TempMap Renderer template from the list.

## Floorplan builder usage

1. Open `/editor` and select **Floor 1**.
2. Choose **Wall** and click to add vertices. Double-click to finish.
3. Choose **Sensor** or **Thermostat** and click to place markers.
4. Use **Door** and click two points on a wall to add a door segment.
5. Click **Save** to persist the floorplan.
6. View rendered output at `/render/live/floor1.png` (or `/render/live/floor2.png`).

## Floorplan schema (version 1)

Stored at `/data/floorplans/{floor_id}.json`.

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

### Endpoints

- `GET /editor` (static frontend)
- `GET /api/floorplans`
- `GET /api/floorplans/{floor_id}`
- `PUT /api/floorplans/{floor_id}`
- `POST /api/floorplans/{floor_id}/validate`
- `POST /api/ha/test`
- `GET /render/live/{floor_id}.png`
- `GET /render/live/{floor_id}.json`
- `GET /render/timelapse.gif?floor=&window=&step=&width=`

## Home Assistant card examples

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
