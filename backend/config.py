from __future__ import annotations

import os
from pathlib import Path
from typing import List

import yaml

from .models import AppConfig, SidebarComponentConfig, SidebarConfig

DATA_ENV = "TEMP_MAP_DATA_PATH"
CONFIG_PATH = Path(__file__).with_name("config.yaml")


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
