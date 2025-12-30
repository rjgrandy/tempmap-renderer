from __future__ import annotations

import json
import os
import threading
import time
import io
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import requests
import yaml
from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError, conlist
from PIL import Image, ImageDraw, ImageFont

DATA_ENV = "TEMP_MAP_DATA_PATH"


class CanvasSize(BaseModel):
    width: int = 1600
    height: int = 1000


Point = conlist(float, min_length=2, max_length=2)
Segment = conlist(Point, min_length=2, max_length=2)


class Calibration(BaseModel):
    p1: Point = Field(default_factory=lambda: [0.0, 0.0])
    p2: Point = Field(default_factory=lambda: [100.0, 0.0])
    distance_m: float = 1.0


class ScaleCalibration(BaseModel):
    mode: str = "calibrated"
    px_per_meter: float = 100.0
    calibration: Calibration = Field(default_factory=Calibration)


class Wall(BaseModel):
    id: str
    points: List[Point]


class DoorMapping(BaseModel):
    open_values: List[str] = Field(default_factory=lambda: ["on", "open"])
    closed_values: List[str] = Field(default_factory=lambda: ["off", "closed"])
    unknown_as: str = "closed"


class Door(BaseModel):
    id: str
    segment: Segment
    entity_id: Optional[str] = None
    mapping: DoorMapping = Field(default_factory=DoorMapping)
    open: bool = False
    open_resistance: Optional[float] = None
    closed_resistance: Optional[float] = None


class Sensor(BaseModel):
    id: str
    entity: Optional[str] = None
    pos: Point
    label: str = ""
    weight: float = 1.0


class Thermostat(BaseModel):
    id: str
    pos: Point
    temperature_entity: str
    setpoint_entity: str
    mode_entity: Optional[str] = None
    device_label: str = ""


class Stairwell(BaseModel):
    id: str
    polygon: List[Point]
    link_to_floor_id: str
    coupling: float = 0.05


class SolverParams(BaseModel):
    grid_w: int = 400
    grid_h: int = 250
    iterations: int = 200
    sensor_pull: float = 0.15
    wall_resistance: float = 5000.0
    default_passage_resistance: float = 2.0


class TemperatureRange(BaseModel):
    min: float = 60.0
    max: float = 80.0


class RenderParams(BaseModel):
    temp_range_f: TemperatureRange = Field(default_factory=TemperatureRange)
    overlay_alpha: float = 0.6
    show_walls: bool = True
    show_labels: bool = True
    show_legend: bool = True
    show_timestamp: bool = True


class FloorplanV1(BaseModel):
    version: int = 1
    floor_id: str = "floor1"
    canvas: CanvasSize = Field(default_factory=CanvasSize)
    scale: ScaleCalibration = Field(default_factory=ScaleCalibration)
    walls: List[Wall] = Field(default_factory=list)
    doors: List[Door] = Field(default_factory=list)
    sensors: List[Sensor] = Field(default_factory=list)
    thermostats: List[Thermostat] = Field(default_factory=list)
    stairwell: Optional[Stairwell] = None
    solver: SolverParams = Field(default_factory=SolverParams)
    render: RenderParams = Field(default_factory=RenderParams)

    class Config:
        extra = "forbid"


class AppConfig(BaseModel):
    data_path: str
    ha_base_url: str
    ha_token: str
    refresh_seconds: int
    default_grid: Tuple[int, int]
    default_legend: Tuple[float, float]


@dataclass
class EntityState:
    state: str
    last_updated: str
    last_changed: str


app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

CONFIG_PATH = Path(__file__).with_name("config.yaml")

ha_lock = threading.Lock()
ha_states: Dict[str, EntityState] = {}
ha_missing: Dict[str, str] = {}
ha_unavailable: Dict[str, str] = {}
ha_last_poll: Optional[str] = None

latest_frames_lock = threading.Lock()


def load_config() -> AppConfig:
    config_data = {}
    if CONFIG_PATH.exists():
        with CONFIG_PATH.open("r", encoding="utf-8") as handle:
            config_data = yaml.safe_load(handle) or {}
    data_path = config_data.get("data", {}).get("path", "/data")
    data_path = os.getenv(DATA_ENV, data_path)
    ha_config = config_data.get("home_assistant", {})
    render_config = config_data.get("render", {})
    return AppConfig(
        data_path=data_path,
        ha_base_url=ha_config.get("base_url", ""),
        ha_token=ha_config.get("token", ""),
        refresh_seconds=int(ha_config.get("refresh_seconds", 15)),
        default_grid=(
            int(render_config.get("default_grid", {}).get("width", 400)),
            int(render_config.get("default_grid", {}).get("height", 250)),
        ),
        default_legend=(
            float(render_config.get("default_legend", {}).get("min_f", 60)),
            float(render_config.get("default_legend", {}).get("max_f", 80)),
        ),
    )


config = load_config()


@app.on_event("startup")
def startup() -> None:
    data_dir = Path(config.data_path)
    (data_dir / "floorplans").mkdir(parents=True, exist_ok=True)
    (data_dir / "frames").mkdir(parents=True, exist_ok=True)
    thread = threading.Thread(target=ha_poll_loop, daemon=True)
    thread.start()


@app.get("/api/floorplans")
def list_floorplans() -> Dict[str, List[str]]:
    floor_dir = Path(config.data_path) / "floorplans"
    floor_ids = sorted([path.stem for path in floor_dir.glob("*.json")])
    return {"floorplans": floor_ids}


@app.get("/api/floorplans/{floor_id}")
def get_floorplan(floor_id: str) -> Dict:
    return load_floorplan_file(floor_id)


@app.put("/api/floorplans/{floor_id}")
def put_floorplan(floor_id: str, payload: Dict) -> Dict:
    validated = parse_floorplan(payload)
    save_floorplan_file(floor_id, validated)
    return validated


@app.post("/api/floorplans/{floor_id}/validate")
def validate_floorplan(floor_id: str, payload: Dict) -> Dict:
    validated = parse_floorplan(payload)
    return {"floor_id": floor_id, "valid": True, "floorplan": validated}


@app.post("/api/ha/test")
def test_ha() -> Dict:
    if not config.ha_base_url or not config.ha_token:
        raise HTTPException(status_code=400, detail="Home Assistant config missing")
    url = f"{config.ha_base_url.rstrip('/')}/api/"
    headers = {"Authorization": f"Bearer {config.ha_token}"}
    response = requests.get(url, headers=headers, timeout=10)
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail=response.text)
    return {"status": "ok"}


@app.get("/render/live/{floor_id}.png")
def render_live_png(floor_id: str) -> Response:
    image = render_floorplan(floor_id)
    image_bytes = image_to_png_bytes(image)
    return Response(content=image_bytes, media_type="image/png", headers={"Cache-Control": "no-store"})


app.mount("/editor", StaticFiles(directory=Path(__file__).parents[1] / "frontend", html=True), name="frontend")


@app.get("/render/live/{floor_id}.json")
def render_live_debug(floor_id: str) -> Dict:
    floorplans = load_all_floorplans()
    if floor_id not in floorplans:
        raise HTTPException(status_code=404, detail="Floorplan not found")
    grids, metadata = solve_all_floorplans(floorplans)
    grid = grids.get(floor_id)
    if grid is None:
        raise HTTPException(status_code=404, detail="Floorplan not found")
    stats = {
        "min": float(np.min(grid)),
        "max": float(np.max(grid)),
        "mean": float(np.mean(grid)),
    }
    with ha_lock:
        states = {entity: state.__dict__ for entity, state in ha_states.items()}
        missing = dict(ha_missing)
        unavailable = dict(ha_unavailable)
        last_poll = ha_last_poll
    return {
        "floor_id": floor_id,
        "timestamp": now_iso(),
        "grid_stats": stats,
        "entities": states,
        "missing_entities": missing,
        "unavailable_entities": unavailable,
        "metadata": metadata.get(floor_id, {}),
        "ha_last_poll": last_poll,
    }


@app.get("/render/timelapse.gif")
def render_timelapse(floor: str, window: int = 3600, step: int = 60, width: Optional[int] = None) -> Response:
    frames_dir = Path(config.data_path) / "frames" / floor
    if not frames_dir.exists():
        raise HTTPException(status_code=404, detail="No frames for floor")
    now_ts = int(time.time())
    start_ts = now_ts - window
    frame_files = sorted(frames_dir.glob("*.png"))
    selected = []
    last_ts = None
    for path in frame_files:
        try:
            ts = int(path.stem)
        except ValueError:
            continue
        if ts < start_ts or ts > now_ts:
            continue
        if last_ts is None or ts - last_ts >= step:
            selected.append(path)
            last_ts = ts
    if not selected:
        raise HTTPException(status_code=404, detail="No frames within window")
    images = []
    for path in selected:
        img = Image.open(path)
        if width:
            ratio = width / img.width
            img = img.resize((width, int(img.height * ratio)), Image.Resampling.LANCZOS)
        images.append(img.convert("P", palette=Image.Palette.ADAPTIVE))
    output_path = Path(config.data_path) / "frames" / floor / "timelapse.gif"
    images[0].save(
        output_path,
        save_all=True,
        append_images=images[1:],
        duration=500,
        loop=0,
    )
    return FileResponse(output_path, media_type="image/gif")


def load_floorplan_file(floor_id: str) -> Dict:
    path = Path(config.data_path) / "floorplans" / f"{floor_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Floorplan not found")
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def save_floorplan_file(floor_id: str, payload: Dict) -> None:
    path = Path(config.data_path) / "floorplans" / f"{floor_id}.json"
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)


def parse_floorplan(payload: Dict) -> Dict:
    try:
        parsed = FloorplanV1.parse_obj(payload)
    except ValidationError as error:
        raise HTTPException(status_code=422, detail=error.errors()) from error
    return json.loads(parsed.json())



def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def point_xy(point: Point) -> Tuple[float, float]:
    return float(point[0]), float(point[1])



def gather_entities(floorplans: Dict[str, Dict]) -> List[str]:
    entities = []
    for floorplan in floorplans.values():
        for door in floorplan.get("doors", []):
            entities.append(door.get("entity_id"))
        for sensor in floorplan.get("sensors", []):
            entities.append(sensor.get("entity"))
        for thermo in floorplan.get("thermostats", []):
            entities.append(thermo.get("temperature_entity"))
            entities.append(thermo.get("setpoint_entity"))
            mode_entity = thermo.get("mode_entity")
            if mode_entity:
                entities.append(mode_entity)
    return sorted({e for e in entities if e})



def ha_poll_loop() -> None:
    global ha_last_poll
    while True:
        try:
            floorplans = load_all_floorplans()
            entities = gather_entities(floorplans)
            if config.ha_base_url and config.ha_token and entities:
                poll_home_assistant(entities)
            render_frames_for_floorplans(floorplans)
        except Exception:
            pass
        ha_last_poll = now_iso()
        time.sleep(config.refresh_seconds)



def load_all_floorplans() -> Dict[str, Dict]:
    floor_dir = Path(config.data_path) / "floorplans"
    floorplans = {}
    for path in floor_dir.glob("*.json"):
        with path.open("r", encoding="utf-8") as handle:
            floorplans[path.stem] = json.load(handle)
    return floorplans



def poll_home_assistant(entities: List[str]) -> None:
    headers = {
        "Authorization": f"Bearer {config.ha_token}",
        "Content-Type": "application/json",
    }
    base = config.ha_base_url.rstrip("/")
    missing = {}
    unavailable = {}
    states: Dict[str, EntityState] = {}
    for entity in entities:
        url = f"{base}/api/states/{entity}"
        try:
            response = requests.get(url, headers=headers, timeout=10)
        except requests.RequestException as error:
            missing[entity] = str(error)
            continue
        if response.status_code != 200:
            missing[entity] = response.text
            continue
        payload = response.json()
        state = payload.get("state", "unknown")
        if state in {"unknown", "unavailable"}:
            unavailable[entity] = state
        states[entity] = EntityState(
            state=state,
            last_updated=payload.get("last_updated", ""),
            last_changed=payload.get("last_changed", ""),
        )
    with ha_lock:
        ha_states.clear()
        ha_states.update(states)
        ha_missing.clear()
        ha_missing.update(missing)
        ha_unavailable.clear()
        ha_unavailable.update(unavailable)



def render_frames_for_floorplans(floorplans: Dict[str, Dict]) -> None:
    grids, metadata = solve_all_floorplans(floorplans)
    for floor_id, floorplan in floorplans.items():
        grid = grids.get(floor_id)
        if grid is None:
            continue
        image = render_floorplan_image(floor_id, floorplan, grid, metadata.get(floor_id, {}))
        save_frame(floor_id, image)
    cleanup_frames()



def save_frame(floor_id: str, image: Image.Image) -> None:
    frames_dir = Path(config.data_path) / "frames" / floor_id
    frames_dir.mkdir(parents=True, exist_ok=True)
    timestamp = int(time.time())
    path = frames_dir / f"{timestamp}.png"
    image.save(path)



def cleanup_frames() -> None:
    cutoff = time.time() - (8 * 24 * 60 * 60)
    frames_root = Path(config.data_path) / "frames"
    for floor_dir in frames_root.glob("*"):
        for path in floor_dir.glob("*.png"):
            try:
                ts = int(path.stem)
            except ValueError:
                continue
            if ts < cutoff:
                path.unlink(missing_ok=True)



def render_floorplan(floor_id: str) -> Image.Image:
    floorplans = load_all_floorplans()
    if floor_id not in floorplans:
        raise HTTPException(status_code=404, detail="Floorplan not found")
    grids, metadata = solve_all_floorplans(floorplans)
    grid = grids.get(floor_id)
    if grid is None:
        raise HTTPException(status_code=404, detail="Floorplan not found")
    return render_floorplan_image(floor_id, floorplans[floor_id], grid, metadata.get(floor_id, {}))



def solve_all_floorplans(floorplans: Dict[str, Dict]) -> Tuple[Dict[str, np.ndarray], Dict[str, Dict]]:
    parsed = {}
    for floor_id, payload in floorplans.items():
        parsed[floor_id] = FloorplanV1.parse_obj(payload)
    grids = {floor_id: initialize_grid(fp) for floor_id, fp in parsed.items()}
    metadata: Dict[str, Dict] = {floor_id: {} for floor_id in parsed}
    max_iterations = max((fp.solver.iterations for fp in parsed.values()), default=0)
    for _ in range(max_iterations):
        for floor_id, fp in parsed.items():
            grids[floor_id] = diffuse_grid(fp, grids[floor_id])
        apply_stairwell_coupling(parsed, grids)
    for floor_id, fp in parsed.items():
        metadata[floor_id] = {
            "grid_width": fp.solver.grid_w,
            "grid_height": fp.solver.grid_h,
            "iterations": fp.solver.iterations,
        }
    return grids, metadata



def initialize_grid(fp: FloorplanV1) -> np.ndarray:
    temps = []
    with ha_lock:
        for sensor in fp.sensors:
            state = ha_states.get(sensor.entity) if sensor.entity else None
            if state:
                temps.append(parse_float(state.state))
    default_temp = float(np.mean(temps)) if temps else 70.0
    grid = np.full((fp.solver.grid_h, fp.solver.grid_w), default_temp, dtype=float)
    return grid



def parse_float(value: str) -> float:
    try:
        return float(value)
    except ValueError:
        return 0.0



def diffuse_grid(fp: FloorplanV1, grid: np.ndarray) -> np.ndarray:
    height, width = grid.shape
    base_conductance = 1.0
    h_edges = np.full((height, width - 1), base_conductance)
    v_edges = np.full((height - 1, width), base_conductance)
    rasterize_walls(fp, h_edges, v_edges)
    rasterize_doors(fp, h_edges, v_edges)
    new_grid = grid.copy()
    for y in range(height):
        for x in range(width):
            neighbors = []
            weights = []
            if x > 0:
                weights.append(h_edges[y, x - 1])
                neighbors.append(grid[y, x - 1])
            if x < width - 1:
                weights.append(h_edges[y, x])
                neighbors.append(grid[y, x + 1])
            if y > 0:
                weights.append(v_edges[y - 1, x])
                neighbors.append(grid[y - 1, x])
            if y < height - 1:
                weights.append(v_edges[y, x])
                neighbors.append(grid[y + 1, x])
            total_weight = sum(weights)
            if total_weight > 0:
                new_grid[y, x] = float(np.dot(neighbors, weights) / total_weight)
    apply_sensor_pull(fp, new_grid)
    return new_grid



def rasterize_walls(fp: FloorplanV1, h_edges: np.ndarray, v_edges: np.ndarray) -> None:
    resistance = fp.solver.wall_resistance
    conductance = 1.0 / resistance
    for wall in fp.walls:
        points = wall.points
        for idx in range(len(points) - 1):
            mark_segment(fp, points[idx], points[idx + 1], h_edges, v_edges, conductance)



def rasterize_doors(fp: FloorplanV1, h_edges: np.ndarray, v_edges: np.ndarray) -> None:
    for door in fp.doors:
        resistance = door_resistance(fp, door)
        conductance = 1.0 / resistance
        mark_segment(fp, door.segment[0], door.segment[1], h_edges, v_edges, conductance)



def door_resistance(fp: FloorplanV1, door: Door) -> float:
    door_open = door.open
    if door.entity_id:
        with ha_lock:
            state = ha_states.get(door.entity_id)
        if state:
            if state.state in door.mapping.open_values:
                door_open = True
            elif state.state in door.mapping.closed_values:
                door_open = False
            else:
                door_open = door.mapping.unknown_as == "open"
    if door_open:
        return door.open_resistance or fp.solver.default_passage_resistance
    return door.closed_resistance or fp.solver.wall_resistance


def mark_segment(
    fp: FloorplanV1,
    a: Point,
    b: Point,
    h_edges: np.ndarray,
    v_edges: np.ndarray,
    conductance: float,
) -> None:
    grid_w = fp.solver.grid_w
    grid_h = fp.solver.grid_h
    ax, ay = point_xy(a)
    bx, by = point_xy(b)
    x0 = int(ax / fp.canvas.width * grid_w)
    y0 = int(ay / fp.canvas.height * grid_h)
    x1 = int(bx / fp.canvas.width * grid_w)
    y1 = int(by / fp.canvas.height * grid_h)
    steps = max(abs(x1 - x0), abs(y1 - y0), 1) * 2
    prev = (x0, y0)
    for step in range(1, steps + 1):
        t = step / steps
        x = int(x0 + (x1 - x0) * t)
        y = int(y0 + (y1 - y0) * t)
        current = (min(max(x, 0), grid_w - 1), min(max(y, 0), grid_h - 1))
        if current != prev:
            mark_edge(prev, current, h_edges, v_edges, conductance)
            prev = current



def mark_edge(
    a: Tuple[int, int],
    b: Tuple[int, int],
    h_edges: np.ndarray,
    v_edges: np.ndarray,
    conductance: float,
) -> None:
    ax, ay = a
    bx, by = b
    if ay == by and ax != bx:
        x = min(ax, bx)
        if 0 <= ay < h_edges.shape[0] and 0 <= x < h_edges.shape[1]:
            h_edges[ay, x] = min(h_edges[ay, x], conductance)
    elif ax == bx and ay != by:
        y = min(ay, by)
        if 0 <= y < v_edges.shape[0] and 0 <= ax < v_edges.shape[1]:
            v_edges[y, ax] = min(v_edges[y, ax], conductance)



def apply_sensor_pull(fp: FloorplanV1, grid: np.ndarray) -> None:
    for sensor in fp.sensors:
        with ha_lock:
            state = ha_states.get(sensor.entity) if sensor.entity else None
        if not state:
            continue
        temp = parse_float(state.state)
        sx, sy = point_xy(sensor.pos)
        gx = int(sx / fp.canvas.width * fp.solver.grid_w)
        gy = int(sy / fp.canvas.height * fp.solver.grid_h)
        gx = min(max(gx, 0), fp.solver.grid_w - 1)
        gy = min(max(gy, 0), fp.solver.grid_h - 1)
        pull = min(max(fp.solver.sensor_pull * sensor.weight, 0.0), 1.0)
        grid[gy, gx] = (1 - pull) * grid[gy, gx] + pull * temp



def apply_stairwell_coupling(parsed: Dict[str, FloorplanV1], grids: Dict[str, np.ndarray]) -> None:
    for floor_id, fp in parsed.items():
        stair = fp.stairwell
        if not stair:
            continue
        target = stair.link_to_floor_id
        if target not in grids:
            continue
        source_grid = grids[floor_id]
        target_grid = grids[target]
        mask = polygon_mask(fp, stair.polygon)
        coupling = stair.coupling
        source_grid[mask] = source_grid[mask] + coupling * (target_grid[mask] - source_grid[mask])
        target_grid[mask] = target_grid[mask] + coupling * (source_grid[mask] - target_grid[mask])
        grids[floor_id] = source_grid
        grids[target] = target_grid



def polygon_mask(fp: FloorplanV1, polygon: List[Point]) -> np.ndarray:
    height, width = fp.solver.grid_h, fp.solver.grid_w
    xs = [point_xy(p)[0] / fp.canvas.width * width for p in polygon]
    ys = [point_xy(p)[1] / fp.canvas.height * height for p in polygon]
    mask = np.zeros((height, width), dtype=bool)
    if not xs or not ys:
        return mask
    min_x = int(max(min(xs), 0))
    max_x = int(min(max(xs), width - 1))
    min_y = int(max(min(ys), 0))
    max_y = int(min(max(ys), height - 1))
    for y in range(min_y, max_y + 1):
        for x in range(min_x, max_x + 1):
            if point_in_polygon(x + 0.5, y + 0.5, xs, ys):
                mask[y, x] = True
    return mask



def point_in_polygon(x: float, y: float, xs: List[float], ys: List[float]) -> bool:
    inside = False
    j = len(xs) - 1
    for i in range(len(xs)):
        xi, yi = xs[i], ys[i]
        xj, yj = xs[j], ys[j]
        intersect = ((yi > y) != (yj > y)) and (
            x < (xj - xi) * (y - yi) / (yj - yi + 1e-9) + xi
        )
        if intersect:
            inside = not inside
        j = i
    return inside



def render_floorplan_image(
    floor_id: str,
    payload: Dict,
    grid: np.ndarray,
    metadata: Dict,
) -> Image.Image:
    fp = FloorplanV1.parse_obj(payload)
    canvas = Image.new("RGBA", (fp.canvas.width, fp.canvas.height), (20, 20, 20, 255))
    heatmap = render_heatmap(
        grid,
        fp.render.temp_range_f.min,
        fp.render.temp_range_f.max,
        fp.render.overlay_alpha,
        canvas.size,
    )
    canvas = Image.alpha_composite(canvas, heatmap)
    draw = ImageDraw.Draw(canvas)
    if fp.render.show_walls:
        draw_walls(draw, fp)
    draw_sensors(draw, fp)
    draw_thermostats(draw, fp)
    if fp.render.show_legend:
        draw_legend(draw, fp)
    if fp.render.show_timestamp:
        draw_timestamp(draw)
    return canvas.convert("RGB")



def render_heatmap(
    grid: np.ndarray,
    min_f: float,
    max_f: float,
    overlay_alpha: float,
    size: Tuple[int, int],
) -> Image.Image:
    norm = np.clip((grid - min_f) / (max_f - min_f + 1e-6), 0, 1)
    colors = np.zeros((grid.shape[0], grid.shape[1], 4), dtype=np.uint8)
    colors[..., 0] = (255 * norm).astype(np.uint8)
    colors[..., 2] = (255 * (1 - norm)).astype(np.uint8)
    colors[..., 1] = (128 * (1 - np.abs(norm - 0.5) * 2)).astype(np.uint8)
    colors[..., 3] = int(255 * min(max(overlay_alpha, 0.0), 1.0))
    image = Image.fromarray(colors, mode="RGBA")
    return image.resize(size, resample=Image.Resampling.BILINEAR)



def draw_walls(draw: ImageDraw.ImageDraw, fp: FloorplanV1) -> None:
    for wall in fp.walls:
        points = [point_xy(p) for p in wall.points]
        draw.line(points, fill=(230, 230, 230), width=3)
    for door in fp.doors:
        points = [point_xy(door.segment[0]), point_xy(door.segment[1])]
        draw.line(points, fill=(120, 200, 255), width=4)



def draw_sensors(draw: ImageDraw.ImageDraw, fp: FloorplanV1) -> None:
    font = ImageFont.load_default()
    for sensor in fp.sensors:
        x, y = point_xy(sensor.pos)
        draw.ellipse((x - 6, y - 6, x + 6, y + 6), fill=(255, 255, 255))
        if fp.render.show_labels:
            label = sensor.label or sensor.entity or ""
            temperature = format_entity_temperature(sensor.entity)
            if temperature:
                label = f"{label} {temperature}" if label else temperature
            draw.text((x + 8, y - 8), label, fill=(255, 255, 255), font=font)



def draw_thermostats(draw: ImageDraw.ImageDraw, fp: FloorplanV1) -> None:
    font = ImageFont.load_default()
    for thermo in fp.thermostats:
        x, y = point_xy(thermo.pos)
        draw.rectangle((x - 8, y - 8, x + 8, y + 8), outline=(255, 200, 50), width=2)
        if fp.render.show_labels:
            temp = read_entity_state(thermo.temperature_entity)
            setpoint = read_entity_state(thermo.setpoint_entity)
            label = f"{temp} / {setpoint}"
            if thermo.mode_entity:
                mode = read_entity_state(thermo.mode_entity)
                label = f"{label} ({mode})"
            if thermo.device_label:
                label = f"{thermo.device_label} {label}"
            draw.text((x + 10, y - 8), label, fill=(255, 200, 50), font=font)



def read_entity_state(entity_id: str) -> str:
    with ha_lock:
        state = ha_states.get(entity_id)
    return state.state if state else "n/a"


def format_entity_temperature(entity_id: Optional[str]) -> str:
    if not entity_id:
        return ""
    state = read_entity_state(entity_id)
    if state == "n/a":
        return ""
    try:
        value = float(state)
    except ValueError:
        return ""
    return f"{value:.1f}F"



def draw_legend(draw: ImageDraw.ImageDraw, fp: FloorplanV1) -> None:
    font = ImageFont.load_default()
    x0, y0 = 20, fp.canvas.height - 80
    x1, y1 = 220, fp.canvas.height - 40
    for i in range(x0, x1):
        t = (i - x0) / (x1 - x0)
        r = int(255 * t)
        b = int(255 * (1 - t))
        g = int(128 * (1 - abs(t - 0.5) * 2))
        draw.line([(i, y0), (i, y1)], fill=(r, g, b))
    draw.rectangle((x0, y0, x1, y1), outline=(255, 255, 255), width=1)
    draw.text((x0, y0 - 18), f"{fp.render.temp_range_f.min}F", fill=(255, 255, 255), font=font)
    draw.text((x1 - 40, y0 - 18), f"{fp.render.temp_range_f.max}F", fill=(255, 255, 255), font=font)



def draw_timestamp(draw: ImageDraw.ImageDraw) -> None:
    font = ImageFont.load_default()
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    draw.text((20, 20), timestamp, fill=(255, 255, 255), font=font)



def image_to_png_bytes(image: Image.Image) -> bytes:
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()
