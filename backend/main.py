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

def is_door_open(fp: FloorplanV1, door: Door) -> bool
