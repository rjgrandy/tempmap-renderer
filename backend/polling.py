from __future__ import annotations

import time
from datetime import datetime, timezone

from .config import config
from . import ha
from .storage import load_all_floorplans
from .timelapse import maybe_render_rolling_timelapses, render_frames_for_floorplans


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ha_poll_loop() -> None:
    while True:
        try:
            floorplans = load_all_floorplans()
            entities = ha.gather_entities(floorplans)
            if config.ha_base_url and config.ha_token and entities:
                ha.poll_home_assistant(entities)
            render_frames_for_floorplans(floorplans)
            maybe_render_rolling_timelapses(floorplans)
        except Exception:
            pass
        ha.ha_last_poll = now_iso()
        time.sleep(config.refresh_seconds)
