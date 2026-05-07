"""TypedDict definitions for the camera-wire-tool data model.

These exist for documentation + mypy. Runtime is still plain dicts so the
existing JSON I/O paths are unchanged.
"""

from __future__ import annotations

from typing import TypedDict, Literal, NotRequired


# ---------------- Symbol type (legend entry, lives at the project level)
class SymbolType(TypedDict):
    label: str
    shape: Literal["ellipse", "rect", "roundrect", "hexagon", "halfrect", "step"]
    color: str           # "#RRGGBB"
    is_camera: bool
    cable_type: NotRequired[str]   # references CableType.id; default per-category
    extra_cables: NotRequired[list[str]]  # optional secondary cables


# ---------------- Cable type (project-wide BOM entry)
class CableType(TypedDict):
    label: str           # human-friendly name shown in BOM
    color: str           # "#RRGGBB" — used to color wires on map
    cost_per_ft: float   # USD per foot


# ---------------- Camera FOV/DORI per device
class FOVSettings(TypedDict, total=False):
    enabled: bool
    lens_mm: float
    sensor: str          # e.g. "1/2.8\""
    resolution: list[int]   # [w, h] pixels
    mount_height_ft: float
    tilt_deg: float      # downward tilt
    azimuth_deg: float   # 0 = up
    show: bool


# ---------------- Device (placed instance on a floor)
class Device(TypedDict):
    id: str              # "s10"
    type: str            # references SymbolType key, e.g. "MD"
    x: float             # drawio page coords
    y: float
    w: float
    h: float
    orig_style: str      # raw style string from .drawio (preserves unknown bits)
    fov: NotRequired[FOVSettings]


# ---------------- Wire route (multi-point path between/from devices)
class WireRoute(TypedDict):
    points: list[tuple[float, float]]   # image-pixel coords
    length_px: float
    length_in: float | None             # None when scale not set
    device_id: str | None
    cable_type: NotRequired[str]


# ---------------- Floor (one drawio file + its alignment to the project frame)
class FloorAlignment(TypedDict):
    tx: float
    ty: float
    scale: float
    rotation_deg: float


class Floor(TypedDict):
    id: str
    name: str
    elevation_ft: float
    drawio_file: str
    alignment: FloorAlignment
    anchor_points: NotRequired[list[list[float]]]


# ---------------- Riser (cable run between floors)
class RiserEndpoint(TypedDict):
    floor: str
    device_id: str


class Riser(TypedDict):
    from_: RiserEndpoint   # JSON key is "from"; we serialize manually
    to: RiserEndpoint
    vertical_ft: float
    horizontal_ft: float
    cable_type: str
    device_id: str | None
