from __future__ import annotations

import bisect
import heapq
import io
import json
import math
import os
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
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
    setpoint_low_entity: Optional[str] = None
    setpoint_high_entity: Optional[str] = None
    mode_entity: Optional[str] = None
    fan_entity: Optional[str] = None
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
    legend_colors: Optional[List[str]] = None
    show_timestamp: bool = True
    show_outside_temp: bool = True
    outside_temp_label: str = "Outside"
    outside_temp_entity: Optional[str] = None
    outside_temp_f: Optional[float] = None
    show_chart: bool = False
    chart_temp_entity: Optional[str] = None
    chart_forecast_entity: Optional[str] = None
    chart_history_hours: float = 12.0
    chart_forecast_hours: float = 12.0
    chart_width: int = 260
    chart_height: int = 80
    thermostat_chart_history_hours: float = 24.0
    thermostat_chart_width: int = 260
    thermostat_chart_height: int = 140
    text_font_size: Optional[int] = None
    text_font_path: Optional[str] = None


class SidebarComponentConfig(BaseModel):
    type: Literal[
        "timestamp",
        "outside_temp",
        "temperature_chart",
        "legend",
        "thermostat_action_chart",
        "thermostat_setpoint_chart",
    ]
    enabled: bool = True
    height: Optional[int] = None
    width: Optional[int] = None
    history_hours: Optional[float] = None
    forecast_hours: Optional[float] = None
    temp_entity: Optional[str] = None
    forecast_entity: Optional[str] = None


class SidebarConfig(BaseModel):
    enabled: bool = True
    components: List[SidebarComponentConfig] = Field(default_factory=list)


class RoomLabel(BaseModel):
    id: str
    pos: Point
    label: str = ""
    font_size: int = 16
    label_offset_x: int = 0
    label_offset_y: int = 0


class FloorplanV1(BaseModel):
    version: int = 1
    floor_id: str = "floor1"
    canvas: CanvasSize = Field(default_factory=CanvasSize)
    scale: ScaleCalibration = Field(default_factory=ScaleCalibration)
    walls: List[Wall] = Field(default_factory=list)
    doors: List[Door] = Field(default_factory=list)
    sensors: List[Sensor] = Field(default_factory=list)
    thermostats: List[Thermostat] = Field(default_factory=list)
    room_labels: List[RoomLabel] = Field(default_factory=list)
    stairwell: Optional[Stairwell] = None
    solver: SolverParams = Field(default_factory=SolverParams)
    render: RenderParams = Field(default_factory=RenderParams)

    class Config:
        extra = "ignore"


class RenameFloorplanRequest(BaseModel):
    new_id: str


class AppConfig(BaseModel):
    data_path: str
    ha_base_url: str
    ha_token: str
    refresh_seconds: int
    default_grid: Tuple[int, int]
    default_legend: Tuple[float, float]
    timelapse_frame_retention_hours: int
    timelapse_window_hours: float
    timelapse_sampling_seconds: int
    timelapse_target_duration_seconds: int
    timelapse_fps: int
    timelapse_output_path: str
    timelapse_rolling_enabled: bool
    timelapse_rolling_interval_seconds: int
    timelapse_stitch_multi_floor: bool
    timelapse_border_px: int
    timelapse_label_font_size: int
    render_sidebar: SidebarConfig


@dataclass
class EntityState:
    state: str
    last_updated: str
    last_changed: str


@dataclass
class SidebarContext:
    floorplans: List[FloorplanV1]
    thermostats: List[Thermostat]
    min_f: float
    max_f: float
    palette: List[Tuple[int, int, int]]
    primary_floorplan: FloorplanV1


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
timelapse_lock = threading.Lock()
timelapse_last_roll: Optional[float] = None
timelapse_is_running = False


def default_sidebar_components() -> List[SidebarComponentConfig]:
    return [
        SidebarComponentConfig(type="timestamp"),
        SidebarComponentConfig(type="outside_temp"),
        SidebarComponentConfig(type="temperature_chart"),
        SidebarComponentConfig(type="legend"),
        SidebarComponentConfig(type="thermostat_action_chart"),
        SidebarComponentConfig(type="thermostat_setpoint_chart"),
    ]


def load_config() -> AppConfig:
    config_data = {}
    if CONFIG_PATH.exists():
        with CONFIG_PATH.open("r", encoding="utf-8") as handle:
            config_data = yaml.safe_load(handle) or {}
    data_path = config_data.get("data", {}).get("path", "/data")
    data_path = os.getenv(DATA_ENV, data_path)
    ha_config = config_data.get("home_assistant", {})
    render_config = config_data.get("render", {})
    timelapse_config = config_data.get("timelapse", {})
    sidebar_config = render_config.get("sidebar", {}) or {}
    raw_components = sidebar_config.get("components", [])
    if not raw_components:
        sidebar_components = default_sidebar_components()
    else:
        sidebar_components = [
            SidebarComponentConfig.parse_obj(component)
            for component in raw_components
            if isinstance(component, dict)
        ]
    data_path = Path(data_path)
    timelapse_output_path = timelapse_config.get("output_path", str(data_path / "timelapses"))
    return AppConfig(
        data_path=str(data_path),
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
        timelapse_frame_retention_hours=int(timelapse_config.get("frame_retention_hours", 48)),
        timelapse_window_hours=float(timelapse_config.get("window_hours", 48)),
        timelapse_sampling_seconds=int(timelapse_config.get("sampling_seconds", 120)),
        timelapse_target_duration_seconds=int(timelapse_config.get("target_duration_seconds", 60)),
        timelapse_fps=int(timelapse_config.get("fps", 10)),
        timelapse_output_path=timelapse_output_path,
        timelapse_rolling_enabled=bool(timelapse_config.get("rolling_enabled", True)),
        timelapse_rolling_interval_seconds=int(timelapse_config.get("rolling_interval_seconds", 900)),
        timelapse_stitch_multi_floor=bool(timelapse_config.get("stitch_multi_floor", True)),
        timelapse_border_px=int(timelapse_config.get("border_px", 12)),
        timelapse_label_font_size=int(timelapse_config.get("label_font_size", 18)),
        render_sidebar=SidebarConfig(
            enabled=bool(sidebar_config.get("enabled", True)),
            components=sidebar_components,
        ),
    )


config = load_config()


@app.on_event("startup")
def startup() -> None:
    data_dir = Path(config.data_path)
    (data_dir / "floorplans").mkdir(parents=True, exist_ok=True)
    (data_dir / "frames").mkdir(parents=True, exist_ok=True)
    Path(config.timelapse_output_path).mkdir(parents=True, exist_ok=True)
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


@app.delete("/api/floorplans/{floor_id}")
def delete_floorplan(floor_id: str) -> Dict:
    floor_id = validate_floorplan_id(floor_id)
    path = Path(config.data_path) / "floorplans" / f"{floor_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Floorplan not found")
    path.unlink(missing_ok=True)
    update_floorplan_links(floor_id, None)
    return {"deleted": floor_id}


@app.post("/api/floorplans/{floor_id}/rename")
def rename_floorplan(floor_id: str, payload: RenameFloorplanRequest) -> Dict:
    new_id = validate_floorplan_id(payload.new_id)
    if new_id == floor_id:
        return load_floorplan_file(floor_id)
    floor_dir = Path(config.data_path) / "floorplans"
    old_path = floor_dir / f"{floor_id}.json"
    new_path = floor_dir / f"{new_id}.json"
    if not old_path.exists():
        raise HTTPException(status_code=404, detail="Floorplan not found")
    if new_path.exists():
        raise HTTPException(status_code=409, detail="Floorplan already exists")
    payload_data = load_floorplan_file(floor_id)
    payload_data["floor_id"] = new_id
    validated = parse_floorplan(payload_data)
    save_floorplan_file(new_id, validated)
    old_path.unlink(missing_ok=True)
    update_floorplan_links(floor_id, new_id)
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


@app.get("/api/ha/states")
def get_ha_states(entities: Optional[str] = None) -> Dict[str, Dict[str, str]]:
    if not entities:
        return {"states": {}}
    entity_list = [e.strip() for e in entities.split(",") if e.strip()]
    if not entity_list:
        return {"states": {}}
    with ha_lock:
        states = {
            entity: ha_states.get(entity).state if ha_states.get(entity) else "n/a"
            for entity in entity_list
        }
    return {"states": states}


@app.get("/render/live/{floor_id}.png")
def render_live_png(floor_id: str) -> Response:
    image = render_floorplan(floor_id)
    image_bytes = image_to_png_bytes(image)
    return Response(content=image_bytes, media_type="image/png", headers={"Cache-Control": "no-store"})


@app.get("/api/timelapse/{floor_id}")
def render_timelapse(
    floor_id: str,
    window: Optional[str] = None,
    sampling_seconds: Optional[int] = None,
    target_duration_seconds: Optional[int] = None,
    fps: Optional[int] = None,
    stitch: Optional[bool] = None,
) -> Response:
    timelapse_path = generate_timelapse_for_request(
        floor_id=floor_id,
        window=window,
        sampling_seconds=sampling_seconds,
        target_duration_seconds=target_duration_seconds,
        fps=fps,
        stitch=stitch,
    )
    return FileResponse(timelapse_path, media_type="video/mp4", filename=Path(timelapse_path).name)


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


def validate_floorplan_id(floor_id: str) -> str:
    cleaned = floor_id.strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail="new_id is required")
    if cleaned in {".", ".."}:
        raise HTTPException(status_code=400, detail="Invalid floorplan id")
    if Path(cleaned).name != cleaned or "/" in cleaned or "\\" in cleaned:
        raise HTTPException(status_code=400, detail="Invalid floorplan id")
    floor_dir = Path(config.data_path) / "floorplans"
    base_dir = floor_dir.resolve()
    target = (floor_dir / f"{cleaned}.json").resolve()
    try:
        target.relative_to(base_dir)
    except ValueError as error:
        raise HTTPException(status_code=400, detail="Invalid floorplan id") from error
    return cleaned


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
            maybe_render_rolling_timelapses(floorplans)
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


def update_floorplan_links(old_id: str, new_id: Optional[str]) -> None:
    floor_dir = Path(config.data_path) / "floorplans"
    for path in floor_dir.glob("*.json"):
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        stairwells = payload.get("stairwells", [])
        updated = False
        for stair in stairwells:
            if stair.get("link_to_floor_id") == old_id:
                stair["link_to_floor_id"] = new_id
                updated = True
        if updated:
            validated = parse_floorplan(payload)
            save_floorplan_file(path.stem, validated)

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
            entities.append(thermo.get("setpoint_low_entity"))
            entities.append(thermo.get("setpoint_high_entity"))
            if thermo.get("mode_entity"):
                entities.append(thermo.get("mode_entity"))
            if thermo.get("fan_entity"):
                entities.append(thermo.get("fan_entity"))
        render_cfg = floorplan.get("render", {})
        entities.append(render_cfg.get("outside_temp_entity"))
        entities.append(render_cfg.get("chart_temp_entity"))
    for component in config.render_sidebar.components:
        if not component.enabled:
            continue
        if component.type == "temperature_chart":
            entities.append(component.temp_entity)
            entities.append(component.forecast_entity)
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
        image = render_floorplan_base_image(
            floor_id,
            floorplan,
            grid,
            metadata.get(floor_id, {}),
        )
        save_frame(floor_id, image)
    cleanup_frames()


def save_frame(floor_id: str, image: Image.Image) -> None:
    frames_dir = Path(config.data_path) / "frames" / floor_id
    frames_dir.mkdir(parents=True, exist_ok=True)
    timestamp = int(time.time())
    path = frames_dir / f"{timestamp}.png"
    image.save(path)


def cleanup_frames() -> None:
    cutoff = time.time() - (config.timelapse_frame_retention_hours * 60 * 60)
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
    if floor_id == "all":
        if not floorplans:
            raise HTTPException(status_code=404, detail="Floorplans not found")
        grids, metadata = solve_all_floorplans(floorplans)
        ranges = []
        for floor_key in sorted(floorplans.keys()):
            grid = grids.get(floor_key)
            if grid is None:
                continue
            fp = FloorplanV1.parse_obj(floorplans[floor_key])
            ranges.append(resolve_temperature_range(fp, grid))
        if not ranges:
            raise HTTPException(status_code=404, detail="Floorplans not found")
        min_f = min(range_pair[0] for range_pair in ranges)
        max_f = max(range_pair[1] for range_pair in ranges)
        images = []
        ordered_floor_ids = sorted(floorplans.keys())
        parsed_floorplans: List[FloorplanV1] = []
        for floor_key in ordered_floor_ids:
            parsed_floorplans.append(FloorplanV1.parse_obj(floorplans[floor_key]))
        for floor_key in ordered_floor_ids:
            grid = grids.get(floor_key)
            if grid is None:
                continue
            images.append(
                (
                    floor_key,
                    render_floorplan_base_image(
                        floor_key,
                        floorplans[floor_key],
                        grid,
                        metadata.get(floor_key, {}),
                        range_override=(min_f, max_f),
                    ),
                )
            )
        if not images:
            raise HTTPException(status_code=404, detail="Floorplans not found")
        max_height = max(image.height for _floor, image in images)
        sidebar_context = resolve_sidebar_context(parsed_floorplans, min_f, max_f)
        sidebar_image = render_sidebar_image(sidebar_context, align="center", target_height=max_height)
        stitched_images: List[Tuple[Optional[str], Image.Image]] = []
        for idx, (floor_key, image) in enumerate(images):
            stitched_images.append((floor_key, image))
            if idx == 0 and sidebar_image is not None:
                stitched_images.append((None, sidebar_image))
        return stitch_images_horizontally(
            stitched_images,
            config.timelapse_border_px,
            config.timelapse_label_font_size,
        )
    if floor_id not in floorplans:
        raise HTTPException(status_code=404, detail="Floorplan not found")
    grids, metadata = solve_all_floorplans(floorplans)
    grid = grids.get(floor_id)
    if grid is None:
        raise HTTPException(status_code=404, detail="Floorplan not found")
    base_image = render_floorplan_base_image(floor_id, floorplans[floor_id], grid, metadata.get(floor_id, {}))
    fp = FloorplanV1.parse_obj(floorplans[floor_id])
    min_f, max_f = resolve_temperature_range(fp, grid)
    sidebar_context = resolve_sidebar_context([fp], min_f, max_f)
    sidebar_image = render_sidebar_image(sidebar_context, align="top", target_height=base_image.height)
    return attach_sidebar_to_floorplan(base_image, sidebar_image)


def maybe_render_rolling_timelapses(floorplans: Dict[str, Dict]) -> None:
    global timelapse_last_roll
    if not config.timelapse_rolling_enabled:
        return
    now = time.time()
    if timelapse_last_roll and (now - timelapse_last_roll) < config.timelapse_rolling_interval_seconds:
        return
    if not check_ffmpeg_available():
        return
    if timelapse_lock.locked():
        return
    timelapse_last_roll = now
    thread = threading.Thread(target=render_rolling_timelapses, args=(floorplans,), daemon=True)
    thread.start()


def render_rolling_timelapses(floorplans: Dict[str, Dict]) -> None:
    global timelapse_is_running
    with timelapse_lock:
        if timelapse_is_running:
            return
        timelapse_is_running = True
        try:
            for floor_id in sorted(floorplans.keys()):
                output_path = Path(config.timelapse_output_path) / floor_id / "rolling.mp4"
                output_path.parent.mkdir(parents=True, exist_ok=True)
                build_timelapse_video(
                    floor_id=floor_id,
                    output_path=output_path,
                    window_seconds=int(config.timelapse_window_hours * 60 * 60),
                    sampling_seconds=config.timelapse_sampling_seconds,
                    target_duration_seconds=config.timelapse_target_duration_seconds,
                    fps=config.timelapse_fps,
                    stitch=False,
                )
            if config.timelapse_stitch_multi_floor and len(floorplans) > 1:
                output_path = Path(config.timelapse_output_path) / "all" / "rolling.mp4"
                output_path.parent.mkdir(parents=True, exist_ok=True)
                build_timelapse_video(
                    floor_id="all",
                    output_path=output_path,
                    window_seconds=int(config.timelapse_window_hours * 60 * 60),
                    sampling_seconds=config.timelapse_sampling_seconds,
                    target_duration_seconds=config.timelapse_target_duration_seconds,
                    fps=config.timelapse_fps,
                    stitch=True,
                )
        finally:
            timelapse_is_running = False


def generate_timelapse_for_request(
    floor_id: str,
    window: Optional[str],
    sampling_seconds: Optional[int],
    target_duration_seconds: Optional[int],
    fps: Optional[int],
    stitch: Optional[bool],
) -> str:
    if not check_ffmpeg_available():
        raise HTTPException(status_code=500, detail="ffmpeg is required but not available")
    window_seconds = parse_window_seconds(window) if window else int(config.timelapse_window_hours * 60 * 60)
    sampling_seconds = sampling_seconds or config.timelapse_sampling_seconds
    target_duration_seconds = target_duration_seconds or config.timelapse_target_duration_seconds
    fps = fps or config.timelapse_fps
    stitch = config.timelapse_stitch_multi_floor if stitch is None else stitch
    output_dir = Path(config.timelapse_output_path) / floor_id
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"timelapse_{int(time.time())}_{uuid.uuid4().hex}.mp4"
    build_timelapse_video(
        floor_id=floor_id,
        output_path=output_path,
        window_seconds=window_seconds,
        sampling_seconds=sampling_seconds,
        target_duration_seconds=target_duration_seconds,
        fps=fps,
        stitch=stitch,
    )
    return str(output_path)


def parse_window_seconds(window: str) -> int:
    window = window.strip().lower()
    if not window:
        raise HTTPException(status_code=400, detail="window parameter cannot be empty")
    suffixes = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    if window[-1] in suffixes:
        try:
            value = float(window[:-1])
        except ValueError as error:
            raise HTTPException(status_code=400, detail="invalid window format") from error
        return int(value * suffixes[window[-1]])
    try:
        return int(float(window) * 3600)
    except ValueError as error:
        raise HTTPException(status_code=400, detail="invalid window format") from error


def check_ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def build_timelapse_video(
    floor_id: str,
    output_path: Path,
    window_seconds: int,
    sampling_seconds: int,
    target_duration_seconds: int,
    fps: int,
    stitch: bool,
) -> None:
    floorplans = load_all_floorplans()
    if floor_id != "all" and floor_id not in floorplans:
        raise HTTPException(status_code=404, detail="Floorplan not found")
    now = time.time()
    start_ts = now - window_seconds
    available_floor_ids = sorted(floorplans.keys())
    if floor_id != "all":
        available_floor_ids = [floor_id]
    elif not stitch:
        stitch = True
    frames_by_floor = {
        fid: load_frame_index(fid, start_ts)
        for fid in available_floor_ids
    }
    sample_times = build_sample_times(start_ts, now, sampling_seconds)
    if not sample_times:
        raise HTTPException(status_code=404, detail="No frames found in requested window")
    sidebar_context = None
    if config.render_sidebar.enabled and available_floor_ids:
        grids, _metadata = solve_all_floorplans(floorplans)
        parsed_floorplans: List[FloorplanV1] = []
        ranges: List[Tuple[float, float]] = []
        for fid in available_floor_ids:
            payload = floorplans.get(fid)
            if not payload:
                continue
            fp = FloorplanV1.parse_obj(payload)
            parsed_floorplans.append(fp)
            grid = grids.get(fid)
            if grid is not None:
                ranges.append(resolve_temperature_range(fp, grid))
        if parsed_floorplans and ranges:
            min_f = min(range_pair[0] for range_pair in ranges)
            max_f = max(range_pair[1] for range_pair in ranges)
            sidebar_context = resolve_sidebar_context(parsed_floorplans, min_f, max_f)
    with tempfile.TemporaryDirectory() as tmp_dir:
        temp_dir = Path(tmp_dir)
        if stitch and len(available_floor_ids) > 1:
            generate_stitched_frames(
                temp_dir=temp_dir,
                sample_times=sample_times,
                frames_by_floor=frames_by_floor,
                floor_ids=available_floor_ids,
                sidebar_context=sidebar_context,
            )
        else:
            generate_single_floor_frames(
                temp_dir=temp_dir,
                sample_times=sample_times,
                frames_by_floor=frames_by_floor,
                floor_id=available_floor_ids[0],
                sidebar_context=sidebar_context,
            )
        frame_paths = sorted(temp_dir.glob("frame_*.png"))
        if not frame_paths:
            raise HTTPException(status_code=404, detail="No frames available to build timelapse")
        max_frames = max(1, int(target_duration_seconds * fps))
        frames_dir = temp_dir
        if len(frame_paths) > max_frames:
            frame_paths = downsample_frames(frame_paths, max_frames)
            frames_dir = temp_dir / "downsampled"
            frames_dir.mkdir(exist_ok=True)
            for idx, path in enumerate(frame_paths):
                new_path = frames_dir / f"frame_{idx:05d}.png"
                new_path.write_bytes(path.read_bytes())
        run_ffmpeg_encode(frames_dir, fps, output_path)


def load_frame_index(floor_id: str, start_ts: float) -> List[Tuple[int, Path]]:
    frames_dir = Path(config.data_path) / "frames" / floor_id
    if not frames_dir.exists():
        return []
    frames = []
    for path in frames_dir.glob("*.png"):
        try:
            ts = int(path.stem)
        except ValueError:
            continue
        if ts >= start_ts:
            frames.append((ts, path))
    return sorted(frames, key=lambda item: item[0])


def build_sample_times(start_ts: float, end_ts: float, sampling_seconds: int) -> List[int]:
    if sampling_seconds <= 0:
        raise HTTPException(status_code=400, detail="sampling_seconds must be positive")
    sample_times = []
    cursor = int(start_ts)
    end_ts = int(end_ts)
    while cursor <= end_ts:
        sample_times.append(cursor)
        cursor += sampling_seconds
    return sample_times


def generate_single_floor_frames(
    temp_dir: Path,
    sample_times: List[int],
    frames_by_floor: Dict[str, List[Tuple[int, Path]]],
    floor_id: str,
    sidebar_context: Optional[SidebarContext],
) -> None:
    frames = frames_by_floor.get(floor_id, [])
    if not frames:
        return
    idx = 0
    for sample_time in sample_times:
        frame_path = resolve_frame_for_time(frames, sample_time)
        if frame_path is None:
            continue
        target = temp_dir / f"frame_{idx:05d}.png"
        if sidebar_context is None:
            target.write_bytes(frame_path.read_bytes())
        else:
            with Image.open(frame_path) as image:
                sidebar_image = render_sidebar_image(
                    sidebar_context,
                    align="top",
                    target_height=image.height,
                    now=datetime.fromtimestamp(sample_time, tz=timezone.utc),
                )
                combined = attach_sidebar_to_floorplan(image.convert("RGB"), sidebar_image)
                combined.save(target)
        idx += 1


def generate_stitched_frames(
    temp_dir: Path,
    sample_times: List[int],
    frames_by_floor: Dict[str, List[Tuple[int, Path]]],
    floor_ids: List[str],
    sidebar_context: Optional[SidebarContext],
) -> None:
    base_size = None
    for floor_id in floor_ids:
        frames = frames_by_floor.get(floor_id, [])
        if not frames:
            continue
        with Image.open(frames[0][1]) as image:
            base_size = image.size
            break
    if base_size is None:
        return
    fallback_images = {}
    for floor_id in floor_ids:
        frames = frames_by_floor.get(floor_id, [])
        if frames:
            fallback_images[floor_id] = Image.open(frames[0][1])
        else:
            fallback_images[floor_id] = Image.new("RGBA", base_size, (0, 0, 0, 255))
    idx = 0
    try:
        for sample_time in sample_times:
            images: List[Tuple[Optional[str], Image.Image]] = []
            opened_images = []
            for floor_id in floor_ids:
                frames = frames_by_floor.get(floor_id, [])
                frame_path = resolve_frame_for_time(frames, sample_time)
                if frame_path is None:
                    images.append((floor_id, fallback_images[floor_id]))
                    continue
                image = Image.open(frame_path)
                images.append((floor_id, image))
                opened_images.append(image)
            sidebar_image = None
            if sidebar_context and images:
                max_height = max(image.height for _label, image in images)
                sidebar_image = render_sidebar_image(
                    sidebar_context,
                    align="center",
                    target_height=max_height,
                    now=datetime.fromtimestamp(sample_time, tz=timezone.utc),
                )
            stitched_images: List[Tuple[Optional[str], Image.Image]] = []
            for idx, (label, image) in enumerate(images):
                stitched_images.append((label, image))
                if idx == 0 and sidebar_image is not None:
                    stitched_images.append((None, sidebar_image))
            stitched = stitch_images_horizontally(
                stitched_images,
                config.timelapse_border_px,
                config.timelapse_label_font_size,
            )
            target = temp_dir / f"frame_{idx:05d}.png"
            stitched.save(target)
            idx += 1
            for image in opened_images:
                image.close()
    finally:
        for image in fallback_images.values():
            image.close()


def resolve_frame_for_time(frames: List[Tuple[int, Path]], sample_time: int) -> Optional[Path]:
    if not frames:
        return None
    timestamps = [frame[0] for frame in frames]
    index = bisect.bisect_right(timestamps, sample_time) - 1
    if index < 0:
        return None
    return frames[index][1]


def stitch_images_horizontally(
    images: List[Tuple[Optional[str], Image.Image]],
    border_px: int,
    label_font_size: int,
) -> Image.Image:
    font = get_font(label_font_size) if label_font_size > 0 else None
    widths = []
    heights = []
    label_heights = []
    for label, image in images:
        widths.append(image.width)
        heights.append(image.height)
        if label and font:
            text_width, text_height = measure_text_size(font, label)
            label_heights.append(text_height)
    max_height = max(heights)
    label_height = max(label_heights) if label_heights else 0
    total_width = sum(widths) + border_px * (len(images) - 1)
    stitched_height = max_height + border_px + label_height
    stitched = Image.new("RGBA", (total_width, stitched_height), (0, 0, 0, 255))
    draw = ImageDraw.Draw(stitched)
    x_cursor = 0
    for (label, image), image_height in zip(images, heights):
        y_offset = label_height + border_px
        stitched.paste(image, (x_cursor, y_offset))
        if label and font:
            text_width, text_height = measure_text_size(font, label)
            text_x = x_cursor + max(0, (image.width - text_width) // 2)
            draw.text((text_x, 0), label, font=font, fill=(255, 255, 255, 255))
        x_cursor += image.width + border_px
    return stitched.convert("RGB")


def downsample_frames(frame_paths: List[Path], max_frames: int) -> List[Path]:
    if max_frames <= 0:
        return []
    if len(frame_paths) <= max_frames:
        return frame_paths
    stride = len(frame_paths) / max_frames
    return [frame_paths[int(i * stride)] for i in range(max_frames)]


def run_ffmpeg_encode(temp_dir: Path, fps: int, output_path: Path) -> None:
    cmd = [
        "ffmpeg",
        "-y",
        "-framerate",
        str(fps),
        "-i",
        str(temp_dir / "frame_%05d.png"),
        "-vf",
        "scale=trunc(iw/2)*2:trunc(ih/2)*2",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise HTTPException(status_code=500, detail=f"ffmpeg failed: {result.stderr.strip()}")


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


def render_floorplan_base_image(
    floor_id: str,
    payload: Dict,
    grid: np.ndarray,
    metadata: Dict,
    range_override: Optional[Tuple[float, float]] = None,
) -> Image.Image:
    fp = FloorplanV1.parse_obj(payload)
    canvas = Image.new("RGBA", (fp.canvas.width, fp.canvas.height), (20, 20, 20, 255))
    min_f, max_f = range_override or resolve_temperature_range(fp, grid)
    palette = resolve_legend_palette(fp)
    
    # Use Flood Fill mask instead of convex hull to handle concave yards properly
    heatmap_mask = build_floorplan_mask_floodfill(fp)
    
    heatmap = render_heatmap(grid, min_f, max_f, fp.render.overlay_alpha, canvas.size, heatmap_mask, palette)
    canvas = Image.alpha_composite(canvas, heatmap)
    draw = ImageDraw.Draw(canvas)
    if fp.render.show_walls: draw_walls(draw, fp)
    draw_sensors(draw, fp)
    draw_thermostats(draw, fp)
    draw_room_labels(draw, fp)
    if fp.render.auto_crop:
        crop_box = compute_floorplan_crop(fp, canvas.size, fp.render.crop_padding)
        if crop_box: 
            canvas, crop_box = expand_canvas_for_crop(canvas, crop_box)
            canvas = canvas.crop(crop_box)
            draw = ImageDraw.Draw(canvas) # Update draw object for cropped canvas if needed
            # Actually we just crop at the end, that's fine.
    return canvas.convert("RGB")

def build_floorplan_mask_floodfill(fp: FloorplanV1) -> np.ndarray:
    """Creates a mask of the 'inside' of the floorplan using flood fill from sensors."""
    # 1. Setup low-res mask
    w, h = fp.canvas.width // 4, fp.canvas.height // 4
    mask_img = Image.new("L", (w, h), 0)
    draw = ImageDraw.Draw(mask_img)

    # 2. Draw ALL Walls as Barriers (White/255)
    for wall in fp.walls:
        pts = [(p[0]/4, p[1]/4) for p in wall.points]
        draw.line(pts, fill=255, width=2)
    
    # 3. Handle Doors
    for door in fp.doors:
        # Scale door coordinates
        pts = [
            (door.segment[0][0] / 4, door.segment[0][1] / 4),
            (door.segment[1][0] / 4, door.segment[1][1] / 4),
        ]
        
        if is_door_open(fp, door):
            # FIX: If door is OPEN, draw BLACK (0) to cut a hole in the wall
            # This allows the flood fill to pass through the doorway.
            draw.line(pts, fill=0, width=3)
        else:
            # If door is CLOSED, draw WHITE (255) to seal it
            draw.line(pts, fill=255, width=2)

    # 4. Flood Fill from Sensors
    arr = np.array(mask_img) # Barriers=255, Empty=0
    filled = np.zeros_like(arr, dtype=bool)
    
    seeds = []
    for s in fp.sensors:
        seeds.append((int(s.pos[0]/4), int(s.pos[1]/4)))
    for t in fp.thermostats:
        seeds.append((int(t.pos[0]/4), int(t.pos[1]/4)))

    stack = []
    for (sx, sy) in seeds:
        if 0 <= sx < w and 0 <= sy < h and arr[sy, sx] == 0:
            stack.append((sx, sy))
            
    visited = set(stack)
    while stack:
        cx, cy = stack.pop()
        filled[cy, cx] = True
        
        for dx, dy in [(-1,0), (1,0), (0,-1), (0,1)]:
            nx, ny = cx + dx, cy + dy
            if 0 <= nx < w and 0 <= ny < h:
                # Pass if not visited AND not a barrier (0)
                if (nx, ny) not in visited and arr[ny, nx] == 0:
                    visited.add((nx, ny))
                    stack.append((nx, ny))

    # 5. Fallback: If map is empty (no valid sensors), show everything
    if not np.any(filled):
         filled = np.ones_like(arr, dtype=bool)

    # 6. Resize to full resolution
    full_mask = Image.fromarray(filled).resize((fp.canvas.width, fp.canvas.height), Image.Resampling.NEAREST)
    return np.array(full_mask)

def render_heatmap(grid: np.ndarray, min_f: float, max_f: float, overlay_alpha: float, size: Tuple[int, int], mask: Optional[np.ndarray], palette: List[Tuple[int, int, int]]) -> Image.Image:
    norm = np.clip((grid - min_f) / (max_f - min_f + 1e-6), 0, 1)
    colors = np.zeros((grid.shape[0], grid.shape[1], 4), dtype=np.uint8)
    colors[..., 0], colors[..., 1], colors[..., 2] = gradient_rgb(norm, palette)
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


def gradient_rgb(norm: np.ndarray, palette: List[Tuple[int, int, int]]) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    if len(palette) < 2:
        palette = [(0, 0, 255), (255, 0, 0)]
    stops = np.linspace(0.0, 1.0, len(palette))
    colors = np.array(palette, dtype=float)
    idx = np.searchsorted(stops, norm, side="right") - 1
    idx = np.clip(idx, 0, len(stops) - 2)
    t = (norm - stops[idx]) / (stops[idx + 1] - stops[idx])
    c0 = colors[idx]
    c1 = colors[idx + 1]
    blended = c0 + (c1 - c0) * t[..., None]
    return blended[..., 0].astype(np.uint8), blended[..., 1].astype(np.uint8), blended[..., 2].astype(np.uint8)

def parse_hex_color(value: str) -> Optional[Tuple[int, int, int]]:
    cleaned = value.strip().lstrip("#")
    if len(cleaned) not in {3, 6}:
        return None
    try:
        if len(cleaned) == 3:
            cleaned = "".join([ch * 2 for ch in cleaned])
        return tuple(int(cleaned[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return None

def resolve_legend_palette(fp: FloorplanV1) -> List[Tuple[int, int, int]]:
    if fp.render.legend_colors:
        parsed = []
        for raw in fp.render.legend_colors:
            color = parse_hex_color(raw)
            if color:
                parsed.append(color)
        if len(parsed) >= 2:
            return parsed
    return [
        (0, 0, 255),
        (0, 255, 255),
        (0, 255, 0),
        (255, 255, 0),
        (255, 0, 0),
    ]


def resolve_temperature_range(fp: FloorplanV1, grid: np.ndarray) -> Tuple[float, float]:
    grid_min = float(np.min(grid))
    grid_max = float(np.max(grid))
    min_f = fp.render.temp_range_f.min if fp.render.scale_min_mode == "absolute" else grid_min
    max_f = fp.render.temp_range_f.max if fp.render.scale_max_mode == "absolute" else grid_max
    if min_f >= max_f: max_f = min_f + 0.1
    return min_f, max_f

def add_exterior_margin(
    image: Image.Image,
    margin: int,
    top_padding: int,
    bottom_padding: int,
    left_padding: Optional[int] = None,
    right_padding: Optional[int] = None,
) -> Image.Image:
    top = top_padding if top_padding > 0 else margin // 2
    bottom = bottom_padding if bottom_padding > 0 else margin // 2
    left = left_padding if left_padding is not None else margin
    right = right_padding if right_padding is not None else margin
    new_width = image.width + left + right
    new_height = image.height + top + bottom
    canvas = Image.new("RGBA", (new_width, new_height), (20, 20, 20, 255))
    canvas.paste(image, (left, top))
    return canvas

def expand_canvas_for_crop(
    image: Image.Image,
    crop_box: Tuple[int, int, int, int],
) -> Tuple[Image.Image, Tuple[int, int, int, int]]:
    min_x, min_y, max_x, max_y = crop_box
    left_pad = max(0, -min_x)
    top_pad = max(0, -min_y)
    right_pad = max(0, max_x - image.width)
    bottom_pad = max(0, max_y - image.height)
    if not any([left_pad, top_pad, right_pad, bottom_pad]):
        return image, crop_box
    new_width = image.width + left_pad + right_pad
    new_height = image.height + top_pad + bottom_pad
    expanded = Image.new("RGBA", (new_width, new_height), (20, 20, 20, 255))
    expanded.paste(image, (left_pad, top_pad))
    shifted_crop = (
        min_x + left_pad,
        min_y + top_pad,
        max_x + left_pad,
        max_y + top_pad,
    )
    return expanded, shifted_crop

def compute_floorplan_crop(fp: FloorplanV1, canvas_size: Tuple[int, int], padding: int) -> Optional[Tuple[int, int, int, int]]:
    points = []
    for wall in fp.walls: points.extend([point_xy(p) for p in wall.points])
    if not points: return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    min_x = int(min(xs) - padding)
    min_y = int(min(ys) - padding)
    max_x = int(max(xs) + padding)
    max_y = int(max(ys) + padding)
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
            font, font_size = resolve_font(fp, sensor.font_size)
            
            # New Logic: Multiline
            lines = []
            if label: lines.append(label)
            if temp_val: lines.append(temp_val)
            
            off_x = sensor.label_offset_x
            current_y = y + sensor.label_offset_y
            
            for line in lines:
                draw.text((x + off_x, current_y), line, fill=(255, 255, 255), font=font)
                # Approximate line height = font_size + 2
                current_y += (font_size + 2)

def draw_thermostats(draw: ImageDraw.ImageDraw, fp: FloorplanV1) -> None:
    for thermo in fp.thermostats:
        x, y = point_xy(thermo.pos)
        draw.rectangle((x - 8, y - 8, x + 8, y + 8), outline=(255, 200, 50), width=2)
        if fp.render.show_labels:
            name_line = thermo.device_label or "Thermostat"
            temp_val = format_entity_temperature(thermo.temperature_entity)
            setpoint_val = format_entity_temperature(thermo.setpoint_entity)
            setpoint_low = format_entity_temperature(thermo.setpoint_low_entity)
            setpoint_high = format_entity_temperature(thermo.setpoint_high_entity)
            mode = read_entity_state(thermo.mode_entity) if thermo.mode_entity else ""
            mode_lower = mode.lower() if mode else ""

            setpoint_line = ""
            if setpoint_low and setpoint_high:
                setpoint_line = f"{setpoint_low} / {setpoint_high}"
            elif mode_lower in {"heat_cool", "auto"}:
                setpoint_line = setpoint_low or setpoint_high or setpoint_val
            elif mode_lower == "heat":
                setpoint_line = setpoint_val or setpoint_low
            elif mode_lower == "cool":
                setpoint_line = setpoint_val or setpoint_high
            else:
                setpoint_line = setpoint_val or setpoint_low or setpoint_high

            action_line = ""
            if mode:
                action_line = mode.replace("_", " ").title()
            fan_state = ""
            if thermo.fan_entity:
                fan_raw = read_entity_state(thermo.fan_entity)
                if fan_raw.lower() in {"on", "true", "1", "enabled"}:
                    fan_state = "Fan On"
            if fan_state:
                action_line = f"{action_line} • {fan_state}" if action_line else fan_state

            font, font_size = resolve_font(fp, thermo.font_size)

            lines = [name_line]
            if temp_val:
                lines.append(temp_val)
            if setpoint_line:
                lines.append(setpoint_line)
            if action_line:
                lines.append(action_line)
            
            off_x = thermo.label_offset_x
            current_y = y + thermo.label_offset_y
            
            for line in lines:
                draw.text((x + off_x, current_y), line, fill=(255, 200, 50), font=font)
                current_y += (font_size + 2)

def draw_room_labels(draw: ImageDraw.ImageDraw, fp: FloorplanV1) -> None:
    for label in fp.room_labels:
        if not label.label:
            continue
        x, y = point_xy(label.pos)
        font, _font_size = resolve_font(fp, label.font_size)
        draw.text((x + label.label_offset_x, y + label.label_offset_y), label.label, fill=(255, 255, 255), font=font)

def resolve_outside_temperature(fp: FloorplanV1) -> Optional[Tuple[float, str]]:
    outside_temp_value = None
    if fp.render.outside_temp_entity:
        outside_temp_value = read_entity_temperature_value(fp.render.outside_temp_entity)
    elif fp.render.outside_temp_f is not None:
        outside_temp_value = fp.render.outside_temp_f
    if outside_temp_value is None:
        return None
    outside_temp = f"{outside_temp_value:.1f}F"
    label = fp.render.outside_temp_label.strip() or "Outside"
    text = f"{label}: {outside_temp}"
    return outside_temp_value, text

def draw_outside_temperature(
    draw: ImageDraw.ImageDraw,
    fp: FloorplanV1,
    min_f: float,
    max_f: float,
    top_offset: int,
    left_offset: int,
    outside_temp_value: float,
    text: str,
) -> int:
    font, font_size = resolve_font(fp, 12)
    box_size = max(16, int(font_size * 1.4))
    box_y = top_offset + max(0, (font_size - box_size) // 2)
    color = color_for_temperature(outside_temp_value, min_f, max_f, resolve_legend_palette(fp))
    draw.rectangle(
        (left_offset, box_y, left_offset + box_size, box_y + box_size),
        fill=color,
        outline=(255, 255, 255),
        width=1,
    )
    draw.text((left_offset + box_size + 6, top_offset), text, fill=(255, 255, 255), font=font)
    return max(box_size, font_size)

def fetch_history_series(
    entity_id: Optional[str],
    hours: float,
    end_time: Optional[datetime] = None,
) -> List[Tuple[datetime, float]]:
    if not entity_id or not config.ha_base_url or not config.ha_token or hours <= 0:
        return []
    end_time = end_time or datetime.now(timezone.utc)
    start = end_time - timedelta(hours=hours)
    url = f"{config.ha_base_url.rstrip('/')}/api/history/period/{start.isoformat()}"
    headers = {"Authorization": f"Bearer {config.ha_token}"}
    params = {
        "filter_entity_id": entity_id,
        "minimal_response": "1",
        "end_time": end_time.isoformat(),
    }
    try:
        response = requests.get(url, headers=headers, params=params, timeout=10)
    except requests.RequestException:
        return []
    if response.status_code != 200:
        return []
    payload = response.json()
    if not payload or not isinstance(payload, list) or not payload[0]:
        return []
    series = []
    for item in payload[0]:
        state = item.get("state")
        if state is None:
            continue
        try:
            temp = float(state)
        except (TypeError, ValueError):
            continue
        raw_ts = item.get("last_updated") or item.get("last_changed")
        if not raw_ts:
            continue
        try:
            ts = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
        except ValueError:
            continue
        series.append((ts, temp))
    return series

def fetch_state_history(
    entity_id: Optional[str],
    hours: float,
    end_time: Optional[datetime] = None,
) -> List[Tuple[datetime, str]]:
    if not entity_id or not config.ha_base_url or not config.ha_token or hours <= 0:
        return []
    end_time = end_time or datetime.now(timezone.utc)
    start = end_time - timedelta(hours=hours)
    url = f"{config.ha_base_url.rstrip('/')}/api/history/period/{start.isoformat()}"
    headers = {"Authorization": f"Bearer {config.ha_token}"}
    params = {
        "filter_entity_id": entity_id,
        "minimal_response": "1",
        "end_time": end_time.isoformat(),
    }
    try:
        response = requests.get(url, headers=headers, params=params, timeout=10)
    except requests.RequestException:
        return []
    if response.status_code != 200:
        return []
    payload = response.json()
    if not payload or not isinstance(payload, list) or not payload[0]:
        return []
    series = []
    for item in payload[0]:
        state = item.get("state")
        if state is None:
            continue
        raw_ts = item.get("last_updated") or item.get("last_changed")
        if not raw_ts:
            continue
        try:
            ts = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
        except ValueError:
            continue
        series.append((ts, str(state)))
    return series

def order_thermostats_for_charts(thermostats: List[Thermostat]) -> List[Thermostat]:
    preferred_labels = ("living room", "loft")

    def sort_key(thermo: Thermostat) -> Tuple[int, int, str]:
        label = (thermo.device_label or thermo.id or "").lower()
        for idx, preferred in enumerate(preferred_labels):
            if preferred in label:
                return (0, idx, label)
        if "up" in label:
            return (1, 0, label)
        if "down" in label:
            return (1, 1, label)
        return (2, 0, label)

    return sorted(thermostats, key=sort_key)

def format_time_tick(ts: datetime) -> str:
    return ts.astimezone().strftime("%I%p").lstrip("0")

def draw_time_axis(
    draw: ImageDraw.ImageDraw,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    font_size: int,
    start: datetime,
    end: datetime,
    x0: float,
    x1: float,
    y_axis: float,
    label_y: float,
) -> None:
    total_seconds = (end - start).total_seconds() or 1.0
    def to_x(ts: datetime) -> float:
        return x0 + ((ts - start).total_seconds() / total_seconds) * (x1 - x0)

    midpoint = start + (end - start) / 2
    for ts in (start, midpoint, end):
        x = to_x(ts)
        draw.line([(x, y_axis), (x, y_axis + 4)], fill=(255, 255, 255), width=1)
        label = format_time_tick(ts)
        label_width = measure_text_width(font, label)
        draw.text((x - label_width / 2, label_y), label, fill=(255, 255, 255), font=font)

def fetch_forecast_series(entity_id: Optional[str]) -> List[Tuple[datetime, float]]:
    if not entity_id or not config.ha_base_url or not config.ha_token:
        return []
    url = f"{config.ha_base_url.rstrip('/')}/api/states/{entity_id}"
    headers = {"Authorization": f"Bearer {config.ha_token}"}
    try:
        response = requests.get(url, headers=headers, timeout=10)
    except requests.RequestException:
        return []
    if response.status_code != 200:
        return []
    payload = response.json()
    forecast = payload.get("attributes", {}).get("forecast") or []
    series = []
    for entry in forecast:
        temp = entry.get("temperature")
        if temp is None and "temp" in entry:
            temp = entry.get("temp")
        if temp is None:
            continue
        ts_raw = entry.get("datetime") or entry.get("time")
        if not ts_raw:
            continue
        try:
            ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
        except ValueError:
            continue
        series.append((ts, float(temp)))
    return series

def draw_temperature_chart(
    draw: ImageDraw.ImageDraw,
    fp: FloorplanV1,
    size: Tuple[int, int],
    min_f: float,
    max_f: float,
    origin: Optional[Tuple[int, int]] = None,
    chart_width: Optional[int] = None,
    chart_height: Optional[int] = None,
    history_hours: Optional[float] = None,
    forecast_hours: Optional[float] = None,
    temp_entity: Optional[str] = None,
    forecast_entity: Optional[str] = None,
    now: Optional[datetime] = None,
) -> int:
    entity_id = temp_entity or fp.render.chart_temp_entity or fp.render.outside_temp_entity
    if not entity_id:
        return 0
    history_hours = max(history_hours if history_hours is not None else fp.render.chart_history_hours, 0.0)
    forecast_hours = max(forecast_hours if forecast_hours is not None else fp.render.chart_forecast_hours, 0.0)
    if history_hours == 0 and forecast_hours == 0:
        return 0
    now = now or datetime.now(timezone.utc)
    history = fetch_history_series(entity_id, history_hours, end_time=now)
    forecast_source = forecast_entity if forecast_entity is not None else fp.render.chart_forecast_entity
    forecast = []
    if forecast_source:
        if abs((datetime.now(timezone.utc) - now).total_seconds()) < 900:
            forecast = fetch_forecast_series(forecast_source)
    start = now - timedelta(hours=history_hours)
    end = now + timedelta(hours=forecast_hours)
    if not history and not forecast:
        return 0

    data_points = [(ts, temp) for ts, temp in history if start <= ts <= end]
    data_points += [(ts, temp) for ts, temp in forecast if start <= ts <= end]
    if not data_points:
        return 0

    temps = [temp for _ts, temp in data_points]
    chart_min = min(min(temps), min_f)
    chart_max = max(max(temps), max_f)
    if chart_min >= chart_max:
        chart_max = chart_min + 0.1

    font, font_size = resolve_font(fp, 11)
    margin = fp.render.exterior_margin
    width = max(160, chart_width if chart_width is not None else fp.render.chart_width)
    height = max(90, chart_height if chart_height is not None else fp.render.chart_height)
    origin_x = origin[0] if origin else max(margin, size[0] - margin - width)
    origin_y = origin[1] if origin else max(margin, size[1] - margin - height)
    title = "Temperature"
    title_height = font_size + 2
    y_axis_label_width = measure_text_width(font, f"{chart_max:.1f}F") + 6
    bottom_label_height = font_size + 6
    right_pad = 6
    x0 = origin_x + y_axis_label_width
    y0 = origin_y + title_height + 4
    x1 = origin_x + width - right_pad
    y1 = origin_y + height - bottom_label_height

    draw.text((origin_x, origin_y), title, fill=(255, 255, 255), font=font)
    draw.rectangle((x0, y0, x1, y1), outline=(255, 255, 255), width=1)

    total_seconds = (end - start).total_seconds() or 1.0
    def to_xy(ts: datetime, temp: float) -> Tuple[float, float]:
        ratio_x = (ts - start).total_seconds() / total_seconds
        ratio_y = (temp - chart_min) / (chart_max - chart_min)
        x = x0 + ratio_x * (x1 - x0)
        y = y1 - ratio_y * (y1 - y0)
        return x, y

    hour_cursor = start.replace(minute=0, second=0, microsecond=0)
    while hour_cursor < end:
        next_hour = hour_cursor + timedelta(hours=1)
        hour_center = hour_cursor + (next_hour - hour_cursor) / 2
        is_day = 6 <= hour_center.astimezone().hour < 18
        if not is_day:
            x_start, _ = to_xy(hour_cursor, chart_min)
            x_end, _ = to_xy(next_hour, chart_min)
            draw.rectangle((x_start, y0, x_end, y1), fill=(80, 80, 80, 60))
        hour_cursor = next_hour

    palette = resolve_legend_palette(fp)
    history_points = [(ts, *to_xy(ts, temp), temp) for ts, temp in history if start <= ts <= now]
    for idx in range(len(history_points) - 1):
        _ts, x_start, y_start, temp_start = history_points[idx]
        _ts2, x_end, y_end, temp_end = history_points[idx + 1]
        segment_temp = (temp_start + temp_end) / 2
        color = color_for_temperature(segment_temp, min_f, max_f, palette)
        draw.line([(x_start, y_start), (x_end, y_end)], fill=color, width=2)

    forecast_points = [(ts, *to_xy(ts, temp), temp) for ts, temp in forecast if now <= ts <= end]
    for idx in range(len(forecast_points) - 1):
        _ts, x_start, y_start, temp_start = forecast_points[idx]
        _ts2, x_end, y_end, temp_end = forecast_points[idx + 1]
        segment_temp = (temp_start + temp_end) / 2
        color = color_for_temperature(segment_temp, min_f, max_f, palette)
        draw.line([(x_start, y_start), (x_end, y_end)], fill=color, width=2)

    current_temp = read_entity_temperature_value(entity_id)
    if current_temp is not None:
        cx, cy = to_xy(now, current_temp)
        draw.ellipse((cx - 3, cy - 3, cx + 3, cy + 3), fill=(255, 255, 255))

    y_ticks = [chart_min, (chart_min + chart_max) / 2, chart_max]
    for tick in y_ticks:
        _, y = to_xy(start, tick)
        draw.line([(x0 - 4, y), (x0, y)], fill=(255, 255, 255), width=1)
        label = f"{tick:.1f}F"
        label_width = measure_text_width(font, label)
        draw.text((x0 - 6 - label_width, y - (font_size / 2)), label, fill=(255, 255, 255), font=font)

    def format_tick_time(ts: datetime) -> str:
        return ts.astimezone().strftime("%I%p").lstrip("0")

    tick_positions = [start, now, end]
    if not (start <= now <= end):
        mid = start + (end - start) / 2
        tick_positions = [start, mid, end]
    for ts in tick_positions:
        x, _ = to_xy(ts, chart_min)
        draw.line([(x, y1), (x, y1 + 4)], fill=(255, 255, 255), width=1)
        label = "Now" if abs((ts - now).total_seconds()) < 900 else format_tick_time(ts)
        label_width = measure_text_width(font, label)
        draw.text((x - label_width / 2, y1 + 4), label, fill=(255, 255, 255), font=font)

    return height

def draw_dashed_line(
    draw: ImageDraw.ImageDraw,
    points: List[Tuple[float, float]],
    color: Tuple[int, int, int],
    dash_length: float = 6.0,
    gap_length: float = 4.0,
    width: int = 2,
) -> None:
    if len(points) < 2:
        return
    for start, end in zip(points, points[1:]):
        x0, y0 = start
        x1, y1 = end
        dx = x1 - x0
        dy = y1 - y0
        dist = math.hypot(dx, dy)
        if dist == 0:
            continue
        ux = dx / dist
        uy = dy / dist
        progress = 0.0
        while progress < dist:
            seg_end = min(progress + dash_length, dist)
            sx = x0 + ux * progress
            sy = y0 + uy * progress
            ex = x0 + ux * seg_end
            ey = y0 + uy * seg_end
            draw.line([(sx, sy), (ex, ey)], fill=color, width=width)
            progress += dash_length + gap_length

def thermostat_action_from_state(state: str) -> str:
    normalized = state.lower()
    if "heat" in normalized:
        return "heat"
    if "cool" in normalized:
        return "cool"
    return "idle"

def build_state_timeline(
    history: List[Tuple[datetime, str]],
    start: datetime,
    end: datetime,
    fallback_state: Optional[str],
) -> List[Tuple[datetime, str]]:
    points = [(ts, state) for ts, state in history if start <= ts <= end]
    if not points:
        if fallback_state:
            return [(start, fallback_state), (end, fallback_state)]
        return []
    if points[0][0] > start:
        points.insert(0, (start, points[0][1]))
    if points[-1][0] < end:
        points.append((end, points[-1][1]))
    return points

def build_step_points(series: List[Tuple[datetime, float]]) -> List[Tuple[datetime, float]]:
    if not series:
        return []
    points = [(series[0][0], series[0][1])]
    for (prev_ts, prev_val), (ts, val) in zip(series, series[1:]):
        if ts <= prev_ts:
            continue
        points.append((ts, prev_val))
        points.append((ts, val))
    return points


def extend_series_to_range(
    series: List[Tuple[datetime, float]],
    start: datetime,
    end: datetime,
) -> List[Tuple[datetime, float]]:
    if not series:
        return []
    series = sorted(series, key=lambda entry: entry[0])
    if series[0][0] > start:
        series.insert(0, (start, series[0][1]))
    if series[-1][0] < end:
        series.append((end, series[-1][1]))
    return series


def draw_time_ticks(
    draw: ImageDraw.ImageDraw,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    font_size: int,
    start: datetime,
    end: datetime,
    x0: float,
    x1: float,
    y_axis: float,
    label_y: float,
    now: Optional[datetime] = None,
) -> None:
    now = now or datetime.now(timezone.utc)
    total_seconds = (end - start).total_seconds() or 1.0

    def to_x(ts: datetime) -> float:
        return x0 + ((ts - start).total_seconds() / total_seconds) * (x1 - x0)

    tick_positions = [start, now, end]
    if not (start <= now <= end):
        mid = start + (end - start) / 2
        tick_positions = [start, mid, end]
    for ts in tick_positions:
        x = to_x(ts)
        draw.line([(x, y_axis), (x, y_axis + 4)], fill=(255, 255, 255), width=1)
        label = "Now" if abs((ts - now).total_seconds()) < 900 else format_time_tick(ts)
        label_width = measure_text_width(font, label)
        draw.text((x - label_width / 2, label_y), label, fill=(255, 255, 255), font=font)


def lighten_color(color: Tuple[int, int, int], factor: float = 0.4) -> Tuple[int, int, int]:
    r, g, b = color
    return (
        int(r + (255 - r) * factor),
        int(g + (255 - g) * factor),
        int(b + (255 - b) * factor),
    )


def compute_action_percentages(
    timeline: List[Tuple[datetime, str]],
    start: datetime,
    end: datetime,
) -> Tuple[int, int]:
    if len(timeline) < 2:
        return 0, 0
    heat_seconds = 0.0
    cool_seconds = 0.0
    total_seconds = (end - start).total_seconds() or 1.0
    for (ts_start, state_start), (ts_end, _state_end) in zip(timeline, timeline[1:]):
        duration = max(0.0, (ts_end - ts_start).total_seconds())
        action = thermostat_action_from_state(state_start)
        if action == "heat":
            heat_seconds += duration
        elif action == "cool":
            cool_seconds += duration
    heat_pct = int(round((heat_seconds / total_seconds) * 100))
    cool_pct = int(round((cool_seconds / total_seconds) * 100))
    return heat_pct, cool_pct


def draw_thermostat_action_chart(
    draw: ImageDraw.ImageDraw,
    fp: FloorplanV1,
    size: Tuple[int, int],
    origin: Optional[Tuple[int, int]] = None,
    thermostats: Optional[List[Thermostat]] = None,
    history_hours: Optional[float] = None,
    chart_width: Optional[int] = None,
    chart_height: Optional[int] = None,
    now: Optional[datetime] = None,
) -> int:
    history_hours = max(history_hours if history_hours is not None else fp.render.thermostat_chart_history_hours, 0.0)
    if history_hours == 0:
        return 0
    available = thermostats if thermostats is not None else fp.thermostats
    thermostats = order_thermostats_for_charts([thermo for thermo in available if thermo.mode_entity])
    if not thermostats:
        return 0
    now = now or datetime.now(timezone.utc)
    start = now - timedelta(hours=history_hours)
    end = now

    font, font_size = resolve_font(fp, 11)
    margin = fp.render.exterior_margin
    width = max(180, chart_width if chart_width is not None else fp.render.thermostat_chart_width)
    height = max(120, chart_height if chart_height is not None else fp.render.thermostat_chart_height)
    chart_gap = 10
    origin_x = origin[0] if origin else max(margin, size[0] - margin - width)
    origin_y = origin[1] if origin else max(margin, size[1] - margin - height)

    total_height = (height * len(thermostats)) + (chart_gap * (len(thermostats) - 1))
    for idx, thermo in enumerate(thermostats):
        chart_origin_y = origin_y + idx * (height + chart_gap)
        title = f"{thermo.device_label or thermo.id} Action (24h)"
        title_height = font_size + 2
        bottom_label_height = font_size + 6
        axis_label_width = measure_text_width(font, "Off") + 6
        x0 = origin_x + axis_label_width
        y0 = chart_origin_y + title_height + 4
        x1 = origin_x + width - 6
        y1 = chart_origin_y + height - bottom_label_height
        if x1 <= x0 or y1 <= y0:
            continue

        draw.text((origin_x, chart_origin_y), title, fill=(255, 255, 255), font=font)
        draw.rectangle((x0, y0, x1, y1), outline=(255, 255, 255), width=1)

        history = fetch_state_history(thermo.mode_entity, history_hours, end_time=now)
        fallback_state = read_entity_state(thermo.mode_entity) if thermo.mode_entity else None
        timeline = build_state_timeline(history, start, end, fallback_state)
        if not timeline:
            continue

        heat_pct, cool_pct = compute_action_percentages(timeline, start, end)
        stats_text = f"Heat {heat_pct}% • Cool {cool_pct}%"
        stats_width = measure_text_width(font, stats_text)
        draw.text((origin_x + width - stats_width, chart_origin_y), stats_text, fill=(180, 180, 180), font=font)

        total_seconds = (end - start).total_seconds() or 1.0

        def to_x(ts: datetime) -> float:
            return x0 + ((ts - start).total_seconds() / total_seconds) * (x1 - x0)

        def to_y(value: float) -> float:
            return y1 - value * (y1 - y0)

        heat_color = (220, 80, 80)
        cool_color = (80, 160, 255)
        for action, color in [("heat", heat_color), ("cool", cool_color)]:
            series = [(ts, 1.0 if thermostat_action_from_state(state) == action else 0.0) for ts, state in timeline]
            points = build_step_points(series)
            for (ts_start, value_start), (ts_end, _value_end) in zip(series, series[1:]):
                if value_start <= 0:
                    continue
                x_start = to_x(ts_start)
                x_end = to_x(ts_end)
                fill_color = (*lighten_color(color, 0.5), 80)
                draw.rectangle((x_start, y0, x_end, y1), fill=fill_color)
            if len(points) > 1:
                draw.line([(to_x(ts), to_y(value)) for ts, value in points], fill=color, width=2)

        for label, value in [("Off", 0.0), ("On", 1.0)]:
            y = to_y(value)
            draw.line([(x0 - 4, y), (x0, y)], fill=(255, 255, 255), width=1)
            label_width = measure_text_width(font, label)
            draw.text((x0 - 6 - label_width, y - (font_size / 2)), label, fill=(255, 255, 255), font=font)

        draw_time_ticks(draw, font, font_size, start, end, x0, x1, y1, y1 + 4, now=now)

    return total_height

def draw_thermostat_setpoint_chart(
    draw: ImageDraw.ImageDraw,
    fp: FloorplanV1,
    size: Tuple[int, int],
    origin: Optional[Tuple[int, int]] = None,
    thermostats: Optional[List[Thermostat]] = None,
    history_hours: Optional[float] = None,
    chart_width: Optional[int] = None,
    chart_height: Optional[int] = None,
    now: Optional[datetime] = None,
) -> int:
    history_hours = max(history_hours if history_hours is not None else fp.render.thermostat_chart_history_hours, 0.0)
    if history_hours == 0:
        return 0
    available = thermostats if thermostats is not None else fp.thermostats
    thermostats = order_thermostats_for_charts(
        [
            thermo
            for thermo in available
            if thermo.setpoint_low_entity or thermo.setpoint_high_entity or thermo.setpoint_entity
        ]
    )
    if not thermostats:
        return 0
    now = now or datetime.now(timezone.utc)
    start = now - timedelta(hours=history_hours)
    end = now

    font, font_size = resolve_font(fp, 11)
    margin = fp.render.exterior_margin
    width = max(180, chart_width if chart_width is not None else fp.render.thermostat_chart_width)
    height = max(120, chart_height if chart_height is not None else fp.render.thermostat_chart_height)
    chart_gap = 10
    origin_x = origin[0] if origin else max(margin, size[0] - margin - width)
    origin_y = origin[1] if origin else max(margin, size[1] - margin - height)

    total_height = (height * len(thermostats)) + (chart_gap * (len(thermostats) - 1))
    for idx, thermo in enumerate(thermostats):
        chart_origin_y = origin_y + idx * (height + chart_gap)
        title = f"{thermo.device_label or thermo.id} Setpoints (24h)"
        title_height = font_size + 2
        bottom_pad = font_size + 6
        y_axis_label_width = measure_text_width(font, "88.8F") + 6
        x0 = origin_x + y_axis_label_width
        y0 = chart_origin_y + title_height + 4
        x1 = origin_x + width - 6
        y1 = chart_origin_y + height - bottom_pad
        if x1 <= x0 or y1 <= y0:
            continue

        draw.text((origin_x, chart_origin_y), title, fill=(255, 255, 255), font=font)
        draw.rectangle((x0, y0, x1, y1), outline=(255, 255, 255), width=1)

        low_series = fetch_history_series(thermo.setpoint_low_entity, history_hours, end_time=now)
        high_series = fetch_history_series(thermo.setpoint_high_entity, history_hours, end_time=now)
        setpoint_series = fetch_history_series(thermo.setpoint_entity, history_hours, end_time=now)
        if not low_series and setpoint_series:
            low_series = setpoint_series
        if not high_series and setpoint_series:
            high_series = setpoint_series
        low_series = extend_series_to_range(low_series, start, end)
        high_series = extend_series_to_range(high_series, start, end)
        all_values = [temp for _ts, temp in (low_series + high_series)]
        if not all_values:
            continue
        chart_min = min(all_values)
        chart_max = max(all_values)
        if chart_min >= chart_max:
            chart_max = chart_min + 0.1

        total_seconds = (end - start).total_seconds() or 1.0

        def to_x(ts: datetime) -> float:
            return x0 + ((ts - start).total_seconds() / total_seconds) * (x1 - x0)

        def to_y(temp: float) -> float:
            ratio = (temp - chart_min) / (chart_max - chart_min)
            return y1 - ratio * (y1 - y0)

        for tick in [chart_min, (chart_min + chart_max) / 2, chart_max]:
            y = to_y(tick)
            draw.line([(x0 - 4, y), (x0, y)], fill=(255, 255, 255), width=1)
            label = f"{tick:.1f}F"
            label_width = measure_text_width(font, label)
            draw.text((x0 - 6 - label_width, y - (font_size / 2)), label, fill=(255, 255, 255), font=font)

        for series, color in [(high_series, (220, 80, 80)), (low_series, (80, 160, 255))]:
            if len(series) < 2:
                continue
            points = build_step_points(series)
            draw_dashed_line(
                draw,
                [(to_x(ts), to_y(temp)) for ts, temp in points],
                color,
                width=2,
            )

        draw_time_ticks(draw, font, font_size, start, end, x0, x1, y1, y1 + 4, now=now)

    return total_height

def get_font(size: int, font_path: Optional[str] = None) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Tries to load a scalable font. Falls back to default if unavailable."""
    # Common paths for DejaVuSans on Linux/Debian
    candidates = [font_path] if font_path else []
    candidates += [
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

def resolve_font(fp: FloorplanV1, default_size: int) -> Tuple[ImageFont.FreeTypeFont | ImageFont.ImageFont, int]:
    size = fp.render.text_font_size or default_size
    return get_font(size, fp.render.text_font_path), size

def read_entity_temperature_value(entity_id: Optional[str]) -> Optional[float]:
    if not entity_id:
        return None
    state = read_entity_state(entity_id)
    if state == "n/a":
        return None
    try:
        return float(state)
    except ValueError:
        return None

def read_entity_state(entity_id: str) -> str:
    with ha_lock: state = ha_states.get(entity_id)
    return state.state if state else "n/a"

def format_entity_temperature(entity_id: Optional[str]) -> str:
    if not entity_id: return ""
    state = read_entity_state(entity_id)
    if state == "n/a": return ""
    try: return f"{float(state):.1f}F"
    except ValueError: return ""

def draw_legend(
    draw: ImageDraw.ImageDraw,
    min_f: float,
    max_f: float,
    origin: Tuple[int, int],
    fp: FloorplanV1,
    palette: List[Tuple[int, int, int]],
) -> int:
    font, font_size = resolve_font(fp, 12)
    x0 = origin[0]
    y0 = origin[1]
    x1 = x0 + 200
    gradient_height = 26
    label_offset = font_size + 4
    y1 = y0 + gradient_height
    for i in range(x0, x1):
        t = (i - x0) / (x1 - x0)
        r, g, b = gradient_rgb(np.array([t]), palette)
        draw.line([(i, y0), (i, y1)], fill=(int(r[0]), int(g[0]), int(b[0])))
    draw.rectangle((x0, y0, x1, y1), outline=(255, 255, 255), width=1)
    draw.text((x0, y0 - label_offset), f"{min_f:.1f}F", fill=(255, 255, 255), font=font)
    max_label = f"{max_f:.1f}F"
    max_label_width = measure_text_width(font, max_label)
    draw.text((x1 - max_label_width, y0 - label_offset), max_label, fill=(255, 255, 255), font=font)
    return label_offset + gradient_height

def build_timestamp_lines(now: datetime) -> List[str]:
    date_line = now.strftime("%b %d, %Y")
    time_line = now.strftime("%I:%M%p").lstrip("0")
    return [date_line, time_line]

def measure_multiline_height(line_count: int, font_size: int, spacing: int) -> int:
    if line_count <= 0:
        return 0
    return (font_size * line_count) + (spacing * (line_count - 1))

def measure_text_width(font: ImageFont.ImageFont | ImageFont.FreeTypeFont, text: str) -> int:
    try:
        bbox = font.getbbox(text)
        return bbox[2] - bbox[0]
    except AttributeError:
        width, _height = font.getsize(text)
        return width


def measure_text_size(font: ImageFont.ImageFont | ImageFont.FreeTypeFont, text: str) -> Tuple[int, int]:
    try:
        bbox = font.getbbox(text)
        return bbox[2] - bbox[0], bbox[3] - bbox[1]
    except AttributeError:
        return font.getsize(text)


def clock_radius_for_font(font_size: int) -> int:
    return max(12, int(font_size * 2.2))

def compute_text_block_height(fp: FloorplanV1) -> int:
    font_size = fp.render.text_font_size or 12
    spacing = max(2, int(font_size * 0.2))
    height = 0
    if fp.render.show_timestamp:
        timestamp_height = measure_multiline_height(1, font_size, spacing)
        clock_height = clock_radius_for_font(font_size) * 2
        height += max(timestamp_height, clock_height)
    if fp.render.show_outside_temp:
        if height:
            height += 4
        height += font_size
    return height

def compute_timestamp_block_height(fp: FloorplanV1) -> int:
    font_size = fp.render.text_font_size or 12
    spacing = max(2, int(font_size * 0.2))
    line_count = len(build_timestamp_lines(datetime.now()))
    timestamp_height = measure_multiline_height(line_count, font_size, spacing)
    clock_height = clock_radius_for_font(font_size) * 2
    gap = 6
    return timestamp_height + gap + clock_height

def compute_timestamp_block_width(fp: FloorplanV1) -> int:
    font, _font_size = resolve_font(fp, 12)
    now = datetime.now()
    text_width = max((measure_text_width(font, line) for line in build_timestamp_lines(now)), default=0)
    clock_diameter = clock_radius_for_font(fp.render.text_font_size or 12) * 2
    return int(max(text_width, clock_diameter))

def compute_legend_height(fp: FloorplanV1) -> int:
    font_size = fp.render.text_font_size or 12
    label_offset = font_size + 4
    gradient_height = 26
    return label_offset + gradient_height

def compute_chart_height(fp: FloorplanV1) -> int:
    return max(120, fp.render.chart_height)

def compute_thermostat_chart_height(fp: FloorplanV1) -> int:
    return max(120, fp.render.thermostat_chart_height)

def should_render_thermostat_charts(fp: FloorplanV1) -> bool:
    return fp.render.thermostat_chart_history_hours > 0

def resolve_sidebar_components() -> List[SidebarComponentConfig]:
    return [component for component in config.render_sidebar.components if component.enabled]


def resolve_primary_floorplan(floorplans: List[FloorplanV1]) -> FloorplanV1:
    return floorplans[0]


def resolve_outside_temperature_for_floorplans(
    floorplans: List[FloorplanV1],
) -> Optional[Tuple[float, str, FloorplanV1]]:
    for fp in floorplans:
        outside_info = resolve_outside_temperature(fp)
        if outside_info:
            outside_temp_value, outside_text = outside_info
            return outside_temp_value, outside_text, fp
    return None


def resolve_sidebar_context(
    floorplans: List[FloorplanV1],
    min_f: float,
    max_f: float,
) -> SidebarContext:
    primary = resolve_primary_floorplan(floorplans)
    thermostats: List[Thermostat] = []
    for fp in floorplans:
        thermostats.extend(fp.thermostats)
    return SidebarContext(
        floorplans=floorplans,
        thermostats=thermostats,
        min_f=min_f,
        max_f=max_f,
        palette=resolve_legend_palette(primary),
        primary_floorplan=primary,
    )


def compute_sidebar_panel_height(context: SidebarContext, components: List[SidebarComponentConfig]) -> int:
    fp = context.primary_floorplan
    font_size = fp.render.text_font_size or 12
    box_size = max(16, int(font_size * 1.4))
    section_gap = 12
    item_gap = 6
    height = 0
    for component in components:
        component_height = 0
        if component.type == "timestamp":
            component_height = compute_timestamp_block_height(fp)
        elif component.type == "outside_temp":
            outside_info = resolve_outside_temperature_for_floorplans(context.floorplans)
            if outside_info:
                component_height = max(box_size, font_size)
        elif component.type == "temperature_chart":
            component_height = max(90, component.height or fp.render.chart_height)
        elif component.type == "legend":
            component_height = compute_legend_height(fp)
        elif component.type == "thermostat_action_chart":
            thermostats = [thermo for thermo in context.thermostats if thermo.mode_entity]
            if thermostats:
                per_height = max(120, component.height or fp.render.thermostat_chart_height)
                component_height = per_height * len(thermostats) + max(0, (len(thermostats) - 1) * 10)
        elif component.type == "thermostat_setpoint_chart":
            thermostats = [
                thermo
                for thermo in context.thermostats
                if thermo.setpoint_low_entity or thermo.setpoint_high_entity or thermo.setpoint_entity
            ]
            if thermostats:
                per_height = max(120, component.height or fp.render.thermostat_chart_height)
                component_height = per_height * len(thermostats) + max(0, (len(thermostats) - 1) * 10)
        if component_height <= 0:
            continue
        if height:
            height += item_gap if component.type in {"timestamp", "outside_temp"} else section_gap
        height += component_height
    return height


def compute_sidebar_panel_width(
    context: SidebarContext,
    components: List[SidebarComponentConfig],
) -> int:
    fp = context.primary_floorplan
    font, font_size = resolve_font(fp, 12)
    width = 0
    for component in components:
        component_width = 0
        if component.type == "timestamp":
            component_width = compute_timestamp_block_width(fp)
        elif component.type == "outside_temp":
            outside_info = resolve_outside_temperature_for_floorplans(context.floorplans)
            if outside_info:
                _outside_temp_value, outside_text, outside_fp = outside_info
                box_size = max(16, int((outside_fp.render.text_font_size or 12) * 1.4))
                component_width = box_size + 6 + measure_text_width(font, outside_text)
        elif component.type == "temperature_chart":
            component_width = max(160, component.width or fp.render.chart_width)
        elif component.type == "legend":
            component_width = 200
        elif component.type == "thermostat_action_chart":
            component_width = max(180, component.width or fp.render.thermostat_chart_width)
        elif component.type == "thermostat_setpoint_chart":
            component_width = max(180, component.width or fp.render.thermostat_chart_width)
        width = max(width, component_width)
    return int(width)


def draw_info_panel(
    draw: ImageDraw.ImageDraw,
    context: SidebarContext,
    size: Tuple[int, int],
    align: str = "top",
    now: Optional[datetime] = None,
) -> None:
    fp = context.primary_floorplan
    components = resolve_sidebar_components()
    margin = fp.render.exterior_margin
    panel_height = compute_sidebar_panel_height(context, components)
    if panel_height <= 0:
        return
    if align == "center":
        start_y = max(margin, int((size[1] - panel_height) / 2))
    else:
        start_y = margin
    y_cursor = start_y - margin
    section_gap = 12
    item_gap = 6
    left = margin
    for component in components:
        if component.type == "timestamp":
            draw_timestamp(draw, fp, (left, y_cursor + margin), now=now)
            y_cursor += compute_timestamp_block_height(fp)
        elif component.type == "outside_temp":
            outside_info = resolve_outside_temperature_for_floorplans(context.floorplans)
            if outside_info:
                outside_temp_value, outside_text, outside_fp = outside_info
                font, font_size = resolve_font(outside_fp, 12)
                box_size = max(16, int(font_size * 1.4))
                if y_cursor > start_y - margin:
                    y_cursor += item_gap
                outside_height = draw_outside_temperature(
                    draw,
                    outside_fp,
                    context.min_f,
                    context.max_f,
                    y_cursor + margin,
                    left,
                    outside_temp_value,
                    outside_text,
                )
                y_cursor += outside_height
            continue
        else:
            if y_cursor > start_y - margin:
                y_cursor += section_gap
        if component.type == "temperature_chart":
            draw_temperature_chart(
                draw,
                fp,
                size,
                context.min_f,
                context.max_f,
                (left, y_cursor + margin),
                chart_width=component.width,
                chart_height=component.height,
                history_hours=component.history_hours,
                forecast_hours=component.forecast_hours,
                temp_entity=component.temp_entity,
                forecast_entity=component.forecast_entity,
                now=now,
            )
            y_cursor += max(90, component.height or fp.render.chart_height)
        elif component.type == "legend":
            y_cursor += draw_legend(draw, context.min_f, context.max_f, (left, y_cursor + margin), fp, context.palette)
        elif component.type == "thermostat_action_chart":
            thermostats = [thermo for thermo in context.thermostats if thermo.mode_entity]
            action_height = draw_thermostat_action_chart(
                draw,
                fp,
                size,
                (left, y_cursor + margin),
                thermostats=thermostats,
                history_hours=component.history_hours,
                chart_width=component.width,
                chart_height=component.height,
                now=now,
            )
            y_cursor += action_height
        elif component.type == "thermostat_setpoint_chart":
            thermostats = [
                thermo
                for thermo in context.thermostats
                if thermo.setpoint_low_entity or thermo.setpoint_high_entity or thermo.setpoint_entity
            ]
            setpoint_height = draw_thermostat_setpoint_chart(
                draw,
                fp,
                size,
                (left, y_cursor + margin),
                thermostats=thermostats,
                history_hours=component.history_hours,
                chart_width=component.width,
                chart_height=component.height,
                now=now,
            )
            y_cursor += setpoint_height


def render_sidebar_image(
    context: SidebarContext,
    align: str = "top",
    target_height: Optional[int] = None,
    now: Optional[datetime] = None,
) -> Optional[Image.Image]:
    if not config.render_sidebar.enabled:
        return None
    components = resolve_sidebar_components()
    if not components:
        return None
    panel_height = compute_sidebar_panel_height(context, components)
    panel_width = compute_sidebar_panel_width(context, components)
    if panel_height <= 0 or panel_width <= 0:
        return None
    margin = context.primary_floorplan.render.exterior_margin
    width = panel_width + margin
    height = panel_height + (margin * 2)
    if target_height is not None:
        height = max(height, target_height)
    canvas = Image.new("RGBA", (width, height), (20, 20, 20, 255))
    draw = ImageDraw.Draw(canvas)
    draw_info_panel(draw, context, canvas.size, align=align, now=now)
    return canvas.convert("RGB")


def attach_sidebar_to_floorplan(
    floorplan_image: Image.Image,
    sidebar_image: Optional[Image.Image],
    gap: int = 0,
) -> Image.Image:
    if sidebar_image is None:
        return floorplan_image
    return stitch_images_horizontally(
        [(None, sidebar_image), (None, floorplan_image)],
        gap,
        label_font_size=0,
    )

def draw_analog_clock(
    draw: ImageDraw.ImageDraw,
    center: Tuple[float, float],
    radius: int,
    now: datetime,
) -> None:
    if radius <= 0:
        return
    cx, cy = center
    draw.ellipse(
        (cx - radius, cy - radius, cx + radius, cy + radius),
        outline=(255, 255, 255),
        width=2,
    )
    hour_angle = (now.hour % 12 + now.minute / 60.0) * 30.0
    minute_angle = (now.minute + now.second / 60.0) * 6.0
    for angle, length, width in [
        (hour_angle, radius * 0.55, 3),
        (minute_angle, radius * 0.8, 2),
    ]:
        radians = math.radians(angle - 90)
        x = cx + math.cos(radians) * length
        y = cy + math.sin(radians) * length
        draw.line((cx, cy, x, y), fill=(255, 255, 255), width=width)
    draw.ellipse((cx - 2, cy - 2, cx + 2, cy + 2), fill=(255, 255, 255))

def draw_timestamp(
    draw: ImageDraw.ImageDraw,
    fp: FloorplanV1,
    origin: Tuple[int, int],
    now: Optional[datetime] = None,
) -> int:
    font, font_size = resolve_font(fp, 12)
    spacing = max(2, int(font_size * 0.2))
    now = now or datetime.now()
    lines = build_timestamp_lines(now)
    draw.multiline_text(
        origin,
        "\n".join(lines),
        fill=(255, 255, 255),
        font=font,
        spacing=spacing,
    )
    text_width = max((measure_text_width(font, line) for line in lines), default=0)
    clock_radius = clock_radius_for_font(font_size)
    text_height = measure_multiline_height(len(lines), font_size, spacing)
    gap = 6
    block_width = max(text_width, clock_radius * 2)
    clock_center = (
        origin[0] + block_width / 2,
        origin[1] + text_height + gap + clock_radius,
    )
    draw_analog_clock(draw, clock_center, clock_radius, now)
    return block_width

def color_for_temperature(value_f: float, min_f: float, max_f: float, palette: List[Tuple[int, int, int]]) -> Tuple[int, int, int]:
    if min_f >= max_f:
        return (255, 255, 255)
    t = (value_f - min_f) / (max_f - min_f)
    t = float(np.clip(t, 0, 1))
    r, g, b = gradient_rgb(np.array([t]), palette)
    return int(r[0]), int(g[0]), int(b[0])

def image_to_png_bytes(image: Image.Image) -> bytes:
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()
