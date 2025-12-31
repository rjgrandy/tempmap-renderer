from __future__ import annotations

import bisect
import heapq
import io
import json
import os
import shutil
import subprocess
import tempfile
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
    setpoint_low_entity: Optional[str] = None
    setpoint_high_entity: Optional[str] = None
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
    show_outside_temp: bool = True
    outside_temp_label: str = "Outside"
    outside_temp_entity: Optional[str] = None
    outside_temp_f: Optional[float] = None


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
timelapse_lock = threading.Lock()
timelapse_last_roll: Optional[float] = None
timelapse_is_running = False


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
        render_cfg = floorplan.get("render", {})
        entities.append(render_cfg.get("outside_temp_entity"))
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
    if floor_id not in floorplans:
        raise HTTPException(status_code=404, detail="Floorplan not found")
    grids, metadata = solve_all_floorplans(floorplans)
    grid = grids.get(floor_id)
    if grid is None:
        raise HTTPException(status_code=404, detail="Floorplan not found")
    return render_floorplan_image(floor_id, floorplans[floor_id], grid, metadata.get(floor_id, {}))


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
    output_path = output_dir / f"timelapse_{int(time.time())}.mp4"
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
    with tempfile.TemporaryDirectory() as tmp_dir:
        temp_dir = Path(tmp_dir)
        if stitch and len(available_floor_ids) > 1:
            generate_stitched_frames(
                temp_dir=temp_dir,
                sample_times=sample_times,
                frames_by_floor=frames_by_floor,
                floor_ids=available_floor_ids,
            )
        else:
            generate_single_floor_frames(
                temp_dir=temp_dir,
                sample_times=sample_times,
                frames_by_floor=frames_by_floor,
                floor_id=available_floor_ids[0],
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
        target.write_bytes(frame_path.read_bytes())
        idx += 1


def generate_stitched_frames(
    temp_dir: Path,
    sample_times: List[int],
    frames_by_floor: Dict[str, List[Tuple[int, Path]]],
    floor_ids: List[str],
) -> None:
    idx = 0
    for sample_time in sample_times:
        images = []
        for floor_id in floor_ids:
            frames = frames_by_floor.get(floor_id, [])
            frame_path = resolve_frame_for_time(frames, sample_time)
            if frame_path is None:
                continue
            images.append((floor_id, Image.open(frame_path)))
        if not images:
            continue
        stitched = stitch_images_horizontally(images, config.timelapse_border_px, config.timelapse_label_font_size)
        target = temp_dir / f"frame_{idx:05d}.png"
        stitched.save(target)
        idx += 1
        for _, image in images:
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
    images: List[Tuple[str, Image.Image]],
    border_px: int,
    label_font_size: int,
) -> Image.Image:
    font = get_font(label_font_size)
    widths = []
    heights = []
    label_heights = []
    for floor_id, image in images:
        widths.append(image.width)
        heights.append(image.height)
        label_text = floor_id
        text_width, text_height = font.getsize(label_text)
        label_heights.append(text_height)
    max_height = max(heights)
    label_height = max(label_heights) if label_heights else 0
    total_width = sum(widths) + border_px * (len(images) - 1)
    stitched_height = max_height + border_px + label_height
    stitched = Image.new("RGBA", (total_width, stitched_height), (0, 0, 0, 255))
    draw = ImageDraw.Draw(stitched)
    x_cursor = 0
    for (floor_id, image), image_height in zip(images, heights):
        y_offset = label_height + border_px
        stitched.paste(image, (x_cursor, y_offset))
        label_text = floor_id
        text_width, text_height = font.getsize(label_text)
        text_x = x_cursor + max(0, (image.width - text_width) // 2)
        draw.text((text_x, 0), label_text, font=font, fill=(255, 255, 255, 255))
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
    draw_room_labels(draw, fp)
    if fp.render.auto_crop:
        crop_box = compute_floorplan_crop(fp, canvas.size, fp.render.crop_padding)
        if crop_box: 
            canvas = canvas.crop(crop_box)
            draw = ImageDraw.Draw(canvas) # Update draw object for cropped canvas if needed
            # Actually we just crop at the end, that's fine.
    
    # Legend/Timestamp margin
    if fp.render.show_legend or fp.render.show_timestamp or fp.render.show_outside_temp:
        canvas = add_exterior_margin(
            canvas,
            fp.render.exterior_margin,
            fp.render.show_timestamp,
            fp.render.show_legend,
            fp.render.show_outside_temp,
        )
        draw = ImageDraw.Draw(canvas)
    
    if fp.render.show_outside_temp:
        draw_outside_temperature(draw, fp, canvas.size)
    if fp.render.show_legend: draw_legend(draw, min_f, max_f, canvas.size, fp.render.exterior_margin)
    if fp.render.show_timestamp: draw_timestamp(draw, fp.render.exterior_margin)
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

def add_exterior_margin(
    image: Image.Image,
    margin: int,
    show_ts: bool,
    show_legend: bool,
    show_outside_temp: bool,
) -> Image.Image:
    top = margin if show_ts or show_outside_temp else margin // 2
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
            name_line = thermo.device_label or "Thermostat"
            temp_val = format_entity_temperature(thermo.temperature_entity)
            setpoint_val = format_entity_temperature(thermo.setpoint_entity)
            setpoint_low = format_entity_temperature(thermo.setpoint_low_entity)
            setpoint_high = format_entity_temperature(thermo.setpoint_high_entity)
            mode = read_entity_state(thermo.mode_entity) if thermo.mode_entity else ""
            mode_lower = mode.lower() if mode else ""

            setpoint_line = ""
            if mode_lower in {"heat_cool", "auto"}:
                if setpoint_low and setpoint_high:
                    setpoint_line = f"{setpoint_low} / {setpoint_high}"
                else:
                    setpoint_line = setpoint_low or setpoint_high or setpoint_val
            elif mode_lower == "heat":
                setpoint_line = setpoint_val or setpoint_low
            elif mode_lower == "cool":
                setpoint_line = setpoint_val or setpoint_high
            else:
                setpoint_line = setpoint_val or setpoint_low or setpoint_high

            detail_parts = []
            if temp_val:
                detail_parts.append(temp_val)
            if setpoint_line:
                detail_parts.append(setpoint_line)
            temp_line = " / ".join(detail_parts)
            if mode:
                temp_line = f"{temp_line} ({mode})" if temp_line else mode
            
            font = get_font(thermo.font_size)

            lines = [name_line]
            if temp_line:
                lines.append(temp_line)
            
            off_x = thermo.label_offset_x
            current_y = y + thermo.label_offset_y
            
            for line in lines:
                draw.text((x + off_x, current_y), line, fill=(255, 200, 50), font=font)
                current_y += (thermo.font_size + 2)

def draw_room_labels(draw: ImageDraw.ImageDraw, fp: FloorplanV1) -> None:
    for label in fp.room_labels:
        if not label.label:
            continue
        x, y = point_xy(label.pos)
        font = get_font(label.font_size)
        draw.text((x + label.label_offset_x, y + label.label_offset_y), label.label, fill=(255, 255, 255), font=font)

def draw_outside_temperature(draw: ImageDraw.ImageDraw, fp: FloorplanV1, size: Tuple[int, int]) -> None:
    if fp.render.outside_temp_entity:
        outside_temp = format_entity_temperature(fp.render.outside_temp_entity)
    elif fp.render.outside_temp_f is not None:
        outside_temp = f"{fp.render.outside_temp_f:.1f}F"
    else:
        outside_temp = ""
    if not outside_temp:
        return
    font = ImageFont.load_default()
    label = fp.render.outside_temp_label.strip() or "Outside"
    text = f"{label}: {outside_temp}"
    margin = fp.render.exterior_margin
    draw.text((margin, margin + 16), text, fill=(255, 255, 255), font=font)

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
