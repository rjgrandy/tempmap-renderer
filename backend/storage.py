from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Optional

from fastapi import HTTPException
from pydantic import ValidationError

from .config import config
from .models import FloorplanV1


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
