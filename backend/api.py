from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

import requests
from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import FileResponse

from . import stats
from .config import config
from .ha import get_entity_states
from .models import RenameFloorplanRequest
from .rendering import image_to_png_bytes, render_floorplan
from .storage import (
    load_floorplan_file,
    parse_floorplan,
    save_floorplan_file,
    update_floorplan_links,
    validate_floorplan_id,
)
from .timelapse import generate_timelapse_for_request

router = APIRouter()


@router.get("/api/floorplans")
def list_floorplans() -> Dict[str, List[str]]:
    floor_dir = Path(config.data_path) / "floorplans"
    floor_ids = sorted([path.stem for path in floor_dir.glob("*.json")])
    return {"floorplans": floor_ids}


@router.get("/api/floorplans/{floor_id}")
def get_floorplan(floor_id: str) -> Dict:
    return load_floorplan_file(floor_id)


@router.put("/api/floorplans/{floor_id}")
def put_floorplan(floor_id: str, payload: Dict) -> Dict:
    validated = parse_floorplan(payload)
    save_floorplan_file(floor_id, validated)
    return validated


@router.delete("/api/floorplans/{floor_id}")
def delete_floorplan(floor_id: str) -> Dict:
    floor_id = validate_floorplan_id(floor_id)
    path = Path(config.data_path) / "floorplans" / f"{floor_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Floorplan not found")
    path.unlink(missing_ok=True)
    update_floorplan_links(floor_id, None)
    return {"deleted": floor_id}


@router.post("/api/floorplans/{floor_id}/rename")
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


@router.post("/api/floorplans/{floor_id}/validate")
def validate_floorplan(floor_id: str, payload: Dict) -> Dict:
    validated = parse_floorplan(payload)
    return {"floor_id": floor_id, "valid": True, "floorplan": validated}


@router.post("/api/ha/test")
def test_ha() -> Dict:
    if not config.ha_base_url or not config.ha_token:
        raise HTTPException(status_code=400, detail="Home Assistant config missing")
    url = f"{config.ha_base_url.rstrip('/')}/api/"
    headers = {"Authorization": f"Bearer {config.ha_token}"}
    response = requests.get(url, headers=headers, timeout=10)
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail=response.text)
    return {"status": "ok"}


@router.get("/api/ha/states")
def get_ha_states(entities: Optional[str] = None) -> Dict[str, Dict[str, str]]:
    if not entities:
        return {"states": {}}
    entity_list = [e.strip() for e in entities.split(",") if e.strip()]
    if not entity_list:
        return {"states": {}}
    states = get_entity_states(entity_list)
    return {"states": states}


@router.get("/render/live/{floor_id}.png")
def render_live_png(floor_id: str) -> Response:
    with stats.timed("live_png_request"):
        image = render_floorplan(floor_id)
        image_bytes = image_to_png_bytes(image)
    return Response(content=image_bytes, media_type="image/png", headers={"Cache-Control": "no-store"})


@router.get("/api/debug/stats")
def debug_stats() -> Dict:
    """CPU/work attribution counters for diagnosing high usage."""
    return stats.snapshot()


@router.get("/api/timelapse/{floor_id}")
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
