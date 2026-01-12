from __future__ import annotations

import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from fastapi import HTTPException
from PIL import Image

from .config import config
from .models import FloorplanV1, SidebarContext
from .rendering import (
    attach_sidebar_to_floorplan,
    render_floorplan_base_image,
    render_sidebar_image,
    resolve_frame_for_time,
    resolve_sidebar_context,
    resolve_temperature_range,
    solve_all_floorplans,
    stitch_images_horizontally,
)
from .storage import load_all_floorplans


timelapse_lock = threading.Lock()
timelapse_last_roll: Optional[float] = None
timelapse_is_running = False


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
    frames_by_floor = {fid: load_frame_index(fid, start_ts) for fid in available_floor_ids}
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
