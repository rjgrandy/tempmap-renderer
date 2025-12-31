from __future__ import annotations

import heapq
import io
import json
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from typing import Literal

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
    # Customization
    label_offset_x: int = 10
    label_offset_y: int = -8
    font_size: int = 12


class Thermostat(BaseModel):
    id: str
    pos: Point
    temperature_entity: str
    setpoint_entity: str
    mode_entity: Optional[str] = None
    device_label: str = ""
    # Customization
    label_offset_x: int = 12
    label_offset_y: int = -8
    font_size: int = 12


class Stairwell(BaseModel):
    id: str
    polygon: List[Point]
    link_to_floor_id: Optional[str] = None
    coupling: float = 0.05


class SolverParams(BaseModel):
    grid_w: int = 400
    grid_h: int = 250
    iterations: int = 500
    sensor_pull: float = 1.0
    wall_resistance: float = 500000.0
    default_passage_resistance: float = 2.0


class TemperatureRange(BaseModel):
    min: float = 60.0
    max: float = 80.0


class RenderParams(BaseModel):
    temp_range_f: TemperatureRange = Field(default_factory=TemperatureRange)
    overlay_alpha: float = 0.6
    scale_min_mode: Literal["absolute", "relative"] = "absolute"
    scale_max_mode: Literal["absolute", "relative"] = "absolute"
    auto_crop: bool = True
    crop_padding: int = 30
    exterior_margin: int = 20
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
        extra = "ignore"


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
            if thermo.get("mode_entity"):
                entities.append(thermo.get("mode_entity"))
    return sorted({e for e in entities if e})

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
    sensor_samples: List[Tuple[int, int, float, float]] = []
    temps: List[float] = []
    with ha_lock:
        for sensor in fp.sensors:
            state = ha_states.get(sensor.entity) if sensor.entity else None
            if not state:
                continue
            temp = parse_float(state.state)
            if temp == 0.0: continue 
            sx, sy = point_xy(sensor.pos)
            gx = int(sx / fp.canvas.width * fp.solver.grid_w)
            gy = int(sy / fp.canvas.height * fp.solver.grid_h)
            gx = min(max(gx, 0), fp.solver.grid_w - 1)
            gy = min(max(gy, 0), fp.solver.grid_h - 1)
            weight = max(sensor.weight, 0.01)
            sensor_samples.append((gx, gy, temp, weight))
            temps.append(temp)
    
    default_temp = float(np.mean(temps)) if temps else 70.0
    if not sensor_samples:
        return np.full((fp.solver.grid_h, fp.solver.grid_w), default_temp, dtype=float)

    h_edges, v_edges = build_edge_conductance(fp)
    height, width = fp.solver.grid_h, fp.solver.grid_w
    
    weighted_sum = np.zeros((height, width), dtype=float)
    weight_sum = np.zeros((height, width), dtype=float)
    
    for gx, gy, temp, weight in sensor_samples:
        distances = dijkstra_distances(gx, gy, h_edges, v_edges)
        sensor_weight = weight * (1.0 / (distances**4 + 1.0))
        weighted_sum += sensor_weight * temp
        weight_sum += sensor_weight
    
    grid = np.divide(
        weighted_sum,
        weight_sum,
        out=np.full((height, width), default_temp, dtype=float),
        where=weight_sum > 0,
    )
    return grid


def parse_float(value: str) -> float:
    try:
        return float(value)
    except ValueError:
        return 0.0


def diffuse_grid(fp: FloorplanV1, grid: np.ndarray) -> np.ndarray:
    h_edges, v_edges = build_edge_conductance(fp)
    w_left = np.pad(h_edges, ((0, 0), (1, 0)), mode='constant', constant_values=0)
    w_right = np.pad(h_edges, ((0, 0), (0, 1)), mode='constant', constant_values=0)
    w_up = np.pad(v_edges, ((1, 0), (0, 0)), mode='constant', constant_values=0)
    w_down = np.pad(v_edges, ((0, 1), (0, 0)), mode='constant', constant_values=0)
    
    n_left = np.roll(grid, 1, axis=1)
    n_right = np.roll(grid, -1, axis=1)
    n_up = np.roll(grid, 1, axis=0)
    n_down = np.roll(grid, -1, axis=0)
    
    numerator = (n_left * w_left) + (n_right * w_right) + (n_up * w_up) + (n_down * w_down)
    denominator = w_left + w_right + w_up + w_down
    
    mask = denominator > 0
    new_grid = grid.copy()
    new_grid[mask] = numerator[mask] / denominator[mask]

    apply_sensor_pull(fp, new_grid)
    return new_grid


def build_edge_conductance(fp: FloorplanV1) -> Tuple[np.ndarray, np.ndarray]:
    height, width = fp.solver.grid_h, fp.solver.grid_w
    
    # 1. Rasterize Walls (Blocks)
    wall_mask = np.zeros((height, width), dtype=bool)
    for wall in fp.walls:
        points = wall.points
        for idx in range(len(points) - 1):
            rasterize_line_to_mask(fp, points[idx], points[idx+1], wall_mask)
            
    # 2. Rasterize Open Doors (Anti-blocks)
    # This fixes diagonal doors: if a door is open, we erase the wall mask at that location
    door_mask = np.zeros((height, width), dtype=bool)
    for door in fp.doors:
        if is_door_open(fp, door):
            rasterize_line_to_mask(fp, door.segment[0], door.segment[1], door_mask)
            
    # Effective mask: Wall exists AND it is not an open door
    effective_mask = wall_mask & (~door_mask)
    
    h_edges = np.ones((height, width - 1), dtype=float)
    v_edges = np.ones((height - 1, width), dtype=float)
    
    # Block edges if they touch an effective wall
    wall_left = effective_mask[:, :-1]
    wall_right = effective_mask[:, 1:]
    h_edges[wall_left | wall_right] = 0.0
    
    wall_up = effective_mask[:-1, :]
    wall_down = effective_mask[1:, :]
    v_edges[wall_up | wall_down] = 0.0
    
    return h_edges, v_edges

def is_door_open(fp: FloorplanV1, door: Door) -> bool:
    door_open = door.open
    if door.entity_id:
        with ha_lock:
            state = ha_states.get(door.entity_id)
        if state:
            if state.state in door.mapping.open_values:
                door_open = True
            elif state.state in door.mapping.closed_values:
                door_open = False
    return door_open

def rasterize_line_to_mask(fp: FloorplanV1, a: Point, b: Point, mask: np.ndarray) -> None:
    grid_w = fp.solver.grid_w
    grid_h = fp.solver.grid_h
    ax, ay = point_xy(a)
    bx, by = point_xy(b)
    x0 = ax / fp.canvas.width * grid_w
    y0 = ay / fp.canvas.height * grid_h
    x1 = bx / fp.canvas.width * grid_w
    y1 = by / fp.canvas.height * grid_h
    dist = max(abs(x1 - x0), abs(y1 - y0))
    if dist == 0: return
    steps = int(dist * 2) + 2
    for i in range(steps):
        t = i / (steps - 1)
        lx = x0 + (x1 - x0) * t
        ly = y0 + (y1 - y0) * t
        gx, gy = int(lx), int(ly)
        if 0 <= gx < grid_w and 0 <= gy < grid_h:
            mask[gy, gx] = True


def dijkstra_distances(start_x: int, start_y: int, h_edges: np.ndarray, v_edges: np.ndarray) -> np.ndarray:
    height, width = h_edges.shape[0], h_edges.shape[1] + 1
    distances = np.full((height, width), np.inf, dtype=float)
    distances[start_y, start_x] = 0.0
    heap: List[Tuple[float, int, int]] = [(0.0, start_y, start_x)]
    while heap:
        cost, y, x = heapq.heappop(heap)
        if cost > distances[y, x]: continue
        
        # Neighbor checks
        if x > 0 and h_edges[y, x - 1] > 0:
            if cost + 1.0 < distances[y, x - 1]:
                distances[y, x - 1] = cost + 1.0
                heapq.heappush(heap, (cost + 1.0, y, x - 1))
        if x < width - 1 and h_edges[y, x] > 0:
            if cost + 1.0 < distances[y, x + 1]:
                distances[y, x + 1] = cost + 1.0
                heapq.heappush(heap, (cost + 1.0, y, x + 1))
        if y > 0 and v_edges[y - 1, x] > 0:
            if cost + 1.0 < distances[y - 1, x]:
                distances[y - 1, x] = cost + 1.0
                heapq.heappush(heap, (cost + 1.0, y - 1, x))
        if y < height - 1 and v_edges[y, x] > 0:
            if cost + 1.0 < distances[y + 1, x]:
                distances[y + 1, x] = cost + 1.0
                heapq.heappush(heap, (cost + 1.0, y + 1, x))
    return distances


def apply_sensor_pull(fp: FloorplanV1, grid: np.ndarray) -> None:
    for sensor in fp.sensors:
        with ha_lock:
            state = ha_states.get(sensor.entity) if sensor.entity else None
        if not state: continue
        temp = parse_float(state.state)
        if temp == 0.0: continue
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
        if not stair or not stair.link_to_floor_id: continue
        target = stair.link_to_floor_id
        if target not in grids: continue
        source_grid = grids[floor_id]
        target_grid = grids[target]
        mask = polygon_mask(fp, stair.polygon)
        coupling = stair.coupling
        source_grid[mask] = source_grid[mask] + coupling * (target_grid[mask] - source_grid[mask])
        target_grid[mask] = target_grid[mask] + coupling * (source_grid[mask] - target_grid[mask])


def polygon_mask(fp: FloorplanV1, polygon: List[Point]) -> np.ndarray:
    height, width = fp.solver.grid_h, fp.solver.grid_w
    xs = [point_xy(p)[0] / fp.canvas.width * width for p in polygon]
    ys = [point_xy(p)[1] / fp.canvas.height * height for p in polygon]
    mask = np.zeros((height, width), dtype=bool)
    if not xs: return mask
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
        if intersect: inside = not inside
        j = i
    return inside


def render_floorplan_image(floor_id: str, payload: Dict, grid: np.ndarray, metadata: Dict) -> Image.Image:
    fp = FloorplanV1.parse_obj(payload)
    canvas = Image.new("RGBA", (fp.canvas.width, fp.canvas.height), (20, 20, 20, 255))
    min_f, max_f = resolve_temperature_range(fp, grid)
    
    # Use Flood Fill mask instead of convex hull to handle concave yards properly
    heatmap_mask = build_floorplan_mask_floodfill(fp)
    
    heatmap = render_heatmap(grid, min_f, max_f, fp.render.overlay_alpha, canvas.size, heatmap_mask)
    canvas = Image.alpha_composite(canvas, heatmap)
    draw = ImageDraw.Draw(canvas)
    if fp.render.show_walls: draw_walls(draw, fp)
    draw_sensors(draw, fp)
    draw_thermostats(draw, fp)
    if fp.render.auto_crop:
        crop_box = compute_floorplan_crop(fp, canvas.size, fp.render.crop_padding)
        if crop_box: 
            canvas = canvas.crop(crop_box)
            draw = ImageDraw.Draw(canvas) # Update draw object for cropped canvas if needed
            # Actually we just crop at the end, that's fine.
    
    # Legend/Timestamp margin
    if fp.render.show_legend or fp.render.show_timestamp:
        canvas = add_exterior_margin(canvas, fp.render.exterior_margin, fp.render.show_timestamp, fp.render.show_legend)
        draw = ImageDraw.Draw(canvas)
    
    if fp.render.show_legend: draw_legend(draw, min_f, max_f, canvas.size, fp.render.exterior_margin)
    if fp.render.show_timestamp: draw_timestamp(draw, fp.render.exterior_margin)
    return canvas.convert("RGB")

def build_floorplan_mask_floodfill(fp: FloorplanV1) -> np.ndarray:
    """Creates a mask of the 'inside' of the floorplan using flood fill from sensors."""
    w, h = fp.canvas.width // 4, fp.canvas.height // 4 # Low-res mask for speed
    mask = Image.new("L", (w, h), 0)
    
    # 1. Draw Walls (Blockers)
    draw = ImageDraw.Draw(mask)
    for wall in fp.walls:
        pts = [(p[0]/4, p[1]/4) for p in wall.points]
        draw.line(pts, fill=255, width=2)

    # 2. Convert to numpy
    arr = np.array(mask)
    # Walls are 255, empty is 0. We want to fill 0s starting from sensors.

    # Identify exterior space (outside the walls) so we do not open doors to the outside.
    outside = np.zeros_like(arr, dtype=bool)
    h, w = arr.shape
    wall_block = arr > 0
    padded = np.pad(wall_block, 1, mode="constant", constant_values=False)
    wall_block = (
        padded[0:-2, 0:-2] | padded[0:-2, 1:-1] | padded[0:-2, 2:] |
        padded[1:-1, 0:-2] | padded[1:-1, 1:-1] | padded[1:-1, 2:] |
        padded[2:, 0:-2] | padded[2:, 1:-1] | padded[2:, 2:]
    )
    stack = []
    for x in range(w):
        if not wall_block[0, x]:
            stack.append((x, 0))
        if not wall_block[h - 1, x]:
            stack.append((x, h - 1))
    for y in range(h):
        if not wall_block[y, 0]:
            stack.append((0, y))
        if not wall_block[y, w - 1]:
            stack.append((w - 1, y))
    while stack:
        cx, cy = stack.pop()
        if outside[cy, cx]:
            continue
        outside[cy, cx] = True
        for dx, dy in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
            nx, ny = cx + dx, cy + dy
            if 0 <= nx < w and 0 <= ny < h and not wall_block[ny, nx] and not outside[ny, nx]:
                stack.append((nx, ny))

    # 2b. Erase open doors so flood fill can pass through (unless they open to outside)
    for door in fp.doors:
        if not is_door_open(fp, door):
            continue
        ax, ay = door.segment[0][0] / 4, door.segment[0][1] / 4
        bx, by = door.segment[1][0] / 4, door.segment[1][1] / 4
        dx, dy = bx - ax, by - ay
        length = (dx * dx + dy * dy) ** 0.5
        if length == 0:
            continue
        nx, ny = -dy / length, dx / length
        mx, my = (ax + bx) / 2, (ay + by) / 2
        offset = 2.0
        side_a = (int(round(mx + nx * offset)), int(round(my + ny * offset)))
        side_b = (int(round(mx - nx * offset)), int(round(my - ny * offset)))
        def is_outside(pt):
            x, y = pt
            if 0 <= x < w and 0 <= y < h:
                return outside[y, x]
            return True
        if is_outside(side_a) or is_outside(side_b):
            continue
        pts = [(ax, ay), (bx, by)]
        draw.line(pts, fill=0, width=3)

    arr = np.array(mask)
    
    seeds = []
    for s in fp.sensors:
        sx, sy = int(s.pos[0]/4), int(s.pos[1]/4)
        if 0 <= sx < w and 0 <= sy < h: seeds.append((sx, sy))
    for t in fp.thermostats:
        sx, sy = int(t.pos[0]/4), int(t.pos[1]/4)
        if 0 <= sx < w and 0 <= sy < h: seeds.append((sx, sy))
        
    # Flood Fill
    filled = np.zeros_like(arr, dtype=bool)
    stack = seeds
    visited = set(seeds)
    
    while stack:
        cx, cy = stack.pop()
        filled[cy, cx] = True
        
        for dx, dy in [(-1,0), (1,0), (0,-1), (0,1)]:
            nx, ny = cx + dx, cy + dy
            if 0 <= nx < w and 0 <= ny < h:
                if not visited.__contains__((nx, ny)) and arr[ny, nx] == 0:
                    visited.add((nx, ny))
                    stack.append((nx, ny))
                    
    # Resize back up
    full_mask = Image.fromarray(filled).resize((fp.canvas.width, fp.canvas.height), Image.Resampling.NEAREST)
    return np.array(full_mask)

def render_heatmap(grid: np.ndarray, min_f: float, max_f: float, overlay_alpha: float, size: Tuple[int, int], mask: Optional[np.ndarray]) -> Image.Image:
    norm = np.clip((grid - min_f) / (max_f - min_f + 1e-6), 0, 1)
    colors = np.zeros((grid.shape[0], grid.shape[1], 4), dtype=np.uint8)
    colors[..., 0], colors[..., 1], colors[..., 2] = gradient_rgb(norm)
    colors[..., 3] = int(255 * overlay_alpha)
    
    image = Image.fromarray(colors, mode="RGBA").resize(size, resample=Image.Resampling.BILINEAR)
    
    # Apply Mask
    if mask is not None:
        mask_img = Image.fromarray((mask * 255).astype(np.uint8), mode="L")
        image.putalpha(mask_img)
        # Re-apply global alpha to the masked area? 
        # Actually putalpha replaces the alpha channel.
        # We want: Alpha = mask * overlay_alpha
        # So:
        r, g, b, a = image.split()
        # Merge mask with constant alpha
        new_a = Image.eval(mask_img, lambda x: int(x * overlay_alpha))
        image = Image.merge("RGBA", (r, g, b, new_a))

    return image


def gradient_rgb(norm: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    stops = np.array([0.0, 0.25, 0.5, 0.75, 1.0])
    colors = np.array([
        [0, 0, 255], [0, 255, 255], [0, 255, 0], [255, 255, 0], [255, 0, 0]
    ], dtype=float)
    idx = np.searchsorted(stops, norm, side="right") - 1
    idx = np.clip(idx, 0, len(stops) - 2)
    t = (norm - stops[idx]) / (stops[idx + 1] - stops[idx])
    t = t * t * (3.0 - 2.0 * t)
    c0 = colors[idx]
    c1 = colors[idx + 1]
    blended = c0 + (c1 - c0) * t[..., None]
    return blended[..., 0].astype(np.uint8), blended[..., 1].astype(np.uint8), blended[..., 2].astype(np.uint8)


def resolve_temperature_range(fp: FloorplanV1, grid: np.ndarray) -> Tuple[float, float]:
    grid_min = float(np.min(grid))
    grid_max = float(np.max(grid))
    min_f = fp.render.temp_range_f.min if fp.render.scale_min_mode == "absolute" else grid_min
    max_f = fp.render.temp_range_f.max if fp.render.scale_max_mode == "absolute" else grid_max
    if min_f >= max_f: max_f = min_f + 0.1
    return min_f, max_f

def add_exterior_margin(image: Image.Image, margin: int, show_ts: bool, show_legend: bool) -> Image.Image:
    top = margin if show_ts else margin // 2
    bottom = margin + 60 if show_legend else margin // 2
    new_width = image.width + margin * 2
    new_height = image.height + top + bottom
    canvas = Image.new("RGBA", (new_width, new_height), (20, 20, 20, 255))
    canvas.paste(image, (margin, top))
    return canvas

def compute_floorplan_crop(fp: FloorplanV1, canvas_size: Tuple[int, int], padding: int) -> Optional[Tuple[int, int, int, int]]:
    points = []
    for wall in fp.walls: points.extend([point_xy(p) for p in wall.points])
    if not points: return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    min_x = max(int(min(xs) - padding), 0)
    min_y = max(int(min(ys) - padding), 0)
    max_x = min(int(max(xs) + padding), canvas_size[0])
    max_y = min(int(max(ys) + padding), canvas_size[1])
    return min_x, min_y, max_x, max_y

def draw_walls(draw: ImageDraw.ImageDraw, fp: FloorplanV1) -> None:
    for wall in fp.walls:
        points = [point_xy(p) for p in wall.points]
        draw.line(points, fill=(230, 230, 230), width=3)
    for door in fp.doors:
        points = [point_xy(door.segment[0]), point_xy(door.segment[1])]
        draw.line(points, fill=(120, 200, 255), width=4)

def draw_sensors(draw: ImageDraw.ImageDraw, fp: FloorplanV1) -> None:
    for sensor in fp.sensors:
        x, y = point_xy(sensor.pos)
        draw.ellipse((x - 6, y - 6, x + 6, y + 6), fill=(255, 255, 255))
        if fp.render.show_labels:
            label = sensor.label or sensor.entity or ""
            temp_val = format_entity_temperature(sensor.entity)
            
            # Helper to get the actual font object (with size)
            font = get_font(sensor.font_size)
            
            # New Logic: Multiline
            lines = []
            if label: lines.append(label)
            if temp_val: lines.append(temp_val)
            
            off_x = sensor.label_offset_x
            current_y = y + sensor.label_offset_y
            
            for line in lines:
                draw.text((x + off_x, current_y), line, fill=(255, 255, 255), font=font)
                # Approximate line height = font_size + 2
                current_y += (sensor.font_size + 2)

def draw_thermostats(draw: ImageDraw.ImageDraw, fp: FloorplanV1) -> None:
    for thermo in fp.thermostats:
        x, y = point_xy(thermo.pos)
        draw.rectangle((x - 8, y - 8, x + 8, y + 8), outline=(255, 200, 50), width=2)
        if fp.render.show_labels:
            temp = read_entity_state(thermo.temperature_entity)
            setpoint = read_entity_state(thermo.setpoint_entity)
            temp_line = f"{temp} / {setpoint}"
            
            name_line = thermo.device_label or "Thermostat"
            if thermo.mode_entity:
                mode = read_entity_state(thermo.mode_entity)
                temp_line = f"{temp_line} ({mode})"
            
            font = get_font(thermo.font_size)
            
            lines = [name_line, temp_line]
            
            off_x = thermo.label_offset_x
            current_y = y + thermo.label_offset_y
            
            for line in lines:
                draw.text((x + off_x, current_y), line, fill=(255, 200, 50), font=font)
                current_y += (thermo.font_size + 2)

def get_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Tries to load a scalable font. Falls back to default if unavailable."""
    # Common paths for DejaVuSans on Linux/Debian
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/TTF/DejaVuSans.ttf",
        "DejaVuSans.ttf"
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except IOError:
            continue
    # Fallback to default (size is ignored)
    return ImageFont.load_default()

def read_entity_state(entity_id: str) -> str:
    with ha_lock: state = ha_states.get(entity_id)
    return state.state if state else "n/a"

def format_entity_temperature(entity_id: Optional[str]) -> str:
    if not entity_id: return ""
    state = read_entity_state(entity_id)
    if state == "n/a": return ""
    try: return f"{float(state):.1f}F"
    except ValueError: return ""

def draw_legend(draw: ImageDraw.ImageDraw, min_f: float, max_f: float, size: Tuple[int, int], margin: int) -> None:
    font = ImageFont.load_default()
    x0, y0 = margin, size[1] - 80
    x1, y1 = x0 + 200, size[1] - 40
    for i in range(x0, x1):
        t = (i - x0) / (x1 - x0)
        r, g, b = gradient_rgb(np.array([t]))
        draw.line([(i, y0), (i, y1)], fill=(int(r[0]), int(g[0]), int(b[0])))
    draw.rectangle((x0, y0, x1, y1), outline=(255, 255, 255), width=1)
    draw.text((x0, y0 - 18), f"{min_f:.1f}F", fill=(255, 255, 255), font=font)
    draw.text((x1 - 48, y0 - 18), f"{max_f:.1f}F", fill=(255, 255, 255), font=font)

def draw_timestamp(draw: ImageDraw.ImageDraw, margin: int) -> None:
    font = ImageFont.load_default()
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    draw.text((margin, margin), timestamp, fill=(255, 255, 255), font=font)

def image_to_png_bytes(image: Image.Image) -> bytes:
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()
