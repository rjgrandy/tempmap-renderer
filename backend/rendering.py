from __future__ import annotations

import bisect
import hashlib
import heapq
import io
import json
import math
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
from fastapi import HTTPException
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .config import config
from .ha import (
    format_entity_temperature,
    is_door_open,
    parse_float,
    read_entity_state,
    read_entity_temperature_value,
    fetch_forecast_series,
    fetch_history_series,
    fetch_state_history,
    ha_lock,
    ha_states,
    current_state_revision,
)
from .models import FloorplanV1, SidebarComponentConfig, SidebarContext, Thermostat
from .storage import load_all_floorplans


# ---------------------------------------------------------------------------
# Visual style
# ---------------------------------------------------------------------------
# A cohesive, modern dark theme. Colors are kept soft and slightly desaturated
# so the heatmap stays the focal point and overlays read cleanly on top of it.

BG_COLOR = (22, 24, 30, 255)          # app background (soft cool charcoal)
WALL_COLOR = (214, 219, 230, 255)     # softened off-white walls
WALL_HIGHLIGHT = (245, 248, 255, 235) # bevel highlight on the raised edge
WALL_EDGE_SHADOW = (8, 9, 12, 220)    # dark lower-right edge for emboss
WALL_SHADOW = (0, 0, 0, 130)          # drop shadow beneath walls for depth
DOOR_COLOR = (120, 190, 255, 255)     # calm blue doorways
SENSOR_FILL = (255, 255, 255, 255)
SENSOR_RING = (18, 20, 26, 210)       # subtle dark outline around dots
SENSOR_HALO = (255, 255, 255, 38)     # soft glow around dots
THERMO_COLOR = (255, 200, 90, 255)
THERMO_FILL = (255, 200, 90, 36)

LABEL_COLOR = (245, 247, 251)
THERMO_LABEL = (255, 206, 105)
LABEL_CHIP = (16, 18, 24, 140)        # translucent backing behind label text
MUTED_TEXT = (168, 174, 188)
AXIS_TEXT = (206, 211, 222)

# NOTE: PIL's ImageDraw overwrites (it does not alpha-blend) when drawing
# directly on the canvas, so these are opaque colors tuned to read as a soft
# frosted "card" over BG_COLOR rather than RGBA values.
PANEL_FILL = (33, 36, 44)             # frosted card background for charts
PANEL_BORDER = (62, 67, 79)           # muted card border
GRID_COLOR = (100, 106, 120)          # subtle gridlines / ticks
NIGHT_SHADE = (26, 28, 35)            # gentle darkening for night hours

TEXT_SHADOW = (0, 0, 0, 150)

# Supersampling factor for crisp, anti-aliased vector overlays (walls, markers,
# clock). Shapes are drawn large and downscaled with LANCZOS.
SHAPE_SS = 3


def draw_text_with_shadow(
    draw: "ImageDraw.ImageDraw",
    pos: Tuple[float, float],
    text: str,
    font,
    fill,
    shadow: Tuple[int, int, int, int] = TEXT_SHADOW,
    offset: Tuple[int, int] = (1, 1),
) -> None:
    """Draw text with a subtle drop shadow for legibility over the heatmap."""
    x, y = pos
    draw.text((x + offset[0], y + offset[1]), text, font=font, fill=shadow)
    draw.text((x, y), text, font=font, fill=fill)


def draw_panel(
    draw: "ImageDraw.ImageDraw",
    box: Tuple[float, float, float, float],
    radius: int = 10,
    fill=PANEL_FILL,
    border=PANEL_BORDER,
    width: int = 1,
) -> None:
    """Draw a soft rounded 'card' used as a backdrop for charts."""
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=border, width=width)


def make_background(size: Tuple[int, int]) -> Image.Image:
    """A flat, cohesive dark backdrop. Kept flat (no gradient) so stitched
    multi-floor panels share an identical, seamless background."""
    return Image.new("RGBA", size, BG_COLOR)


def prettify_floor_name(floor_id: str) -> str:
    """Turn a raw floor id into a friendly display title.

    e.g. "1_first_floor" -> "First Floor", "second-floor" -> "Second Floor".
    A leading ordering prefix like "1_" or "02-" is stripped.
    """
    name = re.sub(r"^\s*\d+\s*[_\-.]\s*", "", floor_id)
    name = name.replace("_", " ").replace("-", " ").strip()
    if not name:
        name = floor_id
    return name.title()


def resolve_shared_range(
    floorplans: List[Tuple[FloorplanV1, np.ndarray]],
) -> Tuple[float, float]:
    """One color scale shared by every floor.

    The scale is static (driven by each floorplan's configured temperature
    range) rather than auto-fitting the data, so a room that warms or cools
    visibly changes color over time instead of the whole gradient shifting
    underneath it. Floors are unified by taking the lowest min and highest max.
    """
    mins: List[float] = []
    maxs: List[float] = []
    for fp, grid in floorplans:
        lo, hi = resolve_temperature_range(fp, grid)
        mins.append(lo)
        maxs.append(hi)
    if not mins:
        return 68.0, 76.0
    min_f = min(mins)
    max_f = max(maxs)
    if min_f >= max_f:
        max_f = min_f + 0.1
    return min_f, max_f


_solve_cache_key: Optional[Tuple[str, int]] = None
_solve_cache_grids: Dict[str, np.ndarray] = {}
_solve_cache_metadata: Dict[str, Dict] = {}


def _floorplans_signature(floorplans: Dict[str, Dict]) -> str:
    payload = json.dumps(floorplans, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def solve_all_floorplans_cached(floorplans: Dict[str, Dict]) -> Tuple[Dict[str, np.ndarray], Dict[str, Dict]]:
    global _solve_cache_key, _solve_cache_grids, _solve_cache_metadata
    key = (_floorplans_signature(floorplans), current_state_revision())
    if _solve_cache_key == key:
        return _solve_cache_grids, _solve_cache_metadata
    grids, metadata = solve_all_floorplans(floorplans)
    _solve_cache_key = key
    _solve_cache_grids = grids
    _solve_cache_metadata = metadata
    return grids, metadata


def point_xy(point: List[float]) -> Tuple[float, float]:
    return float(point[0]), float(point[1])


def render_floorplan(floor_id: str) -> Image.Image:
    floorplans = load_all_floorplans()
    if floor_id == "all":
        if not floorplans:
            raise HTTPException(status_code=404, detail="Floorplans not found")
        grids, metadata = solve_all_floorplans_cached(floorplans)
        images = []
        ordered_floor_ids = sorted(floorplans.keys())
        parsed_floorplans: List[FloorplanV1] = []
        for floor_key in ordered_floor_ids:
            parsed_floorplans.append(FloorplanV1.parse_obj(floorplans[floor_key]))
        # One robust, outlier-resistant color scale shared by every floor.
        range_pairs = [
            (parsed_floorplans[idx], grids[floor_key])
            for idx, floor_key in enumerate(ordered_floor_ids)
            if grids.get(floor_key) is not None
        ]
        if not range_pairs:
            raise HTTPException(status_code=404, detail="Floorplans not found")
        min_f, max_f = resolve_shared_range(range_pairs)
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
    grids, metadata = solve_all_floorplans_cached(floorplans)
    grid = grids.get(floor_id)
    if grid is None:
        raise HTTPException(status_code=404, detail="Floorplan not found")
    fp = FloorplanV1.parse_obj(floorplans[floor_id])
    min_f, max_f = resolve_shared_range([(fp, grid)])
    base_image = render_floorplan_base_image(
        floor_id, floorplans[floor_id], grid, metadata.get(floor_id, {}), range_override=(min_f, max_f)
    )
    sidebar_context = resolve_sidebar_context([fp], min_f, max_f)
    sidebar_image = render_sidebar_image(sidebar_context, align="top", target_height=base_image.height)
    return attach_sidebar_to_floorplan(base_image, sidebar_image)


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
            if temp == 0.0:
                continue
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


def diffuse_grid(fp: FloorplanV1, grid: np.ndarray) -> np.ndarray:
    h_edges, v_edges = build_edge_conductance(fp)
    w_left = np.pad(h_edges, ((0, 0), (1, 0)), mode="constant", constant_values=0)
    w_right = np.pad(h_edges, ((0, 0), (0, 1)), mode="constant", constant_values=0)
    w_up = np.pad(v_edges, ((1, 0), (0, 0)), mode="constant", constant_values=0)
    w_down = np.pad(v_edges, ((0, 1), (0, 0)), mode="constant", constant_values=0)

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
            rasterize_line_to_mask(fp, points[idx], points[idx + 1], wall_mask)

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


def rasterize_line_to_mask(fp: FloorplanV1, a: List[float], b: List[float], mask: np.ndarray) -> None:
    grid_w = fp.solver.grid_w
    grid_h = fp.solver.grid_h
    ax, ay = point_xy(a)
    bx, by = point_xy(b)
    x0 = ax / fp.canvas.width * grid_w
    y0 = ay / fp.canvas.height * grid_h
    x1 = bx / fp.canvas.width * grid_w
    y1 = by / fp.canvas.height * grid_h
    dist = max(abs(x1 - x0), abs(y1 - y0))
    if dist == 0:
        return
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
        if cost > distances[y, x]:
            continue

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
        if not state:
            continue
        temp = parse_float(state.state)
        if temp == 0.0:
            continue
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
        if not stair or not stair.link_to_floor_id:
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


def polygon_mask(fp: FloorplanV1, polygon: List[List[float]]) -> np.ndarray:
    height, width = fp.solver.grid_h, fp.solver.grid_w
    xs = [point_xy(p)[0] / fp.canvas.width * width for p in polygon]
    ys = [point_xy(p)[1] / fp.canvas.height * height for p in polygon]
    mask = np.zeros((height, width), dtype=bool)
    if not xs:
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
        intersect = ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi + 1e-9) + xi)
        if intersect:
            inside = not inside
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
    canvas = make_background((fp.canvas.width, fp.canvas.height))
    min_f, max_f = range_override or resolve_temperature_range(fp, grid)
    palette = resolve_legend_palette(fp)

    # Use Flood Fill mask instead of convex hull to handle concave yards properly
    heatmap_mask = build_floorplan_mask_floodfill(fp)

    # Drop a soft shadow of the whole house silhouette so the floorplan reads as
    # a solid form gently raised off the background (a subtle 3D lift).
    canvas = Image.alpha_composite(canvas, render_house_shadow(heatmap_mask, canvas.size))

    heatmap = render_heatmap(grid, min_f, max_f, fp.render.overlay_alpha, canvas.size, heatmap_mask, palette)
    canvas = Image.alpha_composite(canvas, heatmap)
    canvas, label_bbox = draw_floorplan_overlay(canvas, fp)
    if fp.render.auto_crop:
        crop_box = compute_floorplan_crop(fp, canvas.size, fp.render.crop_padding, label_bbox)
        if crop_box:
            canvas, crop_box = expand_canvas_for_crop(canvas, crop_box)
            canvas = canvas.crop(crop_box)
    return canvas.convert("RGB")


def render_house_shadow(mask: np.ndarray, size: Tuple[int, int]) -> Image.Image:
    """A blurred, downward-offset silhouette of the floorplan footprint, used as
    a drop shadow beneath the house for a sense of depth."""
    shadow = Image.new("RGBA", size, (0, 0, 0, 0))
    if not np.any(mask):
        return shadow
    silhouette = Image.fromarray((mask * 150).astype(np.uint8), mode="L")
    dark = Image.new("RGBA", size, (0, 0, 0, 0))
    dark.putalpha(silhouette)
    offset = max(6, int(min(size) / 65))
    blur = max(8, int(min(size) / 45))
    dark = dark.transform(
        size,
        Image.AFFINE,
        (1, 0, -offset * 0.4, 0, 1, -offset),
        resample=Image.Resampling.BILINEAR,
    )
    dark = dark.filter(ImageFilter.GaussianBlur(blur))
    return dark


def build_floorplan_mask_floodfill(fp: FloorplanV1) -> np.ndarray:
    """Creates a mask of the 'inside' of the floorplan using flood fill from sensors."""
    # 1. Setup low-res mask
    w, h = fp.canvas.width // 4, fp.canvas.height // 4
    mask_img = Image.new("L", (w, h), 0)
    draw = ImageDraw.Draw(mask_img)

    # 2. Draw ALL Walls as Barriers (White/255)
    for wall in fp.walls:
        pts = [(p[0] / 4, p[1] / 4) for p in wall.points]
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
    arr = np.array(mask_img)  # Barriers=255, Empty=0
    filled = np.zeros_like(arr, dtype=bool)

    seeds = []
    for s in fp.sensors:
        seeds.append((int(s.pos[0] / 4), int(s.pos[1] / 4)))
    for t in fp.thermostats:
        seeds.append((int(t.pos[0] / 4), int(t.pos[1] / 4)))

    stack = []
    for (sx, sy) in seeds:
        if 0 <= sx < w and 0 <= sy < h and arr[sy, sx] == 0:
            stack.append((sx, sy))

    visited = set(stack)
    while stack:
        cx, cy = stack.pop()
        filled[cy, cx] = True

        for dx, dy in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
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


def render_heatmap(
    grid: np.ndarray,
    min_f: float,
    max_f: float,
    overlay_alpha: float,
    size: Tuple[int, int],
    mask: Optional[np.ndarray],
    palette: List[Tuple[int, int, int]],
) -> Image.Image:
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
        # Feather the mask edges so the heat fades softly at walls instead of
        # ending in a hard cut. This gives the map a calmer, glowing quality.
        feather = max(2, int(min(size) / 130))
        new_a = new_a.filter(ImageFilter.GaussianBlur(feather))
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
        return tuple(int(cleaned[i : i + 2], 16) for i in (0, 2, 4))
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
    # A refined cool-to-warm thermal ramp. Softer and more perceptually even
    # than a pure rainbow: deep indigo through teal and amber to a muted red.
    return [
        (49, 71, 168),     # deep indigo (coldest)
        (66, 133, 214),    # blue
        (84, 188, 214),    # cyan / teal
        (120, 200, 168),   # soft green
        (210, 213, 128),   # warm chartreuse
        (243, 196, 96),    # amber
        (233, 138, 74),    # orange
        (208, 78, 70),     # muted red (hottest)
    ]


def resolve_temperature_range(fp: FloorplanV1, grid: np.ndarray) -> Tuple[float, float]:
    grid_min = float(np.min(grid))
    grid_max = float(np.max(grid))
    min_f = fp.render.temp_range_f.min if fp.render.scale_min_mode == "absolute" else grid_min
    max_f = fp.render.temp_range_f.max if fp.render.scale_max_mode == "absolute" else grid_max
    if min_f >= max_f:
        max_f = min_f + 0.1
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
    canvas = Image.new("RGBA", (new_width, new_height), BG_COLOR)
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
    expanded = Image.new("RGBA", (new_width, new_height), BG_COLOR)
    expanded.paste(image, (left_pad, top_pad))
    shifted_crop = (
        min_x + left_pad,
        min_y + top_pad,
        max_x + left_pad,
        max_y + top_pad,
    )
    return expanded, shifted_crop


def compute_floorplan_crop(
    fp: FloorplanV1,
    canvas_size: Tuple[int, int],
    padding: int,
    extra_bbox: Optional[Tuple[int, int, int, int]] = None,
) -> Optional[Tuple[int, int, int, int]]:
    points = []
    for wall in fp.walls:
        points.extend([point_xy(p) for p in wall.points])
    if not points:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    min_x = int(min(xs) - padding)
    min_y = int(min(ys) - padding)
    max_x = int(max(xs) + padding)
    max_y = int(max(ys) + padding)
    if extra_bbox is not None:
        # Make sure labels (which can extend past the walls) aren't clipped.
        label_pad = 6
        min_x = min(min_x, extra_bbox[0] - label_pad)
        min_y = min(min_y, extra_bbox[1] - label_pad)
        max_x = max(max_x, extra_bbox[2] + label_pad)
        max_y = max(max_y, extra_bbox[3] + label_pad)
    return min_x, min_y, max_x, max_y


def draw_floorplan_overlay(
    canvas: Image.Image, fp: FloorplanV1
) -> Tuple[Image.Image, Optional[Tuple[int, int, int, int]]]:
    """Composite the crisp vector layer (walls, doorways, sensor and thermostat
    markers) onto the heatmap, then the text labels (on chips, de-overlapped).
    Returns the canvas plus the bounding box of all drawn labels so the caller
    can keep them from being clipped when cropping."""
    shapes = render_shape_layer(fp, canvas.size)
    canvas = Image.alpha_composite(canvas, shapes)
    labels = render_labels_layer(fp, canvas.size)
    label_bbox = labels.getbbox()
    canvas = Image.alpha_composite(canvas, labels)
    return canvas, label_bbox


def render_shape_layer(fp: FloorplanV1, size: Tuple[int, int]) -> Image.Image:
    """Render walls and markers on a supersampled transparent layer for smooth,
    anti-aliased edges, then downscale with high-quality resampling."""
    s = SHAPE_SS
    big = (size[0] * s, size[1] * s)
    layer = Image.new("RGBA", big, (0, 0, 0, 0))

    # Soft drop shadow beneath the walls so each room reads with a little depth,
    # as if gently raised off the background.
    if fp.render.show_walls and fp.walls:
        shadow = Image.new("RGBA", big, (0, 0, 0, 0))
        sdraw = ImageDraw.Draw(shadow)
        offset = int(round(1.6 * s))
        shadow_w = max(1, int(round(3.4 * s)))
        for wall in fp.walls:
            points = [(p[0] * s + offset, p[1] * s + offset) for p in wall.points]
            if len(points) >= 2:
                sdraw.line(points, fill=WALL_SHADOW, width=shadow_w, joint="curve")
        shadow = shadow.filter(ImageFilter.GaussianBlur(2.4 * s))
        layer = Image.alpha_composite(layer, shadow)

    draw = ImageDraw.Draw(layer)

    if fp.render.show_walls:
        wall_w = max(1, int(round(2.6 * s)))
        bevel = max(1, int(round(1.1 * s)))
        for wall in fp.walls:
            points = [(p[0] * s, p[1] * s) for p in wall.points]
            # Emboss: dark lower-right edge + light upper-left edge around a
            # bright core makes each wall look like a raised ridge.
            lowlight = [(px + bevel, py + bevel) for px, py in points]
            highlight = [(px - bevel, py - bevel) for px, py in points]
            if len(points) >= 2:
                draw.line(lowlight, fill=WALL_EDGE_SHADOW, width=wall_w, joint="curve")
                draw.line(highlight, fill=WALL_HIGHLIGHT, width=wall_w, joint="curve")
                draw.line(points, fill=WALL_COLOR, width=wall_w, joint="curve")
            # Rounded caps so wall corners and ends look clean.
            cap = wall_w / 2
            for px, py in points:
                draw.ellipse((px - cap, py - cap, px + cap, py + cap), fill=WALL_COLOR)
        door_w = max(1, int(round(3.4 * s)))
        for door in fp.doors:
            a = (door.segment[0][0] * s, door.segment[0][1] * s)
            b = (door.segment[1][0] * s, door.segment[1][1] * s)
            draw.line([a, b], fill=DOOR_COLOR, width=door_w)

    for sensor in fp.sensors:
        x, y = point_xy(sensor.pos)
        x *= s
        y *= s
        r = 6 * s
        halo = r * 2.1
        draw.ellipse((x - halo, y - halo, x + halo, y + halo), fill=SENSOR_HALO)
        draw.ellipse(
            (x - r, y - r, x + r, y + r),
            fill=SENSOR_FILL,
            outline=SENSOR_RING,
            width=max(1, int(round(1.4 * s))),
        )

    for thermo in fp.thermostats:
        x, y = point_xy(thermo.pos)
        x *= s
        y *= s
        h = 8 * s
        draw.rounded_rectangle(
            (x - h, y - h, x + h, y + h),
            radius=int(3 * s),
            outline=THERMO_COLOR,
            fill=THERMO_FILL,
            width=max(1, int(round(2 * s))),
        )

    return layer.resize(size, Image.Resampling.LANCZOS)


def thermostat_label_lines(fp: FloorplanV1, thermo: Thermostat) -> List[str]:
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

    lines = [name_line]
    if temp_val:
        lines.append(temp_val)
    if setpoint_line:
        lines.append(setpoint_line)
    if action_line:
        lines.append(action_line)
    return lines


def collect_label_specs(fp: FloorplanV1) -> List[Dict]:
    """Gather every text label (sensors, thermostats, room labels) with its
    anchor position, lines and styling into a uniform list for layout."""
    specs: List[Dict] = []
    if fp.render.show_labels:
        for sensor in fp.sensors:
            lines: List[str] = []
            label = sensor.label or sensor.entity or ""
            temp_val = format_entity_temperature(sensor.entity)
            if label:
                lines.append(label)
            if temp_val:
                lines.append(temp_val)
            if not lines:
                continue
            font, font_size = resolve_font(fp, sensor.font_size)
            x, y = point_xy(sensor.pos)
            specs.append(
                {
                    "lines": lines,
                    "color": LABEL_COLOR,
                    "font": font,
                    "font_size": font_size,
                    "x": x + sensor.label_offset_x,
                    "y": y + sensor.label_offset_y,
                }
            )
        for thermo in fp.thermostats:
            lines = thermostat_label_lines(fp, thermo)
            font, font_size = resolve_font(fp, thermo.font_size)
            x, y = point_xy(thermo.pos)
            specs.append(
                {
                    "lines": lines,
                    "color": THERMO_LABEL,
                    "font": font,
                    "font_size": font_size,
                    "x": x + thermo.label_offset_x,
                    "y": y + thermo.label_offset_y,
                }
            )
    for label in fp.room_labels:
        if not label.label:
            continue
        font, font_size = resolve_font(fp, label.font_size)
        x, y = point_xy(label.pos)
        specs.append(
            {
                "lines": [label.label],
                "color": LABEL_COLOR,
                "font": font,
                "font_size": font_size,
                "x": x + label.label_offset_x,
                "y": y + label.label_offset_y,
            }
        )
    return specs


def _label_dimensions(spec: Dict) -> Tuple[int, int]:
    line_h = spec["font_size"] + 2
    width = max((measure_text_width(spec["font"], line) for line in spec["lines"]), default=0)
    height = line_h * len(spec["lines"])
    return width, height


def _boxes_overlap(a, b, pad: int = 4) -> bool:
    return not (
        a[2] + pad <= b[0] or a[0] - pad >= b[2] or a[3] + pad <= b[1] or a[1] - pad >= b[3]
    )


def render_labels_layer(fp: FloorplanV1, size: Tuple[int, int]) -> Image.Image:
    """Draw all labels on chips with a simple vertical de-overlap pass so no two
    labels sit on top of each other, and everything stays readable over the
    heatmap."""
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    specs = collect_label_specs(fp)

    placed: List[Tuple[int, int, int, int]] = []
    chip_pad_x, chip_pad_y = 5, 3
    for spec in specs:
        w, h = _label_dimensions(spec)
        x, y = int(spec["x"]), int(spec["y"])
        box = [x - chip_pad_x, y - chip_pad_y, x + w + chip_pad_x, y + h + chip_pad_y]
        # Nudge downward until it no longer collides with an already-placed label.
        step = max(2, spec["font_size"] // 3)
        guard = 0
        while any(_boxes_overlap(box, other) for other in placed) and guard < 400:
            box[1] += step
            box[3] += step
            y += step
            guard += 1
        # Keep the chip on-canvas vertically.
        if box[3] > size[1]:
            shift = box[3] - size[1]
            box[1] -= shift
            box[3] -= shift
            y -= shift
        placed.append(tuple(box))

        # Translucent rounded chip for guaranteed legibility, then the text.
        draw.rounded_rectangle(tuple(box), radius=7, fill=LABEL_CHIP)
        line_h = spec["font_size"] + 2
        cy = y
        for line in spec["lines"]:
            draw_text_with_shadow(draw, (x, cy), line, spec["font"], spec["color"])
            cy += line_h
    return layer


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
    draw.rounded_rectangle(
        (left_offset, box_y, left_offset + box_size, box_y + box_size),
        radius=max(3, box_size // 4),
        fill=color,
        outline=PANEL_BORDER,
        width=1,
    )
    draw_text_with_shadow(draw, (left_offset + box_size + 8, top_offset), text, font, LABEL_COLOR)
    return max(box_size, font_size)


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
        draw.line([(x, y_axis), (x, y_axis + 4)], fill=GRID_COLOR, width=1)
        label = format_time_tick(ts)
        label_width = measure_text_width(font, label)
        draw.text((x - label_width / 2, label_y), label, fill=AXIS_TEXT, font=font)


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

    draw.text((origin_x, origin_y), title, fill=AXIS_TEXT, font=font)
    draw_panel(draw, (x0, y0, x1, y1), radius=8)

    total_seconds = (end - start).total_seconds() or 1.0

    def to_xy(ts: datetime, temp: float) -> Tuple[float, float]:
        ratio_x = (ts - start).total_seconds() / total_seconds
        ratio_y = (temp - chart_min) / (chart_max - chart_min)
        x = x0 + ratio_x * (x1 - x0)
        y = y1 - ratio_y * (y1 - y0)
        return x, y

    draw_day_night_shading(draw, start, end, x0, x1, y0, y1)

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
        draw.line([(x0 - 4, y), (x0, y)], fill=GRID_COLOR, width=1)
        label = f"{tick:.1f}F"
        label_width = measure_text_width(font, label)
        draw.text((x0 - 6 - label_width, y - (font_size / 2)), label, fill=AXIS_TEXT, font=font)

    def format_tick_time(ts: datetime) -> str:
        return ts.astimezone().strftime("%I%p").lstrip("0")

    tick_positions = [start, now, end]
    if not (start <= now <= end):
        mid = start + (end - start) / 2
        tick_positions = [start, mid, end]
    for ts in tick_positions:
        x, _ = to_xy(ts, chart_min)
        draw.line([(x, y1), (x, y1 + 4)], fill=GRID_COLOR, width=1)
        label = "Now" if abs((ts - now).total_seconds()) < 900 else format_tick_time(ts)
        label_width = measure_text_width(font, label)
        draw.text((x - label_width / 2, y1 + 4), label, fill=AXIS_TEXT, font=font)

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
        draw.line([(x, y_axis), (x, y_axis + 4)], fill=GRID_COLOR, width=1)
        label = "Now" if abs((ts - now).total_seconds()) < 900 else format_time_tick(ts)
        label_width = measure_text_width(font, label)
        draw.text((x - label_width / 2, label_y), label, fill=AXIS_TEXT, font=font)


def draw_day_night_shading(
    draw: ImageDraw.ImageDraw,
    start: datetime,
    end: datetime,
    x0: float,
    x1: float,
    y0: float,
    y1: float,
) -> None:
    total_seconds = (end - start).total_seconds() or 1.0

    def to_x(ts: datetime) -> float:
        return x0 + ((ts - start).total_seconds() / total_seconds) * (x1 - x0)

    hour_cursor = start.replace(minute=0, second=0, microsecond=0)
    while hour_cursor < end:
        next_hour = hour_cursor + timedelta(hours=1)
        hour_center = hour_cursor + (next_hour - hour_cursor) / 2
        is_day = 6 <= hour_center.astimezone().hour < 18
        if not is_day:
            x_start = to_x(hour_cursor)
            x_end = to_x(next_hour)
            draw.rectangle((x_start, y0, x_end, y1), fill=NIGHT_SHADE)
        hour_cursor = next_hour


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
        x1 = origin_x + width - 6

        history = fetch_state_history(thermo.mode_entity, history_hours, end_time=now)
        fallback_state = read_entity_state(thermo.mode_entity) if thermo.mode_entity else None
        timeline = build_state_timeline(history, start, end, fallback_state)
        if not timeline:
            continue

        heat_pct, cool_pct = compute_action_percentages(timeline, start, end)
        stats_text = f"Heat {heat_pct}% • Cool {cool_pct}%"
        stats_width = measure_text_width(font, stats_text)
        title_width = measure_text_width(font, title)
        stats_y = chart_origin_y
        title_block_height = title_height
        if title_width + 8 + stats_width > width:
            stats_y = chart_origin_y + title_height
            title_block_height += title_height
        y0 = chart_origin_y + title_block_height + 4
        y1 = chart_origin_y + height - bottom_label_height
        if x1 <= x0 or y1 <= y0:
            continue

        draw.text((origin_x, chart_origin_y), title, fill=AXIS_TEXT, font=font)
        draw.text((origin_x + width - stats_width, stats_y), stats_text, fill=MUTED_TEXT, font=font)
        draw_panel(draw, (x0, y0, x1, y1), radius=8)

        draw_day_night_shading(draw, start, end, x0, x1, y0, y1)

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
            draw.line([(x0 - 4, y), (x0, y)], fill=GRID_COLOR, width=1)
            label_width = measure_text_width(font, label)
            draw.text((x0 - 6 - label_width, y - (font_size / 2)), label, fill=AXIS_TEXT, font=font)

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

        draw.text((origin_x, chart_origin_y), title, fill=AXIS_TEXT, font=font)
        draw_panel(draw, (x0, y0, x1, y1), radius=8)
        draw_day_night_shading(draw, start, end, x0, x1, y0, y1)

        low_series = fetch_history_series(thermo.setpoint_low_entity, history_hours, end_time=now)
        high_series = fetch_history_series(thermo.setpoint_high_entity, history_hours, end_time=now)
        setpoint_series = fetch_history_series(thermo.setpoint_entity, history_hours, end_time=now)
        temp_series = fetch_history_series(thermo.temperature_entity, history_hours, end_time=now)
        if not low_series and setpoint_series:
            low_series = setpoint_series
        if not high_series and setpoint_series:
            high_series = setpoint_series
        low_series = extend_series_to_range(low_series, start, end)
        high_series = extend_series_to_range(high_series, start, end)
        temp_series = extend_series_to_range(temp_series, start, end)
        all_values = [temp for _ts, temp in (low_series + high_series + temp_series)]
        if not all_values:
            continue
        chart_min = min(all_values) - 1
        chart_max = max(all_values) + 1
        chart_min = max(chart_min, 65)
        chart_max = min(chart_max, 85)
        if chart_min >= chart_max:
            chart_max = min(85, chart_min + 1)
            chart_min = max(65, chart_max - 1)

        total_seconds = (end - start).total_seconds() or 1.0

        def to_x(ts: datetime) -> float:
            return x0 + ((ts - start).total_seconds() / total_seconds) * (x1 - x0)

        def to_y(temp: float) -> float:
            ratio = (temp - chart_min) / (chart_max - chart_min)
            return y1 - ratio * (y1 - y0)

        for tick in [chart_min, (chart_min + chart_max) / 2, chart_max]:
            y = to_y(tick)
            draw.line([(x0 - 4, y), (x0, y)], fill=GRID_COLOR, width=1)
            label = f"{tick:.1f}F"
            label_width = measure_text_width(font, label)
            draw.text((x0 - 6 - label_width, y - (font_size / 2)), label, fill=AXIS_TEXT, font=font)

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

        if len(temp_series) > 1:
            temp_points = [(to_x(ts), to_y(temp)) for ts, temp in temp_series]
            draw.line(temp_points, fill=(255, 200, 50), width=2)

        draw_time_ticks(draw, font, font_size, start, end, x0, x1, y1, y1 + 4, now=now)

    return total_height


def get_font(size: int, font_path: Optional[str] = None) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Tries to load a scalable font. Falls back to default if unavailable."""
    # Common paths for DejaVuSans on Linux/Debian
    candidates = [font_path] if font_path else []
    candidates += [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/TTF/DejaVuSans.ttf",
        "DejaVuSans.ttf",
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
    # Build the gradient on a small strip, then mask it into a rounded pill so
    # the legend reads as a single clean swatch rather than a hard rectangle.
    width = x1 - x0
    ramp = np.linspace(0.0, 1.0, width)
    r, g, b = gradient_rgb(ramp, palette)
    row = np.stack([r, g, b], axis=1).astype(np.uint8)[None, :, :]
    strip = Image.fromarray(np.repeat(row, gradient_height, axis=0), mode="RGB").convert("RGBA")
    radius = gradient_height // 2
    pill_mask = Image.new("L", (width, gradient_height), 0)
    ImageDraw.Draw(pill_mask).rounded_rectangle(
        (0, 0, width - 1, gradient_height - 1), radius=radius, fill=255
    )
    strip.putalpha(pill_mask)
    base = getattr(draw, "_image", None)
    if base is not None:
        base.paste(strip, (x0, y0), strip)
    else:  # Fallback: draw the gradient column by column.
        for i in range(x0, x1):
            t = (i - x0) / max(1, (x1 - x0))
            cr, cg, cb = gradient_rgb(np.array([t]), palette)
            draw.line([(i, y0), (i, y1)], fill=(int(cr[0]), int(cg[0]), int(cb[0])))
    draw.rounded_rectangle((x0, y0, x1, y1), radius=radius, outline=PANEL_BORDER, width=1)
    draw_text_with_shadow(draw, (x0, y0 - label_offset), f"{min_f:.1f}F", font, AXIS_TEXT)
    max_label = f"{max_f:.1f}F"
    max_label_width = measure_text_width(font, max_label)
    draw_text_with_shadow(draw, (x1 - max_label_width, y0 - label_offset), max_label, font, AXIS_TEXT)
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
    section_gap = 18
    item_gap = 10
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


def sidebar_component_width(
    component: SidebarComponentConfig,
    context: SidebarContext,
    font,
) -> int:
    """Footprint width of a single sidebar component, used both for sizing the
    panel and for centering each component within it."""
    fp = context.primary_floorplan
    if component.type == "timestamp":
        return compute_timestamp_block_width(fp)
    if component.type == "outside_temp":
        outside_info = resolve_outside_temperature_for_floorplans(context.floorplans)
        if outside_info:
            _outside_temp_value, outside_text, outside_fp = outside_info
            box_size = max(16, int((outside_fp.render.text_font_size or 12) * 1.4))
            return box_size + 8 + measure_text_width(font, outside_text)
        return 0
    if component.type == "temperature_chart":
        return max(160, component.width or fp.render.chart_width)
    if component.type == "legend":
        return 200
    if component.type in {"thermostat_action_chart", "thermostat_setpoint_chart"}:
        return max(180, component.width or fp.render.thermostat_chart_width)
    return 0


def compute_sidebar_panel_width(
    context: SidebarContext,
    components: List[SidebarComponentConfig],
) -> int:
    font, _font_size = resolve_font(context.primary_floorplan, 12)
    width = 0
    for component in components:
        width = max(width, sidebar_component_width(component, context, font))
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
    section_gap = 18
    item_gap = 10
    font, _font_size = resolve_font(fp, 12)
    content_width = compute_sidebar_panel_width(context, components)
    for component in components:
        # Center each component horizontally within the sidebar column.
        comp_width = sidebar_component_width(component, context, font)
        left = margin + max(0, (content_width - comp_width) // 2)
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
            legend_label_offset = (fp.render.text_font_size or 12) + 4
            y_cursor += draw_legend(
                draw,
                context.min_f,
                context.max_f,
                (left, y_cursor + margin + legend_label_offset),
                fp,
                context.palette,
            )
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
    width = panel_width + (margin * 2)
    height = panel_height + (margin * 2)
    if target_height is not None:
        height = max(height, target_height)
    canvas = Image.new("RGBA", (width, height), BG_COLOR)
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
    # Render the clock supersampled on its own transparent layer for smooth,
    # anti-aliased edges, then composite it down.
    s = SHAPE_SS
    pad = 4 * s
    box = int(radius * 2 * s) + pad * 2
    layer = Image.new("RGBA", (box, box), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    lcx = lcy = box / 2
    r = radius * s
    face = (255, 255, 255, 255)
    # Faint inner face for a touch of depth.
    ld.ellipse((lcx - r, lcy - r, lcx + r, lcy + r), fill=(255, 255, 255, 14))
    ld.ellipse((lcx - r, lcy - r, lcx + r, lcy + r), outline=face, width=max(1, int(round(1.6 * s))))
    hour_angle = (now.hour % 12 + now.minute / 60.0) * 30.0
    minute_angle = (now.minute + now.second / 60.0) * 6.0
    for angle, length, width in [
        (hour_angle, r * 0.52, 3),
        (minute_angle, r * 0.78, 2),
    ]:
        radians = math.radians(angle - 90)
        x = lcx + math.cos(radians) * length
        y = lcy + math.sin(radians) * length
        ld.line((lcx, lcy, x, y), fill=face, width=max(1, int(round(width * s))))
    hub = 2.2 * s
    ld.ellipse((lcx - hub, lcy - hub, lcx + hub, lcy + hub), fill=face)
    layer = layer.resize((int(box / s), int(box / s)), Image.Resampling.LANCZOS)
    base = getattr(draw, "_image", None)
    paste_xy = (int(cx - layer.width / 2), int(cy - layer.height / 2))
    if base is not None:
        base.paste(layer, paste_xy, layer)
    else:
        draw.ellipse(
            (cx - radius, cy - radius, cx + radius, cy + radius),
            outline=(255, 255, 255),
            width=2,
        )


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
        (origin[0] + 1, origin[1] + 1),
        "\n".join(lines),
        fill=TEXT_SHADOW,
        font=font,
        spacing=spacing,
    )
    draw.multiline_text(
        origin,
        "\n".join(lines),
        fill=LABEL_COLOR,
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


def render_title_pill(text: str, font_size: int) -> Image.Image:
    """A centered floor title inside a soft rounded 'pill', supersampled for
    clean edges."""
    s = SHAPE_SS
    font = get_font(font_size * s)
    text_w, text_h = measure_text_size(font, text)
    pad_x = int(font_size * s * 0.85)
    pad_y = int(font_size * s * 0.42)
    w = text_w + pad_x * 2
    h = text_h + pad_y * 2
    radius = h // 2
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    ld.rounded_rectangle((0, 0, w - 1, h - 1), radius=radius, fill=PANEL_FILL, outline=PANEL_BORDER, width=s)
    # Center the glyphs using the font bbox so vertical metrics look balanced.
    bbox = font.getbbox(text)
    tx = (w - (bbox[2] - bbox[0])) // 2 - bbox[0]
    ty = (h - (bbox[3] - bbox[1])) // 2 - bbox[1]
    ld.text((tx + s, ty + s), text, font=font, fill=TEXT_SHADOW)
    ld.text((tx, ty), text, font=font, fill=LABEL_COLOR)
    return layer.resize((w // s, h // s), Image.Resampling.LANCZOS)


def stitch_images_horizontally(
    images: List[Tuple[Optional[str], Image.Image]],
    border_px: int,
    label_font_size: int,
) -> Image.Image:
    # Pre-render a title pill for each labeled panel (floor names prettified).
    # Titles are rendered noticeably larger than the base label size.
    title_font_size = max(30, int(label_font_size * 1.7))
    pills: List[Optional[Image.Image]] = []
    for label, _image in images:
        if label and label_font_size > 0:
            pills.append(render_title_pill(prettify_floor_name(label), title_font_size))
        else:
            pills.append(None)

    heights = [image.height for _label, image in images]
    widths = [image.width for _label, image in images]
    max_height = max(heights)
    pill_height = max((p.height for p in pills if p is not None), default=0)
    title_band = pill_height + (10 if pill_height else 0)
    total_width = sum(widths) + border_px * (len(images) - 1)
    stitched_height = max_height + border_px + title_band
    stitched = Image.new("RGBA", (total_width, stitched_height), BG_COLOR)
    x_cursor = 0
    for (label, image), pill in zip(images, pills):
        y_offset = title_band + border_px
        stitched.paste(image, (x_cursor, y_offset))
        if pill is not None:
            px = x_cursor + max(0, (image.width - pill.width) // 2)
            py = max(0, (title_band - pill.height) // 2)
            stitched.paste(pill, (px, py), pill)
        x_cursor += image.width + border_px
    return stitched.convert("RGB")


def resolve_frame_for_time(frames: List[Tuple[int, Path]], sample_time: int) -> Optional[Path]:
    if not frames:
        return None
    timestamps = [frame[0] for frame in frames]
    index = bisect.bisect_right(timestamps, sample_time) - 1
    if index < 0:
        return None
    return frames[index][1]
