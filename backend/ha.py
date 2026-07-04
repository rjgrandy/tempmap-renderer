from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import requests

from .config import config
from .models import EntityState, FloorplanV1

ha_lock = threading.Lock()
ha_states: Dict[str, EntityState] = {}
ha_missing: Dict[str, str] = {}
ha_unavailable: Dict[str, str] = {}
ha_last_poll: Optional[str] = None
ha_state_revision = 0

# Reuse one HTTP connection pool for all Home Assistant calls instead of
# paying a fresh TCP/TLS handshake for every entity on every poll.
_session = requests.Session()

# Short-lived caches for history/forecast lookups. Charts re-fetch the same
# 12-24h windows on every render (dashboard polls every ~10s; timelapse
# builds render thousands of frames), while the underlying data barely
# changes — so serve slices from a cached window instead.
_history_cache_lock = threading.Lock()
_history_cache: Dict[Tuple[str, float], Tuple[datetime, datetime, List[Tuple[datetime, str]]]] = {}
_HISTORY_CACHE_TTL_SECONDS = 60.0
_HISTORY_CACHE_MAX_ENTRIES = 64
_forecast_cache: Dict[str, Tuple[float, List[Tuple[datetime, float]]]] = {}
_FORECAST_CACHE_TTL_SECONDS = 120.0


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
            response = _session.get(url, headers=headers, timeout=10)
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
    global ha_state_revision
    with ha_lock:
        # Only bump the revision (which invalidates solver/render caches)
        # when a state VALUE changed. Rendering never reads the timestamps,
        # so last_updated churn alone must not trigger a full re-solve.
        changed = {e: s.state for e, s in states.items()} != {
            e: s.state for e, s in ha_states.items()
        }
        if changed:
            ha_state_revision += 1
        ha_states.clear()
        ha_states.update(states)
        ha_missing.clear()
        ha_missing.update(missing)
        ha_unavailable.clear()
        ha_unavailable.update(unavailable)


def read_entity_state(entity_id: str) -> str:
    with ha_lock:
        state = ha_states.get(entity_id)
    return state.state if state else "n/a"


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


def format_entity_temperature(entity_id: Optional[str]) -> str:
    if not entity_id:
        return ""
    state = read_entity_state(entity_id)
    if state == "n/a":
        return ""
    try:
        return f"{float(state):.1f}F"
    except ValueError:
        return ""


def _fetch_history_raw(
    entity_id: str,
    start: datetime,
    end_time: datetime,
) -> Optional[List[Tuple[datetime, str]]]:
    """Fetch raw (timestamp, state) history from HA. Returns None on failure
    so callers can distinguish an error from a genuinely empty window."""
    url = f"{config.ha_base_url.rstrip('/')}/api/history/period/{start.isoformat()}"
    headers = {"Authorization": f"Bearer {config.ha_token}"}
    params = {
        "filter_entity_id": entity_id,
        "minimal_response": "1",
        "end_time": end_time.isoformat(),
    }
    try:
        response = _session.get(url, headers=headers, params=params, timeout=10)
    except requests.RequestException:
        return None
    if response.status_code != 200:
        return None
    payload = response.json()
    if not payload or not isinstance(payload, list) or not payload[0]:
        return []
    series: List[Tuple[datetime, str]] = []
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


def _slice_series(
    series: List[Tuple[datetime, str]],
    start: datetime,
    end: datetime,
) -> List[Tuple[datetime, str]]:
    """Points within [start, end]; the last point before the window (if any)
    is clamped to the window start so charts still anchor at the left edge."""
    out: List[Tuple[datetime, str]] = []
    boundary: Optional[str] = None
    for ts, state in series:
        if ts < start:
            boundary = state
        elif ts <= end:
            out.append((ts, state))
    if boundary is not None:
        out.insert(0, (start, boundary))
    return out


def _cached_history_raw(
    entity_id: str,
    hours: float,
    end_time: Optional[datetime],
) -> List[Tuple[datetime, str]]:
    """History for [end_time - hours, end_time], served from a per-entity
    cached window when possible.

    On a miss the fetch is extended forward to "now", so a timelapse build
    walking sample times in ascending order fetches each entity's history
    once and slices every subsequent frame out of the cached window. Live
    charts (end_time == now) are refreshed once the cached window's edge is
    older than the TTL."""
    now_dt = datetime.now(timezone.utc)
    end_time = end_time or now_dt
    start = end_time - timedelta(hours=hours)
    key = (entity_id, round(hours, 4))
    with _history_cache_lock:
        entry = _history_cache.get(key)
    if entry:
        fetched_start, fetched_end, series = entry
        covered = fetched_start <= start and (
            end_time <= fetched_end
            or (end_time - fetched_end).total_seconds() <= _HISTORY_CACHE_TTL_SECONDS
        )
        if covered:
            return _slice_series(series, start, end_time)
    fetch_end = max(end_time, now_dt)
    series = _fetch_history_raw(entity_id, start, fetch_end)
    if series is None:
        return []
    with _history_cache_lock:
        _history_cache[key] = (start, fetch_end, series)
        while len(_history_cache) > _HISTORY_CACHE_MAX_ENTRIES:
            _history_cache.pop(next(iter(_history_cache)))
    return _slice_series(series, start, end_time)


def fetch_history_series(
    entity_id: Optional[str],
    hours: float,
    end_time: Optional[datetime] = None,
) -> List[Tuple[datetime, float]]:
    if not entity_id or not config.ha_base_url or not config.ha_token or hours <= 0:
        return []
    series: List[Tuple[datetime, float]] = []
    for ts, state in _cached_history_raw(entity_id, hours, end_time):
        try:
            temp = float(state)
        except (TypeError, ValueError):
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
    return _cached_history_raw(entity_id, hours, end_time)


def fetch_forecast_series(entity_id: Optional[str]) -> List[Tuple[datetime, float]]:
    if not entity_id or not config.ha_base_url or not config.ha_token:
        return []
    now_monotonic = time.monotonic()
    with _history_cache_lock:
        cached = _forecast_cache.get(entity_id)
        if cached and now_monotonic - cached[0] < _FORECAST_CACHE_TTL_SECONDS:
            return cached[1]
    url = f"{config.ha_base_url.rstrip('/')}/api/states/{entity_id}"
    headers = {"Authorization": f"Bearer {config.ha_token}"}
    try:
        response = _session.get(url, headers=headers, timeout=10)
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
    with _history_cache_lock:
        _forecast_cache[entity_id] = (now_monotonic, series)
        while len(_forecast_cache) > _HISTORY_CACHE_MAX_ENTRIES:
            _forecast_cache.pop(next(iter(_forecast_cache)))
    return series


def get_entity_states(entities: List[str]) -> Dict[str, str]:
    with ha_lock:
        return {
            entity: ha_states.get(entity).state if ha_states.get(entity) else "n/a"
            for entity in entities
        }



def current_state_revision() -> int:
    with ha_lock:
        return ha_state_revision

def parse_float(value: str) -> float:
    try:
        return float(value)
    except ValueError:
        return 0.0


def is_door_open(fp: FloorplanV1, door: "Door") -> bool:
    from .models import Door

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
