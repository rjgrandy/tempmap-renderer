# TempMap Renderer

A lightweight FastAPI service plus vanilla JS canvas editor for visualizing Home Assistant temperature maps.

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

Place floorplans under `/data/floorplans/{floor_id}.json`.

## Docker

```bash
docker build -f docker/Dockerfile -t tempmap-renderer .
docker run --rm -p 8000:8000 -v $(pwd)/data:/data tempmap-renderer
```

## Floorplan schema (version 1)

Stored at `/data/floorplans/{floor_id}.json`.

```json
{
  "version": 1,
  "canvas": {"width": 1600, "height": 1000},
  "scale": {"pixels_per_unit": 10, "unit": "ft"},
  "walls": [
    {"points": [{"x": 20, "y": 20}, {"x": 200, "y": 20}]}
  ],
  "doors": [
    {
      "segment": {"a": {"x": 200, "y": 20}, "b": {"x": 240, "y": 20}},
      "entity_id": "binary_sensor.front_door",
      "mapping": {"open_states": ["on", "open"], "closed_states": ["off", "closed"]}
    }
  ],
  "sensors": [
    {"pos": {"x": 120, "y": 80}, "entity_id": "sensor.living_room_temperature"}
  ],
  "thermostats": [
    {
      "pos": {"x": 300, "y": 140},
      "temperature_entity": "sensor.living_room_temperature",
      "setpoint_entity": "input_number.living_setpoint",
      "climate_entity": "climate.living_room"
    }
  ],
  "stairwells": [
    {
      "polygon": [
        {"x": 500, "y": 400},
        {"x": 600, "y": 400},
        {"x": 600, "y": 520},
        {"x": 500, "y": 520}
      ],
      "target_floor_id": "upstairs"
    }
  ],
  "solver": {
    "grid_width": 400,
    "grid_height": 250,
    "iterations": 200,
    "wall_resistance": 5000,
    "door_open_resistance": 2,
    "door_closed_resistance": 500,
    "sensor_pull": 0.15,
    "coupling": 0.05
  },
  "render": {
    "legend_min_f": 60,
    "legend_max_f": 80
  }
}
```

### Endpoints

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
image: http://YOUR_HOST:8000/render/live/main_floor.png
name: Main Floor Heatmap
```

### Markdown (PNG refresh)

```yaml
type: markdown
content: >-
  ![Heatmap](http://YOUR_HOST:8000/render/live/main_floor.png?ts={{ now().timestamp() }})
```

### Timelapse

```yaml
type: markdown
content: >-
  ![Timelapse](http://YOUR_HOST:8000/render/timelapse.gif?floor=main_floor&window=86400&step=900&width=800)
```
