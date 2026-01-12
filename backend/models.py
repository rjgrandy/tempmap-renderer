from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple
from typing import Literal

from pydantic import BaseModel, Field, conlist


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
