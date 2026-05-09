"""
Camera Wire Estimation Tool — v0.2.0
=====================================
Loads a .drawio building schematic with security device icons, lets you:
- Calibrate scale (feet/inches)
- Drag device icons to new positions
- Draw wire routes and estimate total wire length
- Edit symbol types (rename shortcodes, change colors/shapes/labels)
  — changes propagate to EVERY instance on the map and auto-regenerate the legend
- Add new symbol types and place instances
- Save everything back to the .drawio file

v0.2.0 polish pass adds:
- Sun Valley dark theme
- Mode enum + segmented mode bar (replaces 4 booleans)
- Icon toolbar with grouped sections + tooltips everywhere
- 3-tab side panel with per-list search
- Universal undo/redo command stack (Ctrl+Z / Ctrl+Y)
- Real error handling with logged dialogs
- Full keyboard shortcuts (M / W / C / Space-pan / [ ] / + - etc.)
- Constants module — all magic numbers named
- Breadcrumb status bar at the bottom
- Dirty-state tracking with asterisk in title
- Recent files menu
"""

from __future__ import annotations

import base64
import io
import json
import math
import os
import re
import sys
import traceback
import xml.etree.ElementTree as ET
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from enum import Enum, auto
from pathlib import Path

import tkinter as tk
from tkinter import colorchooser, filedialog, messagebox, ttk

from PIL import Image, ImageTk

try:
    import sv_ttk  # Sun Valley theme — soft dependency
    _HAS_SVTTK = True
except ImportError:
    _HAS_SVTTK = False

# cwt support package (always available — sits next to this script)
from cwt import bom as bom_mod
from cwt import fov as fov_mod
from cwt import lenses as lenses_mod
from cwt import multifloor as mf_mod

try:
    from cwt import export as export_mod
    _HAS_EXPORT = True
except ImportError:
    _HAS_EXPORT = False
    export_mod = None  # type: ignore


# ============================================================ Constants
@dataclass(frozen=True)
class Constants:
    # Rendering
    DEBOUNCE_MS: int = 16              # ~60 fps
    VIEWPORT_MAX_DIM: int = 4000       # cap output dimension to prevent memory blow-up
    HIT_TEST_MARGIN_RATIO: float = 0.3 # extra clickable margin around devices
    ZOOM_FACTOR: float = 1.15          # mousewheel + button zoom step
    ZOOM_MIN: float = 0.02
    ZOOM_MAX: float = 15.0
    FIT_MARGIN: float = 0.95           # leave 5% gutter when fitting

    # Legend
    LEGEND_ROW_H: int = 38             # drawio units per row in legend
    LEGEND_TITLE_FONT_MIN: int = 7
    LEGEND_TITLE_FONT_MAX: int = 16
    LEGEND_ICON_FONT_MIN: int = 5
    LEGEND_ICON_FONT_MAX: int = 12
    LEGEND_TEXT_FONT_MIN: int = 6
    LEGEND_TEXT_FONT_MAX: int = 13
    LEGEND_DEFAULT_BOX_X: int = 50
    LEGEND_DEFAULT_BOX_Y: int = 1400
    LEGEND_DEFAULT_BOX_W: int = 340
    LEGEND_DEFAULT_BOX_H: int = 740

    # Devices
    DEVICE_LABEL_FONT_MIN: int = 6
    DEVICE_LABEL_FONT_MAX: int = 14
    DEVICE_DEFAULT_W: int = 22
    DEVICE_DEFAULT_H: int = 22
    DEVICE_ID_FONT_MIN: int = 6
    DEVICE_ID_FONT_MAX: int = 11

    # Undo/redo
    HISTORY_LIMIT: int = 100

    # Recent files
    MAX_RECENT_FILES: int = 6

    # Tooltip
    TOOLTIP_DELAY_MS: int = 500

    # Initial render delay
    INITIAL_RENDER_DELAY_MS: int = 150


C = Constants()

# Camera detection: shortcodes treated as cameras even with non-yellow fill
CAMERA_CODES: frozenset[str] = frozenset({"MD", "360", "PTZ", "BC", "180", "HS", "MI"})

# Per-camera-type default FOV presets. Realistic security-camera defaults
# matching common installations. Used when a camera doesn't have its own
# `fov` dict — both for rendering and for the Tools → "Apply default FOV"
# bulk action.
FOV_PRESETS_BY_TYPE: dict[str, dict] = {
    # Fixed minidome — typical interior 4mm lens, 9ft mount, modest tilt
    "MD":  {"lens_mm": 4.0,  "sensor": '1/2.8"', "resolution": [3840, 2160],
            "mount_height_ft": 9.0,  "tilt_deg": 15, "azimuth_deg": 0},
    # 360 fisheye — very wide lens; AOV lookup will give ~110-120° fwd cone
    "360": {"lens_mm": 1.55, "sensor": '1/2.8"', "resolution": [3840, 2160],
            "mount_height_ft": 9.0,  "tilt_deg": 0,  "azimuth_deg": 0},
    # Bullet — usually telephoto, longer reach, tighter cone
    "BC":  {"lens_mm": 6.0,  "sensor": '1/2.8"', "resolution": [3840, 2160],
            "mount_height_ft": 12.0, "tilt_deg": 10, "azimuth_deg": 0},
    # PTZ — moderate lens, mount high to look across area
    "PTZ": {"lens_mm": 4.0,  "sensor": '1/2.8"', "resolution": [3840, 2160],
            "mount_height_ft": 12.0, "tilt_deg": 10, "azimuth_deg": 0},
    # 180° camera (panoramic) — ultra-wide
    "180": {"lens_mm": 2.0,  "sensor": '1/2.8"', "resolution": [3840, 2160],
            "mount_height_ft": 9.0,  "tilt_deg": 15, "azimuth_deg": 0},
    # Height-strip — narrow door coverage, mount lower, more tilt
    "HS":  {"lens_mm": 4.0,  "sensor": '1/2.8"', "resolution": [3840, 2160],
            "mount_height_ft": 8.0,  "tilt_deg": 30, "azimuth_deg": 0},
    # Multi-imager dome — moderate
    "MI":  {"lens_mm": 4.0,  "sensor": '1/2.8"', "resolution": [3840, 2160],
            "mount_height_ft": 9.0,  "tilt_deg": 15, "azimuth_deg": 0},
}


def default_fov_for_type(type_code: str) -> dict:
    """Return a fresh copy of the default FOV preset for a camera type.
    Falls back to MD defaults for unknown types. Always sets show=True
    and enabled=True so the cone immediately renders when used."""
    preset = FOV_PRESETS_BY_TYPE.get(type_code, FOV_PRESETS_BY_TYPE["MD"])
    return {**preset, "show": True, "enabled": True}

# Available shapes (for both drawing and saving)
SHAPE_OPTIONS = ["ellipse", "rect", "roundrect", "hexagon", "halfrect", "step"]

# Style templates for .drawio export
SHAPE_STYLES = {
    "ellipse":   "ellipse;html=1;fillColor={color};strokeColor=#0000FF;strokeWidth=2;fontSize={fs};fontStyle=1;fontColor=#000000",
    "rect":      "rounded=0;html=1;fillColor={color};strokeColor=#0000FF;strokeWidth=1.5;fontSize={fs};fontStyle=1;fontColor=#000000",
    "roundrect": "rounded=1;html=1;fillColor={color};strokeColor=#0000FF;strokeWidth=1.5;fontSize={fs};fontStyle=1;fontColor=#000000",
    "hexagon":   "shape=hexagon;html=1;fillColor={color};strokeColor=#0000FF;strokeWidth=1.5;fontSize={fs};fontStyle=1;fontColor=#000000",
    "halfrect":  "shape=mxgraph.bootstrap.halfRect;html=1;direction=south;fillColor={color};strokeColor=#0000FF;strokeWidth=1.5;fontSize={fs};fontStyle=1;fontColor=#000000",
    "step":      "shape=step;html=1;fillColor={color};strokeColor=#0000FF;strokeWidth=1.5;fontSize={fs};fontStyle=1;fontColor=#000000",
}

# Per-user state files
HOME_DIR = Path.home()
LOG_PATH = HOME_DIR / ".camera_wire_tool.log"
RECENT_FILES_PATH = HOME_DIR / ".camera_wire_tool_recent.json"
SETTINGS_PATH = HOME_DIR / ".camera_wire_tool_settings.json"


# ============================================================ Color helper
def _text_for_bg(hex_color: str) -> str:
    """Return black or white text color depending on background luminance.
    Used so color-swatch cells stay readable."""
    h = hex_color.lstrip("#")
    if len(h) != 6:
        return "#000000"
    try:
        r = int(h[0:2], 16); g = int(h[2:4], 16); b = int(h[4:6], 16)
    except ValueError:
        return "#000000"
    # Standard luma — text is black if bg is bright, white if bg is dark
    luma = (0.299 * r + 0.587 * g + 0.114 * b) / 255
    return "#000000" if luma > 0.55 else "#FFFFFF"


# ============================================================ Logging
def log_error(msg: str, exc: BaseException | None = None) -> None:
    """Append a timestamped error to the user log file. Best-effort; never raises."""
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"\n[{datetime.now().isoformat(timespec='seconds')}] {msg}\n")
            if exc is not None:
                f.write("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
    except OSError:
        pass


# ============================================================ Mode enum
class Mode(Enum):
    MOVE = auto()
    WIRE = auto()
    CALIBRATE = auto()
    PLACE_DEVICE = auto()


# ============================================================ Drawio helpers
def detect_shape(style: str) -> str:
    """Detect shape type from a drawio style string."""
    if "ellipse" in style:
        return "ellipse"
    if "hexagon" in style:
        return "hexagon"
    if "halfRect" in style:
        return "halfrect"
    if "step" in style:
        return "step"
    if "rounded=1" in style:
        return "roundrect"
    return "rect"


def detect_color(style: str) -> str:
    m = re.search(r"fillColor=(#[0-9A-Fa-f]{6})", style)
    return m.group(1) if m else "#FFFF00"


def parse_drawio(filepath: str):
    """Parse a .drawio file. Returns (bg_image, page_w, page_h, devices, symbol_types, legend_box_info)."""
    try:
        tree = ET.parse(filepath)
    except ET.ParseError as e:
        log_error(f"XML parse error in {filepath}", e)
        raise RuntimeError(f"Could not parse .drawio XML:\n{e}") from e
    except OSError as e:
        log_error(f"OS error reading {filepath}", e)
        raise RuntimeError(f"Could not read file:\n{e}") from e

    root = tree.getroot()
    diagram = root.find("diagram")
    if diagram is None:
        raise RuntimeError("No <diagram> element found in .drawio file")
    model = diagram.find("mxGraphModel")
    if model is None:
        raise RuntimeError("No <mxGraphModel> element found in .drawio file")

    page_w = int(model.get("pageWidth", "3024"))
    page_h = int(model.get("pageHeight", "2160"))

    cells = model.findall(".//mxCell")

    bg_image = None
    devices: list[dict] = []
    legend_box_info = None
    legend_icon_list: list[dict] = []
    legend_text_list: list[dict] = []

    for cell in cells:
        cell_id = cell.get("id", "")
        style = cell.get("style", "")
        value = cell.get("value", "")
        geom = cell.find("mxGeometry")
        parent = cell.get("parent", "")

        # background image
        if "shape=image;image=data:image/png," in style and geom is not None:
            try:
                b64_data = style.split("data:image/png,", 1)[1]
                if ";" in b64_data:
                    b64_data = b64_data.split(";")[0]
                img_bytes = base64.b64decode(b64_data)
                bg_image = Image.open(io.BytesIO(img_bytes))
                bg_image.load()
            except (ValueError, OSError) as e:
                log_error("Failed to decode embedded background image", e)
            continue

        # legend box
        if cell_id == "legbox" and geom is not None:
            legend_box_info = {
                "x": float(geom.get("x", "0")),
                "y": float(geom.get("y", "0")),
                "w": float(geom.get("width", str(C.LEGEND_DEFAULT_BOX_W))),
                "h": float(geom.get("height", str(C.LEGEND_DEFAULT_BOX_H))),
                "title": value,
            }
            continue

        # legend children
        if parent == "legbox" and geom is not None:
            y = float(geom.get("y", "0"))
            if cell_id.startswith("legi"):
                legend_icon_list.append({
                    "value": value.strip(), "y": y,
                    "shape": detect_shape(style), "color": detect_color(style),
                })
            elif cell_id.startswith("legt"):
                legend_text_list.append({"value": value, "y": y})
            continue

        if cell_id in ("0", "1"):
            continue

        # device cell — accept any cell with geometry and a non-empty short value
        if geom is not None and value.strip():
            val = value.strip()
            if len(val) > 6:
                continue
            x = float(geom.get("x", "0"))
            y = float(geom.get("y", "0"))
            w = float(geom.get("width", str(C.DEVICE_DEFAULT_W)))
            h = float(geom.get("height", str(C.DEVICE_DEFAULT_H)))
            devices.append({
                "id": cell_id, "type": val,
                "x": x, "y": y, "w": w, "h": h,
                "orig_style": style,
            })

    # Match legend icons with text by sorted y-position
    legend_icon_list.sort(key=lambda e: e["y"])
    legend_text_list.sort(key=lambda e: e["y"])

    symbol_types: dict[str, dict] = {}
    for i, icon in enumerate(legend_icon_list):
        code = icon["value"]
        label = legend_text_list[i]["value"] if i < len(legend_text_list) else code
        symbol_types[code] = {
            "label": label,
            "shape": icon["shape"],
            "color": icon["color"],
            "is_camera": code in CAMERA_CODES or icon["color"].upper() == "#FFFF00",
        }

    # Discover types from placed devices that aren't in legend
    for dev in devices:
        if dev["type"] not in symbol_types:
            symbol_types[dev["type"]] = {
                "label": dev["type"],
                "shape": detect_shape(dev["orig_style"]),
                "color": detect_color(dev["orig_style"]),
                "is_camera": dev["type"] in CAMERA_CODES
                              or detect_color(dev["orig_style"]).upper() == "#FFFF00",
            }

    return bg_image, page_w, page_h, devices, symbol_types, legend_box_info


# ============================================================ Tooltip helper
class Tooltip:
    """Lightweight hover tooltip for any tk widget."""

    def __init__(self, widget: tk.Widget, text: str):
        self.widget = widget
        self.text = text
        self.tip: tk.Toplevel | None = None
        self.after_id: str | None = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _event=None):
        self._cancel()
        self.after_id = self.widget.after(C.TOOLTIP_DELAY_MS, self._show)

    def _cancel(self):
        if self.after_id is not None:
            try:
                self.widget.after_cancel(self.after_id)
            except tk.TclError:
                pass
            self.after_id = None

    def _show(self):
        if self.tip is not None or not self.text:
            return
        x = self.widget.winfo_rootx() + 20
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self.tip = tk.Toplevel(self.widget)
        self.tip.wm_overrideredirect(True)
        self.tip.wm_geometry(f"+{x}+{y}")
        lbl = tk.Label(
            self.tip, text=self.text, justify=tk.LEFT,
            background="#2d2d2d", foreground="#e0e0e0",
            relief=tk.SOLID, borderwidth=1, font=("Segoe UI", 9),
            padx=8, pady=4,
        )
        lbl.pack()

    def _hide(self, _event=None):
        self._cancel()
        if self.tip is not None:
            try:
                self.tip.destroy()
            except tk.TclError:
                pass
            self.tip = None


# ============================================================ Command pattern (undo/redo)
class Command(ABC):
    """A reversible operation. `do()` applies, `undo()` reverses."""

    @abstractmethod
    def do(self) -> None: ...

    @abstractmethod
    def undo(self) -> None: ...

    def label(self) -> str:
        return type(self).__name__


class PasteDevicesCmd(Command):
    """Insert several copied devices at once. Reversible; undo removes
    each pasted device by reference."""

    def __init__(self, app: "CameraWireTool", new_devices: list[dict]):
        self.app = app
        self.new_devices = list(new_devices)

    def do(self):
        for d in self.new_devices:
            if d not in self.app.devices:
                self.app.devices.append(d)

    def undo(self):
        ids = {d["id"] for d in self.new_devices}
        self.app.devices = [d for d in self.app.devices if d["id"] not in ids]


class MoveDeviceCmd(Command):
    def __init__(self, app: "CameraWireTool", device: dict, old_x: float, old_y: float,
                 new_x: float, new_y: float):
        self.app = app
        self.device_id = device["id"]
        self.old = (old_x, old_y)
        self.new = (new_x, new_y)

    def _find(self) -> dict | None:
        return next((d for d in self.app.devices if d["id"] == self.device_id), None)

    def do(self):
        d = self._find()
        if d is not None:
            d["x"], d["y"] = self.new

    def undo(self):
        d = self._find()
        if d is not None:
            d["x"], d["y"] = self.old


class MoveDevicesCmd(Command):
    """Batch-move multiple devices by the same delta. One undo step."""

    def __init__(self, app: "CameraWireTool",
                 deltas: list[tuple[str, tuple[float, float], tuple[float, float]]]):
        self.app = app
        self.deltas = deltas    # list of (device_id, old_xy, new_xy)

    def do(self):
        ids = {did: new_xy for did, _old, new_xy in self.deltas}
        for d in self.app.devices:
            if d["id"] in ids:
                d["x"], d["y"] = ids[d["id"]]

    def undo(self):
        ids = {did: old_xy for did, old_xy, _new in self.deltas}
        for d in self.app.devices:
            if d["id"] in ids:
                d["x"], d["y"] = ids[d["id"]]

    def label(self) -> str:
        return f"move {len(self.deltas)} devices"


class AddWireCmd(Command):
    def __init__(self, app: "CameraWireTool", route: dict):
        self.app = app
        self.route = route

    def do(self):
        if self.route not in self.app.wire_routes:
            self.app.wire_routes.append(self.route)

    def undo(self):
        try:
            self.app.wire_routes.remove(self.route)
        except ValueError:
            pass


class DeleteWireCmd(Command):
    def __init__(self, app: "CameraWireTool", index: int, route: dict):
        self.app = app
        self.index = index
        self.route = route

    def do(self):
        if self.route in self.app.wire_routes:
            self.app.wire_routes.remove(self.route)

    def undo(self):
        idx = min(self.index, len(self.app.wire_routes))
        self.app.wire_routes.insert(idx, self.route)


class ClearWiresCmd(Command):
    def __init__(self, app: "CameraWireTool", routes: list[dict]):
        self.app = app
        self.snapshot = list(routes)

    def do(self):
        self.app.wire_routes.clear()

    def undo(self):
        self.app.wire_routes.clear()
        self.app.wire_routes.extend(self.snapshot)


class SimplifyWireCmd(Command):
    """Remove collinear interior waypoints from a wire. Reversible."""

    def __init__(self, app: "CameraWireTool", route: dict, new_pts: list):
        self.app = app
        self.route = route
        self.old_pts = list(route["points"])
        self.new_pts = list(new_pts)

    def _set(self, pts: list) -> None:
        self.route["points"] = list(pts)
        self.route["length_px"] = self.app._calc_wire_length(pts)
        sf = self.app.scale_factor
        self.route["length_in"] = (self.route["length_px"] * sf) if sf else None

    def do(self):  self._set(self.new_pts)
    def undo(self): self._set(self.old_pts)


class MoveWaypointCmd(Command):
    """Drag a single intermediate waypoint on an existing wire route.
    Captures the wire by reference + point index + old/new coords so it
    survives undo/redo."""

    def __init__(self, app: "CameraWireTool", route: dict, point_idx: int,
                 old_xy: tuple[float, float], new_xy: tuple[float, float]):
        self.app = app
        self.route = route
        self.point_idx = point_idx
        self.old_xy = old_xy
        self.new_xy = new_xy

    def _set(self, xy: tuple[float, float]) -> None:
        if 0 <= self.point_idx < len(self.route["points"]):
            pts = list(self.route["points"])
            pts[self.point_idx] = xy
            self.route["points"] = pts
            # Recompute length now that geometry changed
            self.route["length_px"] = self.app._calc_wire_length(pts)
            sf = self.app.scale_factor
            self.route["length_in"] = (self.route["length_px"] * sf) if sf else None

    def do(self):  self._set(self.new_xy)
    def undo(self): self._set(self.old_xy)


class ClearDeviceWiresCmd(Command):
    """Remove every wire associated with a single device."""

    def __init__(self, app: "CameraWireTool", device_id: str):
        self.app = app
        self.device_id = device_id
        # Snapshot at construction (do() may run multiple times on undo/redo,
        # but the snapshot is what we'll keep adding/removing).
        self.removed: list[tuple[int, dict]] = [
            (i, w) for i, w in enumerate(app.wire_routes)
            if w.get("device_id") == device_id
        ]

    def do(self):
        for _, route in self.removed:
            try:
                self.app.wire_routes.remove(route)
            except ValueError:
                pass

    def undo(self):
        for idx, route in sorted(self.removed, key=lambda r: r[0]):
            insert_idx = min(idx, len(self.app.wire_routes))
            self.app.wire_routes.insert(insert_idx, route)


class AddDeviceCmd(Command):
    def __init__(self, app: "CameraWireTool", device: dict):
        self.app = app
        self.device = device

    def do(self):
        if self.device not in self.app.devices:
            self.app.devices.append(self.device)

    def undo(self):
        try:
            self.app.devices.remove(self.device)
        except ValueError:
            pass


class DeleteDeviceCmd(Command):
    """Delete a device AND all wire routes that reference it. Without
    this, deleting a device leaves orphan wires that the BOM silently
    counts under a fallback cable type — confusing and wrong."""

    def __init__(self, app: "CameraWireTool", device: dict, index: int):
        self.app = app
        self.device = device
        self.index = index
        # captured at do() time so undo restores wires in their
        # original positions
        self._removed_wires: list[tuple[int, dict]] = []

    def do(self):
        if self.device in self.app.devices:
            self.app.devices.remove(self.device)
        did = self.device.get("id")
        # Sweep wires referencing this device. Capture (original_index, route)
        # pairs in REVERSE so we can pop without shifting indexes.
        self._removed_wires = []
        for i in range(len(self.app.wire_routes) - 1, -1, -1):
            if self.app.wire_routes[i].get("device_id") == did:
                self._removed_wires.append((i, self.app.wire_routes.pop(i)))

    def undo(self):
        idx = min(self.index, len(self.app.devices))
        self.app.devices.insert(idx, self.device)
        # Restore wires in original index order (smallest first)
        for i, wr in sorted(self._removed_wires, key=lambda t: t[0]):
            self.app.wire_routes.insert(min(i, len(self.app.wire_routes)), wr)


class EditSymbolCmd(Command):
    """Edit symbol type. Captures old/new info + (if renamed) updates all instances."""

    def __init__(self, app: "CameraWireTool", old_code: str, old_info: dict,
                 new_code: str, new_info: dict, is_new: bool):
        self.app = app
        self.old_code = old_code
        self.old_info = dict(old_info)
        self.new_code = new_code
        self.new_info = dict(new_info)
        self.is_new = is_new

    def do(self):
        if self.is_new:
            self.app.symbol_types[self.new_code] = self.new_info
        else:
            if self.new_code != self.old_code:
                self.app.symbol_types.pop(self.old_code, None)
                for d in self.app.devices:
                    if d["type"] == self.old_code:
                        d["type"] = self.new_code
            self.app.symbol_types[self.new_code] = self.new_info

    def undo(self):
        if self.is_new:
            self.app.symbol_types.pop(self.new_code, None)
        else:
            if self.new_code != self.old_code:
                self.app.symbol_types.pop(self.new_code, None)
                for d in self.app.devices:
                    if d["type"] == self.new_code:
                        d["type"] = self.old_code
            self.app.symbol_types[self.old_code] = self.old_info


class DeleteSymbolCmd(Command):
    """Delete a symbol type, every instance of it on the map, AND their
    wires. Same orphan-wire problem as DeleteDeviceCmd; cascade with undo."""

    def __init__(self, app: "CameraWireTool", code: str, info: dict, removed_devices: list[dict]):
        self.app = app
        self.code = code
        self.info = dict(info)
        self.removed_devices = list(removed_devices)
        self._removed_wires: list[tuple[int, dict]] = []

    def do(self):
        self.app.symbol_types.pop(self.code, None)
        self.app.devices = [d for d in self.app.devices if d["type"] != self.code]
        # Sweep wires for every removed device's id
        removed_ids = {d.get("id") for d in self.removed_devices}
        self._removed_wires = []
        for i in range(len(self.app.wire_routes) - 1, -1, -1):
            if self.app.wire_routes[i].get("device_id") in removed_ids:
                self._removed_wires.append((i, self.app.wire_routes.pop(i)))

    def undo(self):
        self.app.symbol_types[self.code] = self.info
        self.app.devices.extend(self.removed_devices)
        for i, wr in sorted(self._removed_wires, key=lambda t: t[0]):
            self.app.wire_routes.insert(min(i, len(self.app.wire_routes)), wr)


class AddAnnotationCmd(Command):
    def __init__(self, app: "CameraWireTool", ann: dict):
        self.app = app
        self.ann = ann

    def do(self):
        if self.ann not in self.app.annotations:
            self.app.annotations.append(self.ann)

    def undo(self):
        try:
            self.app.annotations.remove(self.ann)
        except ValueError:
            pass


class DeleteAnnotationCmd(Command):
    def __init__(self, app: "CameraWireTool", ann: dict, index: int):
        self.app = app
        self.ann = ann
        self.index = index

    def do(self):
        try:
            self.app.annotations.remove(self.ann)
        except ValueError:
            pass

    def undo(self):
        idx = min(self.index, len(self.app.annotations))
        self.app.annotations.insert(idx, self.ann)


class EditAnnotationCmd(Command):
    """Replace an annotation's mutable fields (text/color/font_size).
    Lives in-place — same dict identity, so existing references keep
    working."""

    EDITED_KEYS = ("text", "color", "font_size")

    def __init__(self, app: "CameraWireTool", ann: dict,
                 old_values: dict, new_values: dict):
        self.app = app
        self.ann = ann
        self.old_values = old_values
        self.new_values = new_values

    def do(self):
        for k in self.EDITED_KEYS:
            if k in self.new_values:
                self.ann[k] = self.new_values[k]

    def undo(self):
        for k in self.EDITED_KEYS:
            if k in self.old_values:
                self.ann[k] = self.old_values[k]


class EditFovCmd(Command):
    """Apply new FOV settings to one or many cameras. Each entry is
    (device, old_fov_or_None, new_fov). `None` for old_fov means the
    device had no fov dict before (undo deletes the key).

    Bulk variant batches every per-camera mutation into a single undoable
    step so 'Apply to all 28 cameras' is one Ctrl+Z, not 28."""

    def __init__(self, app: "CameraWireTool",
                 entries: list[tuple[dict, dict | None, dict]]):
        self.app = app
        self.entries = entries

    def do(self):
        for dev, _old, new in self.entries:
            dev["fov"] = dict(new)

    def undo(self):
        for dev, old, _new in self.entries:
            if old is None:
                dev.pop("fov", None)
            else:
                dev["fov"] = dict(old)


class ReassignWireCmd(Command):
    """Move a wire from one device_id to another (or to None). Without
    this, reassign was a silent direct mutation and Ctrl+Z couldn't roll
    it back — surprising for any other operation."""

    def __init__(self, app: "CameraWireTool", route: dict,
                 old_did: str | None, new_did: str | None):
        self.app = app
        self.route = route
        self.old_did = old_did
        self.new_did = new_did

    def do(self):
        self.route["device_id"] = self.new_did

    def undo(self):
        self.route["device_id"] = self.old_did


class CalibrateCmd(Command):
    def __init__(self, app: "CameraWireTool", old_scale: float | None, new_scale: float):
        self.app = app
        self.old_scale = old_scale
        self.new_scale = new_scale

    def do(self):
        self.app.scale_factor = self.new_scale
        self.app._recalc_all_wires()

    def undo(self):
        self.app.scale_factor = self.old_scale
        self.app._recalc_all_wires()


# ============================================================ Theme
def apply_theme(root: tk.Tk) -> None:
    if _HAS_SVTTK:
        try:
            sv_ttk.set_theme("dark")
        except tk.TclError:
            pass
    # Touch up a few colors via ttk style for our custom segmented control
    style = ttk.Style(root)
    try:
        style.configure("Mode.TButton", padding=(10, 4))
        style.configure("ModeActive.TButton", padding=(10, 4), foreground="#ffffff")
    except tk.TclError:
        pass


# ============================================================ Recent files
def load_recent_files() -> list[str]:
    if not RECENT_FILES_PATH.exists():
        return []
    try:
        data = json.loads(RECENT_FILES_PATH.read_text(encoding="utf-8"))
        return [p for p in data if isinstance(p, str)]
    except (OSError, ValueError):
        return []


def save_recent_files(paths: list[str]) -> None:
    try:
        RECENT_FILES_PATH.write_text(json.dumps(paths[: C.MAX_RECENT_FILES]), encoding="utf-8")
    except OSError:
        pass


def push_recent_file(path: str) -> list[str]:
    paths = [p for p in load_recent_files() if p != path]
    paths.insert(0, path)
    paths = paths[: C.MAX_RECENT_FILES]
    save_recent_files(paths)
    return paths


# ============================================================ User preferences
def load_settings() -> dict:
    """Persisted UI preferences — unit, currency, BOM-modifiers, etc."""
    if not SETTINGS_PATH.exists():
        return {}
    try:
        return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_settings(settings: dict) -> None:
    try:
        SETTINGS_PATH.write_text(json.dumps(settings, indent=2),
                                  encoding="utf-8")
    except OSError:
        pass


# ============================================================ Main app
class CameraWireTool:
    def __init__(self, root: tk.Tk, drawio_path: str):
        self.root = root
        self.drawio_path = drawio_path
        self.root.state("zoomed")

        # Parse — show user-friendly error rather than stack trace
        try:
            (self.bg_image, self.page_w, self.page_h,
             self.devices, self.symbol_types, self.legend_box) = parse_drawio(drawio_path)
        except RuntimeError as e:
            messagebox.showerror("Cannot open file", str(e))
            sys.exit(1)

        if self.bg_image is None:
            messagebox.showerror("Error", "No background image found in .drawio file")
            sys.exit(1)

        self.img_w, self.img_h = self.bg_image.size
        self.dx_scale = self.img_w / self.page_w
        self.dy_scale = self.img_h / self.page_h

        if self.legend_box is None:
            self.legend_box = {
                "x": C.LEGEND_DEFAULT_BOX_X, "y": C.LEGEND_DEFAULT_BOX_Y,
                "w": C.LEGEND_DEFAULT_BOX_W, "h": C.LEGEND_DEFAULT_BOX_H,
                "title": "Symbols",
            }

        self.project_path = os.path.splitext(drawio_path)[0] + ".wiremap.json"

        # State
        self.scale_factor: float | None = None
        self.cal_points: list[tuple[float, float]] = []
        self.wire_routes: list[dict] = []
        self.current_wire_points: list[tuple[float, float]] = []
        self.dragging_device: dict | None = None
        self._drag_start_pos: tuple[float, float] | None = None
        self.drag_offset = (0.0, 0.0)
        self._multi_drag_starts: dict[str, tuple[float, float]] = {}
        # Waypoint-edit state: when a wire is selected and the user drags
        # an intermediate waypoint handle.
        self._dragging_waypoint: tuple[dict, int, tuple[float, float]] | None = None
        # Multi-selection: `selected_device` is a property that returns
        # the primary (most recently clicked) device, while
        # `selected_devices` is the full set highlighted on the map.
        # Ctrl+click toggles membership in `selected_devices`.
        self.selected_devices: list[dict] = []
        self._primary_selected: dict | None = None
        # Ctrl+click-vs-drag detector state
        self._ctrl_press_pos: tuple[int, int] | None = None
        self._ctrl_dragging = False
        self.zoom = 1.0
        self.offset_x = 0.0
        self.offset_y = 0.0
        self.pan_start: tuple[int, int] | None = None
        self._space_pan = False           # space-bar held for temporary pan
        self.unit_var = tk.StringVar(value="ft-in")
        self._tk_ref: ImageTk.PhotoImage | None = None
        self._redraw_pending = False
        self._interactive = False           # use BILINEAR while True (fast)
        self._settle_id: str | None = None  # after-id for crisp post-interaction redraw
        self._next_device_id = 100
        self._place_type: str | None = None
        self._show_device_ids = tk.BooleanVar(value=False)
        self._show_fov = tk.BooleanVar(value=False)
        self._show_dori = tk.BooleanVar(value=True)
        self._show_only_selected_fov = tk.BooleanVar(value=False)
        self._show_legend = tk.BooleanVar(value=True)
        self._show_grid = tk.BooleanVar(value=False)

        # Cable types catalog (project-level)
        self.cable_types: dict = {k: dict(v) for k, v in
                                   bom_mod.DEFAULT_CABLE_TYPES.items()}

        # Multi-floor project (created lazily; single-floor by default)
        # active_floor_id is "F1" for legacy single-floor; multi-floor mode
        # populates self._project (ProjectState) and tracks floors there.
        self._project: mf_mod.ProjectState | None = None
        self._floor_anchors_picking: list[tuple[float, float]] | None = None

        # BOM tab inputs — defaults overridden by persisted user prefs
        prefs = load_settings()
        self._bom_waste_var = tk.DoubleVar(
            value=float(prefs.get("waste_factor", 1.10)))
        self._bom_tax_var = tk.DoubleVar(
            value=float(prefs.get("tax_rate", 0.0)))
        self._bom_currency_var = tk.StringVar(
            value=str(prefs.get("currency", "$")))
        # If a previous session set a unit, prefer it over the per-project
        # `_loaded_unit` only when the project file didn't carry one.
        self._initial_pref_unit = prefs.get("unit")
        self._user_settings = prefs

        # Auto-save (writes per-session recovery file every minute)
        self._autosave_path = self.project_path + ".autosave.json"
        self._autosave_id: str | None = None

        # Wire-drawing snap state — current target the cursor would lock to
        self._wire_snap: tuple[float, float, str] | None = None
        self._shift_held = False
        # Hover state: device or wire-route under the cursor (None when hovering empty space)
        self._hover_device: dict | None = None
        # Legend-drag state — set when the user clicks-and-drags the legend
        # rectangle on the canvas to reposition it.
        self._dragging_legend: dict | None = None    # {start_drawio, start_offset}
        # FOV rotation drag state — set when user drags the FOV handle of a camera
        self._rotating_fov: dict | None = None
        # Cache of FOV handle positions per device, populated by _draw_fov_cones
        # so _on_left_press can hit-test cheaply.
        self._fov_handles: dict[str, tuple[float, float, float, float]] = {}
        # Lasso (box) selection state — set during drag on empty canvas
        # (start_canvas, current_canvas, modifier-flags from press)
        self._lasso: dict | None = None
        # Map annotations: free-floating text labels users place to mark
        # zones (Server Room, Loading Dock, IDF, etc.)
        # Each: {x: drawio-x, y: drawio-y, text, color, font_size}
        self.annotations: list[dict] = []
        self._placing_annotation: dict | None = None    # pending {text, color, font_size}

        # Mode
        self.mode = Mode.MOVE
        self.mode_var = tk.StringVar(value=self.mode.name)

        # Undo/redo
        self.history: list[Command] = []
        self.future: list[Command] = []

        # Dirty tracking
        self._dirty = False

        # Figure out next available device ID
        for d in self.devices:
            m = re.match(r"s(\d+)", d["id"])
            if m:
                self._next_device_id = max(self._next_device_id, int(m.group(1)) + 1)

        self._load_project()

        # Recent files
        push_recent_file(drawio_path)

        # Build UI
        self._build_ui()
        self._update_title()
        self._refresh_mode_ui()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(C.INITIAL_RENDER_DELAY_MS, self._initial_render)

    # ============================================================ project file IO
    def _load_project(self) -> None:
        """Load via multifloor.load_project (handles v1 → v2 migration).

        v1 wiremap.json files (single floor, flat wire_routes list) are
        auto-wrapped into a v2 ProjectState with one floor 'F1'.
        """
        try:
            self._project = mf_mod.load_project(self.project_path, self.drawio_path)
        except Exception as e:
            log_error(f"Could not load project file {self.project_path}", e)
            messagebox.showwarning(
                "Project file unreadable",
                f"Could not load companion project file:\n{self.project_path}\n\n"
                f"Error: {e}\n\nWires and scale will start empty.",
            )
            self._project = mf_mod.ProjectState(
                floors=[mf_mod.FloorState(
                    id="F1", name="First Floor", elevation_ft=0,
                    drawio_file=os.path.basename(self.drawio_path),
                )],
            )
            return

        proj = self._project
        self.scale_factor = proj.scale_factor
        self._loaded_unit = proj.unit
        if proj.cable_types:
            self.cable_types = {**self.cable_types, **proj.cable_types}
        if proj.next_device_id:
            self._next_device_id = max(self._next_device_id, proj.next_device_id)

        # Active floor's wire routes → self.wire_routes (recalc lengths now)
        active = proj.get_floor(proj.active_floor) or proj.floors[0]
        # Restore map annotations for the active floor
        self.annotations = [dict(a) for a in (active.annotations or [])]
        for wr in active.wire_routes:
            pts = [tuple(p) for p in wr.get("points", [])]
            if len(pts) < 2:
                continue
            px_len = self._calc_wire_length(pts)
            real_in = px_len * self.scale_factor if self.scale_factor else None
            entry = {
                "points": pts,
                "length_px": px_len,
                "length_in": real_in,
                "device_id": wr.get("device_id"),
            }
            if wr.get("cable_type"):
                entry["cable_type"] = wr["cable_type"]
            self.wire_routes.append(entry)

    def _save_project(self) -> None:
        """Persist as v2 project file. Captures cable types + active floor wires."""
        if self._project is None:
            self._project = mf_mod.ProjectState(
                floors=[mf_mod.FloorState(
                    id="F1", name="First Floor", elevation_ft=0,
                    drawio_file=os.path.basename(self.drawio_path),
                )],
            )

        proj = self._project
        proj.scale_factor = self.scale_factor
        proj.unit = self.unit_var.get()
        proj.next_device_id = self._next_device_id
        proj.cable_types = {k: dict(v) for k, v in self.cable_types.items()}

        # Sync active floor's wire_routes from the live list
        active = proj.get_floor(proj.active_floor) or proj.floors[0]
        active.annotations = [dict(a) for a in self.annotations]
        active.wire_routes = []
        for wr in self.wire_routes:
            entry = {
                "points": [list(p) for p in wr["points"]],
                "device_id": wr.get("device_id"),
            }
            if wr.get("cable_type"):
                entry["cable_type"] = wr["cable_type"]
            active.wire_routes.append(entry)

        try:
            mf_mod.save_project(proj, self.project_path)
        except OSError as e:
            log_error(f"Could not write project file {self.project_path}", e)
            messagebox.showerror("Save failed", f"Could not write project file:\n{e}")

    # ============================================================ initial render + events
    def _initial_render(self) -> None:
        self.root.update_idletasks()
        if hasattr(self, "_loaded_unit"):
            self.unit_var.set(self._loaded_unit)
        elif self._initial_pref_unit:
            # No project-saved unit, but user has a preference from previous session
            self.unit_var.set(self._initial_pref_unit)
        # also sync the unit combobox once UI is built
        if hasattr(self, "_unit_label_var"):
            for lab, v in self._unit_choices:
                if v == self.unit_var.get():
                    self._unit_label_var.set(lab); break
        # ttk.Button doesn't accept `foreground` via config(); the changing
        # text (⚠-prefixed → plain "Scale:") is enough to signal state.
        self.scale_label.config(text=self._scale_label_text())
        self._fit_image()
        self._refresh_wire_list()
        self._redraw()
        self._bind_events()
        # Autosave: prompt to recover if a fresh autosave outranks the saved
        # file, then start the periodic timer.
        self._maybe_recover_autosave()
        self._schedule_autosave()

    def _bind_events(self) -> None:
        self.canvas.bind("<ButtonPress-1>", self._on_left_press)
        self.canvas.bind("<B1-Motion>", self._on_left_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_left_release)
        self.canvas.bind("<Motion>", self._on_motion)
        # NOTE: right-click is handled by _on_right_click_with_menu below.
        self.canvas.bind("<MouseWheel>", self._on_mousewheel)
        self.canvas.bind("<ButtonPress-2>", self._on_pan_start)
        self.canvas.bind("<B2-Motion>", self._on_pan_move)
        self.canvas.bind("<ButtonRelease-2>", self._on_pan_end)
        # Ctrl+left: click-or-drag detector. A short Ctrl+click toggles
        # the device under the cursor in the multi-selection. A Ctrl+drag
        # (more than ~5 pixels of movement) pans the canvas.
        self.canvas.bind("<Control-ButtonPress-1>", self._on_ctrl_press)
        self.canvas.bind("<Control-B1-Motion>", self._on_ctrl_motion)
        self.canvas.bind("<Control-ButtonRelease-1>", self._on_ctrl_release)

        # Keyboard
        b = self.root.bind
        b("<Delete>", self._on_delete_key)
        b("<Escape>", lambda _e: self._cancel_current())
        b("<Control-z>", lambda _e: self._undo())
        b("<Control-Z>", lambda _e: self._undo())
        b("<Control-y>", lambda _e: self._redo())
        b("<Control-Y>", lambda _e: self._redo())
        b("<Control-Shift-z>", lambda _e: self._redo())
        b("<Control-Shift-Z>", lambda _e: self._redo())
        b("<Control-f>", lambda _e: self._focus_search())
        b("<Control-F>", lambda _e: self._focus_search())
        b("<Control-g>", lambda _e: self._open_quick_jump())
        b("<Control-G>", lambda _e: self._open_quick_jump())
        b("<Control-p>", lambda _e: self._open_quick_jump())
        b("<Control-P>", lambda _e: self._open_quick_jump())
        b("<Home>", lambda _e: (self._fit_image(), self._redraw()))
        b("<plus>", lambda _e: self._zoom_button(1))
        b("<equal>", lambda _e: self._zoom_button(1))
        b("<minus>", lambda _e: self._zoom_button(-1))
        # Z key — zoom to selected device or wire
        b("<KeyPress-z>", self._kbd_zoom_to_selection)
        b("<KeyPress-Z>", self._kbd_zoom_to_selection)
        # F1 / ? — keyboard shortcuts dialog (universal convention)
        b("<F1>", lambda _e: self._show_shortcuts())
        # F11 — toggle fullscreen
        b("<F11>", lambda _e: self._toggle_fullscreen())
        b("<KeyPress-question>", self._kbd_help)
        b("<KeyPress-slash>", self._kbd_help)   # / on US keyboards before Shift
        # R — rotate selected camera's FOV azimuth (Shift+R = 45° steps,
        # Alt+R = counter-clockwise)
        b("<KeyPress-r>", self._kbd_rotate_fov)
        b("<KeyPress-R>", self._kbd_rotate_fov)
        # Side-panel tab quick switching
        b("<Control-Key-1>", lambda _e: self._notebook.select(0))
        b("<Control-Key-2>", lambda _e: self._notebook.select(1))
        b("<Control-Key-3>", lambda _e: self._notebook.select(2))
        b("<Control-Key-4>", lambda _e: self._notebook.select(3))
        b("<Control-Tab>", lambda _e: self._cycle_tab(1))
        b("<Control-Shift-Tab>", lambda _e: self._cycle_tab(-1))
        # Select-all devices on canvas
        b("<Control-a>", lambda _e: self._select_all_devices())
        b("<Control-A>", lambda _e: self._select_all_devices())
        # Copy / paste selected devices
        b("<Control-c>", lambda _e: self._copy_selected_devices())
        b("<Control-C>", lambda _e: self._copy_selected_devices())
        b("<Control-v>", lambda _e: self._paste_devices(event_=_e))
        b("<Control-V>", lambda _e: self._paste_devices(event_=_e))
        b("<bracketleft>", lambda _e: self._cycle_device(-1))
        b("<bracketright>", lambda _e: self._cycle_device(1))
        b("<braceleft>", lambda _e: self._cycle_wire(-1))   # Shift+[
        b("<braceright>", lambda _e: self._cycle_wire(1))   # Shift+]
        # Single-key mode shortcuts (only when canvas-focused, never inside Entry)
        b("<KeyPress-m>", self._kbd_mode_move)
        b("<KeyPress-M>", self._kbd_mode_move)
        b("<KeyPress-w>", self._kbd_mode_wire)
        b("<KeyPress-W>", self._kbd_mode_wire)
        b("<KeyPress-c>", self._kbd_mode_cal)
        b("<KeyPress-C>", self._kbd_mode_cal)
        b("<KeyPress-space>", self._kbd_space_press)
        b("<KeyRelease-space>", self._kbd_space_release)
        # Configure (resize / sash drag) is throttled HARD: don't redraw on
        # every event, just schedule a settle. Tk clips the existing canvas
        # content automatically, so dragging feels instant.
        self.canvas.bind("<Configure>", lambda _e: self._on_canvas_configure())
        # Right-click: finish/cancel wire, otherwise show context menu
        self.canvas.bind("<Button-3>", self._on_right_click_with_menu)
        # Clear cursor coord readout when cursor leaves the canvas
        self.canvas.bind("<Leave>", lambda _e: self.cursor_label.config(text=""))

    # ============================================================ Tab cycling / select-all
    def _cycle_tab(self, direction: int) -> None:
        """Ctrl+Tab / Ctrl+Shift+Tab — cycle through the side-panel tabs."""
        if not hasattr(self, "_notebook"):
            return
        try:
            cur = self._notebook.index("current")
            n = len(self._notebook.tabs())
            self._notebook.select((cur + direction) % n)
        except tk.TclError:
            pass

    # ============================================================ Copy / paste devices
    def _copy_selected_devices(self) -> None:
        # Don't intercept Ctrl+C in text widgets
        try:
            focused = self.root.focus_get()
        except Exception:
            focused = None
        if isinstance(focused, (ttk.Entry, tk.Entry, tk.Text)):
            return
        if not self.selected_devices:
            self._flash_status("Nothing selected to copy.", ttl_ms=2000)
            return
        self._device_clipboard = [
            {k: v for k, v in d.items() if k != "id"}
            for d in self.selected_devices
        ]
        self._flash_status(
            f"Copied {len(self._device_clipboard)} device(s) to clipboard.",
            ttl_ms=2500,
        )

    def _paste_devices(self, event_=None) -> None:
        try:
            focused = self.root.focus_get()
        except Exception:
            focused = None
        if isinstance(focused, (ttk.Entry, tk.Entry, tk.Text)):
            return
        clip = getattr(self, "_device_clipboard", None)
        if not clip:
            self._flash_status(
                "Nothing on clipboard. Ctrl+C copies selected devices.",
                ttl_ms=2500,
            )
            return
        # Anchor: cursor position when invoked via mouse-near keypress;
        # otherwise center of viewport.
        if event_ is not None and hasattr(event_, "x") and event_.x >= 0:
            try:
                anchor_ix, anchor_iy = self._canvas_to_img(event_.x, event_.y)
            except Exception:
                anchor_ix = anchor_iy = None
        else:
            anchor_ix = anchor_iy = None
        if anchor_ix is None or anchor_iy is None:
            cw = self.canvas.winfo_width() or 800
            ch = self.canvas.winfo_height() or 600
            anchor_ix, anchor_iy = self._canvas_to_img(cw / 2, ch / 2)

        # Treat the FIRST clipboard entry's center as the offset reference;
        # paste maintains relative geometry of multi-device groups.
        first = clip[0]
        first_cx = (first["x"] + first["w"]/2) * self.dx_scale
        first_cy = (first["y"] + first["h"]/2) * self.dy_scale
        anchor_dx, anchor_dy = self._img_to_drawio(anchor_ix, anchor_iy)
        ref_dx = first["x"] + first["w"] / 2
        ref_dy = first["y"] + first["h"] / 2
        delta_dx = anchor_dx - ref_dx
        delta_dy = anchor_dy - ref_dy

        new_devices: list[dict] = []
        for src in clip:
            new_id = f"s{self._next_device_id}"
            self._next_device_id += 1
            d_new = {**src, "id": new_id,
                     "x": src["x"] + delta_dx,
                     "y": src["y"] + delta_dy}
            # Deep-copy mutable nested dicts so edits don't bleed into
            # the original.
            if "fov" in d_new and isinstance(d_new["fov"], dict):
                d_new["fov"] = dict(d_new["fov"])
            new_devices.append(d_new)
        self._execute(PasteDevicesCmd(self, new_devices))
        # Select the newly pasted devices so user can drag/move/delete them
        self.selected_devices = list(new_devices)
        self._primary_selected = new_devices[-1]
        self._post_command_refresh()
        self._flash_status(
            f"Pasted {len(new_devices)} device(s). "
            "Drag to move; Ctrl+Z to undo.",
            ttl_ms=3500,
        )

    def _select_all_devices(self) -> None:
        """Ctrl+A — select every visible (filtered) device on the canvas."""
        # Skip if a text-input widget has focus (then Ctrl+A means select-all-text)
        try:
            focused = self.root.focus_get()
        except Exception:
            focused = None
        if isinstance(focused, (ttk.Entry, tk.Entry, tk.Text)):
            return
        if not self.devices:
            return
        # Use _filtered_devices (matches whatever's currently in the listbox)
        target = (self._filtered_devices
                  if hasattr(self, "_filtered_devices") and self._filtered_devices
                  else list(self.devices))
        self.selected_devices = list(target)
        self._primary_selected = target[-1]
        self._flash_status(
            f"Selected all {len(target)} device(s). Press Del to bulk-delete.",
        )
        self._redraw()

    def _simplify_selected_wire(self) -> None:
        """Drop interior waypoints whose direction equals the previous step's
        direction (collinear). Result has the same shape but fewer points."""
        route = self._selected_wire()
        if route is None:
            messagebox.showinfo("Select", "Select a wire to simplify.")
            return
        pts = route["points"]
        if len(pts) <= 2:
            self._flash_status("Wire already minimal — nothing to simplify.")
            return
        new_pts = [pts[0]]
        for i in range(1, len(pts) - 1):
            px, py = pts[i - 1]
            cx, cy = pts[i]
            nx, ny = pts[i + 1]
            # Treat colinear as: cross product of (current - prev) and
            # (next - current) is essentially zero.
            cross = (cx - px) * (ny - cy) - (cy - py) * (nx - cx)
            if abs(cross) > 1e-3:
                new_pts.append(pts[i])
        new_pts.append(pts[-1])
        if len(new_pts) == len(pts):
            self._flash_status("Wire is already minimal.")
            return
        cmd = SimplifyWireCmd(self, route, new_pts)
        self._execute(cmd)
        self._post_command_refresh()
        self._flash_status(
            f"Simplified wire — {len(pts)} → {len(new_pts)} points."
        )

    def _focus_search(self) -> None:
        # Focus whichever search field belongs to the visible tab
        widget = getattr(self, "_active_search_entry", None)
        if widget is not None:
            try:
                widget.focus_set()
                widget.select_range(0, tk.END)
            except tk.TclError:
                pass

    # ============================================================ UI
    def _build_ui(self) -> None:
        # Menu bar
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)

        # File
        file_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="File", menu=file_menu)
        file_menu.add_command(label="Open .drawio…", command=self._file_open, accelerator="Ctrl+O")
        self._recent_menu = tk.Menu(file_menu, tearoff=0)
        file_menu.add_cascade(label="Open Recent", menu=self._recent_menu)
        self._refresh_recent_menu()
        file_menu.add_separator()
        file_menu.add_command(label="Save", command=self._save_drawio, accelerator="Ctrl+S")
        file_menu.add_command(label="Save As…", command=self._file_save_as,
                              accelerator="Ctrl+Shift+S")
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self._on_close)

        self.root.bind("<Control-s>", lambda _e: self._save_drawio())
        self.root.bind("<Control-S>", lambda _e: self._file_save_as())
        self.root.bind("<Control-o>", lambda _e: self._file_open())

        # Edit
        edit_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Edit", menu=edit_menu)
        edit_menu.add_command(label="Undo", command=self._undo, accelerator="Ctrl+Z")
        edit_menu.add_command(label="Redo", command=self._redo, accelerator="Ctrl+Y")
        edit_menu.add_separator()
        edit_menu.add_command(label="Delete Selected", command=self._on_delete_key,
                              accelerator="Del")
        edit_menu.add_command(label="Clear All Wires…", command=self._clear_wires)

        # View
        view_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="View", menu=view_menu)
        view_menu.add_command(label="Fit View",
                              command=lambda: (self._fit_image(), self._redraw()),
                              accelerator="Home")
        view_menu.add_command(label="Zoom In", command=lambda: self._zoom_button(1),
                              accelerator="+")
        view_menu.add_command(label="Zoom Out", command=lambda: self._zoom_button(-1),
                              accelerator="-")
        view_menu.add_separator()
        view_menu.add_checkbutton(label="Show Device IDs on Map",
                                  variable=self._show_device_ids,
                                  command=self._redraw)
        view_menu.add_checkbutton(label="Show Camera FOV Cones",
                                  variable=self._show_fov,
                                  command=self._redraw)
        view_menu.add_checkbutton(label="Show DORI Rings (D/O/R/I)",
                                  variable=self._show_dori,
                                  command=self._redraw)
        view_menu.add_checkbutton(
            label="Show ONLY selected camera's FOV",
            variable=self._show_only_selected_fov,
            command=self._redraw)
        view_menu.add_separator()
        view_menu.add_checkbutton(label="Show Legend on Map",
                                  variable=self._show_legend,
                                  command=self._redraw)
        view_menu.add_checkbutton(label="Show Alignment Grid",
                                  variable=self._show_grid,
                                  command=self._redraw)

        # Mode
        mode_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Mode", menu=mode_menu)
        mode_menu.add_radiobutton(label="Move", variable=self.mode_var, value=Mode.MOVE.name,
                                  accelerator="M",
                                  command=lambda: self._set_mode(Mode.MOVE))
        mode_menu.add_radiobutton(label="Wire", variable=self.mode_var, value=Mode.WIRE.name,
                                  accelerator="W",
                                  command=lambda: self._set_mode(Mode.WIRE))
        mode_menu.add_radiobutton(label="Calibrate", variable=self.mode_var,
                                  value=Mode.CALIBRATE.name, accelerator="C",
                                  command=self._start_calibration)

        # Floors
        floor_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Floors", menu=floor_menu)
        self._floor_menu = floor_menu
        self._refresh_floor_menu()

        # Tools
        tools_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Tools", menu=tools_menu)
        tools_menu.add_command(label="Cable Types Editor…",
                               command=self._open_cable_types_editor)
        tools_menu.add_command(label="Edit FOV for Selected Camera…",
                               command=self._open_fov_dialog_for_selected)
        tools_menu.add_command(
            label="Apply Default FOV to All Cameras",
            command=self._apply_default_fov_all_cameras)
        tools_menu.add_command(
            label="Clear FOV from All Cameras",
            command=self._clear_fov_all_cameras)
        tools_menu.add_separator()
        tools_menu.add_command(label="Add Map Annotation…",
                               command=self._start_place_annotation)
        tools_menu.add_separator()
        tools_menu.add_command(label="Preferences…",
                               command=self._open_preferences_dialog)

        # Export submenu inside File
        file_menu.add_separator()
        export_menu = tk.Menu(file_menu, tearoff=0)
        file_menu.add_cascade(label="Export", menu=export_menu)
        export_menu.add_command(label="BOM (CSV)…", command=self._export_bom_csv)
        export_menu.add_command(label="All CSVs (BOM + Devices + Wires)…",
                                command=self._export_csv_bundle)
        export_menu.add_command(label="Project PDF Report…",
                                command=self._export_pdf)
        export_menu.add_separator()
        export_menu.add_command(label="Map Snapshot (PNG)…",
                                command=self._export_map_png)

        # Help
        help_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Help", menu=help_menu)
        help_menu.add_command(label="Keyboard Shortcuts", command=self._show_shortcuts)
        help_menu.add_command(label="About", command=self._show_about)

        # Toolbar
        toolbar = ttk.Frame(self.root)
        toolbar.pack(side=tk.TOP, fill=tk.X)

        # Text-labelled buttons render reliably on Windows where emoji glyphs
        # in ttk.Button silently fall back to empty squares. Width=None lets
        # each button size to its label.
        def _ttkbtn(text, cmd, tip, width=None):
            kwargs = {"text": text, "command": cmd}
            if width is not None:
                kwargs["width"] = width
            b = ttk.Button(toolbar, **kwargs)
            b.pack(side=tk.LEFT, padx=2, pady=4)
            Tooltip(b, tip)
            return b

        _ttkbtn("Open",  self._file_open,   "Open .drawio  (Ctrl+O)")
        _ttkbtn("Save",  self._save_drawio, "Save  (Ctrl+S)")
        ttk.Separator(toolbar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=4, pady=4)

        self._undo_btn = _ttkbtn("← Undo", self._undo, "Undo  (Ctrl+Z)")
        self._redo_btn = _ttkbtn("Redo →", self._redo, "Redo  (Ctrl+Y)")
        ttk.Separator(toolbar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=4, pady=4)

        _ttkbtn("Set Scale",
                self._start_calibration,
                "Set real-world scale by clicking a known dimension  (C)")
        _ttkbtn("Fit",
                lambda: (self._fit_image(), self._redraw()),
                "Fit floor plan to viewport  (Home)")
        _ttkbtn("Zoom +", lambda: self._zoom_button(1),  "Zoom in  (+)",  width=8)
        _ttkbtn("Zoom −", lambda: self._zoom_button(-1), "Zoom out (-)", width=8)
        ttk.Separator(toolbar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=4, pady=4)

        # Quick toggles
        ids_btn = ttk.Checkbutton(
            toolbar, text="IDs", variable=self._show_device_ids,
            style="Toolbutton", command=self._redraw,
        )
        ids_btn.pack(side=tk.LEFT, padx=2, pady=2)
        Tooltip(ids_btn, "Show device-ID labels above each icon on the map")
        grid_btn = ttk.Checkbutton(
            toolbar, text="Grid", variable=self._show_grid,
            style="Toolbutton", command=self._redraw,
        )
        grid_btn.pack(side=tk.LEFT, padx=2, pady=2)
        Tooltip(grid_btn, "Show alignment grid (5 ft / 1 m intervals)")
        legend_btn = ttk.Checkbutton(
            toolbar, text="Legend", variable=self._show_legend,
            style="Toolbutton", command=self._redraw,
        )
        legend_btn.pack(side=tk.LEFT, padx=2, pady=2)
        Tooltip(legend_btn, "Show / hide the auto-generated legend on the map")
        fov_btn = ttk.Checkbutton(
            toolbar, text="FOV", variable=self._show_fov,
            style="Toolbutton", command=self._redraw,
        )
        fov_btn.pack(side=tk.LEFT, padx=2, pady=2)
        Tooltip(fov_btn, "Show / hide camera FOV cones with DORI rings")
        ttk.Separator(toolbar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=4, pady=4)

        # Mode segmented control
        seg = ttk.Frame(toolbar)
        seg.pack(side=tk.LEFT, padx=4, pady=2)
        self._mode_buttons: dict[Mode, ttk.Radiobutton] = {}
        for m, label, tip in (
            (Mode.MOVE,       "Move",       "Move/select devices  (M)"),
            (Mode.WIRE,       "Wire",       "Draw wire routes  (W)"),
            (Mode.CALIBRATE,  "Calibrate",  "Set real-world scale  (C)"),
        ):
            rb = ttk.Radiobutton(
                seg, text=label, value=m.name, variable=self.mode_var,
                command=lambda mm=m: self._on_mode_radio(mm),
                style="Toolbutton",
            )
            rb.pack(side=tk.LEFT, padx=1)
            Tooltip(rb, tip)
            self._mode_buttons[m] = rb

        ttk.Separator(toolbar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=4, pady=4)
        ttk.Label(toolbar, text="Units:").pack(side=tk.LEFT, padx=(4, 0))
        # Imperial + metric in a single dropdown — keeps the toolbar tidy.
        self._unit_choices = [
            ("Feet & Inches  (12'-6 1/2\")", "ft-in"),
            ("Inches  (150.50\")",            "inches"),
            ("Decimal feet  (12.54 ft)",     "feet-dec"),
            ("Meters  (3.823 m)",            "meters"),
            ("Centimeters  (382.3 cm)",      "cm"),
            ("Meters & cm  (3 m 82.3 cm)",   "m-cm"),
        ]
        self._unit_label_var = tk.StringVar()
        self._unit_combo = ttk.Combobox(
            toolbar, textvariable=self._unit_label_var,
            values=[label for label, _val in self._unit_choices],
            state="readonly", width=22,
        )
        self._unit_combo.pack(side=tk.LEFT, padx=4)
        Tooltip(self._unit_combo, "Switch length-display units (imperial or metric)")

        # Initial selection from existing unit_var
        def _sync_combo_from_unit():
            for lab, v in self._unit_choices:
                if v == self.unit_var.get():
                    self._unit_label_var.set(lab); return
            self._unit_label_var.set(self._unit_choices[0][0])
        _sync_combo_from_unit()

        def _on_unit_combo(_e=None):
            label = self._unit_label_var.get()
            for lab, v in self._unit_choices:
                if lab == label:
                    self.unit_var.set(v); break
            self._on_unit_change()
        self._unit_combo.bind("<<ComboboxSelected>>", _on_unit_combo)

        # Scale display: button-styled when unset (so it's obviously
        # clickable), plain label after calibration. Click jumps straight
        # into the calibration flow.
        self.scale_label = ttk.Button(
            toolbar,
            text="⚠ Scale not set — click to calibrate",
            command=self._start_calibration,
        )
        self.scale_label.pack(side=tk.LEFT, padx=10, pady=2)
        Tooltip(self.scale_label,
                "Without a scale, wire lengths show in pixels.\n"
                "Click here, then click two points of a known dimension on the plan.")

        # Zoom percentage indicator — quietly tucked next to scale, useful
        # for orientation. Updates every redraw.
        self.zoom_indicator = ttk.Label(
            toolbar, text="Zoom: 100%",
            font=("Consolas", 9), foreground="#888888",
        )
        self.zoom_indicator.pack(side=tk.LEFT, padx=8, pady=2)
        Tooltip(self.zoom_indicator,
                "Current viewport zoom.\n"
                "Mouse wheel zooms toward cursor; Home fits to view; Z zooms to selection.")

        # Always-visible project metrics on the right side of the toolbar.
        # Updated whenever wires / devices / cable types change.
        self.total_toolbar_label = ttk.Label(
            toolbar, text="—",
            font=("Segoe UI", 10, "bold"))
        self.total_toolbar_label.pack(side=tk.RIGHT, padx=10)

        # Main split. Using legacy tk.PanedWindow (NOT ttk) specifically so
        # we can set opaqueresize=False — that turns sash drag into a fast
        # phantom-line drag that commits on mouse-release. ttk.PanedWindow
        # has no equivalent option and forces live resize, which on a
        # canvas-with-86-devices + Treeview side panel is unbearably slow.
        bg_paned = "#1e1e1e" if _HAS_SVTTK else "#cccccc"
        main = tk.PanedWindow(
            self.root, orient=tk.HORIZONTAL,
            opaqueresize=False,
            sashrelief=tk.RAISED, sashwidth=6, sashpad=1,
            borderwidth=0, bg=bg_paned,
        )
        main.pack(fill=tk.BOTH, expand=True)

        # Canvas
        canvas_frame = ttk.Frame(main)
        canvas_bg = "#1e1e1e" if _HAS_SVTTK else "#2b2b2b"
        self.canvas = tk.Canvas(canvas_frame, bg=canvas_bg, highlightthickness=0,
                                cursor="hand2")
        self.canvas.pack(fill=tk.BOTH, expand=True)
        # Mini-map: overview canvas pinned to the canvas's top-right.
        # Sized comfortably for navigation without dominating the canvas.
        self._minimap_w, self._minimap_h = 280, 180
        self.minimap = tk.Canvas(
            canvas_frame, width=self._minimap_w, height=self._minimap_h,
            bg="#0e0e0e", highlightthickness=1, highlightbackground="#888888",
            cursor="hand2",
        )
        # `place` instead of pack so it floats over the main canvas
        self.minimap.place(relx=1.0, rely=0.0, anchor="ne", x=-10, y=10)
        self.minimap.bind("<ButtonPress-1>", self._minimap_click)
        self.minimap.bind("<B1-Motion>", self._minimap_click)
        self.minimap.bind("<Double-Button-1>",
                           lambda _e: (self._fit_image(), self._redraw(),
                                        self._flash_status("Fit to view.",
                                                            ttl_ms=1500)))
        Tooltip(self.minimap,
                "Mini-map\n"
                "  · click to pan there\n"
                "  · double-click to fit view")
        self._minimap_photo: ImageTk.PhotoImage | None = None
        self._minimap_built = False
        main.add(canvas_frame, stretch="always", minsize=300)

        # Side panel
        side = ttk.Frame(main, width=380)
        main.add(side, stretch="never", minsize=240, width=380)

        notebook = ttk.Notebook(side)
        notebook.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)
        self._notebook = notebook
        notebook.bind("<<NotebookTabChanged>>", self._on_tab_change)

        # --- Tab 1: Devices ---
        tab_dev = ttk.Frame(notebook)
        notebook.add(tab_dev, text="Devices")
        ttk.Label(tab_dev, text="Placed Devices",
                  font=("Segoe UI", 10, "bold")).pack(anchor=tk.W, padx=6, pady=(6, 2))
        self._dev_search_var = tk.StringVar()
        s_frame = ttk.Frame(tab_dev)
        s_frame.pack(fill=tk.X, padx=6, pady=2)
        ttk.Label(s_frame, text="🔎").pack(side=tk.LEFT)
        self._dev_search_entry = ttk.Entry(s_frame, textvariable=self._dev_search_var)
        self._dev_search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4)
        Tooltip(self._dev_search_entry, "Filter devices by ID, type, or label (Ctrl+F)")
        self._dev_search_var.trace_add("write", lambda *_: self._refresh_device_list())

        df = ttk.Frame(tab_dev)
        df.pack(fill=tk.BOTH, expand=True, padx=4, pady=2)
        # EXTENDED selectmode lets the user Ctrl/Shift-click rows to
        # build a multi-selection — synced to the canvas via _on_dev_select.
        self.dev_listbox = tk.Listbox(df, font=("Consolas", 9), selectmode=tk.EXTENDED,
                                       activestyle="dotbox")
        sb1 = ttk.Scrollbar(df, orient=tk.VERTICAL, command=self.dev_listbox.yview)
        self.dev_listbox.config(yscrollcommand=sb1.set)
        self.dev_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb1.pack(side=tk.RIGHT, fill=tk.Y)
        self.dev_listbox.bind("<<ListboxSelect>>", self._on_dev_select)
        self.dev_listbox.bind("<Double-Button-1>", lambda _e: self._center_on_selected())
        self.dev_listbox.bind("<Button-3>", self._on_dev_listbox_rclick)

        ttk.Button(tab_dev, text="Center on Selected (Enter)",
                   command=self._center_on_selected).pack(anchor=tk.W, padx=6, pady=4)

        # --- Tab 2: Wires ---
        tab_wire = ttk.Frame(notebook)
        notebook.add(tab_wire, text="Wires")
        ttk.Label(tab_wire, text="Wire Runs",
                  font=("Segoe UI", 10, "bold")).pack(anchor=tk.W, padx=6, pady=(6, 2))

        # Big hint banner so users know the easy way to add wires.
        hint = ttk.Label(
            tab_wire,
            text=("Tip:  right-click any device on the map → "
                  "\"Draw wire from here…\" → click route points → "
                  "right-click to finish.\n"
                  "Or press W with a device selected, then click points."),
            justify=tk.LEFT, foreground="#7fc4ff",
            font=("Segoe UI", 9, "italic"),
            wraplength=440,
        )
        hint.pack(anchor=tk.W, padx=8, pady=(0, 6))

        self._wire_search_var = tk.StringVar()
        ws_frame = ttk.Frame(tab_wire)
        ws_frame.pack(fill=tk.X, padx=6, pady=2)
        ttk.Label(ws_frame, text="🔎").pack(side=tk.LEFT)
        self._wire_search_entry = ttk.Entry(ws_frame, textvariable=self._wire_search_var)
        self._wire_search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4)
        Tooltip(self._wire_search_entry, "Filter wires by associated device ID (Ctrl+F)")
        self._wire_search_var.trace_add("write", lambda *_: self._refresh_wire_list())

        wf = ttk.Frame(tab_wire)
        wf.pack(fill=tk.BOTH, expand=True, padx=4, pady=2)
        # EXTENDED so user can Ctrl-click multiple wires for bulk delete.
        self.wire_listbox = tk.Listbox(wf, font=("Consolas", 9), selectmode=tk.EXTENDED,
                                        activestyle="dotbox")
        sb2 = ttk.Scrollbar(wf, orient=tk.VERTICAL, command=self.wire_listbox.yview)
        self.wire_listbox.config(yscrollcommand=sb2.set)
        self.wire_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb2.pack(side=tk.RIGHT, fill=tk.Y)
        self.wire_listbox.bind("<<ListboxSelect>>", self._on_wire_select)
        self.wire_listbox.bind("<Button-3>", self._on_wire_listbox_rclick)
        self.wire_listbox.bind("<Double-Button-1>", lambda _e: self._reassign_selected_wire())

        wbtns = ttk.Frame(tab_wire)
        wbtns.pack(fill=tk.X, padx=6, pady=4)
        ttk.Button(wbtns, text="Delete",
                   command=self._delete_selected_wire).pack(side=tk.LEFT)
        ttk.Button(wbtns, text="Reassign…",
                   command=self._reassign_selected_wire).pack(side=tk.LEFT, padx=4)
        ttk.Button(wbtns, text="Re-route",
                   command=self._reroute_selected_wire).pack(side=tk.LEFT, padx=4)
        simp_btn = ttk.Button(wbtns, text="Simplify",
                               command=self._simplify_selected_wire)
        simp_btn.pack(side=tk.LEFT, padx=4)
        Tooltip(simp_btn,
                "Remove redundant collinear waypoints from this wire.\n"
                "Same shape, fewer handles.")

        ttk.Separator(tab_wire, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=4, pady=4)
        # Big total — same prominence as the toolbar metric badge
        self.total_label = ttk.Label(tab_wire, text="Total Wire: --",
                                     font=("Segoe UI", 14, "bold"),
                                     foreground="#3ddc84")
        self.total_label.pack(anchor=tk.W, padx=8, pady=(2, 8))

        # --- Tab 3: Symbols ---
        tab_sym = ttk.Frame(notebook)
        notebook.add(tab_sym, text="Symbols")
        ttk.Label(tab_sym, text="Symbol Types",
                  font=("Segoe UI", 10, "bold")).pack(anchor=tk.W, padx=6, pady=(6, 2))

        self._sym_search_var = tk.StringVar()
        ss_frame = ttk.Frame(tab_sym)
        ss_frame.pack(fill=tk.X, padx=6, pady=2)
        ttk.Label(ss_frame, text="🔎").pack(side=tk.LEFT)
        self._sym_search_entry = ttk.Entry(ss_frame, textvariable=self._sym_search_var)
        self._sym_search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4)
        Tooltip(self._sym_search_entry, "Filter symbol types (Ctrl+F)")
        self._sym_search_var.trace_add("write", lambda *_: self._refresh_symbol_list())

        sf = ttk.Frame(tab_sym)
        sf.pack(fill=tk.BOTH, expand=True, padx=4, pady=2)
        self.sym_listbox = tk.Listbox(sf, font=("Consolas", 9), selectmode=tk.SINGLE,
                                       activestyle="dotbox")
        sb3 = ttk.Scrollbar(sf, orient=tk.VERTICAL, command=self.sym_listbox.yview)
        self.sym_listbox.config(yscrollcommand=sb3.set)
        self.sym_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb3.pack(side=tk.RIGHT, fill=tk.Y)
        # Double-click on a symbol → place an instance immediately. Edit
        # is still available via the Edit button, the right-click menu, or
        # selecting + Enter.
        self.sym_listbox.bind("<Double-Button-1>",
                              lambda _e: self._place_symbol_from_listbox())
        self.sym_listbox.bind("<Button-3>", self._on_sym_listbox_rclick)
        self.sym_listbox.bind("<Return>", lambda _e: self._edit_symbol_type())

        sym_btns = ttk.Frame(tab_sym)
        sym_btns.pack(fill=tk.X, padx=4, pady=4)
        ttk.Button(sym_btns, text="Place on Map",
                   command=self._place_symbol_from_listbox).pack(side=tk.LEFT, padx=2)
        ttk.Button(sym_btns, text="Edit",
                   command=self._edit_symbol_type).pack(side=tk.LEFT, padx=2)
        ttk.Button(sym_btns, text="Add…",
                   command=self._add_symbol_type).pack(side=tk.LEFT, padx=2)
        ttk.Button(sym_btns, text="Delete",
                   command=self._delete_symbol_type).pack(side=tk.LEFT, padx=2)
        rp_btn = ttk.Button(sym_btns, text="+ Run Point",
                             command=self._add_run_point_type)
        rp_btn.pack(side=tk.LEFT, padx=2)
        Tooltip(rp_btn,
                "Quick-create an IDF / NVR / switch (wire-run-point) symbol.\n"
                "Run points get a yellow halo on the map.")

        # Help line
        ttk.Label(tab_sym,
                  text="Tip: double-click any symbol to drop it on the map. "
                       "Right-click for more options.",
                  foreground="#7fc4ff", font=("Segoe UI", 9, "italic"),
                  wraplength=440, justify=tk.LEFT,
                  ).pack(anchor=tk.W, padx=8, pady=(4, 6))

        # Internal: place_combo / place_type_var are still consumed by
        # _start_place_device. We keep them in memory but no longer display
        # the redundant "Place Device" section at the bottom.
        self.place_type_var = tk.StringVar()
        self.place_combo = ttk.Combobox(tab_sym, textvariable=self.place_type_var,
                                         state="readonly")

        # --- Tab 4: BOM ---
        tab_bom = ttk.Frame(notebook)
        notebook.add(tab_bom, text="BOM")
        ttk.Label(tab_bom, text="Bill of Materials",
                  font=("Segoe UI", 10, "bold")).pack(anchor=tk.W, padx=6, pady=(6, 2))

        # Cost adjustment row
        adj = ttk.Frame(tab_bom)
        adj.pack(fill=tk.X, padx=6, pady=2)
        ttk.Label(adj, text="Waste ×").pack(side=tk.LEFT)
        ws_entry = ttk.Entry(adj, textvariable=self._bom_waste_var, width=6)
        ws_entry.pack(side=tk.LEFT, padx=(2, 8))
        Tooltip(ws_entry, "Waste multiplier (1.10 = 10% extra material)")
        ttk.Label(adj, text="Tax %").pack(side=tk.LEFT)
        tax_entry = ttk.Entry(adj, textvariable=self._bom_tax_var, width=6)
        tax_entry.pack(side=tk.LEFT, padx=(2, 8))
        Tooltip(tax_entry, "Sales tax rate (decimal, e.g. 0.08 for 8%)")
        ttk.Label(adj, text="Currency:").pack(side=tk.LEFT)
        ttk.Entry(adj, textvariable=self._bom_currency_var, width=4).pack(side=tk.LEFT, padx=2)
        # (No explicit refresh button — the BOM auto-refreshes on every state
        # change via _post_command_refresh; the variables also trigger a
        # refresh when their values change.)

        # BOM table (Treeview) — cable totals
        ttk.Label(tab_bom, text="By cable type",
                  font=("Segoe UI", 9, "bold")).pack(anchor=tk.W, padx=6, pady=(4, 0))
        bom_frame = ttk.Frame(tab_bom)
        bom_frame.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)
        cols = ("cable", "label", "runs", "ft", "rate", "subtotal")
        self._bom_tree = ttk.Treeview(bom_frame, columns=cols, show="headings",
                                       height=4)
        # Sortable headers — click to sort, click again to reverse.
        # _bom_sort_state: (column-id, reverse-flag)
        self._bom_sort_state: tuple[str, bool] = ("cable", False)
        def _make_sort_cmd(col_id):
            def cmd():
                cur_col, cur_rev = self._bom_sort_state
                self._bom_sort_state = (col_id, not cur_rev if cur_col == col_id else False)
                self._refresh_bom_tab()
            return cmd
        for c, w, anchor, h in (
            ("cable",    50,  tk.W, "Cable"),
            ("label",    130, tk.W, "Description"),
            ("runs",     50,  tk.E, "Runs"),
            ("ft",       70,  tk.E, "Feet"),
            ("rate",     55,  tk.E, "$/ft"),
            ("subtotal", 80,  tk.E, "Subtotal"),
        ):
            self._bom_tree.heading(c, text=h, command=_make_sort_cmd(c))
            self._bom_tree.column(c, width=w, anchor=anchor, stretch=False)
        sb_bom = ttk.Scrollbar(bom_frame, orient=tk.VERTICAL,
                                command=self._bom_tree.yview)
        self._bom_tree.config(yscrollcommand=sb_bom.set)
        self._bom_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb_bom.pack(side=tk.RIGHT, fill=tk.Y)
        self._bom_tree.bind("<Button-3>", self._on_bom_cable_rclick)

        # Devices-by-symbol Treeview — count + cable + wire totals per type
        ttk.Label(tab_bom, text="By device type",
                  font=("Segoe UI", 9, "bold")).pack(anchor=tk.W, padx=6, pady=(8, 0))
        dev_frame = ttk.Frame(tab_bom)
        dev_frame.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)
        dev_cols = ("code", "label", "count", "cable", "runs", "wire")
        self._bom_devices_tree = ttk.Treeview(
            dev_frame, columns=dev_cols, show="headings", height=10,
        )
        for c, w, anchor, h in (
            ("code",  45,  tk.W, "Code"),
            ("label", 150, tk.W, "Label"),
            ("count", 50,  tk.E, "Count"),
            ("cable", 60,  tk.W, "Cable"),
            ("runs",  45,  tk.E, "Runs"),
            ("wire",  100, tk.E, "Wire"),
        ):
            self._bom_devices_tree.heading(c, text=h)
            self._bom_devices_tree.column(c, width=w, anchor=anchor, stretch=False)
        sb_dev = ttk.Scrollbar(dev_frame, orient=tk.VERTICAL,
                                command=self._bom_devices_tree.yview)
        self._bom_devices_tree.config(yscrollcommand=sb_dev.set)
        self._bom_devices_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb_dev.pack(side=tk.RIGHT, fill=tk.Y)

        # Totals — laid out as 4 pills + 1 prominent grand-total
        totals_frame = ttk.Frame(tab_bom)
        totals_frame.pack(fill=tk.X, padx=8, pady=(6, 0))
        # Use plain tk.Label so we can color the pills individually
        self._bom_pill_subtotal = tk.Label(
            totals_frame, text="Subtotal\n—", padx=10, pady=4,
            bg="#23304a", fg="#e0e8ff", font=("Segoe UI", 9),
            justify=tk.CENTER,
        )
        self._bom_pill_subtotal.pack(side=tk.LEFT, padx=(0, 4))
        self._bom_pill_waste = tk.Label(
            totals_frame, text="Waste\n—", padx=10, pady=4,
            bg="#33231a", fg="#ffd0a0", font=("Segoe UI", 9),
            justify=tk.CENTER,
        )
        self._bom_pill_waste.pack(side=tk.LEFT, padx=2)
        self._bom_pill_tax = tk.Label(
            totals_frame, text="Tax\n—", padx=10, pady=4,
            bg="#1a3328", fg="#a0e0c0", font=("Segoe UI", 9),
            justify=tk.CENTER,
        )
        self._bom_pill_tax.pack(side=tk.LEFT, padx=2)
        self._bom_pill_total = tk.Label(
            totals_frame, text="GRAND TOTAL\n—",
            padx=12, pady=4,
            bg="#202060", fg="#FFFFFF", font=("Segoe UI", 10, "bold"),
            justify=tk.CENTER,
        )
        self._bom_pill_total.pack(side=tk.RIGHT, padx=(8, 0))
        # Keep the legacy label name for callers that still reference it,
        # but make it a no-op stub.
        self._bom_totals_label = ttk.Label(tab_bom, text="")
        # not packed — the pills above are the visible totals now

        bom_btns = ttk.Frame(tab_bom)
        bom_btns.pack(fill=tk.X, padx=6, pady=6)
        ttk.Button(bom_btns, text="Cable Types…",
                   command=self._open_cable_types_editor).pack(side=tk.LEFT)
        ttk.Button(bom_btns, text="Export CSV",
                   command=self._export_bom_csv).pack(side=tk.LEFT, padx=4)
        if _HAS_EXPORT:
            ttk.Button(bom_btns, text="Export PDF",
                       command=self._export_pdf).pack(side=tk.LEFT, padx=4)

        # Refresh on every input change AND persist the new value so the
        # next session opens with the same preferences.
        def _bom_input_changed(*_):
            self._refresh_bom_tab()
            self._persist_user_settings()
        for var in (self._bom_waste_var, self._bom_tax_var, self._bom_currency_var):
            var.trace_add("write", _bom_input_changed)

        # Bottom breadcrumb status bar
        self._statusbar = ttk.Frame(self.root)
        self._statusbar.pack(side=tk.BOTTOM, fill=tk.X)
        self.status_label = ttk.Label(self._statusbar, text="", anchor=tk.W,
                                       font=("Segoe UI", 9))
        self.status_label.pack(side=tk.LEFT, padx=8, pady=2, fill=tk.X, expand=True)
        self.cursor_label = ttk.Label(self._statusbar, text="", anchor=tk.E,
                                       font=("Consolas", 9), foreground="#888888")
        self.cursor_label.pack(side=tk.RIGHT, padx=8, pady=2)
        # "Saved Xm ago / Modified" indicator — updates every 15s
        self.save_indicator = ttk.Label(
            self._statusbar, text="", anchor=tk.E,
            font=("Segoe UI", 9), foreground="#888888",
        )
        self.save_indicator.pack(side=tk.RIGHT, padx=8, pady=2)
        self._last_save_time: float | None = None
        self._tick_save_indicator()

        self._refresh_symbol_list()
        self._refresh_device_list()
        self._refresh_bom_tab()
        self._update_undo_redo_state()

        # Default search hook for Ctrl+F = devices tab
        self._active_search_entry = self._dev_search_entry

    def _on_tab_change(self, _event=None):
        try:
            idx = self._notebook.index("current")
        except tk.TclError:
            return
        self._active_search_entry = (
            self._dev_search_entry if idx == 0
            else self._wire_search_entry if idx == 1
            else self._sym_search_entry
        )

    def _refresh_recent_menu(self) -> None:
        self._recent_menu.delete(0, tk.END)
        recent = load_recent_files()
        if not recent:
            self._recent_menu.add_command(label="(none)", state=tk.DISABLED)
            return
        for p in recent:
            short = os.path.basename(p) if os.path.exists(p) else f"(missing) {os.path.basename(p)}"
            self._recent_menu.add_command(
                label=short,
                command=lambda path=p: self._open_recent(path),
            )

    def _open_recent(self, path: str) -> None:
        if not os.path.exists(path):
            messagebox.showwarning("Missing", f"File no longer exists:\n{path}")
            return
        if self._dirty and not self._confirm_discard():
            return
        self.root.destroy()
        new_root = tk.Tk()
        apply_theme(new_root)
        new_root.title("Camera Wire Estimation Tool")
        CameraWireTool(new_root, path)
        new_root.mainloop()

    # ============================================================ Mode handling
    def _on_mode_radio(self, m: Mode) -> None:
        if m == Mode.CALIBRATE:
            self._start_calibration()
        else:
            self._set_mode(m)

    def _set_mode(self, new_mode: Mode) -> None:
        if self.mode == new_mode:
            return
        # Use the non-recursive cleanup helper. _cancel_current is for
        # user-initiated Esc and would call _set_mode itself → infinite
        # recursion when switching out of an action mode.
        self._reset_action_state()
        self.mode = new_mode
        self.mode_var.set(new_mode.name)
        self._refresh_mode_ui()

    def _refresh_mode_ui(self) -> None:
        cursor = {
            Mode.MOVE: "hand2",
            Mode.WIRE: "crosshair",
            Mode.CALIBRATE: "tcross",
            Mode.PLACE_DEVICE: "tcross",
        }.get(self.mode, "arrow")
        try:
            self.canvas.config(cursor=cursor)
        except tk.TclError:
            pass
        self.mode_var.set(self.mode.name)
        self._update_status()
        # Banner is part of the canvas redraw — without this, entering an
        # action mode via the toolbar/keyboard wouldn't paint the banner
        # until the next unrelated redraw (motion, scroll, etc.).
        self._redraw()

    def _kbd_mode_move(self, event):
        if self._is_text_focus(event):
            return
        self._set_mode(Mode.MOVE)
        self._flash_status("Mode: Move (M)  —  click devices to select / drag")

    def _kbd_mode_wire(self, event):
        if self._is_text_focus(event):
            return
        self._set_mode(Mode.WIRE)
        self._flash_status("Mode: Wire (W)  —  click points; right-click finishes")

    def _kbd_mode_cal(self, event):
        if self._is_text_focus(event):
            return
        self._start_calibration()

    def _kbd_rotate_fov(self, event):
        """R / Shift+R / Alt+R — rotate the selected camera's FOV.

        Step is 15° normally, 45° with Shift, sign flips with Alt for
        counter-clockwise. Auto-applies a default FOV first if the camera
        doesn't have one yet.
        """
        if self._is_text_focus(event):
            return
        if self._primary_selected is None:
            self._flash_status("Select a camera first, then press R to rotate FOV.")
            return
        dev = self._primary_selected
        info = self.symbol_types.get(dev["type"], {})
        if not info.get("is_camera"):
            self._flash_status(f"{dev['id']} isn't a camera — nothing to rotate.")
            return
        shift = bool(event.state & 0x0001)
        alt   = bool(event.state & 0x20000)   # Win Alt
        step = 45 if shift else 15
        if alt:
            step = -step
        if not dev.get("fov"):
            dev["fov"] = default_fov_for_type(dev["type"])
        new_az = (dev["fov"].get("azimuth_deg", 0) + step) % 360
        dev["fov"]["azimuth_deg"] = new_az
        # Compass label so the user knows roughly where it's pointing
        dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
        compass = dirs[round(new_az / 45) % 8]
        self._show_fov.set(True)
        self._mark_dirty()
        self._redraw()
        self._flash_status(
            f"Camera {dev['id']} → azimuth {new_az}° ({compass})",
            ttl_ms=2000)

    def _kbd_help(self, event):
        if self._is_text_focus(event):
            return
        # Only Shift+/ → '?' should fire on the unshifted '/' key.
        # If the event keysym says it's slash without shift, ignore.
        if event.keysym == "slash" and not (event.state & 0x0001):
            return
        self._show_shortcuts()

    def _kbd_space_press(self, event):
        if self._is_text_focus(event):
            return
        self._space_pan = True
        try:
            self.canvas.config(cursor="fleur")
        except tk.TclError:
            pass

    def _kbd_space_release(self, _event):
        self._space_pan = False
        self._refresh_mode_ui()

    def _is_text_focus(self, event) -> bool:
        w = event.widget if hasattr(event, "widget") else None
        return isinstance(w, (ttk.Entry, tk.Entry, tk.Text))

    # ============================================================ Auto-pan during drag
    def _maybe_autopan(self, event) -> None:
        """If the mouse cursor is within `edge_px` of the canvas edge while
        dragging, scroll the viewport that way so the user can drop a
        waypoint anywhere without releasing the mouse."""
        edge_px = 40
        speed = 18
        cw = self.canvas.winfo_width() or 800
        ch = self.canvas.winfo_height() or 600
        dx = dy = 0
        if event.x < edge_px:
            dx = speed
        elif event.x > cw - edge_px:
            dx = -speed
        if event.y < edge_px:
            dy = speed
        elif event.y > ch - edge_px:
            dy = -speed
        if dx or dy:
            self.offset_x += dx
            self.offset_y += dy

    # ============================================================ Wire-drawing snap
    def _wire_snap_target_drag(self, ix: float, iy: float, route: dict, pidx: int):
        """Variant of _wire_snap_target used while dragging an existing
        waypoint: skips snapping to the route's own neighbouring points
        (which would 'lock' the dragged point onto its current adjacents).
        """
        thresh = max(8.0, 14.0 / max(self.zoom, 0.01))
        best = None; best_d = float("inf"); best_kind = None
        # Device centers
        for d in self.devices:
            cx = (d["x"] + d["w"] / 2) * self.dx_scale
            cy = (d["y"] + d["h"] / 2) * self.dy_scale
            dd = math.hypot(ix - cx, iy - cy)
            if dd < thresh and dd < best_d:
                best = (cx, cy); best_d = dd; best_kind = "device"
        # Other wires' endpoints (not this wire's own)
        for r in self.wire_routes:
            if r is route:
                continue
            for p in (r["points"][0], r["points"][-1]):
                dd = math.hypot(ix - p[0], iy - p[1])
                if dd < thresh and dd < best_d:
                    best = p; best_d = dd; best_kind = "endpoint"
        if best is not None:
            return (best[0], best[1], best_kind)
        # Shift-orthogonal from the previous point on this wire
        if self._shift_held and pidx > 0:
            lx, ly = route["points"][pidx - 1]
            dx, dy = ix - lx, iy - ly
            if dx == 0 and dy == 0:
                return None
            ang = math.atan2(dy, dx)
            step = math.pi / 4
            snap_ang = round(ang / step) * step
            dist = math.hypot(dx, dy)
            return (lx + dist * math.cos(snap_ang),
                    ly + dist * math.sin(snap_ang),
                    "ortho")
        return None

    def _wire_snap_target(self, ix: float, iy: float):
        """Compute an optional snap target for the wire-drawing cursor.

        Snap priority:
          1. Device centers within `thresh` (highest)
          2. Endpoints of existing wire routes
          3. Waypoints of the wire currently being drawn (close the loop)
          4. Holding Shift: lock direction from previous point to nearest
             45° increment (orthogonal-friendly).

        Returns (sx, sy, kind) — `kind` ∈ {device, endpoint, self, ortho},
        or None if no snap.
        """
        thresh = max(8.0, 14.0 / max(self.zoom, 0.01))
        best = None; best_d = float("inf"); best_kind = None

        # 1) device centers (in image-pixel coords)
        for d in self.devices:
            cx = (d["x"] + d["w"] / 2) * self.dx_scale
            cy = (d["y"] + d["h"] / 2) * self.dy_scale
            dd = math.hypot(ix - cx, iy - cy)
            if dd < thresh and dd < best_d:
                best = (cx, cy); best_d = dd; best_kind = "device"

        # 2) endpoints of existing wire routes
        for r in self.wire_routes:
            pts = r["points"]
            for p in (pts[0], pts[-1]):
                dd = math.hypot(ix - p[0], iy - p[1])
                if dd < thresh and dd < best_d:
                    best = p; best_d = dd; best_kind = "endpoint"

        # 3) waypoints of the in-progress wire (snap to close the loop)
        for p in self.current_wire_points:
            dd = math.hypot(ix - p[0], iy - p[1])
            if dd < thresh and dd < best_d:
                best = p; best_d = dd; best_kind = "self"

        if best is not None:
            return (best[0], best[1], best_kind)

        # 4) Shift-lock: orthogonal-or-45° from the last placed point
        if self._shift_held and self.current_wire_points:
            lx, ly = self.current_wire_points[-1]
            dx, dy = ix - lx, iy - ly
            if dx == 0 and dy == 0:
                return None
            ang = math.atan2(dy, dx)
            step = math.pi / 4   # snap every 45°
            snap_ang = round(ang / step) * step
            dist = math.hypot(dx, dy)
            return (lx + dist * math.cos(snap_ang),
                    ly + dist * math.sin(snap_ang),
                    "ortho")
        return None

    # ============================================================ Undo / redo
    def _execute(self, cmd: Command) -> None:
        cmd.do()
        self.history.append(cmd)
        if len(self.history) > C.HISTORY_LIMIT:
            self.history.pop(0)
        self.future.clear()
        self._mark_dirty()
        self._update_undo_redo_state()

    def _undo(self) -> None:
        if not self.history:
            self._flash_status("Nothing to undo.", ttl_ms=2000)
            return
        cmd = self.history.pop()
        cmd.undo()
        self.future.append(cmd)
        self._mark_dirty()
        self._post_command_refresh()
        self._flash_status(f"Undid: {self._cmd_human_label(cmd)}", ttl_ms=3000)

    def _redo(self) -> None:
        if not self.future:
            self._flash_status("Nothing to redo.", ttl_ms=2000)
            return
        cmd = self.future.pop()
        cmd.do()
        self.history.append(cmd)
        self._mark_dirty()
        self._post_command_refresh()
        self._flash_status(f"Redid: {self._cmd_human_label(cmd)}", ttl_ms=3000)

    def _cmd_human_label(self, cmd: "Command") -> str:
        """Friendly description of an undoable command for status messages."""
        cls = type(cmd).__name__
        labels = {
            "MoveDeviceCmd":      lambda c: f"move device {c.device_id}",
            "MoveDevicesCmd":     lambda c: f"move {len(c.deltas)} devices",
            "AddWireCmd":         lambda c: f"add wire ({len(c.route['points'])} pts)",
            "DeleteWireCmd":      lambda c: f"delete wire",
            "ClearWiresCmd":      lambda c: f"clear all wires",
            "ClearDeviceWiresCmd":lambda c: f"clear wires on {c.device_id}",
            "MoveWaypointCmd":    lambda c: f"reshape wire (waypoint #{c.point_idx})",
            "AddDeviceCmd":       lambda c: f"add device {c.device['id']}",
            "DeleteDeviceCmd":    lambda c: f"delete device {c.device['id']}",
            "EditSymbolCmd":      lambda c: f"edit symbol {c.old_code} → {c.new_code}",
            "DeleteSymbolCmd":    lambda c: f"delete symbol {c.code}",
            "ReassignWireCmd":    lambda c: (
                f"reassign wire {c.old_did or '—'} → {c.new_did or '—'}"),
            "EditFovCmd":         lambda c: (
                f"edit FOV ({len(c.entries)} cameras)"
                if len(c.entries) > 1 else
                f"edit FOV on {c.entries[0][0].get('id', '?')}"),
            "AddAnnotationCmd":   lambda c: f"add annotation '{c.ann.get('text','')[:20]}'",
            "DeleteAnnotationCmd":lambda c: f"delete annotation '{c.ann.get('text','')[:20]}'",
            "EditAnnotationCmd":  lambda c: f"edit annotation",
            "CalibrateCmd":       lambda c: f"calibrate scale",
        }
        try:
            return labels.get(cls, lambda _c: cls)(cmd)
        except Exception:
            return cls

    def _post_command_refresh(self) -> None:
        self._recalc_all_wires()
        self._refresh_symbol_list()
        self._refresh_device_list()
        self._refresh_wire_list()
        self._refresh_bom_tab()
        self._redraw()
        self._update_undo_redo_state()

    def _update_undo_redo_state(self) -> None:
        try:
            self._undo_btn.state(["!disabled"] if self.history else ["disabled"])
            self._redo_btn.state(["!disabled"] if self.future else ["disabled"])
        except (tk.TclError, AttributeError):
            pass

    # ============================================================ Dirty state / title
    def _mark_dirty(self) -> None:
        self._dirty = True
        self._update_title()

    def _mark_clean(self) -> None:
        self._dirty = False
        import time as _t
        self._last_save_time = _t.time()
        self._update_title()
        self._tick_save_indicator()

    def _update_title(self) -> None:
        name = os.path.basename(self.drawio_path)
        star = "*" if self._dirty else ""
        self.root.title(f"Camera Wire Tool — {name}{star}")

    def _on_close(self) -> None:
        if self._dirty and not self._confirm_discard():
            return
        self.root.quit()

    def _confirm_discard(self) -> bool:
        ans = messagebox.askyesnocancel(
            "Unsaved changes",
            "You have unsaved changes. Save before closing?",
        )
        if ans is None:
            return False
        if ans:
            self._save_drawio()
        return True

    # ============================================================ Symbol list
    def _refresh_symbol_list(self) -> None:
        self.sym_listbox.delete(0, tk.END)
        counts: dict[str, int] = {}
        for d in self.devices:
            counts[d["type"]] = counts.get(d["type"], 0) + 1

        self._sym_keys = sorted(self.symbol_types.keys())
        query = (self._sym_search_var.get() if hasattr(self, "_sym_search_var") else "").lower().strip()

        # Group by category so the listbox is scannable: cameras, then
        # other devices (access/intrusion/etc.), then run points.
        groups: dict[str, list[tuple[str, dict]]] = {
            "Cameras":   [],
            "Devices":   [],
            "Run points":[],
        }
        for code in self._sym_keys:
            info = self.symbol_types[code]
            if query and query not in code.lower() and query not in info.get("label", "").lower():
                continue
            if info.get("is_run_point"):
                groups["Run points"].append((code, info))
            elif info.get("is_camera"):
                groups["Cameras"].append((code, info))
            else:
                groups["Devices"].append((code, info))

        for group_name in ("Cameras", "Devices", "Run points"):
            entries = groups[group_name]
            if not entries:
                continue
            header = f"── {group_name}  ({len(entries)}) ──"
            self.sym_listbox.insert(tk.END, header)
            self.sym_listbox.itemconfig(self.sym_listbox.size() - 1,
                                         foreground="#7fc4ff",
                                         selectbackground="#222244",
                                         selectforeground="#7fc4ff")
            for code, info in entries:
                n = counts.get(code, 0)
                if info.get("is_run_point"):
                    tag = "[RP]"
                elif info.get("is_camera"):
                    tag = "[CAM]"
                else:
                    tag = "    "
                cable = info.get("cable_type") or bom_mod.default_cable_for(info)
                self.sym_listbox.insert(
                    tk.END,
                    f' {tag:5s} {code:5s} {info["label"][:28]:28s} {cable:8s} x{n}',
                )

        # update place combobox
        self.place_combo["values"] = self._sym_keys
        if self._sym_keys and not self.place_type_var.get():
            self.place_type_var.set(self._sym_keys[0])

    def _refresh_device_list(self) -> None:
        self.dev_listbox.delete(0, tk.END)
        # Sort by type (alphabetically), then by id within each group.
        # Cameras float to the top, then access/intrusion, then run points.
        def _sort_key(d):
            sym = self.symbol_types.get(d["type"], {})
            cat = (0 if sym.get("is_camera")
                   else 2 if sym.get("is_run_point")
                   else 1)
            # Numeric-aware id sort: s10 < s100 < s11 (string-sort) is bad,
            # so convert "s\d+" to int.
            m = re.match(r"s(\d+)", d["id"])
            n = int(m.group(1)) if m else 0
            return (cat, d["type"], n)
        self.devices.sort(key=_sort_key)

        wire_by_device: dict[str, dict] = {}
        for wr in self.wire_routes:
            did = wr.get("device_id")
            if did:
                wire_by_device.setdefault(did, {"runs": 0, "total_in": 0.0})
                wire_by_device[did]["runs"] += 1
                wire_by_device[did]["total_in"] += (wr.get("length_in") or 0)

        query = (
            self._dev_search_var.get() if hasattr(self, "_dev_search_var") else ""
        ).lower().strip()

        # _filtered_devices stays as a flat list of devices; the listbox
        # rows are a mix of group headers and devices, so we also keep a
        # parallel `_dev_listbox_rows` that maps row-index → device-or-None.
        self._filtered_devices: list[dict] = []
        self._dev_listbox_rows: list[dict | None] = []

        # Group by symbol type
        by_type: dict[str, list[dict]] = {}
        for d in self.devices:
            info = self.symbol_types.get(d["type"], {})
            label = info.get("label", d["type"])
            if query and not (
                query in d["id"].lower()
                or query in d["type"].lower()
                or query in label.lower()
            ):
                continue
            by_type.setdefault(d["type"], []).append(d)
            self._filtered_devices.append(d)

        for t_code in sorted(by_type.keys(),
                              key=lambda c: (
                                  0 if self.symbol_types.get(c, {}).get("is_camera")
                                  else 2 if self.symbol_types.get(c, {}).get("is_run_point")
                                  else 1, c)):
            sym = self.symbol_types.get(t_code, {})
            sym_label = sym.get("label", t_code)
            cat = ("cameras"  if sym.get("is_camera")
                   else "run points" if sym.get("is_run_point")
                   else "devices")
            count = len(by_type[t_code])
            header = f"── {t_code}  {sym_label[:24]:24s}  ({count} {cat}) ──"
            self.dev_listbox.insert(tk.END, header)
            self._dev_listbox_rows.append(None)
            self.dev_listbox.itemconfig(self.dev_listbox.size() - 1,
                                         foreground="#7fc4ff",
                                         selectbackground="#222244",
                                         selectforeground="#7fc4ff")
            for d in by_type[t_code]:
                wire_info = wire_by_device.get(d["id"])
                wire_str = (
                    f'  [{wire_info["runs"]}w: '
                    f'{self._format_length(wire_info["total_in"])}]'
                    if wire_info else ""
                )
                self.dev_listbox.insert(
                    tk.END, f'  {d["id"]:6s} {d["type"]:5s}{wire_str}',
                )
                self._dev_listbox_rows.append(d)

        if not self._filtered_devices:
            placeholder = ("(no devices yet — Symbols tab → pick a type → "
                           "Place on Map)" if not self.devices
                           else "(no matches for current search)")
            self.dev_listbox.insert(tk.END, placeholder)
            self._dev_listbox_rows.append(None)
            self.dev_listbox.itemconfig(0, foreground="#888888")

    # ============================================================ Symbol editor dialog
    def _edit_symbol_type(self) -> None:
        # _selected_symbol_code parses the displayed row format safely
        # (handles the [CAM]/[RP] tag + code + label + cable + count format).
        code = self._selected_symbol_code()
        if not code:
            messagebox.showinfo("Select", "Select a symbol type to edit")
            return
        if code not in self.symbol_types:
            return
        info = self.symbol_types[code]
        self._open_type_editor(code, info, is_new=False)

    def _add_symbol_type(self) -> None:
        # Suggest the next available short code: NEW, NEW2, NEW3...
        suggested = "NEW"
        n = 2
        while suggested in self.symbol_types:
            suggested = f"NEW{n}"
            n += 1
        info = {"label": "New Device", "shape": "ellipse",
                "color": "#FFFF00", "is_camera": False}
        self._open_type_editor(suggested, info, is_new=True)

    def _selected_symbol_code(self) -> str | None:
        """Resolve the symbol code from the current listbox selection.
        Robust against filtering / sort order — reads the code straight
        from the displayed row text."""
        sel = self.sym_listbox.curselection()
        if not sel:
            return None
        text = self.sym_listbox.get(sel[0]).strip()
        # Format: "{tag} {code} {label} {cable} x{n}" — code is 2nd whitespace token
        parts = text.split()
        # First token is the tag (📷 / 🎯 / blank-spaces). Skip 0–1 leading emoji glyphs.
        for tok in parts:
            if tok in self.symbol_types:
                return tok
        return None

    def _place_symbol_from_listbox(self) -> None:
        """Double-click / context-menu entry: place an instance of the
        currently-selected symbol type on the map."""
        code = self._selected_symbol_code()
        if not code:
            return
        self.place_type_var.set(code)
        self._start_place_device()

    def _on_sym_listbox_rclick(self, event) -> None:
        """Right-click on a row in the Symbols list — context menu."""
        idx = self.sym_listbox.nearest(event.y)
        if idx < 0:
            return
        self.sym_listbox.selection_clear(0, tk.END)
        self.sym_listbox.selection_set(idx)
        code = self._selected_symbol_code()
        if not code:
            return
        info = self.symbol_types[code]
        n = sum(1 for d in self.devices if d["type"] == code)

        m = tk.Menu(self.root, tearoff=0)
        m.add_command(label=f"{code} — {info['label']}  (x{n} placed)",
                       state=tk.DISABLED)
        m.add_separator()
        m.add_command(label="📍  Place on map…",
                       command=self._place_symbol_from_listbox)
        m.add_command(label="✏️  Edit symbol…",
                       command=self._edit_symbol_type)
        m.add_separator()
        m.add_command(label="🗑  Delete symbol type…",
                       command=self._delete_symbol_type)
        try:
            m.tk_popup(event.x_root, event.y_root)
        finally:
            m.grab_release()

    def _add_run_point_type(self) -> None:
        """Pre-filled symbol-editor for a new wire-run point (IDF/NVR/switch)."""
        # Suggest a sensible default shortcode that doesn't collide.
        for cand in ("IDF", "NVR", "MDF", "SW", "RP"):
            if cand not in self.symbol_types:
                code = cand
                break
        else:
            n = 1
            while f"RP{n}" in self.symbol_types:
                n += 1
            code = f"RP{n}"
        info = {
            "label": {"IDF": "IDF / Network Closet",
                      "NVR": "Network Video Recorder",
                      "MDF": "Main Distribution Frame",
                      "SW":  "Network Switch",
                      "RP":  "Wire Run Point"}.get(code, "Run Point"),
            "shape": "rect",
            "color": "#FFD060",
            "is_camera": False,
            "is_run_point": True,
            "cable_type": "Cat6",
        }
        self._open_type_editor(code, info, is_new=True)

    def _open_type_editor(self, old_code: str, info: dict, is_new: bool = False) -> None:
        dlg = tk.Toplevel(self.root)
        dlg.title("Edit Symbol Type" if not is_new else "Add Symbol Type")
        # Wider so the 120×120 preview canvas in column 2 fits cleanly next
        # to the wide checkbox-text rows (columnspan=2). Resizable so the
        # user can grow it if their display has bigger system fonts.
        dlg.geometry("680x460")
        dlg.minsize(600, 420)
        dlg.transient(self.root)
        dlg.grab_set()
        dlg.bind("<Escape>", lambda _e: dlg.destroy())
        # Let column 1 (the input column) absorb extra width when resized
        try:
            dlg.grid_columnconfigure(1, weight=1, minsize=180)
        except tk.TclError:
            pass

        pad = {"padx": 10, "pady": 4}
        row = 0

        ttk.Label(dlg, text="Shortcode (shown inside icon):").grid(
            row=row, column=0, sticky=tk.W, **pad)
        # For new symbols, pre-fill with the suggested unique code (NEW,
        # NEW2, ...) so the user has a working default and can just hit
        # Apply if they don't care.
        code_var = tk.StringVar(value=old_code)
        code_entry = ttk.Entry(dlg, textvariable=code_var, width=10)
        code_entry.grid(row=row, column=1, sticky=tk.W, **pad)
        if is_new:
            # Select the placeholder so user can type to replace it
            code_entry.select_range(0, tk.END)
            code_entry.focus_set()
        row += 1

        ttk.Label(dlg, text="Label (legend description):").grid(
            row=row, column=0, sticky=tk.W, **pad)
        label_var = tk.StringVar(value=info["label"])
        ttk.Entry(dlg, textvariable=label_var, width=30).grid(row=row, column=1, sticky=tk.W, **pad)
        row += 1

        ttk.Label(dlg, text="Shape:").grid(row=row, column=0, sticky=tk.W, **pad)
        shape_var = tk.StringVar(value=info["shape"])
        ttk.Combobox(dlg, textvariable=shape_var, values=SHAPE_OPTIONS,
                     state="readonly", width=12).grid(row=row, column=1, sticky=tk.W, **pad)
        row += 1

        ttk.Label(dlg, text="Color:").grid(row=row, column=0, sticky=tk.W, **pad)
        color_frame = ttk.Frame(dlg)
        color_frame.grid(row=row, column=1, sticky=tk.W, **pad)
        color_var = tk.StringVar(value=info["color"])
        color_swatch = tk.Label(color_frame, bg=info["color"], width=4, height=1,
                                 relief=tk.RAISED, cursor="hand2")
        color_swatch.pack(side=tk.LEFT, padx=(0, 6))
        color_hex_label = ttk.Label(color_frame, text=info["color"], cursor="hand2")
        color_hex_label.pack(side=tk.LEFT, padx=(0, 6))

        def pick_color(_event=None):
            try:
                c = colorchooser.askcolor(initialcolor=color_var.get(), title="Pick color")
            except tk.TclError:
                return
            if c is None or c[1] is None:
                return
            hexv = c[1].upper()
            color_var.set(hexv)
            color_swatch.config(bg=hexv)
            color_hex_label.config(text=hexv)

        color_swatch.bind("<Button-1>", pick_color)
        color_hex_label.bind("<Button-1>", pick_color)
        ttk.Button(color_frame, text="Pick…", command=pick_color).pack(side=tk.LEFT)
        row += 1

        cam_var = tk.BooleanVar(value=info["is_camera"])
        ttk.Checkbutton(dlg, text="Is a camera (counts toward camera totals)",
                        variable=cam_var).grid(row=row, column=0, columnspan=2, sticky=tk.W, **pad)
        row += 1

        runpoint_var = tk.BooleanVar(value=bool(info.get("is_run_point", False)))
        rp_check = ttk.Checkbutton(
            dlg, text="Wire-run point  (IDF / NVR / switch — wires terminate here)",
            variable=runpoint_var,
        )
        rp_check.grid(row=row, column=0, columnspan=2, sticky=tk.W, **pad)
        Tooltip(rp_check, "Run points are aggregation targets for cable runs\n"
                          "(IDF closets, NVRs, switches). Visually marked\n"
                          "with a yellow halo + bullseye on the map.")
        row += 1

        # Cable type combobox
        ttk.Label(dlg, text="Default cable:").grid(row=row, column=0, sticky=tk.W, **pad)
        cable_choices = sorted(self.cable_types.keys())
        cable_var = tk.StringVar(
            value=info.get("cable_type") or bom_mod.default_cable_for(info)
        )
        cable_combo = ttk.Combobox(dlg, textvariable=cable_var, width=18,
                                    values=cable_choices, state="readonly")
        cable_combo.grid(row=row, column=1, sticky=tk.W, **pad)
        Tooltip(cable_combo,
                "Cable type used for wires drawn to devices of this symbol.\n"
                "Edit the Cable Types catalog under Tools → Cable Types…")
        row += 1

        if not is_new:
            count = sum(1 for d in self.devices if d["type"] == old_code)
            ttk.Label(dlg, text=f"Instances on map: {count}", foreground="gray").grid(
                row=row, column=0, columnspan=2, sticky=tk.W, **pad)
            row += 1

        # Preview canvas — bigger so the user can actually see what they're
        # creating. Run-point halo also previewed so they can confirm the
        # full visual treatment.
        preview_size = 120
        preview_canvas = tk.Canvas(
            dlg, width=preview_size, height=preview_size,
            bg="white", relief=tk.SUNKEN, highlightthickness=0,
        )
        preview_canvas.grid(row=0, column=2, rowspan=4, padx=12, pady=12)

        def update_preview(*_):
            preview_canvas.delete("all")
            s = shape_var.get()
            c = color_var.get()
            cx = cy = preview_size // 2
            r = 36
            # Run-point halo + bullseye — only when checkbox is on. Read
            # via closure; runpoint_var is bound earlier in the dialog.
            is_rp = False
            try:
                is_rp = bool(runpoint_var.get())
            except Exception:
                pass
            if is_rp:
                halo_r = r * 1.6
                preview_canvas.create_oval(
                    cx - halo_r, cy - halo_r, cx + halo_r, cy + halo_r,
                    outline="#FFD060", dash=(4, 3), width=2,
                )
                inner = max(2, r * 0.25)
                preview_canvas.create_oval(
                    cx - inner, cy - inner, cx + inner, cy + inner,
                    outline="#FFD060", width=1,
                )
            if s == "ellipse":
                preview_canvas.create_oval(cx-r, cy-r, cx+r, cy+r, fill=c,
                                           outline="#0000FF", width=2)
            elif s in ("rect", "roundrect"):
                preview_canvas.create_rectangle(cx-r, cy-r, cx+r, cy+r, fill=c,
                                                outline="#0000FF", width=2)
            elif s == "hexagon":
                pts = []
                for a in range(0, 360, 60):
                    rad = math.radians(a - 30)
                    pts.extend([cx + r*math.cos(rad), cy + r*math.sin(rad)])
                preview_canvas.create_polygon(pts, fill=c, outline="#0000FF", width=2)
            elif s == "halfrect":
                preview_canvas.create_arc(cx-r, cy-r, cx+r, cy+r, start=0, extent=180,
                                          fill=c, outline="#0000FF", width=2)
            elif s == "step":
                pts = [cx-r, cy-r, cx+r*0.4, cy-r, cx+r, cy, cx+r*0.4, cy+r, cx-r, cy+r]
                preview_canvas.create_polygon(pts, fill=c, outline="#0000FF", width=2)
            cv = code_var.get() or "?"
            preview_canvas.create_text(cx, cy, text=cv,
                                        font=("Arial", 14, "bold"))

        shape_var.trace_add("write", update_preview)
        color_var.trace_add("write", update_preview)
        code_var.trace_add("write", update_preview)
        runpoint_var.trace_add("write", update_preview)
        update_preview()

        def apply():
            new_code = code_var.get().strip()
            if not new_code:
                messagebox.showerror("Error", "Shortcode cannot be empty"); return
            if len(new_code) > 5:
                messagebox.showerror("Error", "Shortcode max 5 characters"); return
            if new_code != old_code and new_code in self.symbol_types:
                messagebox.showerror("Error", f'Shortcode "{new_code}" already exists'); return

            new_info = {
                "label": label_var.get().strip() or new_code,
                "shape": shape_var.get(),
                "color": color_var.get().upper(),
                "is_camera": cam_var.get(),
                "is_run_point": runpoint_var.get(),
                "cable_type": cable_var.get(),
            }
            cmd = EditSymbolCmd(self, old_code, info, new_code, new_info, is_new)
            self._execute(cmd)
            self._post_command_refresh()
            dlg.destroy()
            # NEW symbol: nudge user straight into placing one. Most of the
            # time, that's why they created it.
            if is_new:
                if messagebox.askyesno(
                    "Place on map?",
                    f"Symbol '{new_code}' ({new_info['label']}) created.\n\n"
                    f"Click YES to place an instance on the map now, or NO "
                    f"to add it later from the Symbols tab → Place Device."):
                    self.place_type_var.set(new_code)
                    self._start_place_device()

        btns = ttk.Frame(dlg)
        btns.grid(row=row, column=0, columnspan=3, pady=12)
        ttk.Button(btns, text="Apply", command=apply).pack(side=tk.LEFT, padx=4)
        ttk.Button(btns, text="Cancel", command=dlg.destroy).pack(side=tk.LEFT, padx=4)
        dlg.bind("<Return>", lambda _e: apply())
        dlg.bind("<Escape>", lambda _e: dlg.destroy())

    def _delete_symbol_type(self) -> None:
        code = self._selected_symbol_code()
        if not code:
            return
        if code not in self.symbol_types:
            return
        info = self.symbol_types[code]
        removed = [d for d in self.devices if d["type"] == code]
        removed_ids = {d.get("id") for d in removed}
        wires_to_drop = sum(
            1 for w in self.wire_routes if w.get("device_id") in removed_ids
        )
        if removed:
            wire_clause = (f"\nAll {wires_to_drop} associated wire run(s) "
                           f"will also be removed."
                           if wires_to_drop else "")
            if not messagebox.askyesno(
                "Delete Type",
                f'Delete symbol type "{code}"?\n'
                f'This will remove all {len(removed)} instance(s) from the map.'
                f'{wire_clause}\n\nUndo with Ctrl+Z.',
            ):
                return
        self._execute(DeleteSymbolCmd(self, code, info, removed))
        self._post_command_refresh()

    # ============================================================ Place new device
    def _start_place_device(self) -> None:
        code = self.place_type_var.get()
        if not code or code not in self.symbol_types:
            messagebox.showinfo("Select", "Pick a symbol type first")
            return
        self._place_type = code
        self._set_mode(Mode.PLACE_DEVICE)
        self.status_label.config(text=f"Click on map to place a '{code}' device")

    def _do_place_device(self, ix: float, iy: float) -> None:
        code = self._place_type
        if code is None:
            return
        # Special sentinel — the user is placing a map annotation, not a device.
        if code == "__annotation__" and self._placing_annotation:
            dx, dy = self._img_to_drawio(ix, iy)
            ann = {**self._placing_annotation, "x": dx, "y": dy}
            self._execute(AddAnnotationCmd(self, ann))
            self._placing_annotation = None
            self._set_mode(Mode.MOVE)
            self._post_command_refresh()
            self._flash_status(
                f"Placed annotation '{ann['text']}'. Right-click to edit. "
                f"Ctrl+Z to undo.",
                ttl_ms=3500)
            return
        # Centre the icon on the click point (drawio coords). Without this
        # offset the icon's TOP-LEFT corner sits on the cursor, which is
        # confusing.
        dw = C.DEVICE_DEFAULT_W
        dh = C.DEVICE_DEFAULT_H
        dx_click, dy_click = self._img_to_drawio(ix, iy)
        dx = dx_click - dw / 2
        dy = dy_click - dh / 2

        new_id = f"s{self._next_device_id}"
        self._next_device_id += 1
        info = self.symbol_types[code]
        device = {
            "id": new_id, "type": code,
            "x": dx, "y": dy,
            "w": dw, "h": dh,
            "orig_style": SHAPE_STYLES.get(info["shape"], SHAPE_STYLES["ellipse"]).format(
                color=info["color"], fs=8,
            ),
        }
        self._execute(AddDeviceCmd(self, device))
        # Auto-apply default FOV preset for new cameras so the cone is
        # visible immediately — addresses "I don't see how FOV works".
        if info.get("is_camera"):
            try:
                self._apply_default_fov_to_one(device)
                self._show_fov.set(True)
            except Exception:
                pass
        self.selected_device = device   # so it shows red-outlined immediately
        self._set_mode(Mode.MOVE)
        self._post_command_refresh()
        # Flash a 'placed!' marker around it for ~1.2s
        self._flash_placement(dx_click, dy_click, code, new_id)
        cam_hint = "  FOV cone auto-applied." if info.get("is_camera") else ""
        self.status_label.config(
            text=f"✓ Placed {code} ({new_id}).{cam_hint}  Ctrl+Z undoes; "
                 f"right-click for options."
        )

    def _flash_placement(self, dx: float, dy: float, code: str, new_id: str) -> None:
        """Briefly draw an animated ring around a freshly-placed device so
        the user can SEE where it landed, even if they clicked at the edge
        of the viewport."""
        ix, iy = self._drawio_to_img(dx, dy)
        cx, cy = self._img_to_canvas(ix, iy)
        for i, (r, color) in enumerate([(40, "#FFFF00"), (28, "#FFD060"),
                                         (16, "#FF8000")]):
            tag = f"_flash_{new_id}_{i}"
            self.canvas.create_oval(cx - r, cy - r, cx + r, cy + r,
                                    outline=color, width=3, tags=tag)
            self.root.after(400 + i * 200, lambda t=tag: self.canvas.delete(t))

    # ============================================================ Coords
    def _drawio_to_img(self, dx, dy):
        return dx * self.dx_scale, dy * self.dy_scale

    def _img_to_drawio(self, ix, iy):
        return ix / self.dx_scale, iy / self.dy_scale

    def _img_to_canvas(self, ix, iy):
        return ix * self.zoom + self.offset_x, iy * self.zoom + self.offset_y

    def _canvas_to_img(self, cx, cy):
        return (cx - self.offset_x) / self.zoom, (cy - self.offset_y) / self.zoom

    # ============================================================ Rendering
    def _fit_image(self) -> None:
        cw = self.canvas.winfo_width() or 800
        ch = self.canvas.winfo_height() or 600
        zx = cw / self.img_w
        zy = ch / self.img_h
        self.zoom = min(zx, zy) * C.FIT_MARGIN
        self.offset_x = (cw - self.img_w * self.zoom) / 2
        self.offset_y = (ch - self.img_h * self.zoom) / 2

    def _get_viewport_image(self):
        cw = self.canvas.winfo_width() or 800
        ch = self.canvas.winfo_height() or 600
        img_left, img_top = self._canvas_to_img(0, 0)
        img_right, img_bot = self._canvas_to_img(cw, ch)
        src_x0 = max(0, int(math.floor(img_left)))
        src_y0 = max(0, int(math.floor(img_top)))
        src_x1 = min(self.img_w, int(math.ceil(img_right)))
        src_y1 = min(self.img_h, int(math.ceil(img_bot)))
        if src_x1 <= src_x0 or src_y1 <= src_y0:
            return None, 0, 0
        cropped = self.bg_image.crop((src_x0, src_y0, src_x1, src_y1))
        dst_w = max(1, int((src_x1 - src_x0) * self.zoom))
        dst_h = max(1, int((src_y1 - src_y0) * self.zoom))
        if dst_w > C.VIEWPORT_MAX_DIM or dst_h > C.VIEWPORT_MAX_DIM:
            sc = C.VIEWPORT_MAX_DIM / max(dst_w, dst_h)
            dst_w = max(1, int(dst_w * sc))
            dst_h = max(1, int(dst_h * sc))
        # Two-tier resampling: BILINEAR during active interaction (panning,
        # zooming, sash dragging) is ~10× faster than LANCZOS; we snap back
        # to LANCZOS on settle for the final crisp frame.
        resample = Image.BILINEAR if self._interactive else Image.LANCZOS
        resized = cropped.resize((dst_w, dst_h), resample)
        tk_img = ImageTk.PhotoImage(resized)
        place_x, place_y = self._img_to_canvas(src_x0, src_y0)
        return tk_img, place_x, place_y

    def _schedule_redraw(self) -> None:
        # Mark interactive (use fast resample) and schedule a crisp settle.
        self._interactive = True
        if self._settle_id is not None:
            try:
                self.root.after_cancel(self._settle_id)
            except tk.TclError:
                pass
        self._settle_id = self.root.after(180, self._settle_redraw)
        if not self._redraw_pending:
            self._redraw_pending = True
            self.root.after(C.DEBOUNCE_MS, self._do_deferred_redraw)

    def _do_deferred_redraw(self) -> None:
        self._redraw_pending = False
        self._redraw()

    def _settle_redraw(self) -> None:
        """After ~180ms with no further interaction, do one crisp LANCZOS pass."""
        self._settle_id = None
        self._interactive = False
        self._redraw()

    def _on_canvas_configure(self) -> None:
        """Configure handler.

        With opaqueresize=False on the PanedWindow, the sash drag no longer
        fires continuous Configure events — they only land on mouse-release
        and on actual window resize. So we just redraw immediately (BILINEAR
        for speed) and schedule a settle 180ms out for one crisp LANCZOS
        pass. No artifact gap.
        """
        self._interactive = True
        if self._settle_id is not None:
            try:
                self.root.after_cancel(self._settle_id)
            except tk.TclError:
                pass
        self._redraw()
        self._settle_id = self.root.after(180, self._settle_redraw)

    def _redraw(self) -> None:
        self.canvas.delete("all")
        tk_img, px, py = self._get_viewport_image()
        if tk_img is not None:
            self.canvas.create_image(px, py, anchor=tk.NW, image=tk_img, tags="img")
            self._tk_ref = tk_img

        # Optional alignment grid
        if self._show_grid.get():
            self._draw_grid_overlay()

        # FOV cones go BEHIND wires so the wire labels stay readable
        if self._show_fov.get():
            self._draw_fov_cones()

        for i, route in enumerate(self.wire_routes):
            self._draw_wire_route(route, i)

        if self.current_wire_points:
            pts_canvas = [self._img_to_canvas(*p) for p in self.current_wire_points]
            for j in range(len(pts_canvas) - 1):
                self.canvas.create_line(
                    pts_canvas[j][0], pts_canvas[j][1],
                    pts_canvas[j+1][0], pts_canvas[j+1][1],
                    fill="#00BFFF", width=2, dash=(6, 3), tags="wip_wire",
                )
            for cx, cy in pts_canvas:
                self.canvas.create_oval(cx-3, cy-3, cx+3, cy+3,
                                        fill="#00BFFF", outline="white", tags="wip_wire")

        for d in self.devices:
            self._draw_device(d)

        if self._show_legend.get():
            self._draw_legend()

        # Map annotations sit on top of devices/wires so they're never hidden
        if self.annotations:
            self._draw_annotations()

        for pt in self.cal_points:
            cx, cy = self._img_to_canvas(*pt)
            self.canvas.create_oval(cx-5, cy-5, cx+5, cy+5,
                                    fill="yellow", outline="black", width=2, tags="cal")

        self._draw_mode_banner()
        self._draw_welcome_banner()
        self._draw_minimap()
        # Zoom indicator updates with every redraw — cheap, accurate
        if hasattr(self, "zoom_indicator"):
            try:
                self.zoom_indicator.config(text=f"Zoom: {self.zoom*100:.0f}%")
            except tk.TclError:
                pass

    # ============================================================ Welcome banner
    def _draw_welcome_banner(self) -> None:
        """A friendly onboarding card on the canvas — only shows when the
        project hasn't been calibrated yet OR has zero wire routes. Goes
        away as soon as either threshold is crossed."""
        # Active state check: hide if scale is set AND there's at least 1 wire.
        if self.scale_factor and self.wire_routes:
            return
        cw = self.canvas.winfo_width() or 800
        if cw < 460:   # too narrow to fit
            return
        # Banner sits below the mode banner (which is 0..32) at top center
        x0 = max(40, (cw - 540) // 2)
        y0 = 48
        x1 = x0 + 540
        y1 = y0 + 78

        self.canvas.create_rectangle(
            x0, y0, x1, y1,
            fill="#1a1f2e", outline="#7fc4ff", width=2,
            tags=("welcome", "welcome_bg"),
        )
        self.canvas.create_text(
            x0 + 16, y0 + 8, anchor=tk.NW,
            text="Getting started", fill="#7fc4ff",
            font=("Segoe UI", 10, "bold"), tags="welcome",
        )
        # Step indicators with state colors
        steps = [
            ("1.  Set Scale",
             bool(self.scale_factor),
             "Click two points of a known dimension on the plan."),
            ("2.  Draw Wires",
             bool(self.wire_routes),
             "Right-click any device on the map → 'Draw wire from here'."),
        ]
        sx = x0 + 16
        sy = y0 + 30
        for label, done, hint in steps:
            color = "#3ddc84" if done else "#FFB347"
            check = "✓" if done else "○"
            self.canvas.create_text(
                sx, sy, anchor=tk.NW, text=check,
                fill=color, font=("Segoe UI", 11, "bold"),
                tags="welcome",
            )
            self.canvas.create_text(
                sx + 24, sy, anchor=tk.NW, text=label,
                fill="#FFFFFF" if not done else "#7fc4ff",
                font=("Segoe UI", 10, "bold"), tags="welcome",
            )
            self.canvas.create_text(
                sx + 24, sy + 16, anchor=tk.NW, text=hint,
                fill="#a0a8b8", font=("Segoe UI", 8, "italic"),
                tags="welcome",
            )
            sx += 270  # next column

    # ============================================================ Grid overlay
    def _draw_grid_overlay(self) -> None:
        """Faint orthogonal grid for visual alignment when placing or
        moving devices. Spacing is calibrated to the real-world unit so
        every line represents a clean foot/inch (or meter) interval."""
        cw = self.canvas.winfo_width() or 800
        ch = self.canvas.winfo_height() or 600
        # Choose a real-world spacing that yields ~30-90 canvas-pixels
        # apart. If no scale set, fall back to image-pixel grid at 50px.
        if self.scale_factor:
            unit = self.unit_var.get()
            if unit in ("meters", "cm", "m-cm"):
                step_in = self._IN_PER_M  # 1 m
                small_in = self._IN_PER_M / 5  # 20 cm
                label = "1 m"
            else:
                step_in = 12 * 5   # 5 ft
                small_in = 12      # 1 ft
                label = "5'"
            step_px = step_in / self.scale_factor      # image-pixels per major
            small_px = small_in / self.scale_factor
            # If too dense, drop the minor grid
            draw_minor = (small_px * self.zoom) >= 20
        else:
            step_px = 200; small_px = 50
            draw_minor = self.zoom > 0.4
            label = "px"

        # Visible image-pixel range
        ix0, iy0 = self._canvas_to_img(0, 0)
        ix1, iy1 = self._canvas_to_img(cw, ch)

        # Minor grid (very faint)
        if draw_minor:
            x = math.floor(ix0 / small_px) * small_px
            while x < ix1:
                cx, _ = self._img_to_canvas(x, 0)
                self.canvas.create_line(cx, 0, cx, ch,
                                         fill="#222a33", tags="grid")
                x += small_px
            y = math.floor(iy0 / small_px) * small_px
            while y < iy1:
                _, cy = self._img_to_canvas(0, y)
                self.canvas.create_line(0, cy, cw, cy,
                                         fill="#222a33", tags="grid")
                y += small_px

        # Major grid (slightly stronger)
        x = math.floor(ix0 / step_px) * step_px
        while x < ix1:
            cx, _ = self._img_to_canvas(x, 0)
            self.canvas.create_line(cx, 0, cx, ch,
                                     fill="#3a4253", width=1, tags="grid")
            x += step_px
        y = math.floor(iy0 / step_px) * step_px
        while y < iy1:
            _, cy = self._img_to_canvas(0, y)
            self.canvas.create_line(0, cy, cw, cy,
                                     fill="#3a4253", width=1, tags="grid")
            y += step_px

        # Label in the corner so user knows the spacing
        self.canvas.create_text(
            cw - 8, ch - 8, anchor=tk.SE,
            text=f"grid: {label}", fill="#5a6273",
            font=("Segoe UI", 8, "italic"), tags="grid")

    # ============================================================ Mini-map
    def _build_minimap_image(self) -> None:
        """Pre-render the floor plan thumbnail once. Re-runs only on
        explicit invalidation (floor change). Cheap to keep — saved as
        a PhotoImage on the minimap canvas."""
        if self._minimap_built:
            return
        try:
            thumb = self.bg_image.copy()
            thumb.thumbnail((self._minimap_w, self._minimap_h), Image.LANCZOS)
            self._minimap_photo = ImageTk.PhotoImage(thumb)
            self._minimap_thumb_size = thumb.size
            self._minimap_built = True
        except Exception as e:
            log_error("minimap thumbnail failed", e)

    def _draw_minimap(self) -> None:
        """Re-render the mini-map. To avoid expensive rebuilds during pure
        pan/zoom, the dots-and-path layer is cached and only rebuilt when
        the project content changes; the viewport rectangle gets refreshed
        every frame (cheap)."""
        self._build_minimap_image()
        if self._minimap_photo is None:
            return
        m = self.minimap
        thumb_w, thumb_h = self._minimap_thumb_size
        ox = (self._minimap_w - thumb_w) // 2
        oy = (self._minimap_h - thumb_h) // 2

        # Compute a fingerprint of content state so we know when to rebuild.
        sel_ids = frozenset(d["id"] for d in self.selected_devices)
        sel_wire = self._selected_wire()
        sel_wire_id = id(sel_wire) if sel_wire is not None else 0
        token = (
            len(self.devices), len(self.wire_routes),
            sel_ids, sel_wire_id,
            tuple((d["id"], round(d["x"], 1), round(d["y"], 1))
                  for d in self.devices),
        )
        if token != getattr(self, "_minimap_state_token", None):
            # Full rebuild — new state
            m.delete("all")
            m.create_image(ox, oy, anchor=tk.NW, image=self._minimap_photo)
            self._minimap_redraw_dots(ox, oy, thumb_w, thumb_h)
            self._minimap_state_token = token
        else:
            # Just update the viewport rect — devices/wires unchanged.
            m.delete("vp")

        # Always-redrawn: viewport rectangle (cheap; updates on every
        # pan/zoom). Stays inside the "vp" tag so we can wipe it cleanly.
        cw = self.canvas.winfo_width() or 800
        ch = self.canvas.winfo_height() or 600
        ix0, iy0 = self._canvas_to_img(0, 0)
        ix1, iy1 = self._canvas_to_img(cw, ch)
        scale_x = thumb_w / self.img_w
        scale_y = thumb_h / self.img_h
        rx0 = ox + max(0, ix0) * scale_x
        ry0 = oy + max(0, iy0) * scale_y
        rx1 = ox + min(self.img_w, ix1) * scale_x
        ry1 = oy + min(self.img_h, iy1) * scale_y
        m.create_rectangle(rx0, ry0, rx1, ry1,
                            outline="#FFD060", width=2, tags="vp")

    def _minimap_redraw_dots(self, ox: int, oy: int,
                              thumb_w: int, thumb_h: int) -> None:
        """Repopulate device dots + selected-wire path on the mini-map.
        Only called when project state changes (devices added/moved,
        selection changed) — not on every pan/zoom."""
        m = self.minimap
        scale_x = thumb_w / self.img_w
        scale_y = thumb_h / self.img_h

        # Selected wire path overlaid on the mini-map
        sel_wire = self._selected_wire()
        if sel_wire is not None and len(sel_wire["points"]) >= 2:
            poly = []
            for px, py in sel_wire["points"]:
                poly.extend([ox + px * scale_x, oy + py * scale_y])
            m.create_line(poly, fill="#FF4444", width=2, smooth=False)

        # Tiny dots for placed devices. Selected ones bigger + red.
        sel_ids = {d["id"] for d in self.selected_devices}
        for d in self.devices:
            cx = (d["x"] + d["w"]/2) * self.dx_scale
            cy = (d["y"] + d["h"]/2) * self.dy_scale
            mx = ox + cx * scale_x
            my = oy + cy * scale_y
            info = self.symbol_types.get(d["type"], {})
            if d["id"] in sel_ids:
                m.create_oval(mx-3, my-3, mx+3, my+3,
                               fill="#FF3030", outline="#FFFFFF", width=1)
            else:
                color = "#FFE680" if info.get("is_camera") else "#7FC4FF"
                m.create_oval(mx-1.5, my-1.5, mx+1.5, my+1.5,
                               fill=color, outline="")

    def _minimap_click(self, event) -> None:
        """Click anywhere on the mini-map to pan the main view to that
        location."""
        if not self._minimap_built or self._minimap_photo is None:
            return
        thumb_w, thumb_h = self._minimap_thumb_size
        ox = (self._minimap_w - thumb_w) // 2
        oy = (self._minimap_h - thumb_h) // 2
        # Convert click → image coords
        click_x = event.x - ox
        click_y = event.y - oy
        if click_x < 0 or click_y < 0 or click_x > thumb_w or click_y > thumb_h:
            return
        ix = click_x * (self.img_w / thumb_w)
        iy = click_y * (self.img_h / thumb_h)
        cw = self.canvas.winfo_width() or 800
        ch = self.canvas.winfo_height() or 600
        self.offset_x = cw / 2 - ix * self.zoom
        self.offset_y = ch / 2 - iy * self.zoom
        self._redraw()

    def _draw_mode_banner(self) -> None:
        """Big yellow banner across the top of the canvas during action
        modes, so the user can't miss what's expected of their next click."""
        if self.mode == Mode.MOVE:
            return
        cw = self.canvas.winfo_width() or 800
        if cw < 50:
            return
        msg = ""
        bg = "#FFD060"; fg = "#202020"
        if self.mode == Mode.PLACE_DEVICE and self._place_type:
            info = self.symbol_types.get(self._place_type, {})
            label = info.get("label", self._place_type)
            msg = (f"📍  PLACEMENT MODE — click anywhere on the map to drop a "
                   f"'{self._place_type}' ({label}).   Esc to cancel.")
        elif self.mode == Mode.CALIBRATE:
            n = len(self.cal_points)
            msg = (f"📏  CALIBRATE — click point {n+1} of 2 on a known dimension. "
                   f"Esc to cancel.")
            bg = "#FF8C42"
        elif self.mode == Mode.WIRE:
            n = len(self.current_wire_points)
            if self.selected_device is not None:
                msg = (f"📐  WIRE MODE — drawing for {self.selected_device['id']}. "
                       f"Click route points ({n} so far). Right-click to finish, "
                       f"Esc to cancel.")
            else:
                msg = ("📐  WIRE MODE — click points to draw a wire. "
                       "Tip: right-click a device → 'Draw wire from here…' "
                       "to associate the wire with a device. Right-click to "
                       "finish, Esc to cancel.")
            bg = "#7fc4ff"
        if not msg:
            return
        # Banner background bar
        self.canvas.create_rectangle(0, 0, cw, 32, fill=bg, outline="",
                                     tags="mode_banner")
        self.canvas.create_text(cw / 2, 16, text=msg, fill=fg,
                                font=("Segoe UI", 10, "bold"),
                                tags="mode_banner")

    def _draw_legend(self) -> None:
        box = self.legend_box
        bx, by = self._drawio_to_img(box["x"], box["y"])
        bw = box["w"] * self.dx_scale
        sorted_codes = sorted(self.symbol_types.keys())
        n_types = len(sorted_codes)
        needed_h = (n_types * C.LEGEND_ROW_H + 50) * self.dy_scale
        # Cache legend bounds in image coords so _on_left_press can hit-test
        # the click against this rectangle to start a drag.
        self._legend_img_bbox = (bx, by, bx + bw, by + needed_h)
        cx, cy = self._img_to_canvas(bx, by)
        cw = bw * self.zoom
        ch = needed_h * self.zoom

        self.canvas.create_rectangle(cx, cy, cx + cw, cy + ch,
                                     fill="white", outline="#0000FF", width=2, tags="legend")

        title_fs = max(C.LEGEND_TITLE_FONT_MIN,
                       min(C.LEGEND_TITLE_FONT_MAX,
                           int(14 * self.zoom * self.dx_scale)))
        self.canvas.create_text(
            cx + cw / 2, cy + 10 * self.zoom * self.dy_scale,
            text=box["title"], font=("Segoe UI", title_fs, "bold"),
            fill="black", anchor=tk.N, tags="legend",
        )

        icon_fs = max(C.LEGEND_ICON_FONT_MIN, min(C.LEGEND_ICON_FONT_MAX,
                                                  int(8 * self.zoom * self.dx_scale)))
        text_fs = max(C.LEGEND_TEXT_FONT_MIN, min(C.LEGEND_TEXT_FONT_MAX,
                                                  int(11 * self.zoom * self.dx_scale)))

        for i, code in enumerate(sorted_codes):
            info = self.symbol_types[code]
            icon_dx = box["x"] + 20
            icon_dy = box["y"] + 38 + i * C.LEGEND_ROW_H
            ix, iy = self._drawio_to_img(icon_dx, icon_dy)
            iw = 22 * self.dx_scale
            ih = 22 * self.dy_scale
            icx, icy = self._img_to_canvas(ix, iy)
            icw = iw * self.zoom
            ich = ih * self.zoom
            center_x = icx + icw / 2
            center_y = icy + ich / 2
            r = max(icw, ich) / 2

            self._draw_shape(info["shape"], info["color"], center_x, center_y, r,
                             icx, icy, icw, ich, "legend")
            self.canvas.create_text(center_x, center_y, text=code,
                                    font=("Arial", icon_fs, "bold"), fill="black", tags="legend")

            text_dx = box["x"] + 55
            text_dy = box["y"] + 38 + i * C.LEGEND_ROW_H
            tx, ty = self._drawio_to_img(text_dx, text_dy)
            tcx, tcy = self._img_to_canvas(tx, ty)
            count = sum(1 for d in self.devices if d["type"] == code)
            label_text = f'{info["label"]}  ({count})'
            self.canvas.create_text(tcx, tcy + ich / 2, text=label_text,
                                    font=("Segoe UI", text_fs), fill="black",
                                    anchor=tk.W, tags="legend")

    def _draw_shape(self, shape, color, center_x, center_y, r, bx, by, bw, bh, tag,
                    outline="#0000FF", outline_width=1.5):
        if shape == "ellipse":
            self.canvas.create_oval(center_x-r, center_y-r, center_x+r, center_y+r,
                                    fill=color, outline=outline, width=outline_width, tags=tag)
        elif shape in ("rect", "roundrect"):
            self.canvas.create_rectangle(bx, by, bx+bw, by+bh,
                                         fill=color, outline=outline, width=outline_width, tags=tag)
        elif shape == "hexagon":
            pts = []
            for a in range(0, 360, 60):
                rad = math.radians(a - 30)
                pts.extend([center_x + r*math.cos(rad), center_y + r*math.sin(rad)])
            self.canvas.create_polygon(pts, fill=color, outline=outline,
                                       width=outline_width, tags=tag)
        elif shape == "halfrect":
            self.canvas.create_arc(center_x-r, center_y-r, center_x+r, center_y+r,
                                   start=0, extent=180, fill=color, outline=outline,
                                   width=outline_width, tags=tag)
        elif shape == "step":
            pts = [bx, by, bx+bw*0.7, by, bx+bw, by+bh*0.5,
                   bx+bw*0.7, by+bh, bx, by+bh]
            self.canvas.create_polygon(pts, fill=color, outline=outline,
                                       width=outline_width, tags=tag)
        else:
            self.canvas.create_oval(center_x-r, center_y-r, center_x+r, center_y+r,
                                    fill=color, outline=outline, width=outline_width, tags=tag)

    def _draw_device(self, dev) -> None:
        info = self.symbol_types.get(dev["type"])
        if info is None:
            return
        ix, iy = self._drawio_to_img(dev["x"], dev["y"])
        iw, ih = dev["w"] * self.dx_scale, dev["h"] * self.dy_scale
        cx, cy = self._img_to_canvas(ix, iy)
        cw = iw * self.zoom
        ch = ih * self.zoom
        # Viewport culling — skip devices entirely off-screen. Big perf win
        # at high zoom on plans with hundreds of devices.
        canvas_w = self.canvas.winfo_width() or 800
        canvas_h = self.canvas.winfo_height() or 600
        margin = 20
        if (cx + cw < -margin or cy + ch < -margin
                or cx > canvas_w + margin or cy > canvas_h + margin):
            return
        center_x = cx + cw / 2
        center_y = cy + ch / 2
        r = max(cw, ch) / 2

        is_selected = any(d["id"] == dev["id"] for d in self.selected_devices)
        is_run_point = bool(info.get("is_run_point"))
        # Subtle blue ring when this device's wire is the selected wire —
        # makes the wire→device association obvious without forcing a full
        # device-selection.
        sel_wire = self._selected_wire()
        is_wire_owner = (sel_wire is not None
                          and sel_wire.get("device_id") == dev["id"]
                          and not is_selected)
        outline_color = "#FF3030" if is_selected else "#0000FF"
        outline_width = 3 if is_selected else 2
        tag = f'dev_{dev["id"]}'

        if is_wire_owner:
            # Subtle blue halo around the wire's source device
            ix, iy = self._drawio_to_img(dev["x"], dev["y"])
            cx_outer, cy_outer = self._img_to_canvas(ix, iy)
            iw_outer = dev["w"] * self.dx_scale * self.zoom
            ih_outer = dev["h"] * self.dy_scale * self.zoom
            cxc = cx_outer + iw_outer / 2
            cyc = cy_outer + ih_outer / 2
            ro = max(iw_outer, ih_outer) * 0.85
            self.canvas.create_oval(
                cxc - ro, cyc - ro, cxc + ro, cyc + ro,
                outline="#7FCDFF", width=2, dash=(3, 3),
                tags=(tag, "wire_owner_halo"),
            )

        # Run-point halo: dashed yellow ring + bullseye dot.
        if is_run_point:
            halo_r = r * 1.6
            self.canvas.create_oval(
                center_x - halo_r, center_y - halo_r,
                center_x + halo_r, center_y + halo_r,
                outline="#FFD060", dash=(4, 3), width=2, tags=tag,
            )
            inner = max(2, r * 0.25)
            self.canvas.create_oval(
                center_x - inner, center_y - inner,
                center_x + inner, center_y + inner,
                outline="#FFD060", width=1, tags=tag,
            )

        self._draw_shape(info["shape"], info["color"], center_x, center_y, r,
                         cx, cy, cw, ch, tag,
                         outline=outline_color, outline_width=outline_width)

        font_size = max(C.DEVICE_LABEL_FONT_MIN,
                        min(C.DEVICE_LABEL_FONT_MAX, int(8 * self.zoom * self.dx_scale)))
        self.canvas.create_text(center_x, center_y, text=dev["type"],
                                font=("Arial", font_size, "bold"),
                                fill="#000000", tags=tag)

        # Optional device-ID label above icon
        if self._show_device_ids.get():
            id_fs = max(C.DEVICE_ID_FONT_MIN,
                        min(C.DEVICE_ID_FONT_MAX, int(7 * self.zoom * self.dx_scale)))
            self.canvas.create_text(
                center_x, cy - 4, text=dev["id"],
                font=("Consolas", id_fs), fill="#FFFFFF" if not is_selected else "#FF3030",
                anchor=tk.S, tags=tag,
            )

    def _draw_wire_route(self, route, idx) -> None:
        if len(route["points"]) < 2:
            return
        sel = self.wire_listbox.curselection()
        is_selected = bool(sel) and self._wire_index_for_listbox(sel[0]) == idx

        did = route.get("device_id")
        wire_color = "#00BFFF"
        # Priority: explicit cable on the route → device's symbol cable → device color
        cable_id = route.get("cable_type")
        if not cable_id and did:
            dev = next((d for d in self.devices if d["id"] == did), None)
            if dev:
                sym = self.symbol_types.get(dev["type"], {})
                cable_id = sym.get("cable_type") or bom_mod.default_cable_for(sym)
        if cable_id and cable_id in self.cable_types:
            wire_color = self.cable_types[cable_id].get("color", wire_color)
        elif did:
            dev = next((d for d in self.devices if d["id"] == did), None)
            if dev:
                info = self.symbol_types.get(dev["type"], {})
                wire_color = info.get("color", "#00BFFF")

        color = "#FF4444" if is_selected else wire_color
        width = 4 if is_selected else 2
        pts_canvas = [self._img_to_canvas(*p) for p in route["points"]]
        # If the wire's selected, draw a soft dim glow underneath so the
        # red line really pops against busy floor-plan backgrounds.
        if is_selected:
            for j in range(len(pts_canvas) - 1):
                self.canvas.create_line(
                    pts_canvas[j][0], pts_canvas[j][1],
                    pts_canvas[j+1][0], pts_canvas[j+1][1],
                    fill="#FFFFFF", width=8,
                    tags=(f"wire_{idx}", "wire_glow"),
                )
        for j in range(len(pts_canvas) - 1):
            self.canvas.create_line(pts_canvas[j][0], pts_canvas[j][1],
                                    pts_canvas[j+1][0], pts_canvas[j+1][1],
                                    fill=color, width=width, tags=f"wire_{idx}")
        # When a wire is selected ALL its points become big draggable
        # handles. Source endpoint is GREEN, destination is ORANGE,
        # intermediate points are white circles — visual flow direction.
        if is_selected:
            for k, (cx, cy) in enumerate(pts_canvas):
                is_first = (k == 0)
                is_last = (k == len(pts_canvas) - 1)
                if is_first:
                    fill = "#3ddc84"      # green = source
                    outline = "#1a7c3a"
                elif is_last:
                    fill = "#FF8C42"      # orange = destination
                    outline = "#a04a10"
                else:
                    fill = "#FFFFFF"
                    outline = color
                if is_first or is_last:
                    self.canvas.create_rectangle(
                        cx-7, cy-7, cx+7, cy+7,
                        fill=fill, outline=outline, width=2,
                        tags=(f"wire_{idx}", "wp_handle"),
                    )
                else:
                    self.canvas.create_oval(
                        cx-7, cy-7, cx+7, cy+7,
                        fill=fill, outline=outline, width=2,
                        tags=(f"wire_{idx}", "wp_handle"),
                    )
                # Centre dot for the intermediate handles only — endpoint
                # squares already convey direction via fill color.
                if not (is_first or is_last):
                    self.canvas.create_oval(
                        cx-2, cy-2, cx+2, cy+2,
                        fill=color, outline="",
                        tags=(f"wire_{idx}", "wp_handle"),
                    )
        else:
            for cx, cy in pts_canvas:
                self.canvas.create_oval(cx-3, cy-3, cx+3, cy+3, fill=color,
                                        outline="white", tags=f"wire_{idx}")
        # Direction chevron on the selected wire's midpoint segment so
        # the user can see source → destination at a glance.
        if is_selected and len(pts_canvas) >= 2:
            # Pick the segment with the midpoint of the path
            mid = len(pts_canvas) // 2
            # Use the segment ending at `mid` so the chevron sits near
            # the path's center
            seg_a = pts_canvas[mid - 1] if mid > 0 else pts_canvas[0]
            seg_b = pts_canvas[mid] if mid < len(pts_canvas) else pts_canvas[-1]
            sax, say = seg_a
            sbx, sby = seg_b
            mx = (sax + sbx) / 2
            my = (say + sby) / 2
            seg_len = math.hypot(sbx - sax, sby - say)
            if seg_len > 14:
                # Unit vector along seg_a→seg_b
                ux = (sbx - sax) / seg_len
                uy = (sby - say) / seg_len
                # Perpendicular for the chevron arms
                px_, py_ = -uy, ux
                arm = 7
                tip_x = mx + ux * arm
                tip_y = my + uy * arm
                back_x = mx - ux * arm
                back_y = my - uy * arm
                left_x = back_x + px_ * arm * 0.7
                left_y = back_y + py_ * arm * 0.7
                right_x = back_x - px_ * arm * 0.7
                right_y = back_y - py_ * arm * 0.7
                self.canvas.create_polygon(
                    tip_x, tip_y, left_x, left_y, right_x, right_y,
                    fill="#FFFFFF", outline=color, width=1,
                    tags=(f"wire_{idx}", "wire_chevron"),
                )

        # Wire label visibility tiers based on zoom level:
        #   zoom < 0.18    → no labels (would be unreadable noise)
        #   0.18 ≤ z < 0.45 → compact: length only, no device id
        #   zoom ≥ 0.45    → full: "{did}: {length}"
        if self.zoom >= 0.18:
            mid_idx = len(pts_canvas) // 2
            mx, my = pts_canvas[mid_idx]
            length_in = route.get("length_in")
            length_str = (self._format_length(length_in) if length_in is not None
                          else f'{route["length_px"]:.0f}px')
            if self.zoom >= 0.45:
                dev_tag = f"{did}: " if did else ""
                label = f"{dev_tag}{length_str}"
            else:
                label = length_str
            self.canvas.create_text(mx, my - 12, text=label, fill="#00FFFF",
                                    font=("Consolas", 9, "bold"),
                                    tags=f"wire_{idx}")

    def _wire_index_for_listbox(self, list_index: int) -> int | None:
        """Listbox row → wire_routes index. Returns None for header rows."""
        if not hasattr(self, "_wire_listbox_rows"):
            return list_index
        if 0 <= list_index < len(self._wire_listbox_rows):
            return self._wire_listbox_rows[list_index]
        return None

    # ============================================================ Formatting
    # 1 inch = 2.54 cm = 0.0254 m
    _IN_PER_M  = 1.0 / 0.0254     # ≈ 39.3701
    _IN_PER_CM = 1.0 / 2.54

    def _scale_label_text(self) -> str:
        """Render the scale label string in whichever unit is active."""
        if self.scale_factor is None:
            return "⚠ Scale not set — click to calibrate"
        unit = self.unit_var.get()
        sf = self.scale_factor   # inches per pixel
        if unit in ("meters", "cm", "m-cm"):
            cm_per_px = sf / self._IN_PER_CM
            if cm_per_px < 1:
                return f"Scale:  1 px = {cm_per_px*10:.3f} mm"
            return f"Scale:  1 px = {cm_per_px:.3f} cm"
        return f"Scale:  1 px = {sf:.4f}\""

    def _format_length(self, inches) -> str:
        if inches is None:
            return "--"
        unit = self.unit_var.get()
        if unit == "inches":
            return f'{inches:.2f}"'
        if unit == "feet-dec":
            return f"{inches / 12:.2f} ft"
        if unit == "meters":
            return f"{inches / self._IN_PER_M:.3f} m"
        if unit == "cm":
            return f"{inches / self._IN_PER_CM:.1f} cm"
        if unit == "m-cm":
            # e.g. "3 m 82 cm"
            total_cm = inches / self._IN_PER_CM
            m = int(total_cm // 100)
            cm = total_cm - m * 100
            return (f"{m} m {cm:.1f} cm" if m > 0 else f"{cm:.1f} cm")
        # default ft-in
        feet = int(inches // 12)
        rem = inches % 12
        whole_in = int(rem)
        frac = rem - whole_in
        sixteenths = round(frac * 16)
        if sixteenths == 16:
            whole_in += 1
            sixteenths = 0
        if sixteenths == 0:
            frac_str = ""
        else:
            from math import gcd
            g = gcd(sixteenths, 16)
            frac_str = f" {sixteenths // g}/{16 // g}"
        return f"{feet}'-{whole_in}{frac_str}\"" if feet > 0 else f'{whole_in}{frac_str}"'

    # ============================================================ Hit testing
    def _selected_wire(self) -> dict | None:
        """Return the route currently highlighted in the Wires listbox, if any."""
        sel = self.wire_listbox.curselection() if hasattr(self, "wire_listbox") else ()
        if not sel or not hasattr(self, "_filtered_wire_indices"):
            return None
        if sel[0] >= len(self._filtered_wire_indices):
            return None
        idx = self._filtered_wire_indices[sel[0]]
        if 0 <= idx < len(self.wire_routes):
            return self.wire_routes[idx]
        return None

    def _waypoint_at(self, ix: float, iy: float):
        """If the click is near ANY waypoint of the selected wire (start,
        intermediate, or end), return (route, point_index). Hit threshold
        scales inversely with zoom so the handle is grabbable at any
        zoom level."""
        route = self._selected_wire()
        if route is None:
            return None
        pts = route["points"]
        if len(pts) < 2:
            return None
        # 10 canvas-pixel grab radius → image-pixel equivalent
        thresh = max(6.0, 10.0 / max(self.zoom, 0.01))
        for i in range(len(pts)):       # all points draggable
            px, py = pts[i]
            if math.hypot(ix - px, iy - py) <= thresh:
                return (route, i)
        return None

    def _wire_at(self, ix: float, iy: float) -> int | None:
        """Return the wire index whose line passes near (ix, iy). Used to
        click-to-select a wire on the canvas."""
        thresh = max(4.0, 6.0 / max(self.zoom, 0.01))
        best_idx = None
        best_dist = float("inf")
        for idx, route in enumerate(self.wire_routes):
            pts = route["points"]
            for j in range(len(pts) - 1):
                d = self._point_to_segment_dist((ix, iy), pts[j], pts[j+1])
                if d < best_dist and d <= thresh:
                    best_dist = d
                    best_idx = idx
        return best_idx

    def _legend_at(self, ix: float, iy: float) -> bool:
        """Is the (image-coord) point inside the legend rectangle? Used
        to start the legend-drag in _on_left_press."""
        if not self._show_legend.get():
            return False
        if not hasattr(self, "_legend_img_bbox"):
            return False
        bx0, by0, bx1, by1 = self._legend_img_bbox
        return bx0 <= ix <= bx1 and by0 <= iy <= by1

    @staticmethod
    def _point_to_segment_dist(p, a, b) -> float:
        px, py = p; ax, ay = a; bx, by = b
        dx = bx - ax; dy = by - ay
        if dx == 0 and dy == 0:
            return math.hypot(px - ax, py - ay)
        t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx*dx + dy*dy)))
        cx = ax + t * dx
        cy = ay + t * dy
        return math.hypot(px - cx, py - cy)

    def _select_wire_in_listbox(self, route_index: int) -> None:
        """Programmatically select a wire in the listbox so its waypoints
        light up as draggable handles."""
        if not hasattr(self, "_wire_listbox_rows"):
            return
        try:
            # Find the listbox row (not the unfiltered list) where this
            # wire is rendered. _wire_listbox_rows includes None entries
            # for the group headers.
            list_pos = self._wire_listbox_rows.index(route_index)
        except ValueError:
            return
        self.wire_listbox.selection_clear(0, tk.END)
        self.wire_listbox.selection_set(list_pos)
        self.wire_listbox.see(list_pos)

    # ------------------------------------------------------------ Selection
    @property
    def selected_device(self) -> dict | None:
        """The primary (most recently clicked) selected device, or None.

        Backed by `selected_devices`. Setting this attribute REPLACES the
        full multi-selection with a single device (or clears it). To add
        without clearing existing selection, use _toggle_device_selection.
        """
        return self._primary_selected

    @selected_device.setter
    def selected_device(self, dev: dict | None) -> None:
        self._primary_selected = dev
        self.selected_devices = [dev] if dev is not None else []

    def _toggle_device_selection(self, dev: dict) -> None:
        """Ctrl-click style: add the device to the selection set if it's
        not there, otherwise remove it."""
        if any(d is dev for d in self.selected_devices):
            self.selected_devices = [d for d in self.selected_devices if d is not dev]
            if self._primary_selected is dev:
                self._primary_selected = (self.selected_devices[-1]
                                          if self.selected_devices else None)
        else:
            self.selected_devices.append(dev)
            self._primary_selected = dev

    def _device_at(self, ix, iy):
        hit = None
        for d in self.devices:
            dx, dy = self._drawio_to_img(d["x"], d["y"])
            dw, dh = d["w"] * self.dx_scale, d["h"] * self.dy_scale
            margin = max(dw, dh) * C.HIT_TEST_MARGIN_RATIO
            if (dx - margin <= ix <= dx + dw + margin
                    and dy - margin <= iy <= dy + dh + margin):
                info = self.symbol_types.get(d["type"], {})
                if info.get("is_camera"):
                    return d
                hit = d
        return hit

    # ============================================================ Calibration
    def _start_calibration(self) -> None:
        self._set_mode(Mode.CALIBRATE)
        self.cal_points = []
        self.status_label.config(
            text="CALIBRATE: Click two points of a known distance (Esc to cancel)",
        )

    def _finish_calibration(self) -> None:
        p1, p2 = self.cal_points
        px_dist = math.dist(p1, p2)
        if px_dist < 1:
            messagebox.showerror("Error", "Points too close together")
            self.cal_points = []
            self._set_mode(Mode.MOVE)
            return

        dlg = tk.Toplevel(self.root)
        dlg.title("Enter Known Distance")
        dlg.geometry("520x320")
        dlg.transient(self.root)
        dlg.grab_set()
        dlg.bind("<Escape>", lambda _e: dlg.destroy())

        ttk.Label(
            dlg,
            text=f"You measured {px_dist:.1f} pixels on the map.",
            font=("Segoe UI", 11, "bold"),
        ).pack(anchor=tk.W, padx=14, pady=(14, 2))
        ttk.Label(
            dlg,
            text="Enter the real-world distance between those two points:",
            font=("Segoe UI", 10),
        ).pack(anchor=tk.W, padx=14, pady=(2, 8))

        unit = self.unit_var.get()
        metric = unit in ("meters", "cm", "m-cm")

        input_frame = ttk.Frame(dlg)
        input_frame.pack(pady=4)
        a_var = tk.StringVar(value="0")
        b_var = tk.StringVar(value="0")

        if metric:
            ttk.Label(input_frame, text="Meters:").grid(row=0, column=0, padx=4)
            ttk.Entry(input_frame, textvariable=a_var, width=10).grid(row=0, column=1, padx=4)
            ttk.Label(input_frame, text="cm:").grid(row=0, column=2, padx=4)
            ttk.Entry(input_frame, textvariable=b_var, width=10).grid(row=0, column=3, padx=4)
        else:
            ttk.Label(input_frame, text="Feet:").grid(row=0, column=0, padx=4)
            ttk.Entry(input_frame, textvariable=a_var, width=10).grid(row=0, column=1, padx=4)
            ttk.Label(input_frame, text="Inches:").grid(row=0, column=2, padx=4)
            ttk.Entry(input_frame, textvariable=b_var, width=10).grid(row=0, column=3, padx=4)

        def apply():
            try:
                a = float(a_var.get())
                b = float(b_var.get())
            except ValueError:
                messagebox.showerror("Error", "Enter valid numbers")
                return
            if metric:
                total_in = a * self._IN_PER_M + b * self._IN_PER_CM
            else:
                total_in = a * 12 + b
            if total_in <= 0:
                messagebox.showerror("Error", "Distance must be positive")
                return
            new_scale = total_in / px_dist
            self._execute(CalibrateCmd(self, self.scale_factor, new_scale))
            self.scale_label.config(text=self._scale_label_text())
            self._refresh_wire_list()
            self._refresh_device_list()
            self._redraw()
            dlg.destroy()

        def cancel():
            dlg.destroy()

        # Reference card with common distances — helps users pick a
        # reliable thing to measure on their architectural drawings.
        ref = ttk.LabelFrame(dlg, text="Common reference distances")
        ref.pack(fill=tk.X, padx=14, pady=(8, 4))
        if metric:
            tips = [
                ("Standard door width",  "0.91 m  (91 cm)"),
                ("Hallway width",        "1.20 – 1.50 m"),
                ("Standard parking",     "2.50 m wide × 5.00 m long"),
                ("Ceiling height",       "2.50 – 3.00 m"),
            ]
        else:
            tips = [
                ("Standard door width",  "3'-0\"  (36 in)"),
                ("Hallway width",        "4'-0\"  to  5'-0\""),
                ("Standard parking",     "9'-0\" wide × 18'-0\" long"),
                ("Ceiling height",       "8'-0\"  to  10'-0\""),
            ]
        for i, (label_, value) in enumerate(tips):
            ttk.Label(ref, text=label_, font=("Segoe UI", 9),
                      foreground="#a0a8b8",
                      ).grid(row=i, column=0, sticky=tk.W, padx=8, pady=1)
            ttk.Label(ref, text=value, font=("Consolas", 9),
                      foreground="#FFD060",
                      ).grid(row=i, column=1, sticky=tk.W, padx=12, pady=1)

        btns = ttk.Frame(dlg)
        btns.pack(pady=10)
        ttk.Button(btns, text="Apply", command=apply).pack(side=tk.LEFT, padx=4)
        ttk.Button(btns, text="Cancel", command=cancel).pack(side=tk.LEFT, padx=4)
        dlg.bind("<Return>", lambda _e: apply())
        dlg.bind("<Escape>", lambda _e: cancel())

        self.cal_points = []
        self._set_mode(Mode.MOVE)
        self.status_label.config(text="")

    # ============================================================ Wire math
    def _calc_wire_length(self, points) -> float:
        return sum(math.dist(points[i], points[i+1]) for i in range(len(points) - 1))

    def _recalc_all_wires(self) -> None:
        for route in self.wire_routes:
            route["length_px"] = self._calc_wire_length(route["points"])
            route["length_in"] = (
                route["length_px"] * self.scale_factor if self.scale_factor else None
            )

    # ============================================================ Mouse handlers
    def _on_left_press(self, event) -> None:
        if (event.state & 0x4) or self._space_pan:
            self._on_pan_start(event)
            return
        ix, iy = self._canvas_to_img(event.x, event.y)

        # Floor-anchor picking takes priority over normal modes
        if self._floor_anchors_picking is not None:
            self._floor_anchors_picking.append((ix, iy))
            cx, cy = self._img_to_canvas(ix, iy)
            self.canvas.create_oval(cx-6, cy-6, cx+6, cy+6,
                                    fill="#FFC0CB", outline="black", width=2, tags="anchor")
            n = len(self._floor_anchors_picking)
            active, ref = self._floor_anchor_target
            if n < 2:
                self.status_label.config(
                    text=f"ANCHOR: click point #{n+1}/2 on {active.id}")
                return
            # 2 picked on this floor; remember and switch to the reference floor
            active.anchor_points = [list(p) for p in self._floor_anchors_picking]
            self._floor_anchors_picking = None
            messagebox.showinfo(
                "Switch floors",
                f"Anchors stored for {active.id}. Click OK, then on {ref.id} click "
                f"the SAME 2 physical points (in the same order).")
            # Begin picking on the reference floor
            self._floor_anchors_picking = []
            self._floor_anchor_target = (ref, active)
            # Switch to ref floor
            self._switch_floor(ref.id)
            return

        if self.mode == Mode.CALIBRATE:
            self.cal_points.append((ix, iy))
            cx, cy = self._img_to_canvas(ix, iy)
            self.canvas.create_oval(cx-5, cy-5, cx+5, cy+5,
                                    fill="yellow", outline="black", width=2, tags="cal")
            if len(self.cal_points) == 1:
                self.status_label.config(text="CALIBRATE: Click the second point")
            elif len(self.cal_points) == 2:
                self._finish_calibration()
            return

        if self.mode == Mode.PLACE_DEVICE:
            self._do_place_device(ix, iy)
            return

        if self.mode == Mode.MOVE:
            # Multi-device drag start: if click is on one of the currently
            # multi-selected devices, capture all their starting positions
            # so we can move them as a group.
            self._multi_drag_starts: dict[str, tuple[float, float]] = {}

            # -1. FOV rotation handle — highest priority. If the click is
            # on a visible FOV handle, start rotating that camera's cone.
            for did, (hx, hy, ax, ay) in self._fov_handles.items():
                if math.hypot(event.x - hx, event.y - hy) <= 12:
                    dev_obj = next((dd for dd in self.devices
                                    if dd["id"] == did), None)
                    if dev_obj is not None:
                        # Make sure FOV is set on this camera so we can mutate
                        if not dev_obj.get("fov"):
                            dev_obj["fov"] = default_fov_for_type(dev_obj["type"])
                        self._rotating_fov = {
                            "device": dev_obj,
                            "apex_canvas": (ax, ay),
                            "old_az": dev_obj["fov"].get("azimuth_deg", 0),
                        }
                        try:
                            self.canvas.config(cursor="exchange")
                        except tk.TclError:
                            pass
                        return

            # 0. Legend drag — if the click is inside the legend rectangle
            # AND not on a device (devices on top win), start dragging the
            # legend. This lets users move the legend out of the way.
            if (self._legend_at(ix, iy)
                    and self._device_at(ix, iy) is None):
                # Store starting drawio coords of legend + click offset
                self._dragging_legend = {
                    "start_drawio": (self.legend_box["x"], self.legend_box["y"]),
                    "click_drawio": self._img_to_drawio(ix, iy),
                }
                try:
                    self.canvas.config(cursor="fleur")
                except tk.TclError:
                    pass
                return

            # 1. Highest priority: dragging an existing wire's waypoint.
            hit = self._waypoint_at(ix, iy)
            if hit is not None:
                route, pidx = hit
                self._dragging_waypoint = (route, pidx, route["points"][pidx])
                try:
                    self.canvas.config(cursor="fleur")
                except tk.TclError:
                    pass
                return

            # 2. Click on a wire line (not at a device, not on a handle):
            #    select that wire so its waypoints become editable.
            if self._device_at(ix, iy) is None:
                w_idx = self._wire_at(ix, iy)
                if w_idx is not None:
                    route = self.wire_routes[w_idx]
                    self._select_wire_in_listbox(w_idx)
                    # If wire has an associated device, also select that device.
                    did = route.get("device_id")
                    if did:
                        dev = next((d for d in self.devices if d["id"] == did), None)
                        if dev is not None:
                            self.selected_device = dev
                    self._redraw()
                    self.status_label.config(
                        text=f"Wire selected ({len(route['points'])} pts, "
                             f"{self._format_length(route.get('length_in') or 0)}). "
                             "Drag any interior handle to reshape.")
                    return

            dev = self._device_at(ix, iy)
            if dev is not None:
                # If the clicked device is in a multi-selection, drag the
                # whole group as a unit. Otherwise click resets to single.
                clicked_in_multi = (
                    len(self.selected_devices) > 1
                    and any(d["id"] == dev["id"] for d in self.selected_devices)
                )
                if not clicked_in_multi:
                    self.selected_device = dev
                    self._sync_dev_listbox_to_canvas()
                else:
                    # Snapshot every selected device's starting position
                    self._multi_drag_starts = {
                        d["id"]: (d["x"], d["y"]) for d in self.selected_devices
                    }
                self.dragging_device = dev
                self._drag_start_pos = (dev["x"], dev["y"])
                dx, dy = self._drawio_to_img(dev["x"], dev["y"])
                self.drag_offset = (ix - dx, iy - dy)
                dev_wires = [w for w in self.wire_routes if w.get("device_id") == dev["id"]]
                if dev_wires:
                    total_in = sum(w.get("length_in", 0) or 0 for w in dev_wires)
                    self.status_label.config(
                        text=f'Selected {dev["id"]} ({dev["type"]}) — '
                             f'{len(dev_wires)} wire run(s), total: {self._format_length(total_in)}',
                    )
                else:
                    self.status_label.config(
                        text=f'Selected {dev["id"]} ({dev["type"]}) — '
                             f'no wires (switch to Wire Mode to add)',
                    )
                self._redraw()
                return
            # Empty-canvas click in Move mode: arm a potential lasso. We
            # don't clear the selection yet — that happens on release if
            # the user didn't drag (treating it as a real "click on empty"
            # gesture). Shift held = ADD to selection; Ctrl held would
            # otherwise have already entered the pan-or-select branch.
            self._lasso = {
                "start": (event.x, event.y),
                "current": (event.x, event.y),
                "shift": bool(event.state & 0x0001),
                "moved": False,
            }
            return

        if self.mode == Mode.WIRE:
            # First click in Wire mode: if it lands on a device, auto-anchor
            # there (so you don't have to switch to Move first to "select"
            # a source). The wire's device_id will inherit from selected_device.
            if not self.current_wire_points:
                hit_dev = self._device_at(ix, iy)
                if hit_dev is not None:
                    self.selected_device = hit_dev
                    cx_dev, cy_dev = self._drawio_to_img(
                        hit_dev["x"] + hit_dev["w"] / 2,
                        hit_dev["y"] + hit_dev["h"] / 2,
                    )
                    self.current_wire_points.append((cx_dev, cy_dev))
                    self._wire_snap = None
                    self._redraw()
                    info = self.symbol_types.get(hit_dev["type"], {})
                    self.status_label.config(
                        text=f"Wire from {hit_dev['id']} ({info.get('label','?')}) "
                             f"— click route points, right-click to finish.")
                    return
            # Subsequent clicks: snap if motion handler found a target,
            # otherwise place at raw cursor.
            self._shift_held = bool(event.state & 0x0001)
            snap = self._wire_snap_target(ix, iy)
            if snap is not None:
                ix, iy, _ = snap
            self.current_wire_points.append((ix, iy))
            self._wire_snap = None
            self._redraw()
            if len(self.current_wire_points) == 1:
                self.status_label.config(
                    text="Wire: click to add points, right-click to finish. "
                         "Shift = orthogonal lock; cursor snaps to nearby points.",
                )

    def _on_left_drag(self, event) -> None:
        if (event.state & 0x4) or self._space_pan or self.pan_start is not None:
            self._on_pan_move(event); return

        # Lasso (box) selection — drag past threshold from empty-canvas click
        if self._lasso is not None:
            sx, sy = self._lasso["start"]
            if not self._lasso["moved"]:
                if abs(event.x - sx) + abs(event.y - sy) < 5:
                    return
                self._lasso["moved"] = True
            self._lasso["current"] = (event.x, event.y)
            self.canvas.delete("lasso")
            x0, y0 = sx, sy
            x1, y1 = event.x, event.y
            self.canvas.create_rectangle(
                x0, y0, x1, y1,
                outline="#7FCDFF", width=1, dash=(4, 3),
                tags="lasso",
            )
            self.canvas.create_rectangle(
                x0, y0, x1, y1,
                fill="#7FCDFF", outline="",
                stipple="gray12",   # very subtle blue tint
                tags="lasso",
            )
            return

        # FOV rotation: cursor position relative to camera apex sets azimuth
        if self._rotating_fov is not None:
            dev = self._rotating_fov["device"]
            apex_cx, apex_cy = self._rotating_fov["apex_canvas"]
            dx = event.x - apex_cx
            dy = event.y - apex_cy
            if dx == 0 and dy == 0:
                return
            # Angle in our coords: 0° = up (-y), increasing clockwise
            angle_rad = math.atan2(dx, -dy)
            az = (math.degrees(angle_rad) + 360) % 360
            # Snap to 5° increments unless Shift held (free)
            if not (event.state & 0x0001):
                az = round(az / 5) * 5
            dev["fov"]["azimuth_deg"] = az
            self._schedule_redraw()
            return

        # Legend repositioning
        if self._dragging_legend is not None:
            ix, iy = self._canvas_to_img(event.x, event.y)
            cur_drawio = self._img_to_drawio(ix, iy)
            click_drawio = self._dragging_legend["click_drawio"]
            start_x, start_y = self._dragging_legend["start_drawio"]
            self.legend_box["x"] = start_x + (cur_drawio[0] - click_drawio[0])
            self.legend_box["y"] = start_y + (cur_drawio[1] - click_drawio[1])
            self._schedule_redraw()
            return

        if self._dragging_waypoint is not None:
            route, pidx, _old = self._dragging_waypoint
            ix, iy = self._canvas_to_img(event.x, event.y)
            # Snap-to-points / shift-ortho while dragging — reuse the
            # same machinery as wire-drawing.
            self._shift_held = bool(event.state & 0x0001)
            # Temporarily expose previous-and-next waypoint as the
            # "anchors" so shift-orthogonal works against the prior point.
            saved = self.current_wire_points
            try:
                self.current_wire_points = [route["points"][max(0, pidx - 1)]]
                snap = self._wire_snap_target_drag(ix, iy, route, pidx)
            finally:
                self.current_wire_points = saved
            if snap is not None:
                ix, iy, _kind = snap
            pts = list(route["points"])
            pts[pidx] = (ix, iy)
            route["points"] = pts
            route["length_px"] = self._calc_wire_length(pts)
            sf = self.scale_factor
            route["length_in"] = (route["length_px"] * sf) if sf else None

            # Auto-pan when the drag is near the canvas edge so the user
            # can drag a waypoint anywhere without releasing.
            self._maybe_autopan(event)
            self._schedule_redraw()
            return

        if self.dragging_device is not None:
            ix, iy = self._canvas_to_img(event.x, event.y)
            new_ix = ix - self.drag_offset[0]
            new_iy = iy - self.drag_offset[1]
            new_dx, new_dy = self._img_to_drawio(new_ix, new_iy)
            # Snap to alignment grid (when shown). Snap on the device's
            # CENTER, not its top-left, so drops feel correct.
            if self._show_grid.get() and self.scale_factor:
                # Same step as _draw_grid_overlay: 5 ft / 1 m major grid
                unit = self.unit_var.get()
                step_in = (self._IN_PER_M if unit in ("meters", "cm", "m-cm")
                           else 12 * 5)   # 1 m or 5 ft
                step_px = step_in / self.scale_factor      # image pixels per step
                step_d = step_px / self.dx_scale            # drawio units per step
                w_d = self.dragging_device["w"]
                h_d = self.dragging_device["h"]
                center_dx = new_dx + w_d / 2
                center_dy = new_dy + h_d / 2
                # Snap center to nearest grid point
                snap_cx = round(center_dx / step_d) * step_d
                snap_cy = round(center_dy / step_d) * step_d
                new_dx = snap_cx - w_d / 2
                new_dy = snap_cy - h_d / 2
            # If we're in a multi-device drag, move every selected device by
            # the same delta from its starting position.
            if self._multi_drag_starts and self.dragging_device["id"] in self._multi_drag_starts:
                anchor_old = self._multi_drag_starts[self.dragging_device["id"]]
                delta_x = new_dx - anchor_old[0]
                delta_y = new_dy - anchor_old[1]
                ids_to_starts = self._multi_drag_starts
                for d in self.devices:
                    if d["id"] in ids_to_starts:
                        ox, oy = ids_to_starts[d["id"]]
                        d["x"] = ox + delta_x
                        d["y"] = oy + delta_y
            else:
                self.dragging_device["x"] = new_dx
                self.dragging_device["y"] = new_dy
            self._schedule_redraw()

    def _on_left_release(self, event) -> None:
        if self.pan_start is not None:
            self._on_pan_end(event); return

        if self._lasso is not None:
            self.canvas.delete("lasso")
            if self._lasso["moved"]:
                # Real lasso drag — pick every device whose center sits
                # inside the rectangle (image-coord box).
                sx, sy = self._lasso["start"]
                ex, ey = self._lasso["current"]
                ix0, iy0 = self._canvas_to_img(min(sx, ex), min(sy, ey))
                ix1, iy1 = self._canvas_to_img(max(sx, ex), max(sy, ey))
                hits = []
                for d in self.devices:
                    cx_img = (d["x"] + d["w"]/2) * self.dx_scale
                    cy_img = (d["y"] + d["h"]/2) * self.dy_scale
                    if ix0 <= cx_img <= ix1 and iy0 <= cy_img <= iy1:
                        hits.append(d)
                if self._lasso["shift"]:
                    # Shift-drag: ADD to existing selection
                    for d in hits:
                        if not any(s["id"] == d["id"] for s in self.selected_devices):
                            self.selected_devices.append(d)
                else:
                    self.selected_devices = hits
                self._primary_selected = (hits[-1] if hits
                                           else self._primary_selected)
                if hits:
                    self._flash_status(
                        f"Lasso selected {len(hits)} device(s).",
                        ttl_ms=2500)
                else:
                    self._flash_status("Lasso: nothing inside the box.",
                                        ttl_ms=2000)
            else:
                # Plain click on empty canvas — clear selection (legacy
                # behavior preserved).
                self.selected_device = None
                self.dragging_device = None
            self._lasso = None
            self._update_status()
            self._redraw()
            return

        if self._rotating_fov is not None:
            dev = self._rotating_fov["device"]
            old_az = self._rotating_fov["old_az"]
            new_az = dev["fov"].get("azimuth_deg", 0)
            if abs(old_az - new_az) > 0.1:
                self._mark_dirty()
                dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
                compass = dirs[round(new_az / 45) % 8]
                self._flash_status(
                    f"Camera {dev['id']} → {int(new_az)}° ({compass})",
                    ttl_ms=2000,
                )
            self._rotating_fov = None
            self._refresh_mode_ui()  # restore cursor
            self._redraw()
            return

        if self._dragging_legend is not None:
            self._dragging_legend = None
            self._mark_dirty()
            self._refresh_mode_ui()  # restore default cursor
            self._flash_status("Legend repositioned. Save to keep this layout.",
                                ttl_ms=3000)
            self._redraw()
            return

        # Waypoint drag finished — push undoable command if it actually moved
        if self._dragging_waypoint is not None:
            route, pidx, old_xy = self._dragging_waypoint
            new_xy = route["points"][pidx]
            if (abs(old_xy[0] - new_xy[0]) > 0.5
                    or abs(old_xy[1] - new_xy[1]) > 0.5):
                cmd = MoveWaypointCmd(self, route, pidx, old_xy, new_xy)
                self.history.append(cmd)
                if len(self.history) > C.HISTORY_LIMIT:
                    self.history.pop(0)
                self.future.clear()
                self._mark_dirty()
                self._update_undo_redo_state()
                self._refresh_wire_list()
                self._refresh_device_list()
                self._refresh_bom_tab()
                self.status_label.config(
                    text=f"Wire reshaped — new length "
                         f"{self._format_length(route.get('length_in') or 0)}.")
                # Endpoint dropped on a different device → offer reassign.
                # pidx 0 = source endpoint, pidx == last = destination.
                is_endpoint = (pidx == 0 or pidx == len(route["points"]) - 1)
                if is_endpoint:
                    landed_dev = self._device_at(*new_xy)
                    cur_did = route.get("device_id")
                    if (landed_dev is not None
                            and landed_dev["id"] != cur_did
                            and pidx == 0):
                        # source-endpoint moved onto a different device
                        if messagebox.askyesno(
                            "Reassign wire?",
                            f"This wire's source endpoint landed on "
                            f"{landed_dev['id']} ({landed_dev['type']}).\n\n"
                            f"Reassociate the wire from "
                            f"{cur_did or '(unassigned)'} to "
                            f"{landed_dev['id']}?"):
                            self._execute(ReassignWireCmd(
                                self, route, cur_did, landed_dev["id"]))
                            self._post_command_refresh()
                            self._flash_status(
                                f"Wire reassigned to {landed_dev['id']}. "
                                f"Ctrl+Z to undo.")
            self._dragging_waypoint = None
            self._refresh_mode_ui()  # restore cursor
            self._redraw()
            return

        if self.dragging_device is not None:
            # Record undoable command if anything actually moved
            if self._drag_start_pos is not None:
                if self._multi_drag_starts and len(self._multi_drag_starts) > 1:
                    # Batch move: gather (id, old, new) for everything that moved
                    deltas = []
                    for d in self.devices:
                        if d["id"] in self._multi_drag_starts:
                            ox, oy = self._multi_drag_starts[d["id"]]
                            if (abs(ox - d["x"]) > 0.01
                                    or abs(oy - d["y"]) > 0.01):
                                deltas.append((d["id"], (ox, oy),
                                                (d["x"], d["y"])))
                    if deltas:
                        cmd = MoveDevicesCmd(self, deltas)
                        self.history.append(cmd)
                        if len(self.history) > C.HISTORY_LIMIT:
                            self.history.pop(0)
                        self.future.clear()
                        self._mark_dirty()
                        self._update_undo_redo_state()
                        self._flash_status(
                            f"Moved {len(deltas)} devices.", ttl_ms=2500)
                else:
                    old_x, old_y = self._drag_start_pos
                    if (abs(old_x - self.dragging_device["x"]) > 0.01
                            or abs(old_y - self.dragging_device["y"]) > 0.01):
                        cmd = MoveDeviceCmd(
                            self, self.dragging_device,
                            old_x, old_y,
                            self.dragging_device["x"], self.dragging_device["y"],
                        )
                        self.history.append(cmd)
                        if len(self.history) > C.HISTORY_LIMIT:
                            self.history.pop(0)
                        self.future.clear()
                        self._mark_dirty()
                        self._update_undo_redo_state()
            self._drag_start_pos = None
            self.dragging_device = None
            self._multi_drag_starts = {}
            self._redraw()

    def _on_motion(self, event) -> None:
        # Cursor coordinate readout
        ix, iy = self._canvas_to_img(event.x, event.y)
        if self.scale_factor:
            real_in = math.hypot(ix, iy) * self.scale_factor
            self.cursor_label.config(
                text=f"{int(ix):>5d}, {int(iy):>5d} px  •  {self._format_length(real_in)} from origin",
            )
        else:
            self.cursor_label.config(text=f"{int(ix):>5d}, {int(iy):>5d} px")

        # Hover info: show a small popup with device ID + label when the
        # cursor is over a device (Move mode only, otherwise it competes
        # with wire-snap rings or placement cursor).
        # Throttled: at most one update every 33ms (~30fps) to avoid
        # spamming canvas redraws when the user moves the cursor fast.
        if self.mode == Mode.MOVE:
            import time as _t
            now = _t.monotonic()
            last = getattr(self, "_hover_last_ts", 0.0)
            if now - last < 0.033:
                return
            self._hover_last_ts = now
            # Wire-line hover (only when not over a device, since devices win)
            wire_under = None
            dev_under = self._device_at(ix, iy)
            if dev_under is None:
                w_idx = self._wire_at(ix, iy)
                if w_idx is not None:
                    wire_under = self.wire_routes[w_idx]
            if wire_under is not None:
                # Show wire info popup
                self.canvas.delete("hover_info")
                self._hover_device = None
                did = wire_under.get("device_id") or "—"
                cable = wire_under.get("cable_type") or "?"
                length = (self._format_length(wire_under.get("length_in") or 0)
                          if wire_under.get("length_in") is not None
                          else f"{wire_under['length_px']:.0f}px")
                segs = len(wire_under["points"]) - 1
                lines = [f"  Wire → {did}  ",
                         f"  {length}, {segs} segments  ",
                         f"  Cable: {cable}  "]
                cw = self.canvas.winfo_width() or 800
                ch = self.canvas.winfo_height() or 600
                box_w = max(180, 8 * max(len(s) for s in lines))
                box_h = 22 * len(lines)
                px = event.x + 18
                py = event.y + 18
                if px + box_w > cw: px = event.x - 18 - box_w
                if py + box_h > ch: py = event.y - 18 - box_h
                self.canvas.create_rectangle(
                    px, py, px + box_w, py + box_h,
                    fill="#1a2a1a", outline="#80FFC8", width=1,
                    tags="hover_info",
                )
                self.canvas.create_text(
                    px + 4, py + 4, text="\n".join(lines), anchor=tk.NW,
                    fill="#FFFFFF", font=("Segoe UI", 9), tags="hover_info",
                )
                return
            if dev_under is not self._hover_device:
                self._hover_device = dev_under
                self.canvas.delete("hover_info")
                if dev_under is not None:
                    info = self.symbol_types.get(dev_under["type"], {})
                    label = info.get("label", dev_under["type"])
                    n_wires = sum(1 for w in self.wire_routes
                                  if w.get("device_id") == dev_under["id"])
                    msg_lines = [f"  {dev_under['id']} — {dev_under['type']}  ",
                                 f"  {label}  "]
                    if n_wires:
                        total_in = sum((w.get("length_in") or 0)
                                        for w in self.wire_routes
                                        if w.get("device_id") == dev_under["id"])
                        msg_lines.append(
                            f"  {n_wires} wire run(s), "
                            f"{self._format_length(total_in)}  ")
                    if info.get("is_run_point"):
                        msg_lines.append("  [run point]  ")
                    # FOV info if camera has it configured
                    fov = dev_under.get("fov")
                    if fov and info.get("is_camera"):
                        try:
                            cone = fov_mod.compute_fov(fov)
                            d_ft = cone.distances_in.get("D", 0) / 12
                            i_ft = cone.distances_in.get("I", 0) / 12
                            msg_lines.append(
                                f"  FOV: {cone.aov_h_deg:.0f}°  "
                                f"detect {d_ft:.0f}ft  ID {i_ft:.0f}ft  "
                            )
                        except Exception:
                            pass
                    text = "\n".join(msg_lines)
                    # Position the popup just below+right of the cursor
                    # (clamped to canvas so it doesn't fall off the edge)
                    cw = self.canvas.winfo_width() or 800
                    ch = self.canvas.winfo_height() or 600
                    box_w, box_h = 14 * 26, 22 * len(msg_lines)
                    px = event.x + 18
                    py = event.y + 18
                    if px + box_w > cw: px = event.x - 18 - box_w
                    if py + box_h > ch: py = event.y - 18 - box_h
                    self.canvas.create_rectangle(
                        px, py, px + box_w, py + box_h,
                        fill="#1a1a2e", outline="#7fc4ff", width=1,
                        tags="hover_info",
                    )
                    self.canvas.create_text(
                        px + 4, py + 4, text=text, anchor=tk.NW,
                        fill="#FFFFFF", font=("Segoe UI", 9),
                        tags="hover_info",
                    )

        # Calibration rubber-band: after the first cal point is set, draw
        # a yellow preview line from it to the cursor + the running pixel
        # distance, so the user can see what they're about to commit to.
        if self.mode == Mode.CALIBRATE and len(self.cal_points) == 1:
            self.canvas.delete("cal_preview")
            cx0, cy0 = self._img_to_canvas(*self.cal_points[0])
            self.canvas.create_line(cx0, cy0, event.x, event.y,
                                    fill="#FFD060", width=2, dash=(6, 3),
                                    tags="cal_preview")
            ix2, iy2 = self._canvas_to_img(event.x, event.y)
            dist_px = math.hypot(ix2 - self.cal_points[0][0],
                                 iy2 - self.cal_points[0][1])
            mid_x = (cx0 + event.x) / 2
            mid_y = (cy0 + event.y) / 2
            text = f"{dist_px:.0f} px"
            box_w = 8 * len(text) + 12
            self.canvas.create_rectangle(
                mid_x - box_w/2, mid_y - 24,
                mid_x + box_w/2, mid_y - 6,
                fill="#332a08", outline="#FFD060", width=1,
                tags="cal_preview",
            )
            self.canvas.create_text(
                mid_x, mid_y - 15, text=text, fill="#FFD060",
                font=("Segoe UI", 9, "bold"), tags="cal_preview",
            )

        if self.mode == Mode.WIRE and self.current_wire_points:
            # event.state & 0x1 is the Shift modifier — read it live so we
            # don't need keyboard focus for the orthogonal-lock to work.
            self._shift_held = bool(event.state & 0x0001)
            self.canvas.delete("wire_preview")
            self.canvas.delete("wire_snap")
            last = self.current_wire_points[-1]
            ix_raw, iy_raw = self._canvas_to_img(event.x, event.y)
            snap = self._wire_snap_target(ix_raw, iy_raw)
            if snap is not None:
                self._wire_snap = snap
                ix2, iy2, kind = snap
                # Highlight the snap target — distinct color per snap type
                # so the user knows at a glance what they'll lock onto.
                scx, scy = self._img_to_canvas(ix2, iy2)
                ring_color = {
                    "device":   "#FFD060",   # gold — snapping to a device
                    "endpoint": "#7FCDFF",   # sky blue — another wire's end
                    "self":     "#FF80B0",   # pink — close-the-loop on self
                    "ortho":    "#80FFC8",   # green — Shift-lock orthogonal
                }.get(kind, "#FFD060")
                self.canvas.create_oval(scx-9, scy-9, scx+9, scy+9,
                                        outline=ring_color, width=2,
                                        tags="wire_snap")
                self.canvas.create_oval(scx-2, scy-2, scx+2, scy+2,
                                        fill=ring_color, outline="",
                                        tags="wire_snap")
                # Tiny tag label so user knows what snap they're hitting
                kind_label = {
                    "device":   "device",
                    "endpoint": "wire end",
                    "self":     "this wire",
                    "ortho":    "orthogonal",
                }.get(kind, kind)
                self.canvas.create_text(
                    scx + 14, scy - 14, text=kind_label, anchor=tk.W,
                    fill=ring_color, font=("Segoe UI", 8, "italic"),
                    tags="wire_snap",
                )
            else:
                self._wire_snap = None
                ix2, iy2 = ix_raw, iy_raw

            # Preview line goes from last placed point → snapped target
            sx, sy = self._img_to_canvas(*last)
            ex, ey = self._img_to_canvas(ix2, iy2)
            self.canvas.create_line(sx, sy, ex, ey,
                                    fill="#00BFFF", width=2, dash=(4, 4),
                                    tags="wire_preview")
            all_pts = self.current_wire_points + [(ix2, iy2)]
            total_px = self._calc_wire_length(all_pts)
            # Floating "running total" label near the cursor — much more
            # visible than the tiny status-bar text alone.
            running_str = (self._format_length(total_px * self.scale_factor)
                           if self.scale_factor else f"{total_px:.0f} px")
            self.canvas.create_rectangle(
                ex + 14, ey - 22, ex + 14 + 8 * len(running_str) + 6, ey - 4,
                fill="#202060", outline="#7fc4ff", width=1,
                tags="wire_preview",
            )
            self.canvas.create_text(
                ex + 18, ey - 14, text=running_str, anchor=tk.W,
                fill="#FFFFFF", font=("Segoe UI", 10, "bold"),
                tags="wire_preview",
            )
            shift_hint = "  (Shift = lock 0/45/90°)" if not self._shift_held else "  [SHIFT-LOCK]"
            if self.scale_factor:
                total_in = total_px * self.scale_factor
                self.status_label.config(
                    text=f"Wire route: {self._format_length(total_in)}  |  Right-click to finish{shift_hint}",
                )
            else:
                self.status_label.config(
                    text=f"Wire route: {total_px:.0f}px  |  Right-click to finish{shift_hint}",
                )

    def _on_right_click(self, _event) -> None:
        if self.mode == Mode.WIRE and len(self.current_wire_points) >= 2:
            self._finish_wire_route()
        else:
            self._cancel_current()

    def _finish_wire_route(self) -> None:
        if len(self.current_wire_points) < 2:
            self._cancel_current(); return
        px_len = self._calc_wire_length(self.current_wire_points)
        # Sanity: catch accidental fat-finger 2-click that produces a near-
        # zero wire (often happens when right-clicking too quickly).
        too_short = (px_len < 5
                     if not self.scale_factor
                     else (px_len * self.scale_factor) < 1.0)  # < 1 inch real
        if too_short:
            if not messagebox.askyesno(
                "Very short wire",
                f"This wire is only "
                f"{(self._format_length(px_len * self.scale_factor) if self.scale_factor else f'{px_len:.0f}px')}"
                f" — looks like an accidental click.\n\nKeep it anyway?"):
                self._cancel_current()
                return
        real_in = px_len * self.scale_factor if self.scale_factor else None
        device_id = self.selected_device["id"] if self.selected_device else None
        # Pick a cable. Source is the selected device. Try to detect the
        # destination from the last waypoint — if it lands on a device,
        # apply the source-vs-runpoint rule so cabling is consistent with
        # auto-routed wires.
        end_dev = None
        if self.current_wire_points:
            ex, ey = self.current_wire_points[-1]
            end_dev = self._device_at(ex, ey)
        cable = (self._resolve_cable_for_pair(self.selected_device, end_dev)
                 if self.selected_device is not None else None)
        route = {
            "points": list(self.current_wire_points),
            "length_px": px_len,
            "length_in": real_in,
            "device_id": device_id,
        }
        if cable:
            route["cable_type"] = cable
        self._execute(AddWireCmd(self, route))
        self.current_wire_points = []
        self._refresh_wire_list()
        self._refresh_device_list()
        self._redraw()
        dev_label = f" for {device_id}" if device_id else " (unassigned)"
        self.status_label.config(text=f"Wire route saved{dev_label}. Click to start a new one.")

    def _reset_action_state(self) -> None:
        """Pure cleanup: clear in-progress wire / cal / anchor state and
        any preview canvas items. DOES NOT change mode (so it's safe to
        call from _set_mode without recursion)."""
        self.current_wire_points = []
        try:
            self.canvas.delete("wire_preview")
            self.canvas.delete("wip_wire")
            self.canvas.delete("anchor")
            self.canvas.delete("cal_preview")
        except tk.TclError:
            pass
        self.cal_points = []
        self._floor_anchors_picking = None

    def _cancel_current(self) -> None:
        """User-initiated cancel (Esc / right-click). Reset state AND
        return to MOVE if we're currently in an action mode. If already
        in Move mode and nothing's in progress, Esc clears the selection."""
        was_acting = self.mode in (Mode.PLACE_DEVICE, Mode.CALIBRATE,
                                    Mode.WIRE)
        had_action_state = bool(self.current_wire_points or self.cal_points
                                 or self._floor_anchors_picking)
        self._reset_action_state()
        if was_acting:
            self.mode = Mode.MOVE
            self.mode_var.set(self.mode.name)
            self._refresh_mode_ui()
        elif (self.mode == Mode.MOVE and not had_action_state
              and (self.selected_devices or
                   (hasattr(self, "wire_listbox") and self.wire_listbox.curselection()))):
            # Nothing was in-progress — interpret Esc as "clear selection"
            self.selected_device = None
            try:
                self.wire_listbox.selection_clear(0, tk.END)
            except (tk.TclError, AttributeError):
                pass
            self._refresh_wire_list()
            self._redraw()
            self._flash_status("Selection cleared.", ttl_ms=2000)
        self._update_status()

    def _on_mousewheel(self, event) -> None:
        factor = C.ZOOM_FACTOR if event.delta > 0 else 1 / C.ZOOM_FACTOR
        new_zoom = max(C.ZOOM_MIN, min(self.zoom * factor, C.ZOOM_MAX))
        ix, iy = self._canvas_to_img(event.x, event.y)
        self.zoom = new_zoom
        self.offset_x = event.x - ix * self.zoom
        self.offset_y = event.y - iy * self.zoom
        self._clamp_pan()
        self._schedule_redraw()

    def _zoom_button(self, direction: int) -> None:
        cw = self.canvas.winfo_width() or 800
        ch = self.canvas.winfo_height() or 600
        factor = C.ZOOM_FACTOR if direction > 0 else 1 / C.ZOOM_FACTOR
        new_zoom = max(C.ZOOM_MIN, min(self.zoom * factor, C.ZOOM_MAX))
        ix, iy = self._canvas_to_img(cw / 2, ch / 2)
        self.zoom = new_zoom
        self.offset_x = cw / 2 - ix * self.zoom
        self.offset_y = ch / 2 - iy * self.zoom
        self._clamp_pan()
        self._redraw()

    def _on_pan_start(self, event) -> None:
        self.pan_start = (event.x, event.y)

    def _on_pan_move(self, event) -> None:
        if self.pan_start:
            dx = event.x - self.pan_start[0]
            dy = event.y - self.pan_start[1]
            self.offset_x += dx
            self.offset_y += dy
            self.pan_start = (event.x, event.y)
            self._schedule_redraw()

    def _on_pan_end(self, _event) -> None:
        self.pan_start = None
        self._clamp_pan()
        self._redraw()

    def _clamp_pan(self) -> None:
        """Stop the user from panning the floor plan entirely off-screen.
        At least ~10% of the canvas dimension must still show map area."""
        cw = self.canvas.winfo_width() or 800
        ch = self.canvas.winfo_height() or 600
        img_pw = self.img_w * self.zoom    # rendered width in canvas pixels
        img_ph = self.img_h * self.zoom
        margin_x = 0.10 * cw
        margin_y = 0.10 * ch
        # offset_x is the canvas-x where the image's (0,0) lands.
        # We want:  margin_x  ≤  offset_x + img_pw   (right edge ≥ margin_x)
        # And:      offset_x  ≤  cw - margin_x       (left edge ≤ canvas - margin)
        self.offset_x = max(margin_x - img_pw,
                             min(cw - margin_x, self.offset_x))
        self.offset_y = max(margin_y - img_ph,
                             min(ch - margin_y, self.offset_y))

    # ------------------------------------------------------------ Ctrl+click multi-select
    def _on_ctrl_press(self, event):
        """Ctrl+left press — could become a click (toggle selection) or
        a drag (pan). We don't decide yet; just record the start."""
        self._ctrl_press_pos = (event.x, event.y)
        self._ctrl_dragging = False

    def _on_ctrl_motion(self, event):
        if self._ctrl_press_pos is None:
            return
        if not self._ctrl_dragging:
            px, py = self._ctrl_press_pos
            if abs(event.x - px) + abs(event.y - py) > 5:
                self._ctrl_dragging = True
                self.pan_start = self._ctrl_press_pos
        if self._ctrl_dragging and self.pan_start is not None:
            self._on_pan_move(event)

    def _on_ctrl_release(self, event):
        if self._ctrl_dragging:
            self._on_pan_end(event)
        elif self._ctrl_press_pos is not None:
            # Treat as a Ctrl+click → toggle device under cursor in selection
            ix, iy = self._canvas_to_img(event.x, event.y)
            dev = self._device_at(ix, iy)
            if dev is not None:
                self._toggle_device_selection(dev)
                n = len(self.selected_devices)
                self.status_label.config(
                    text=f"{n} device(s) selected. "
                         "Ctrl+click another to add/remove. "
                         "Right-click or Del to delete all."
                )
                self._redraw()
        self._ctrl_press_pos = None
        self._ctrl_dragging = False

    def _on_delete_key(self, _event=None) -> None:
        # Prefer wires if any wire-list rows are selected (supports multi)
        if self.wire_listbox.curselection():
            sels = list(self.wire_listbox.curselection())
            if len(sels) >= 2:
                self._delete_selected_wires_multi(sels)
            else:
                self._delete_selected_wire()
            return
        if self.selected_devices:
            n = len(self.selected_devices)
            label = (f"{n} devices" if n > 1
                     else f"{self.selected_devices[0]['id']} "
                          f"({self.selected_devices[0]['type']})")
            # Count wires that will go with the devices so the prompt
            # accurately reflects the cascade.
            sel_ids = {d["id"] for d in self.selected_devices}
            wires_to_drop = sum(
                1 for w in self.wire_routes if w.get("device_id") in sel_ids
            )
            wire_clause = (f"\n\nThis will also remove {wires_to_drop} "
                           f"associated wire run(s)."
                           if wires_to_drop else "")
            if not messagebox.askyesno(
                "Delete", f"Delete {label}?{wire_clause}\n\nUndo with Ctrl+Z."
            ):
                return
            for dev in list(self.selected_devices):
                try:
                    idx = self.devices.index(dev)
                except ValueError:
                    continue
                self._execute(DeleteDeviceCmd(self, dev, idx))
            self.selected_device = None  # clears multi too
            self._post_command_refresh()
            self._flash_status(
                (f"Deleted {label} + {wires_to_drop} wire(s). Ctrl+Z to undo."
                 if wires_to_drop else
                 f"Deleted {label}. Ctrl+Z to undo."),
                ttl_ms=3000)
            return

    def _delete_selected_wires_multi(self, list_indices) -> None:
        """Bulk-delete every wire whose listbox row is in the selection."""
        if not hasattr(self, "_wire_listbox_rows"):
            return
        routes_to_remove = []
        for li in list_indices:
            ridx = self._wire_index_for_listbox(li)
            if ridx is None:
                continue   # header rows
            if 0 <= ridx < len(self.wire_routes):
                routes_to_remove.append((ridx, self.wire_routes[ridx]))
        if not routes_to_remove:
            return
        if not messagebox.askyesno(
            "Delete", f"Delete {len(routes_to_remove)} wire run(s)?"):
            return
        # Issue DeleteWireCmd in reverse-index order so insert positions
        # stay valid for undo.
        for ridx, route in sorted(routes_to_remove, key=lambda x: -x[0]):
            self._execute(DeleteWireCmd(self, ridx, route))
        self._post_command_refresh()

    def _cycle_device(self, direction: int) -> None:
        if not self.devices:
            return
        cur = (self.devices.index(self.selected_device)
               if self.selected_device in self.devices else -1)
        nxt = (cur + direction) % len(self.devices)
        self.selected_device = self.devices[nxt]
        self._center_on_selected()
        self._redraw()

    def _cycle_wire(self, direction: int) -> None:
        """Shift+[ / Shift+] — pick the previous/next wire in the listbox."""
        if not self.wire_routes:
            return
        sel = self.wire_listbox.curselection()
        # Find the currently selected route index (skip header rows)
        cur_route_idx = None
        if sel and hasattr(self, "_wire_listbox_rows"):
            row_val = self._wire_listbox_rows[sel[0]] if sel[0] < len(self._wire_listbox_rows) else None
            cur_route_idx = row_val
        nxt = (cur_route_idx + direction) if cur_route_idx is not None else 0
        nxt = nxt % len(self.wire_routes)
        self._select_wire_in_listbox(nxt)
        # Center on its midpoint
        r = self.wire_routes[nxt]
        if r["points"]:
            mid = r["points"][len(r["points"]) // 2]
            cw = self.canvas.winfo_width() or 800
            ch = self.canvas.winfo_height() or 600
            self.offset_x = cw / 2 - mid[0] * self.zoom
            self.offset_y = ch / 2 - mid[1] * self.zoom
        self._redraw()
        self._flash_status(f"Wire {nxt+1}/{len(self.wire_routes)} selected.",
                            ttl_ms=2500)

    def _kbd_zoom_to_selection(self, event=None) -> None:
        """Z key — zoom and center the viewport so the current selection
        fills most of the canvas. Works for a selected device or wire."""
        if event is not None and self._is_text_focus(event):
            return
        sel_wire = self._selected_wire()
        if sel_wire is not None:
            xs = [p[0] for p in sel_wire["points"]]
            ys = [p[1] for p in sel_wire["points"]]
            self._zoom_to_image_bbox(min(xs), min(ys), max(xs), max(ys),
                                      pad=80)
            self.status_label.config(text=f"Zoomed to selected wire.")
            return
        if self.selected_devices:
            xs0 = [d["x"] * self.dx_scale for d in self.selected_devices]
            xs1 = [(d["x"] + d["w"]) * self.dx_scale for d in self.selected_devices]
            ys0 = [d["y"] * self.dy_scale for d in self.selected_devices]
            ys1 = [(d["y"] + d["h"]) * self.dy_scale for d in self.selected_devices]
            self._zoom_to_image_bbox(min(xs0), min(ys0), max(xs1), max(ys1),
                                      pad=120)
            n = len(self.selected_devices)
            self.status_label.config(
                text=f"Zoomed to {n} selected device(s).")
            return
        self.status_label.config(
            text="Nothing selected — click a device or wire first, then press Z.")

    def _zoom_to_image_bbox(self, ix0, iy0, ix1, iy1, pad: float = 60) -> None:
        """Set zoom + offset so the given image-coord rectangle fills most
        of the visible canvas, with a margin of `pad` canvas-pixels."""
        cw = self.canvas.winfo_width() or 800
        ch = self.canvas.winfo_height() or 600
        bw = max(1.0, ix1 - ix0)
        bh = max(1.0, iy1 - iy0)
        zx = max(0.0, cw - 2 * pad) / bw
        zy = max(0.0, ch - 2 * pad) / bh
        self.zoom = max(C.ZOOM_MIN, min(C.ZOOM_MAX, min(zx, zy)))
        cx_img = (ix0 + ix1) / 2
        cy_img = (iy0 + iy1) / 2
        self.offset_x = cw / 2 - cx_img * self.zoom
        self.offset_y = ch / 2 - cy_img * self.zoom
        self._redraw()

    def _center_on_selected(self) -> None:
        sel_idx = self.dev_listbox.curselection()
        if (sel_idx and hasattr(self, "_dev_listbox_rows")
                and sel_idx[0] < len(self._dev_listbox_rows)):
            row = self._dev_listbox_rows[sel_idx[0]]
            if row is not None:   # not a group header
                self.selected_device = row
        if self.selected_device is None:
            return
        d = self.selected_device
        ix, iy = self._drawio_to_img(d["x"] + d["w"] / 2, d["y"] + d["h"] / 2)
        cw = self.canvas.winfo_width() or 800
        ch = self.canvas.winfo_height() or 600
        self.offset_x = cw / 2 - ix * self.zoom
        self.offset_y = ch / 2 - iy * self.zoom
        self._redraw()

    # ============================================================ List management
    def _refresh_wire_list(self) -> None:
        self.wire_listbox.delete(0, tk.END)
        query = (
            self._wire_search_var.get() if hasattr(self, "_wire_search_var") else ""
        ).lower().strip()
        # _filtered_wire_indices: actual indices into wire_routes for items
        # that pass the filter. _wire_listbox_rows: parallel list mapping
        # listbox row → wire-routes index OR None (header / placeholder).
        self._filtered_wire_indices: list[int] = []
        self._wire_listbox_rows: list[int | None] = []

        # Group filtered wires by device id (None = unassigned)
        groups: dict[str | None, list[tuple[int, dict]]] = {}
        for i, route in enumerate(self.wire_routes):
            did = route.get("device_id")
            dev = next((d for d in self.devices if d["id"] == did), None) if did else None
            dev_type = dev["type"] if dev else ""
            sym_info = self.symbol_types.get(dev_type, {}) if dev_type else {}
            sym_label = sym_info.get("label", dev_type or "")
            cable = route.get("cable_type", "")
            search_blob = f"{did or ''} {dev_type} {sym_label} {cable}".lower()
            if query and query not in search_blob:
                continue
            groups.setdefault(did, []).append((i, route))

        # Render groups, with None (unassigned) last
        ordered_keys = (
            sorted([k for k in groups if k is not None]) +
            ([None] if None in groups else [])
        )
        for did in ordered_keys:
            entries = groups[did]
            if did is None:
                header = f"── unassigned  ({len(entries)} wire(s)) ──"
            else:
                dev = next((d for d in self.devices if d["id"] == did), None)
                dev_type = dev["type"] if dev else "—"
                sym_label = (self.symbol_types.get(dev_type, {})
                             .get("label", dev_type)) if dev else ""
                tot_in = sum((r.get("length_in") or 0) for _, r in entries)
                tot_str = (self._format_length(tot_in) if tot_in
                           else f"{int(sum(r['length_px'] for _, r in entries))} px")
                header = (f"── {did}  {dev_type:5s}  {sym_label[:24]}"
                          f"  ({len(entries)} wire(s), {tot_str}) ──")
            self.wire_listbox.insert(tk.END, header)
            self._wire_listbox_rows.append(None)
            self.wire_listbox.itemconfig(self.wire_listbox.size() - 1,
                                          foreground="#7fc4ff",
                                          selectbackground="#222244",
                                          selectforeground="#7fc4ff")
            for ri, route in entries:
                self._filtered_wire_indices.append(ri)
                if route["length_in"] is not None:
                    length_str = self._format_length(route["length_in"])
                else:
                    length_str = f'{route["length_px"]:.0f}px'
                n_segs = len(route["points"]) - 1
                cable = route.get("cable_type", "")
                row = (f'   {n_segs}seg  {length_str}'
                       + (f"  ({cable})" if cable else ""))
                self.wire_listbox.insert(tk.END, row)
                self._wire_listbox_rows.append(ri)

        if not self._filtered_wire_indices:
            placeholder = ("(no wires yet — right-click any device on the map → "
                           "Draw wire from here)" if not self.wire_routes
                           else "(no matches for current search)")
            self.wire_listbox.insert(tk.END, placeholder)
            self._wire_listbox_rows.append(None)
            self.wire_listbox.itemconfig(0, foreground="#888888")

        # Side-panel total (Wires tab footer)
        if self.scale_factor:
            grand_total_in = sum((r.get("length_in") or 0) for r in self.wire_routes)
            total_text = f"Total Wire: {self._format_length(grand_total_in)}"
        elif self.wire_routes:
            total_px = sum(r["length_px"] for r in self.wire_routes)
            total_text = f"Total Wire: {total_px:.0f}px (calibrate first)"
        else:
            total_text = "Total Wire: --"
        self.total_label.config(text=total_text)

        # Toolbar metrics: device counts + total wire + total cost.
        n_dev = len(self.devices)
        n_cam = sum(1 for d in self.devices
                    if self.symbol_types.get(d["type"], {}).get("is_camera"))
        n_wire = len(self.wire_routes)
        cur = self._bom_currency_var.get() or "$"
        try:
            report = self._build_bom_report()
            cost_str = f"  •  {cur}{report.grand_total:,.2f}"
        except Exception:
            cost_str = ""
        wire_str = (self._format_length(grand_total_in)
                    if self.scale_factor and self.wire_routes
                    else (f"{int(sum(r['length_px'] for r in self.wire_routes))}px"
                          if self.wire_routes else "—"))
        self.total_toolbar_label.config(
            text=f"Devices: {n_dev}  ·  Cameras: {n_cam}  ·  "
                 f"Wires: {n_wire}  ·  {wire_str}{cost_str}",
        )

    def _on_dev_select(self, _event) -> None:
        sel = self.dev_listbox.curselection()
        if not sel or not hasattr(self, "_dev_listbox_rows"):
            return
        # Header rows map to None in _dev_listbox_rows — skip them so a
        # user clicking a group header doesn't clear their selection.
        chosen = [self._dev_listbox_rows[i] for i in sel
                  if i < len(self._dev_listbox_rows)
                  and self._dev_listbox_rows[i] is not None]
        if not chosen:
            return
        self.selected_devices = chosen
        # Primary = the row that was selected most recently (Tk reports
        # all selected rows; the last one in the iteration is fine for
        # our purposes since user can re-click to set a specific primary).
        self._primary_selected = chosen[-1]
        if self.mode == Mode.WIRE:
            dev = self._primary_selected
            info = self.symbol_types.get(dev["type"], {})
            self.status_label.config(
                text=f'Wire Mode for {dev["id"]} ({dev["type"]} - {info.get("label", "")}) — '
                     f'click to route, right-click to finish',
            )
        elif len(chosen) > 1:
            self.status_label.config(
                text=f"{len(chosen)} devices selected. "
                     "Right-click or press Del to delete all.")
        self._redraw()

    def _on_wire_select(self, _event) -> None:
        self._redraw()

    def _on_unit_change(self) -> None:
        # Update scale-label format
        if hasattr(self, "scale_label"):
            self.scale_label.config(text=self._scale_label_text())
        self._refresh_wire_list()
        self._refresh_device_list()
        self._refresh_bom_tab()
        self._redraw()
        self._mark_dirty()
        self._persist_user_settings()

    def _persist_user_settings(self) -> None:
        """Write current UI prefs (unit, currency, waste, tax) to the
        per-user settings file. Cheap; called whenever any of those vars
        change."""
        try:
            prefs = {
                "unit":          self.unit_var.get(),
                "currency":      self._bom_currency_var.get() or "$",
                "waste_factor":  float(self._bom_waste_var.get()),
                "tax_rate":      float(self._bom_tax_var.get()),
            }
        except (tk.TclError, ValueError):
            return
        save_settings(prefs)

    def _on_wire_listbox_rclick(self, event) -> None:
        """Right-click on a wire-list row → context menu.

        If the row under the cursor isn't already selected, switch to
        single-select on it. Otherwise leave the existing multi-selection
        intact so the user can bulk-delete.
        """
        idx = self.wire_listbox.nearest(event.y)
        if idx < 0:
            return
        # Empty-state placeholder row OR group header — no actions apply.
        if not hasattr(self, "_wire_listbox_rows"):
            return
        if (idx < len(self._wire_listbox_rows)
                and self._wire_listbox_rows[idx] is None):
            # Group header → offer "Select all wires in group"
            self._show_wire_group_menu(event, idx)
            return
        existing_sel = self.wire_listbox.curselection()
        if idx not in existing_sel:
            self.wire_listbox.selection_clear(0, tk.END)
            self.wire_listbox.selection_set(idx)
        self._on_wire_select(None)
        n_selected = len(self.wire_listbox.curselection())
        m = tk.Menu(self.root, tearoff=0)
        if n_selected <= 1:
            m.add_command(label="🔁  Re-route this wire",
                          command=self._reroute_selected_wire)
            m.add_command(label="🔗  Reassign device…",
                          command=self._reassign_selected_wire)
            m.add_separator()
            m.add_command(label="🗑  Delete wire",
                          command=self._delete_selected_wire,
                          accelerator="Del")
        else:
            m.add_command(label=f"({n_selected} wires selected)",
                          state=tk.DISABLED)
            m.add_separator()
            m.add_command(label=f"🗑  Delete {n_selected} selected wires",
                          command=lambda: self._delete_selected_wires_multi(
                              list(self.wire_listbox.curselection())),
                          accelerator="Del")
        try:
            m.tk_popup(event.x_root, event.y_root)
        finally:
            m.grab_release()

    def _reroute_selected_wire(self) -> None:
        """Delete the selected wire and start fresh from its device (or the
        first waypoint if it has no device association)."""
        sel = self.wire_listbox.curselection()
        if not sel:
            messagebox.showinfo("Select", "Select a wire to re-route")
            return
        idx = self._wire_index_for_listbox(sel[0])
        if idx is None:
            return  # header / placeholder row
        route = self.wire_routes[idx]
        did = route.get("device_id")
        device = next((d for d in self.devices if d["id"] == did), None)

        if not messagebox.askyesno(
            "Re-route wire?",
            f"Delete this wire ({len(route['points'])} points, "
            f"{self._format_length(route.get('length_in') or 0)}) "
            "and start a new one in its place?"):
            return
        self._execute(DeleteWireCmd(self, idx, route))
        self._post_command_refresh()
        if device is not None:
            self._draw_wire_from(device)
        else:
            # Wire was unassigned; just enter Wire mode
            self._set_mode(Mode.WIRE)
            self.status_label.config(
                text="Click a device to start, then click route points")

    def _delete_selected_wire(self, _event=None) -> None:
        sel = self.wire_listbox.curselection()
        if not sel:
            return
        idx = self._wire_index_for_listbox(sel[0])
        if idx is None:    # header / placeholder row
            return
        route = self.wire_routes[idx]
        self._execute(DeleteWireCmd(self, idx, route))
        self._post_command_refresh()

    def _reassign_selected_wire(self) -> None:
        sel = self.wire_listbox.curselection()
        if not sel:
            messagebox.showinfo("Select", "Select a wire to reassign")
            return
        idx = self._wire_index_for_listbox(sel[0])
        if idx is None:
            return  # header / placeholder row
        route = self.wire_routes[idx]

        dlg = tk.Toplevel(self.root)
        dlg.title("Reassign wire")
        dlg.geometry("520x520")
        dlg.transient(self.root); dlg.grab_set()
        dlg.bind("<Escape>", lambda _e: dlg.destroy())

        # Header — current state of the wire
        cur_did = route.get("device_id")
        cur_dev = next((d for d in self.devices if d["id"] == cur_did), None)
        cur_label = "(unassigned)"
        if cur_dev:
            sym = self.symbol_types.get(cur_dev["type"], {})
            cur_label = (f"{cur_dev['id']}  ({cur_dev['type']} — "
                         f"{sym.get('label', cur_dev['type'])})")
        ttk.Label(dlg, text=f"Currently assigned to:  {cur_label}",
                  foreground="#7fc4ff", font=("Segoe UI", 9, "italic"),
                  ).pack(anchor=tk.W, padx=12, pady=(10, 2))
        ttk.Label(dlg, text="Pick a new device for this wire:",
                  font=("Segoe UI", 9)).pack(anchor=tk.W, padx=12, pady=(4, 4))

        # Search filter
        search_var = tk.StringVar()
        search_row = ttk.Frame(dlg)
        search_row.pack(fill=tk.X, padx=12, pady=2)
        ttk.Label(search_row, text="Filter:").pack(side=tk.LEFT)
        ttk.Entry(search_row, textvariable=search_var).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=4)

        # List of devices
        list_frame = ttk.Frame(dlg)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=4)
        lb = tk.Listbox(list_frame, font=("Consolas", 10),
                        selectmode=tk.SINGLE, activestyle="dotbox")
        sb = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=lb.yview)
        lb.config(yscrollcommand=sb.set)
        lb.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)

        # Build candidates: (label_for_display, device_id_or_None)
        candidates: list[tuple[str, str | None]] = [
            ("(unassigned — no device)", None)
        ]
        for d in sorted(self.devices, key=lambda x: x["id"]):
            sym = self.symbol_types.get(d["type"], {})
            tag = "[CAM]" if sym.get("is_camera") else (
                "[RP] " if sym.get("is_run_point") else "     ")
            candidates.append((
                f' {tag}  {d["id"]:6s}  {d["type"]:5s}  '
                f'{sym.get("label", d["type"])[:34]}',
                d["id"],
            ))

        def populate(query: str = "") -> None:
            lb.delete(0, tk.END)
            q = query.lower().strip()
            shown = []
            for disp, did in candidates:
                if not q or q in disp.lower() or (did and q in did.lower()):
                    lb.insert(tk.END, disp)
                    shown.append((disp, did))
            # Pre-select the current row
            for i, (_, did) in enumerate(shown):
                if did == cur_did:
                    lb.selection_set(i); lb.see(i); break
            return shown

        shown = populate()
        search_var.trace_add("write", lambda *_: shown.__init__(populate(search_var.get())))

        def apply():
            sel = lb.curselection()
            if not sel:
                return
            visible = []
            q = search_var.get().lower().strip()
            for disp, did in candidates:
                if not q or q in disp.lower() or (did and q in did.lower()):
                    visible.append((disp, did))
            _, new_did = visible[sel[0]]
            if new_did == cur_did:
                dlg.destroy(); return
            self._execute(ReassignWireCmd(self, route, cur_did, new_did))
            self._post_command_refresh()
            dlg.destroy()

        btns = ttk.Frame(dlg); btns.pack(fill=tk.X, padx=12, pady=10)
        ttk.Button(btns, text="Apply", command=apply).pack(side=tk.LEFT, padx=4)
        ttk.Button(btns, text="Cancel", command=dlg.destroy).pack(side=tk.LEFT, padx=4)
        lb.bind("<Double-Button-1>", lambda _e: apply())
        dlg.bind("<Return>", lambda _e: apply())

    def _clear_wires(self) -> None:
        if not self.wire_routes:
            return
        if not messagebox.askyesno("Confirm", "Clear all wire routes?"):
            return
        self._execute(ClearWiresCmd(self, self.wire_routes))
        self._post_command_refresh()

    # ============================================================ Status
    def _flash_status(self, text: str, ttl_ms: int = 4000) -> None:
        """Show a transient status-bar message that auto-clears after ttl_ms.
        Subsequent calls (or a mode change) reset the timer."""
        self.status_label.config(text=text)
        if getattr(self, "_status_clear_id", None) is not None:
            try:
                self.root.after_cancel(self._status_clear_id)
            except tk.TclError:
                pass
        def _restore():
            self._status_clear_id = None
            self._update_status()  # back to mode/selection breadcrumb
        self._status_clear_id = self.root.after(ttl_ms, _restore)

    def _update_status(self) -> None:
        # Default to Move so an unhandled mode never raises KeyError (which
        # used to surface as a nonsense "obstacle grid" error from the
        # routing exception handler).
        mode_str = {
            Mode.MOVE: "Move",
            Mode.WIRE: "Wire",
            Mode.CALIBRATE: "Calibrate",
            Mode.PLACE_DEVICE: f"Place {self._place_type or '?'}",
        }.get(self.mode, "Move")

        parts = [f"Mode: {mode_str}"]
        # Floor (only show when multi-floor)
        if self._project is not None and len(self._project.floors) > 1:
            parts.append(f"Floor: {self._project.active_floor}")

        # Selection summary OR overall project counts (when nothing selected)
        n_sel = len(self.selected_devices)
        if n_sel == 1:
            d = self.selected_devices[0]
            info = self.symbol_types.get(d["type"], {})
            sel_str = f"Selected: {d['id']} ({d['type']} – {info.get('label','?')})"
            wires = [w for w in self.wire_routes if w.get("device_id") == d["id"]]
            if wires:
                tot = sum(w.get("length_in", 0) or 0 for w in wires)
                sel_str += f", {len(wires)} run(s), {self._format_length(tot)}"
            parts.append(sel_str)
        elif n_sel > 1:
            parts.append(f"Selected: {n_sel} devices")
        else:
            # No selection — show useful project-wide counts as the
            # always-visible default. Quiet, info-dense.
            n_dev = len(self.devices)
            n_cam = sum(1 for d in self.devices
                        if self.symbol_types.get(d["type"], {}).get("is_camera"))
            n_other = n_dev - n_cam
            n_wire = len(self.wire_routes)
            parts.append(
                f"{n_dev} device(s) ({n_cam} cameras, {n_other} other)  •  "
                f"{n_wire} wire run(s)"
            )

        self.status_label.config(text="  •  ".join(parts))

    # ============================================================ File operations
    def _file_open(self) -> None:
        if self._dirty and not self._confirm_discard():
            return
        path = filedialog.askopenfilename(
            title="Open .drawio file",
            initialdir=os.path.dirname(self.drawio_path),
            filetypes=[("Draw.io files", "*.drawio")])
        if not path:
            return
        self.root.destroy()
        new_root = tk.Tk()
        apply_theme(new_root)
        new_root.title("Camera Wire Estimation Tool")
        CameraWireTool(new_root, path)
        new_root.mainloop()

    def _file_save_as(self) -> None:
        path = filedialog.asksaveasfilename(
            title="Save .drawio As",
            initialdir=os.path.dirname(self.drawio_path),
            initialfile=os.path.basename(self.drawio_path),
            defaultextension=".drawio",
            filetypes=[("Draw.io files", "*.drawio")])
        if not path:
            return
        self.drawio_path = path
        self.project_path = os.path.splitext(path)[0] + ".wiremap.json"
        self._update_title()
        self._save_drawio()

    def _save_drawio(self) -> None:
        """Write all changes back to .drawio + companion .wiremap.json."""
        try:
            tree = ET.parse(self.drawio_path)
        except (ET.ParseError, OSError) as e:
            log_error(f"Save failed re-parsing {self.drawio_path}", e)
            messagebox.showerror("Save failed",
                                 f"Could not re-read source .drawio:\n{e}\n\n"
                                 f"The companion project file was NOT written either.")
            return

        try:
            xml_root = tree.getroot()
            root_elem = xml_root.find(".//root")
            if root_elem is None:
                root_elem = xml_root.find(".//mxGraphModel/root")
            if root_elem is None:
                raise RuntimeError("Could not find <root> in XML")

            xml_cells = root_elem.findall("mxCell")
            existing_ids = {cell.get("id", "") for cell in xml_cells}
            dev_map = {d["id"]: d for d in self.devices}
            cells_to_remove = []

            for cell in xml_cells:
                cid = cell.get("id", "")
                parent = cell.get("parent", "")
                if cid in dev_map:
                    dev = dev_map[cid]
                    info = self.symbol_types.get(dev["type"], {})
                    cell.set("value", dev["type"])
                    fs = 7 if len(dev["type"]) >= 3 else 8
                    new_style = SHAPE_STYLES.get(
                        info.get("shape", "ellipse"), SHAPE_STYLES["ellipse"]
                    ).format(color=info.get("color", "#FFFF00"), fs=fs)
                    cell.set("style", new_style)
                    geom = cell.find("mxGeometry")
                    if geom is not None:
                        geom.set("x", f"{dev['x']:.1f}")
                        geom.set("y", f"{dev['y']:.1f}")
                if parent == "legbox" or cid == "legbox":
                    cells_to_remove.append(cell)

            for cell in cells_to_remove:
                root_elem.remove(cell)

            # Add new device cells
            for dev in self.devices:
                if dev["id"] not in existing_ids:
                    info = self.symbol_types.get(dev["type"], {})
                    fs = 7 if len(dev["type"]) >= 3 else 8
                    style = SHAPE_STYLES.get(
                        info.get("shape", "ellipse"), SHAPE_STYLES["ellipse"]
                    ).format(color=info.get("color", "#FFFF00"), fs=fs)
                    new_cell = ET.SubElement(root_elem, "mxCell")
                    new_cell.set("id", dev["id"])
                    new_cell.set("value", dev["type"])
                    new_cell.set("style", style)
                    new_cell.set("vertex", "1")
                    new_cell.set("parent", "1")
                    geom = ET.SubElement(new_cell, "mxGeometry")
                    geom.set("x", f"{dev['x']:.1f}")
                    geom.set("y", f"{dev['y']:.1f}")
                    geom.set("width", f"{dev['w']:.0f}")
                    geom.set("height", f"{dev['h']:.0f}")
                    geom.set("as", "geometry")

            # Remove deleted device cells
            current_ids = {d["id"] for d in self.devices}
            for cell in list(root_elem.findall("mxCell")):
                cid = cell.get("id", "")
                if cid.startswith("s") and cid not in current_ids and cid not in ("0", "1"):
                    val = cell.get("value", "")
                    if val and len(val) <= 6:
                        root_elem.remove(cell)

            # Regenerate legend
            box = self.legend_box
            sorted_codes = sorted(self.symbol_types.keys())
            row_h = C.LEGEND_ROW_H
            total_h = len(sorted_codes) * row_h + 50

            legbox = ET.SubElement(root_elem, "mxCell")
            legbox.set("id", "legbox")
            legbox.set("value", box["title"])
            legbox.set("style",
                       "rounded=0;html=1;fillColor=#FFFFFF;strokeColor=#0000FF;"
                       "strokeWidth=2;fontSize=14;fontStyle=1;verticalAlign=top;align=center")
            legbox.set("vertex", "1")
            legbox.set("parent", "1")
            geom = ET.SubElement(legbox, "mxGeometry")
            geom.set("x", f"{box['x']:.0f}")
            geom.set("y", f"{box['y']:.0f}")
            geom.set("width", f"{box['w']:.0f}")
            geom.set("height", f"{total_h}")
            geom.set("as", "geometry")

            for i, code in enumerate(sorted_codes):
                info = self.symbol_types[code]
                y_offset = 38 + i * row_h
                fs = 7 if len(code) >= 3 else 8
                icon_style = SHAPE_STYLES.get(info["shape"], SHAPE_STYLES["ellipse"]).format(
                    color=info["color"], fs=fs)
                icon_cell = ET.SubElement(root_elem, "mxCell")
                icon_cell.set("id", f"legi{i}")
                icon_cell.set("value", code)
                icon_cell.set("style", icon_style)
                icon_cell.set("vertex", "1")
                icon_cell.set("parent", "legbox")
                ig = ET.SubElement(icon_cell, "mxGeometry")
                ig.set("x", "20")
                ig.set("y", str(y_offset))
                ig.set("width", "22")
                ig.set("height", "22")
                ig.set("as", "geometry")

                text_cell = ET.SubElement(root_elem, "mxCell")
                text_cell.set("id", f"legt{i}")
                text_cell.set("value", info["label"])
                text_cell.set("style",
                              "text;html=1;align=left;verticalAlign=middle;"
                              "fontSize=11;fontStyle=0")
                text_cell.set("vertex", "1")
                text_cell.set("parent", "legbox")
                tg = ET.SubElement(text_cell, "mxGeometry")
                tg.set("x", "55")
                tg.set("y", str(y_offset - 2))
                tg.set("width", "275")
                tg.set("height", "26")
                tg.set("as", "geometry")

            tree.write(self.drawio_path, xml_declaration=True, encoding="utf-8")
            self._save_project()
        except Exception as e:
            log_error(f"Failed to write {self.drawio_path}", e)
            messagebox.showerror("Save failed", f"Could not write file:\n{e}")
            return

        self._mark_clean()
        self._cleanup_autosave()    # autosave is no longer needed
        push_recent_file(self.drawio_path)
        self._refresh_recent_menu()
        # Quiet flash instead of a modal popup — the title bar's `*` going
        # away and the "Saved just now" indicator already convey success.
        self._flash_status(
            f"✓ Saved {os.path.basename(self.drawio_path)} + "
            f"{os.path.basename(self.project_path)}",
            ttl_ms=3500,
        )

    # ============================================================ Quick-jump (Ctrl+G / Ctrl+P)
    def _open_quick_jump(self) -> None:
        """Searchable quick-jump palette. Type to filter every device,
        symbol type, and wire run. Enter selects it (centers the map +
        sets the right selection), Esc cancels."""
        dlg = tk.Toplevel(self.root)
        dlg.title("Quick jump")
        dlg.geometry("520x420")
        dlg.transient(self.root); dlg.grab_set()
        dlg.bind("<Escape>", lambda _e: dlg.destroy())

        ttk.Label(dlg, text="Type to find any device, symbol, or wire:",
                  font=("Segoe UI", 9)).pack(anchor=tk.W, padx=10, pady=(8, 2))
        q_var = tk.StringVar()
        entry = ttk.Entry(dlg, textvariable=q_var, font=("Segoe UI", 11))
        entry.pack(fill=tk.X, padx=10, pady=4)
        entry.focus_set()

        listbox = tk.Listbox(dlg, font=("Consolas", 10), activestyle="dotbox")
        sb = ttk.Scrollbar(dlg, orient=tk.VERTICAL, command=listbox.yview)
        listbox.config(yscrollcommand=sb.set)
        listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(10, 0), pady=4)
        sb.pack(side=tk.LEFT, fill=tk.Y, pady=4)

        # Build the candidate list — devices + symbol types + wires
        candidates: list[tuple[str, str, callable]] = []
        for d in self.devices:
            info = self.symbol_types.get(d["type"], {})
            label = (f" DEV   {d['id']:6s}  {d['type']:5s}  "
                     f"{info.get('label', d['type'])}")
            candidates.append((label, f"{d['id']} {d['type']} {info.get('label','')}",
                               lambda d=d: self._jump_to_device(d)))
        for code, info in sorted(self.symbol_types.items()):
            n = sum(1 for d in self.devices if d["type"] == code)
            tag = "[RP]" if info.get("is_run_point") else (
                  "[CAM]" if info.get("is_camera") else "    ")
            label = f" {tag:5s}  {code:5s}  symbol: {info.get('label', code)} (x{n})"
            candidates.append((label, f"{code} {info.get('label','')} symbol",
                               lambda c=code: self._jump_to_symbol(c)))
        for i, r in enumerate(self.wire_routes):
            did = r.get("device_id") or "—"
            length = self._format_length(r.get("length_in") or 0) if r.get("length_in") else f"{r['length_px']:.0f}px"
            label = f" WIRE  wire #{i+1}  to {did:6s}  {length}"
            candidates.append((label, f"wire {did} {length}",
                               lambda i=i: self._jump_to_wire(i)))

        def populate(query: str = "") -> None:
            listbox.delete(0, tk.END)
            q = query.lower().strip()
            for visible_label, search_text, _ in candidates:
                if not q or all(tok in search_text.lower() for tok in q.split()):
                    listbox.insert(tk.END, visible_label)
            if listbox.size():
                listbox.selection_set(0)
        populate()

        # Filter list as the user types
        q_var.trace_add("write", lambda *_: populate(q_var.get()))

        def filtered_candidates(query: str):
            q = query.lower().strip()
            return [c for c in candidates
                    if not q or all(tok in c[1].lower() for tok in q.split())]

        def commit():
            sel = listbox.curselection()
            if not sel:
                return
            visible = filtered_candidates(q_var.get())
            if sel[0] >= len(visible):
                return
            _, _, action = visible[sel[0]]
            dlg.destroy()
            action()

        # Up/Down/Enter navigation while in entry
        def on_arrow(direction):
            cur = listbox.curselection()
            n = listbox.size()
            if n == 0: return
            new = (cur[0] + direction if cur else 0) % n
            listbox.selection_clear(0, tk.END)
            listbox.selection_set(new)
            listbox.see(new)
        entry.bind("<Down>", lambda _e: (on_arrow(1), "break"))
        entry.bind("<Up>", lambda _e: (on_arrow(-1), "break"))
        entry.bind("<Return>", lambda _e: (commit(), "break"))
        listbox.bind("<Double-Button-1>", lambda _e: commit())
        listbox.bind("<Return>", lambda _e: commit())

    def _jump_to_device(self, device: dict) -> None:
        self.selected_device = device
        self._center_on_selected()
        self._redraw()
        self._notebook.select(0)   # Devices tab
        self.status_label.config(
            text=f"Jumped to {device['id']} ({device['type']}).")

    def _jump_to_symbol(self, code: str) -> None:
        self._notebook.select(2)   # Symbols tab
        # Find the listbox row whose first non-tag token is the code
        for i in range(self.sym_listbox.size()):
            text = self.sym_listbox.get(i).strip()
            if any(tok == code for tok in text.split()):
                self.sym_listbox.selection_clear(0, tk.END)
                self.sym_listbox.selection_set(i)
                self.sym_listbox.see(i)
                break
        self.status_label.config(text=f"Jumped to symbol {code}.")

    def _jump_to_wire(self, route_index: int) -> None:
        self._notebook.select(1)   # Wires tab
        self._select_wire_in_listbox(route_index)
        # Pan canvas to the wire's midpoint so user sees it
        if 0 <= route_index < len(self.wire_routes):
            r = self.wire_routes[route_index]
            mid = r["points"][len(r["points"]) // 2]
            cw = self.canvas.winfo_width() or 800
            ch = self.canvas.winfo_height() or 600
            self.offset_x = cw / 2 - mid[0] * self.zoom
            self.offset_y = ch / 2 - mid[1] * self.zoom
        self._redraw()
        self.status_label.config(text=f"Jumped to wire #{route_index+1}.")

    # ============================================================ Save indicator
    def _tick_save_indicator(self) -> None:
        """Refresh the 'Saved Xm ago / Modified' label and re-arm in 15s."""
        if not hasattr(self, "save_indicator"):
            return
        import time as _t
        if self._dirty:
            self.save_indicator.config(text="● Modified",
                                        foreground="#FF8C42")
            tip = ("There are unsaved changes.\n"
                   "Press Ctrl+S to save (or wait ≤60s for auto-save snapshot).")
        elif self._last_save_time is not None:
            elapsed = _t.time() - self._last_save_time
            if elapsed < 15:
                txt = "Saved just now"
            elif elapsed < 60:
                txt = f"Saved {int(elapsed)}s ago"
            elif elapsed < 3600:
                txt = f"Saved {int(elapsed/60)}m ago"
            else:
                txt = f"Saved {int(elapsed/3600)}h ago"
            self.save_indicator.config(text=txt, foreground="#3ddc84")
            stamp = _t.strftime("%H:%M:%S",
                                 _t.localtime(self._last_save_time))
            tip = f"Last saved at {stamp}\n(Ctrl+S to save now.)"
        else:
            self.save_indicator.config(text="", foreground="#888888")
            tip = "No saves yet this session. Ctrl+S to save."
        # Attach/refresh tooltip
        if not hasattr(self, "_save_tip"):
            self._save_tip = Tooltip(self.save_indicator, tip)
        else:
            self._save_tip.text = tip
        # Re-arm
        try:
            self.root.after(15_000, self._tick_save_indicator)
        except tk.TclError:
            pass

    # ============================================================ Auto-save / recovery
    def _schedule_autosave(self) -> None:
        """Re-arm the periodic-save timer (60s). Cheap to call repeatedly —
        cancels any pending one first so we don't pile up callbacks."""
        if self._autosave_id is not None:
            try:
                self.root.after_cancel(self._autosave_id)
            except tk.TclError:
                pass
        self._autosave_id = self.root.after(60_000, self._auto_save_tick)

    def _auto_save_tick(self) -> None:
        self._autosave_id = None
        try:
            self._write_autosave()
        except Exception as e:
            log_error("Auto-save tick failed", e)
        self._schedule_autosave()

    def _write_autosave(self) -> None:
        """Write a per-session recovery snapshot. Skipped if nothing's changed
        since the last full save."""
        if not self._dirty:
            return
        data = {
            "format": "camera-wire-tool/autosave/v1",
            "saved_at": datetime.now().isoformat(timespec="seconds"),
            "drawio_file": os.path.basename(self.drawio_path),
            "scale_factor": self.scale_factor,
            "unit": self.unit_var.get(),
            "next_device_id": self._next_device_id,
            "devices": self.devices,
            "symbol_types": self.symbol_types,
            "cable_types": self.cable_types,
            "wire_routes": [
                {**{k: v for k, v in wr.items() if k != "points"},
                 "points": [list(p) for p in wr["points"]]}
                for wr in self.wire_routes
            ],
        }
        tmp = self._autosave_path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, default=str)
            os.replace(tmp, self._autosave_path)
        except OSError as e:
            log_error(f"Auto-save write failed: {self._autosave_path}", e)

    def _maybe_recover_autosave(self) -> None:
        """Called once on startup. If a fresh autosave outranks the saved
        files, prompt the user to recover. Stale autosaves are silently
        cleaned up."""
        if not os.path.exists(self._autosave_path):
            return
        try:
            mt_auto = os.path.getmtime(self._autosave_path)
            mt_drawio = os.path.getmtime(self.drawio_path)
            mt_proj = (os.path.getmtime(self.project_path)
                       if os.path.exists(self.project_path) else 0.0)
        except OSError:
            return
        if mt_auto <= max(mt_drawio, mt_proj) + 1:
            # Saved file is newer or essentially equal — autosave is stale
            self._cleanup_autosave()
            return
        try:
            with open(self._autosave_path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            self._cleanup_autosave()
            return
        when = data.get("saved_at", "<unknown time>")
        if not messagebox.askyesno(
            "Recover unsaved changes?",
            f"An auto-save from {when} is newer than the last manual save.\n\n"
            f"Recover those changes?\n"
            f"(YES → restore unsaved work and mark project dirty)\n"
            f"(NO → discard the auto-save and load the saved version)"):
            self._cleanup_autosave()
            return
        try:
            self._apply_autosave(data)
        except Exception as e:
            log_error("Recovery apply failed", e)
            messagebox.showerror("Recover failed",
                                  f"Could not apply autosave:\n{e}")

    def _apply_autosave(self, data: dict) -> None:
        """Overlay recovered state onto the freshly-loaded project."""
        if data.get("scale_factor") is not None:
            self.scale_factor = float(data["scale_factor"])
        if data.get("unit"):
            self.unit_var.set(data["unit"])
        if data.get("next_device_id"):
            self._next_device_id = max(self._next_device_id,
                                        int(data["next_device_id"]))
        if data.get("symbol_types"):
            self.symbol_types = data["symbol_types"]
        if data.get("cable_types"):
            self.cable_types = data["cable_types"]
        if data.get("devices"):
            self.devices = data["devices"]
        # Wire routes: rebuild lengths
        self.wire_routes = []
        for wr in data.get("wire_routes", []):
            pts = [tuple(p) for p in wr.get("points", [])]
            if len(pts) < 2:
                continue
            px_len = self._calc_wire_length(pts)
            real_in = (px_len * self.scale_factor) if self.scale_factor else None
            entry = {
                "points": pts, "length_px": px_len, "length_in": real_in,
                "device_id": wr.get("device_id"),
            }
            if wr.get("cable_type"):
                entry["cable_type"] = wr["cable_type"]
            self.wire_routes.append(entry)
        self._mark_dirty()
        self._refresh_symbol_list()
        self._refresh_device_list()
        self._refresh_wire_list()
        self._refresh_bom_tab()
        self._redraw()
        self.status_label.config(text="✓ Recovered unsaved changes from autosave.")

    def _cleanup_autosave(self) -> None:
        if os.path.exists(self._autosave_path):
            try:
                os.remove(self._autosave_path)
            except OSError:
                pass

    # ============================================================ Map annotations
    def _start_place_annotation(self) -> None:
        """Tools → Add Map Annotation. Pops a tiny dialog asking for the
        label text, then enters a one-shot placement mode where the next
        canvas left-click drops the annotation at that position."""
        dlg = tk.Toplevel(self.root)
        dlg.title("Add Map Annotation")
        dlg.geometry("420x220")
        dlg.transient(self.root); dlg.grab_set()
        dlg.bind("<Escape>", lambda _e: dlg.destroy())

        ttk.Label(dlg, text="Drop a text label on the floor plan",
                  font=("Segoe UI", 11, "bold"),
                  ).pack(anchor=tk.W, padx=14, pady=(12, 2))
        ttk.Label(dlg,
                  text="Useful for room labels (Lobby, Server Room, IDF…), "
                       "construction notes, and zone callouts.",
                  wraplength=380, foreground="#888888", font=("Segoe UI", 9),
                  ).pack(anchor=tk.W, padx=14, pady=(0, 8))

        form = ttk.Frame(dlg); form.pack(fill=tk.X, padx=14, pady=4)
        ttk.Label(form, text="Text:", width=10).grid(row=0, column=0, sticky=tk.E, pady=4)
        text_var = tk.StringVar(value="Server Room")
        entry = ttk.Entry(form, textvariable=text_var, width=34)
        entry.grid(row=0, column=1, sticky=tk.W, padx=4)
        entry.select_range(0, tk.END); entry.focus_set()

        ttk.Label(form, text="Size:", width=10).grid(row=1, column=0, sticky=tk.E, pady=4)
        size_var = tk.IntVar(value=18)
        ttk.Spinbox(form, textvariable=size_var, from_=8, to=72, width=6,
                    increment=2,
                    ).grid(row=1, column=1, sticky=tk.W, padx=4)

        ttk.Label(form, text="Color:", width=10).grid(row=2, column=0, sticky=tk.E, pady=4)
        color_row = ttk.Frame(form); color_row.grid(row=2, column=1, sticky=tk.W, padx=4)
        color_var = tk.StringVar(value="#FFD060")
        sw = tk.Label(color_row, bg=color_var.get(), width=4, height=1,
                      relief=tk.RAISED, cursor="hand2")
        sw.pack(side=tk.LEFT, padx=(0, 4))
        ttk.Label(color_row, textvariable=color_var, width=10,
                  font=("Consolas", 9)).pack(side=tk.LEFT)
        def pick(_e=None):
            c = colorchooser.askcolor(initialcolor=color_var.get(),
                                       title="Annotation color")
            if c and c[1]:
                color_var.set(c[1].upper())
                sw.config(bg=c[1].upper())
        sw.bind("<Button-1>", pick)
        ttk.Button(color_row, text="Pick…", command=pick).pack(side=tk.LEFT, padx=4)

        def commit():
            text = text_var.get().strip()
            if not text:
                messagebox.showerror("Error", "Annotation text can't be empty.")
                return
            self._placing_annotation = {
                "text": text, "color": color_var.get(),
                "font_size": int(size_var.get()),
            }
            self._set_mode(Mode.PLACE_DEVICE)   # reuse the cursor / banner
            self._place_type = "__annotation__"   # sentinel so _do_place_device knows
            self.status_label.config(
                text=f"Click on the map to place '{text}' annotation. Esc cancels.")
            dlg.destroy()

        btns = ttk.Frame(dlg); btns.pack(side=tk.BOTTOM, pady=10)
        ttk.Button(btns, text="Place on Map →",
                   command=commit).pack(side=tk.LEFT, padx=4)
        ttk.Button(btns, text="Cancel", command=dlg.destroy).pack(side=tk.LEFT, padx=4)
        dlg.bind("<Return>", lambda _e: commit())

    def _draw_annotations(self) -> None:
        """Draw all map annotations as canvas text at their drawio coords."""
        for ann in self.annotations:
            ix, iy = self._drawio_to_img(ann["x"], ann["y"])
            cx, cy = self._img_to_canvas(ix, iy)
            # Scale font with zoom so annotations stay readable
            fs = max(8, int(ann.get("font_size", 16) * self.zoom * self.dx_scale))
            color = ann.get("color", "#FFD060")
            text = ann.get("text", "")
            # Background rectangle for legibility
            t_id = self.canvas.create_text(
                cx, cy, text=text, fill=color,
                font=("Segoe UI", fs, "bold"),
                anchor=tk.CENTER, tags=("annotation",))
            bbox = self.canvas.bbox(t_id)
            if bbox:
                self.canvas.create_rectangle(
                    bbox[0]-4, bbox[1]-2, bbox[2]+4, bbox[3]+2,
                    fill="#1a1a1a", outline=color, width=1,
                    tags=("annotation", "anno_bg"),
                )
                # Re-raise the text above the background
                self.canvas.tag_raise(t_id)

    def _annotation_at(self, ix: float, iy: float) -> dict | None:
        """Image-coord hit test for annotations (for right-click menu)."""
        # Use canvas item lookup since text bbox is easier to do that way
        cx, cy = self._img_to_canvas(ix, iy)
        for item in reversed(self.canvas.find_withtag("annotation")):
            bbox = self.canvas.bbox(item)
            if bbox and bbox[0] <= cx <= bbox[2] and bbox[1] <= cy <= bbox[3]:
                # Map canvas position back to image-coord and find matching annotation
                # by closest x/y in image coords
                ix_a = (bbox[0] + bbox[2]) / 2
                iy_a = (bbox[1] + bbox[3]) / 2
                ix_img, iy_img = self._canvas_to_img(ix_a, iy_a)
                best = None; best_d = float("inf")
                for ann in self.annotations:
                    a_ix, a_iy = self._drawio_to_img(ann["x"], ann["y"])
                    d = math.hypot(ix_img - a_ix, iy_img - a_iy)
                    if d < best_d:
                        best_d = d; best = ann
                return best
        return None

    def _sync_dev_listbox_to_canvas(self) -> None:
        """When a device is selected on the canvas (single-select), scroll
        the Devices tab to its row and highlight it. Two-way coupling that
        makes navigation feel cohesive."""
        if not hasattr(self, "_dev_listbox_rows") or not self.selected_device:
            return
        target_id = self.selected_device.get("id")
        for idx, row in enumerate(self._dev_listbox_rows):
            if row is not None and row.get("id") == target_id:
                self.dev_listbox.selection_clear(0, tk.END)
                self.dev_listbox.selection_set(idx)
                self.dev_listbox.see(idx)
                break

    def _edit_annotation(self, ann: dict) -> None:
        """Tiny inline editor for an existing annotation."""
        dlg = tk.Toplevel(self.root)
        dlg.title("Edit Annotation")
        dlg.geometry("420x200")
        dlg.transient(self.root); dlg.grab_set()
        ttk.Label(dlg, text="Edit annotation",
                  font=("Segoe UI", 11, "bold"),
                  ).pack(anchor=tk.W, padx=14, pady=(12, 8))
        form = ttk.Frame(dlg); form.pack(fill=tk.X, padx=14)
        ttk.Label(form, text="Text:", width=8).grid(row=0, column=0, sticky=tk.E, pady=4)
        text_var = tk.StringVar(value=ann.get("text", ""))
        ent = ttk.Entry(form, textvariable=text_var, width=34)
        ent.grid(row=0, column=1, sticky=tk.W, padx=4)
        ent.select_range(0, tk.END); ent.focus_set()
        ttk.Label(form, text="Size:", width=8).grid(row=1, column=0, sticky=tk.E, pady=4)
        size_var = tk.IntVar(value=int(ann.get("font_size", 18)))
        ttk.Spinbox(form, textvariable=size_var, from_=8, to=72, width=6,
                    increment=2,
                    ).grid(row=1, column=1, sticky=tk.W, padx=4)
        ttk.Label(form, text="Color:", width=8).grid(row=2, column=0, sticky=tk.E, pady=4)
        color_row = ttk.Frame(form); color_row.grid(row=2, column=1, sticky=tk.W, padx=4)
        color_var = tk.StringVar(value=ann.get("color", "#FFD060"))
        sw = tk.Label(color_row, bg=color_var.get(), width=4, height=1,
                      relief=tk.RAISED, cursor="hand2")
        sw.pack(side=tk.LEFT, padx=(0, 4))
        ttk.Label(color_row, textvariable=color_var, width=10,
                  font=("Consolas", 9)).pack(side=tk.LEFT)
        def pick(_e=None):
            c = colorchooser.askcolor(initialcolor=color_var.get(),
                                       title="Annotation color")
            if c and c[1]:
                color_var.set(c[1].upper()); sw.config(bg=c[1].upper())
        sw.bind("<Button-1>", pick)
        ttk.Button(color_row, text="Pick…", command=pick).pack(side=tk.LEFT, padx=4)

        def commit():
            t = text_var.get().strip()
            if not t:
                messagebox.showerror("Error", "Annotation text can't be empty.")
                return
            old = {"text": ann.get("text"),
                   "color": ann.get("color"),
                   "font_size": ann.get("font_size")}
            new = {"text": t, "color": color_var.get(),
                   "font_size": int(size_var.get())}
            if old == new:
                dlg.destroy(); return
            self._execute(EditAnnotationCmd(self, ann, old, new))
            self._post_command_refresh()
            dlg.destroy()

        btns = ttk.Frame(dlg); btns.pack(side=tk.BOTTOM, pady=10)
        ttk.Button(btns, text="Save", command=commit).pack(side=tk.LEFT, padx=4)
        ttk.Button(btns, text="Cancel", command=dlg.destroy).pack(side=tk.LEFT, padx=4)
        dlg.bind("<Return>", lambda _e: commit())
        dlg.bind("<Escape>", lambda _e: dlg.destroy())

    def _delete_annotation(self, ann: dict) -> None:
        try:
            idx = self.annotations.index(ann)
        except ValueError:
            return
        self._execute(DeleteAnnotationCmd(self, ann, idx))
        self._post_command_refresh()
        self._flash_status("Annotation deleted. Ctrl+Z to undo.", ttl_ms=2500)

    def _toggle_fullscreen(self) -> None:
        try:
            cur = bool(self.root.attributes("-fullscreen"))
            self.root.attributes("-fullscreen", not cur)
            self._flash_status(
                "Fullscreen ON  (F11 to exit)" if not cur else "Fullscreen OFF",
                ttl_ms=2500)
        except tk.TclError:
            pass

    # ============================================================ Preferences dialog
    def _open_preferences_dialog(self) -> None:
        """A small Toplevel showing the per-user persisted settings,
        with reset buttons. Changes auto-save (consistent with the
        toolbar behavior). Also lets the user clear the recent-files
        list and the autosave file."""
        dlg = tk.Toplevel(self.root)
        dlg.title("Preferences")
        dlg.geometry("520x440")
        dlg.transient(self.root); dlg.grab_set()
        dlg.bind("<Escape>", lambda _e: dlg.destroy())

        ttk.Label(dlg, text="Preferences",
                  font=("Segoe UI", 14, "bold"),
                  ).pack(anchor=tk.W, padx=14, pady=(12, 2))
        ttk.Label(
            dlg,
            text="Changes here save automatically to "
                 f"{SETTINGS_PATH.name} in your home folder, and apply "
                 "to every project you open.",
            wraplength=480, foreground="#7fc4ff",
            font=("Segoe UI", 9, "italic"),
        ).pack(anchor=tk.W, padx=14, pady=(0, 8))

        # Defaults card
        defaults = ttk.LabelFrame(dlg, text="BOM defaults")
        defaults.pack(fill=tk.X, padx=14, pady=4)

        row = ttk.Frame(defaults); row.pack(fill=tk.X, padx=8, pady=4)
        ttk.Label(row, text="Display unit:", width=18).pack(side=tk.LEFT)
        unit_combo = ttk.Combobox(
            row, textvariable=self._unit_label_var,
            values=[label for label, _val in self._unit_choices],
            state="readonly", width=24,
        )
        unit_combo.pack(side=tk.LEFT)
        def on_unit_pick(_e=None):
            label = self._unit_label_var.get()
            for lab, v in self._unit_choices:
                if lab == label:
                    self.unit_var.set(v); break
            self._on_unit_change()
        unit_combo.bind("<<ComboboxSelected>>", on_unit_pick)

        row = ttk.Frame(defaults); row.pack(fill=tk.X, padx=8, pady=4)
        ttk.Label(row, text="Currency:", width=18).pack(side=tk.LEFT)
        ttk.Entry(row, textvariable=self._bom_currency_var,
                   width=8).pack(side=tk.LEFT)
        ttk.Label(row, text="(e.g. $, €, £, ¥)",
                  foreground="#888888", font=("Segoe UI", 9, "italic"),
                  ).pack(side=tk.LEFT, padx=8)

        row = ttk.Frame(defaults); row.pack(fill=tk.X, padx=8, pady=4)
        ttk.Label(row, text="Default waste factor:", width=18).pack(side=tk.LEFT)
        ttk.Entry(row, textvariable=self._bom_waste_var,
                   width=8).pack(side=tk.LEFT)
        ttk.Label(row, text="(1.10 = +10% material allowance)",
                  foreground="#888888", font=("Segoe UI", 9, "italic"),
                  ).pack(side=tk.LEFT, padx=8)

        row = ttk.Frame(defaults); row.pack(fill=tk.X, padx=8, pady=4)
        ttk.Label(row, text="Default tax rate:", width=18).pack(side=tk.LEFT)
        ttk.Entry(row, textvariable=self._bom_tax_var,
                   width=8).pack(side=tk.LEFT)
        ttk.Label(row, text="(decimal, e.g. 0.08 = 8%)",
                  foreground="#888888", font=("Segoe UI", 9, "italic"),
                  ).pack(side=tk.LEFT, padx=8)

        # Cleanup card
        cleanup = ttk.LabelFrame(dlg, text="Per-user data")
        cleanup.pack(fill=tk.X, padx=14, pady=10)

        row = ttk.Frame(cleanup); row.pack(fill=tk.X, padx=8, pady=4)
        ttk.Label(row,
                  text=f"Settings file: {SETTINGS_PATH.name}",
                  font=("Consolas", 9),
                  ).pack(side=tk.LEFT)

        row = ttk.Frame(cleanup); row.pack(fill=tk.X, padx=8, pady=8)

        def reset_defaults():
            if not messagebox.askyesno(
                "Reset preferences?",
                "Reset BOM unit, currency, waste, and tax to factory "
                "defaults?\n(Project files are not affected.)"):
                return
            self.unit_var.set("ft-in")
            self._bom_currency_var.set("$")
            self._bom_waste_var.set(1.10)
            self._bom_tax_var.set(0.0)
            for lab, v in self._unit_choices:
                if v == "ft-in":
                    self._unit_label_var.set(lab); break
            self._persist_user_settings()
            self._refresh_bom_tab()
            self._refresh_wire_list()
            self._flash_status("Preferences reset to defaults.")

        def clear_recents():
            if not messagebox.askyesno(
                "Clear recent files?",
                "Remove all entries from the File → Open Recent menu?"):
                return
            try:
                if RECENT_FILES_PATH.exists():
                    RECENT_FILES_PATH.unlink()
            except OSError:
                pass
            self._refresh_recent_menu()
            self._flash_status("Recent files cleared.")

        ttk.Button(row, text="Reset BOM defaults",
                   command=reset_defaults).pack(side=tk.LEFT, padx=4)
        ttk.Button(row, text="Clear Recent Files",
                   command=clear_recents).pack(side=tk.LEFT, padx=4)

        ttk.Button(dlg, text="Close (Esc)",
                   command=dlg.destroy).pack(side=tk.BOTTOM, pady=10)

    # ============================================================ Help dialogs
    def _show_shortcuts(self) -> None:
        """Friendlier-than-messagebox keyboard shortcuts dialog with
        section headers, two-column layout, and a "Got it" close button."""
        dlg = tk.Toplevel(self.root)
        dlg.title("Keyboard Shortcuts")
        dlg.geometry("560x520")
        dlg.transient(self.root); dlg.grab_set()
        dlg.bind("<Escape>", lambda _e: dlg.destroy())

        # Header
        ttk.Label(dlg, text="Keyboard Shortcuts",
                  font=("Segoe UI", 14, "bold"),
                  ).pack(anchor=tk.W, padx=14, pady=(12, 2))
        ttk.Label(dlg,
                  text="Press F1 or ? at any time to open this list.",
                  foreground="#7fc4ff", font=("Segoe UI", 9, "italic"),
                  ).pack(anchor=tk.W, padx=14, pady=(0, 8))

        # Scrollable body
        outer = ttk.Frame(dlg)
        outer.pack(fill=tk.BOTH, expand=True, padx=14, pady=4)
        canvas = tk.Canvas(outer, bg="#1e1e1e", highlightthickness=0)
        sb = ttk.Scrollbar(outer, orient=tk.VERTICAL, command=canvas.yview)
        body = ttk.Frame(canvas)
        body.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=body, anchor="nw", width=520)
        canvas.config(yscrollcommand=sb.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)

        sections = [
            ("Modes", [
                ("M",      "Move mode — click devices to select/drag"),
                ("W",      "Wire mode — click points to draw a wire"),
                ("C",      "Calibrate — set the real-world scale"),
            ]),
            ("Editing", [
                ("Ctrl+Z",       "Undo last action"),
                ("Ctrl+Y",       "Redo"),
                ("Ctrl+A",       "Select all visible devices"),
                ("Drag",         "Lasso-select (drag empty canvas in Move mode)"),
                ("Shift+drag",   "Lasso-add to existing selection"),
                ("Ctrl+click",   "Toggle a device in selection"),
                ("Ctrl+C",       "Copy selected devices to clipboard"),
                ("Ctrl+V",       "Paste devices at cursor position"),
                ("Delete",       "Delete selected wire or device(s)"),
                ("Escape",       "Cancel action / clear selection"),
            ]),
            ("Tabs", [
                ("Ctrl+1 / 2 / 3 / 4", "Devices / Wires / Symbols / BOM"),
                ("Ctrl+Tab",           "Cycle to next tab"),
                ("Ctrl+Shift+Tab",     "Cycle to previous tab"),
            ]),
            ("View / Navigation", [
                ("Mouse wheel",  "Zoom toward cursor"),
                ("Middle-drag",  "Pan the canvas"),
                ("Ctrl+drag",    "Pan (alternate)"),
                ("Space + drag", "Temporary pan"),
                ("Home",         "Fit floor plan to viewport"),
                ("+ / −",        "Zoom in / out around viewport center"),
                ("Z",            "Zoom to current selection"),
                ("[ / ]",        "Cycle through devices in id order"),
                ("Shift+[ / ]",  "Cycle through wires"),
                ("R",            "Rotate selected camera's FOV by 15°"),
                ("Shift+R",      "Rotate by 45° (Alt = counter-clockwise)"),
            ]),
            ("File", [
                ("Ctrl+O",         "Open .drawio…"),
                ("Ctrl+S",         "Save"),
                ("Ctrl+Shift+S",   "Save As…"),
            ]),
            ("Wire drawing", [
                ("Shift (hold)", "Lock segment to 0/45/90° from last point"),
                ("(automatic)",  "Cursor snaps to nearby device centers, "
                                 "wire endpoints, and other waypoints"),
                ("Right-click",  "Finish current wire"),
            ]),
            ("Search & jump", [
                ("Ctrl+F",       "Focus search on active tab"),
                ("Ctrl+G",       "Quick jump (find any device/symbol/wire)"),
                ("Ctrl+P",       "Quick jump (alias)"),
                ("F1 or ?",      "Open this dialog"),
            ]),
        ]
        for title, rows in sections:
            ttk.Label(body, text=title,
                      font=("Segoe UI", 11, "bold"),
                      foreground="#7fc4ff",
                      ).grid(sticky=tk.W, padx=4, pady=(8, 2),
                              columnspan=2)
            for keys, desc in rows:
                ttk.Label(body, text=keys,
                          font=("Consolas", 10),
                          foreground="#FFD060",
                          ).grid(sticky=tk.W, padx=(20, 16))
                ttk.Label(body, text=desc, font=("Segoe UI", 10),
                          ).grid(row=body.grid_size()[1] - 1, column=1,
                                  sticky=tk.W)

        # Footer
        footer = ttk.Frame(dlg)
        footer.pack(fill=tk.X, padx=14, pady=10)
        ttk.Button(footer, text="Got it (Esc)",
                   command=dlg.destroy).pack(side=tk.RIGHT)

    def _show_about(self) -> None:
        dlg = tk.Toplevel(self.root)
        dlg.title("About Camera Wire Tool")
        dlg.geometry("520x460")
        dlg.transient(self.root); dlg.grab_set()
        dlg.bind("<Escape>", lambda _e: dlg.destroy())

        # Big title
        ttk.Label(dlg, text="Camera Wire Tool",
                  font=("Segoe UI", 18, "bold"),
                  ).pack(anchor=tk.W, padx=20, pady=(20, 0))
        ttk.Label(dlg, text="v1.0.0  ·  estimation for security-camera cable runs",
                  foreground="#7fc4ff", font=("Segoe UI", 10, "italic"),
                  ).pack(anchor=tk.W, padx=20, pady=(0, 14))

        # Feature bullets
        feat = ttk.LabelFrame(dlg, text="What it does")
        feat.pack(fill=tk.X, padx=20, pady=4)
        for line in (
            "• Loads draw.io plans, drag/drop devices, draw multi-point wire routes",
            "• Cable types + BOM with cost, waste %, tax, currency",
            "• Camera FOV / DORI overlays (IEC 62676-4)",
            "• Multi-floor projects with anchor-point alignment + risers",
            "• PDF + CSV report export",
            "• Auto-save with crash recovery, undo/redo with batch ops",
            "• Snap-to-orthogonal wire drawing, drag-to-reshape waypoints",
        ):
            ttk.Label(feat, text=line, font=("Segoe UI", 9),
                      ).pack(anchor=tk.W, padx=10, pady=1)

        # Stats card showing what's loaded
        stats = ttk.LabelFrame(dlg, text="This project")
        stats.pack(fill=tk.X, padx=20, pady=10)
        n_dev = len(self.devices)
        n_cam = sum(1 for d in self.devices
                    if self.symbol_types.get(d["type"], {}).get("is_camera"))
        n_wire = len(self.wire_routes)
        n_floor = len(self._project.floors) if self._project else 1
        for label, value in (
            ("File:",    os.path.basename(self.drawio_path)),
            ("Devices:", f"{n_dev}  ({n_cam} cameras, {n_dev - n_cam} other)"),
            ("Wires:",   str(n_wire)),
            ("Floors:",  str(n_floor)),
            ("Scale:",   self._scale_label_text().replace("Scale:  ", "")),
        ):
            row = ttk.Frame(stats); row.pack(fill=tk.X, padx=8, pady=2)
            ttk.Label(row, text=label, width=10,
                      foreground="#888888", font=("Segoe UI", 9),
                      ).pack(side=tk.LEFT)
            ttk.Label(row, text=value, font=("Segoe UI", 9),
                      ).pack(side=tk.LEFT)

        # Footer
        ttk.Label(
            dlg,
            text="MIT License  ·  See PLAYBOOK.md for the architecture",
            foreground="#888", font=("Segoe UI", 8),
        ).pack(anchor=tk.W, padx=20, pady=(10, 4))
        ttk.Button(dlg, text="Close (Esc)",
                   command=dlg.destroy).pack(side=tk.BOTTOM, pady=10)

    # ============================================================ right-click context menu
    def _on_right_click_with_menu(self, event) -> None:
        # In Wire Mode, right-click finishes (≥2 points) or cancels.
        if self.mode == Mode.WIRE:
            if len(self.current_wire_points) >= 2:
                self._finish_wire_route()
            else:
                self._cancel_current()
            return
        # In other action modes, right-click cancels rather than menuing.
        if self.mode in (Mode.CALIBRATE, Mode.PLACE_DEVICE):
            self._cancel_current()
            return
        # Right-click directly on a FOV handle → open the FOV editor for
        # that camera. (Hit-test by canvas coords against cached handles.)
        for did, (hx, hy, _ax, _ay) in self._fov_handles.items():
            if math.hypot(event.x - hx, event.y - hy) <= 12:
                target = next((dd for dd in self.devices
                               if dd["id"] == did), None)
                if target is not None:
                    self._open_fov_dialog(target)
                    return

        ix, iy = self._canvas_to_img(event.x, event.y)
        # Annotation right-click takes priority — no device/wire conflicts.
        ann = self._annotation_at(ix, iy)
        if ann is not None:
            menu = tk.Menu(self.root, tearoff=0)
            menu.add_command(label=f"Annotation: \"{ann['text']}\"",
                             state=tk.DISABLED)
            menu.add_separator()
            menu.add_command(label="✏  Edit text / color / size…",
                             command=lambda a=ann: self._edit_annotation(a))
            menu.add_command(label="🗑  Delete annotation",
                             command=lambda a=ann: self._delete_annotation(a))
            try:
                menu.tk_popup(event.x_root, event.y_root)
            finally:
                menu.grab_release()
            return
        dev = self._device_at(ix, iy)
        # Wire under cursor (only when not on a device — devices win)
        wire_idx = self._wire_at(ix, iy) if dev is None else None
        menu = tk.Menu(self.root, tearoff=0)
        if wire_idx is not None and dev is None:
            # Right-click directly on a wire line: show wire actions menu.
            self._select_wire_in_listbox(wire_idx)
            route = self.wire_routes[wire_idx]
            did = route.get("device_id") or "—"
            length = (self._format_length(route.get("length_in") or 0)
                      if route.get("length_in") is not None
                      else f"{route['length_px']:.0f}px")
            menu.add_command(
                label=f"Wire #{wire_idx+1} → {did}  ({length})",
                state=tk.DISABLED,
            )
            menu.add_separator()
            menu.add_command(label="🔁  Re-route this wire",
                             command=self._reroute_selected_wire)
            menu.add_command(label="✂  Simplify (drop colinear points)",
                             command=self._simplify_selected_wire)
            menu.add_command(label="🔗  Reassign to different device…",
                             command=self._reassign_selected_wire)
            menu.add_separator()
            menu.add_command(label="🗑  Delete wire",
                             command=self._delete_selected_wire,
                             accelerator="Del")
            try:
                self._redraw()
                menu.tk_popup(event.x_root, event.y_root)
            finally:
                menu.grab_release()
            return
        if dev is not None:
            # Don't clobber existing multi-selection if user right-clicks
            # one that's already in the multi-selection — just keep going.
            if not any(d is dev for d in self.selected_devices):
                self.selected_device = dev
            self._redraw()
            info = self.symbol_types.get(dev["type"], {})
            menu.add_command(label=f"Selected: {dev['id']} ({dev['type']})",
                             state=tk.DISABLED)
            if len(self.selected_devices) > 1:
                menu.add_command(
                    label=f"({len(self.selected_devices)} devices in selection)",
                    state=tk.DISABLED)
            menu.add_separator()
            menu.add_command(
                label="📐  Draw wire from here…",
                command=lambda d=dev: self._draw_wire_from(d),
            )
            menu.add_command(
                label="🔁  Replace last wire (re-route)…",
                command=lambda d=dev: self._reroute_last_wire_from(d),
            )
            wires_for_dev = [w for w in self.wire_routes
                             if w.get("device_id") == dev["id"]]
            if wires_for_dev:
                menu.add_command(
                    label=f"🗑  Clear all wires for this device "
                          f"({len(wires_for_dev)} run(s))",
                    command=lambda d=dev: self._clear_wires_for_device(d),
                )
            menu.add_separator()
            if info.get("is_camera"):
                menu.add_command(label="📷  Edit FOV / DORI…",
                                 command=lambda d=dev: self._open_fov_dialog(d))
                menu.add_command(
                    label=f"Apply default FOV for {dev['type']}",
                    command=lambda d=dev: self._apply_default_fov_to_one(d),
                )
                menu.add_command(
                    label="Toggle FOV cone for this camera",
                    command=lambda d=dev: self._toggle_camera_fov(d),
                )
                menu.add_separator()
            del_label = (f"🗑  Delete {len(self.selected_devices)} selected devices"
                         if len(self.selected_devices) > 1
                         else "🗑  Delete device")
            menu.add_command(label=del_label, command=self._on_delete_key,
                             accelerator="Del")
        else:
            # Empty canvas. If the user has anything selected, give them
            # delete + clear-selection right at the top.
            n_dev = len(self.selected_devices)
            wire_sel = self.wire_listbox.curselection() if hasattr(
                self, "wire_listbox") else ()
            n_wire = len(wire_sel)
            if n_dev > 0:
                menu.add_command(
                    label=f"🗑  Delete {n_dev} selected device(s)",
                    command=self._on_delete_key, accelerator="Del")
            if n_wire > 0:
                menu.add_command(
                    label=f"🗑  Delete {n_wire} selected wire(s)",
                    command=lambda: (
                        self._delete_selected_wires_multi(list(wire_sel))
                        if n_wire > 1 else self._delete_selected_wire()),
                    accelerator="Del")
            if n_dev or n_wire:
                menu.add_command(
                    label="🔍 Fit to selection (Z)",
                    command=self._kbd_zoom_to_selection)
                menu.add_command(
                    label="Clear selection",
                    command=lambda: (
                        setattr(self, "selected_device", None),
                        self.wire_listbox.selection_clear(0, tk.END),
                        self._redraw(), self._refresh_wire_list()))
                menu.add_separator()
            # When scale isn't set, surface the calibration action right
            # where the user is right-clicking. Big first-time UX win.
            if self.scale_factor is None:
                menu.add_command(label="📏  Set Scale (calibrate)…",
                                 command=self._start_calibration)
                menu.add_separator()
            menu.add_command(label="Fit view",
                             command=lambda: (self._fit_image(), self._redraw()))
            menu.add_separator()
            menu.add_command(label="Show device IDs",
                             command=lambda: (self._show_device_ids.set(
                                 not self._show_device_ids.get()), self._redraw()))
            menu.add_command(label="Toggle FOV overlays",
                             command=lambda: (self._show_fov.set(
                                 not self._show_fov.get()), self._redraw()))

        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    # ============================================================ BOM tab
    def _build_bom_report(self) -> bom_mod.BomReport:
        try:
            waste = float(self._bom_waste_var.get())
        except tk.TclError:
            waste = 1.10
        try:
            tax = float(self._bom_tax_var.get())
        except tk.TclError:
            tax = 0.0
        risers = self._project.risers if self._project else []
        return bom_mod.rollup(
            self.wire_routes, self.devices, self.symbol_types, self.cable_types,
            waste_factor=waste, tax_rate=tax, risers=risers,
            scale_factor=self.scale_factor,
        )

    def _refresh_bom_tab(self) -> None:
        if not hasattr(self, "_bom_tree"):
            return
        for item in self._bom_tree.get_children():
            self._bom_tree.delete(item)
        report = self._build_bom_report()
        cur = self._bom_currency_var.get() or "$"

        # Apply user sort to the BOM lines
        sort_col, reverse = getattr(self, "_bom_sort_state", ("cable", False))
        sort_key = {
            "cable":    lambda l: l.cable_id.lower(),
            "label":    lambda l: l.label.lower(),
            "runs":     lambda l: l.runs,
            "ft":       lambda l: l.total_ft,
            "rate":     lambda l: l.cost_per_ft,
            "subtotal": lambda l: l.subtotal,
        }.get(sort_col, lambda l: l.cable_id.lower())
        lines_sorted = sorted(report.lines, key=sort_key, reverse=reverse)

        # Update column headers with arrow indicator
        for c, base in (
            ("cable", "Cable"), ("label", "Description"),
            ("runs",  "Runs"),  ("ft", "Feet"),
            ("rate",  "$/ft"),  ("subtotal", "Subtotal"),
        ):
            arrow = (" ▼" if reverse else " ▲") if c == sort_col else ""
            self._bom_tree.heading(c, text=base + arrow)

        for line in lines_sorted:
            self._bom_tree.insert(
                "", tk.END,
                values=(line.cable_id, line.label, line.runs,
                        f"{line.total_ft:,.2f}",
                        f"{cur}{line.cost_per_ft:.4f}",
                        f"{cur}{line.subtotal:,.2f}"),
            )
        if not lines_sorted:
            # Friendly empty state instead of a blank table
            self._bom_tree.insert(
                "", tk.END,
                values=("—", "(no wire runs yet — draw a wire first)",
                        "", "", "", ""))

        # Per-symbol inventory (cameras + access + intrusion etc.)
        if hasattr(self, "_bom_devices_tree"):
            for item in self._bom_devices_tree.get_children():
                self._bom_devices_tree.delete(item)
            inv = bom_mod.device_inventory(
                self.devices, self.symbol_types, self.wire_routes,
                self.cable_types,
            )
            cam_count = 0; cam_runs = 0; cam_in = 0.0
            total_count = 0; total_runs = 0; total_in = 0.0
            for line in inv:
                is_cam = self.symbol_types.get(line.code, {}).get("is_camera")
                tag = "cam" if is_cam else "other"
                wire_str = (self._format_length(line.total_in)
                            if line.total_in else "—")
                self._bom_devices_tree.insert(
                    "", tk.END,
                    values=(line.code, line.label[:24],
                            line.count, line.cable_id, line.runs, wire_str),
                    tags=(tag,),
                )
                total_count += line.count
                total_runs  += line.runs
                total_in    += line.total_in
                if is_cam:
                    cam_count += line.count
                    cam_runs  += line.runs
                    cam_in    += line.total_in
            # Subtotal rows
            self._bom_devices_tree.insert(
                "", tk.END,
                values=("CAMS", "— cameras subtotal —",
                        cam_count, "", cam_runs,
                        self._format_length(cam_in) if cam_in else "—"),
                tags=("subtotal",),
            )
            self._bom_devices_tree.insert(
                "", tk.END,
                values=("ALL", "— ALL devices —",
                        total_count, "", total_runs,
                        self._format_length(total_in) if total_in else "—"),
                tags=("total",),
            )
            self._bom_devices_tree.tag_configure("cam",      foreground="#FFD060")
            self._bom_devices_tree.tag_configure("other",    foreground="#A0C0FF")
            self._bom_devices_tree.tag_configure("subtotal",
                                                 background="#2a2a2a",
                                                 foreground="#FFFFFF")
            self._bom_devices_tree.tag_configure("total",
                                                 background="#202060",
                                                 foreground="#FFFFFF")

        # Update pills if they exist (legacy code path before _build_ui sets them)
        if hasattr(self, "_bom_pill_subtotal"):
            self._bom_pill_subtotal.config(
                text=f"Subtotal\n{cur}{report.subtotal:,.2f}")
            self._bom_pill_waste.config(
                text=f"Waste ×{report.waste_factor:.2f}\n"
                     f"{cur}{report.waste_added:,.2f}")
            self._bom_pill_tax.config(
                text=f"Tax {report.tax_rate*100:.2f}%\n"
                     f"{cur}{report.tax_amount:,.2f}")
            self._bom_pill_total.config(
                text=f"GRAND TOTAL\n{cur}{report.grand_total:,.2f}")
        # Unassigned-wires warning lives on the side-panel total label
        # (still shown; the pill row is fixed-width and doesn't include it)
        if report.unassigned_runs:
            warn = (f"\n  ⚠ {report.unassigned_runs} unassigned wire(s) — "
                    f"{self._format_length(report.unassigned_inches)}")
            self._bom_totals_label.config(text=warn, foreground="#FF8C42")

    # ============================================================ BOM right-click
    def _on_bom_cable_rclick(self, event) -> None:
        """Right-click a row in the 'By cable type' Treeview → menu with
        'Highlight wires of this cable' and 'Edit this cable type'."""
        row = self._bom_tree.identify_row(event.y)
        if not row:
            return
        self._bom_tree.selection_set(row)
        values = self._bom_tree.item(row, "values")
        if not values:
            return
        cable_id = values[0]
        if cable_id == "—" or cable_id not in self.cable_types:
            return
        m = tk.Menu(self.root, tearoff=0)
        m.add_command(label=f"{cable_id} — {self.cable_types[cable_id].get('label','')}",
                      state=tk.DISABLED)
        m.add_separator()
        m.add_command(
            label=f"🔍  Highlight wires using {cable_id}",
            command=lambda c=cable_id: self._highlight_wires_by_cable(c),
        )
        m.add_command(
            label=f"✏️  Edit cable type {cable_id}…",
            command=self._open_cable_types_editor,
        )
        try:
            m.tk_popup(event.x_root, event.y_root)
        finally:
            m.grab_release()

    def _highlight_wires_by_cable(self, cable_id: str) -> None:
        """Select every wire whose effective cable is `cable_id` so they
        all light up red on the canvas + are visible in the listbox."""
        # Resolve effective cable for each route via the same rule the BOM uses
        devices_by_id = {d["id"]: d for d in self.devices}
        matching: list[int] = []
        for i, r in enumerate(self.wire_routes):
            cid = bom_mod.cable_for_route(r, devices_by_id, self.symbol_types,
                                          self.cable_types)
            if cid == cable_id:
                matching.append(i)
        if not matching:
            self._flash_status(f"No wires currently use {cable_id}.")
            return
        # Switch to Wires tab and select all matches
        self._notebook.select(1)
        self.wire_listbox.selection_clear(0, tk.END)
        # Map route indices → listbox row indices via _wire_listbox_rows
        for ri in matching:
            try:
                row = self._wire_listbox_rows.index(ri)
                self.wire_listbox.selection_set(row)
                self.wire_listbox.see(row)
            except ValueError:
                continue
        self._redraw()
        self._flash_status(
            f"Highlighted {len(matching)} wire(s) using {cable_id}.",
            ttl_ms=3500,
        )

    # ============================================================ Cable types editor (master-detail)
    def _open_cable_types_editor(self) -> None:
        """Master-detail cable-types editor.

        Top half is the cable list (with a colored swatch per row).
        Bottom half is the always-visible edit form for whichever cable
        is currently selected (or for a new one being added). Changes
        autosave on focus-out / Enter — no per-row dialog dance.
        """
        dlg = tk.Toplevel(self.root)
        dlg.title("Cable Types")
        dlg.geometry("560x520")
        dlg.transient(self.root); dlg.grab_set()
        dlg.bind("<Escape>", lambda _e: dlg.destroy())

        ttk.Label(
            dlg,
            text="These cables show up in the BOM. Click a row to edit it; "
                 "click 'Add new cable…' for a fresh one.",
            wraplength=520, foreground="#7fc4ff", font=("Segoe UI", 9),
        ).pack(anchor=tk.W, padx=12, pady=(10, 4))

        # ---------------- top: cable list with color swatches via tags
        list_frame = ttk.Frame(dlg)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=4)
        cols = ("id", "label", "cost", "swatch")
        tree = ttk.Treeview(list_frame, columns=cols, show="headings",
                             height=8, selectmode="browse")
        for c, w, anchor, h in (
            ("id",     70,  tk.W, "ID"),
            ("label",  240, tk.W, "Description"),
            ("cost",   90,  tk.E, "Cost / ft"),
            ("swatch", 100, tk.W, "Color"),
        ):
            tree.heading(c, text=h)
            tree.column(c, width=w, anchor=anchor, stretch=False)
        sb = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=tree.yview)
        tree.config(yscrollcommand=sb.set)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)

        # ---------------- bottom: detail form
        form = ttk.LabelFrame(dlg, text="Edit cable")
        form.pack(fill=tk.X, padx=12, pady=(8, 8))

        ttk.Label(form, text="ID:").grid(row=0, column=0, sticky=tk.E, padx=8, pady=6)
        id_var = tk.StringVar()
        id_entry = ttk.Entry(form, textvariable=id_var, width=18)
        id_entry.grid(row=0, column=1, sticky=tk.W, padx=4)
        id_help = ttk.Label(form, text="(short identifier, e.g. Cat6 — can't change after add)",
                            foreground="#888", font=("Segoe UI", 8, "italic"))
        id_help.grid(row=0, column=2, sticky=tk.W, padx=4)

        ttk.Label(form, text="Description:").grid(row=1, column=0, sticky=tk.E, padx=8, pady=6)
        label_var = tk.StringVar()
        ttk.Entry(form, textvariable=label_var, width=44).grid(
            row=1, column=1, columnspan=2, sticky=tk.W, padx=4)

        ttk.Label(form, text="Cost / ft ($):").grid(row=2, column=0, sticky=tk.E, padx=8, pady=6)
        cost_var = tk.StringVar()
        ttk.Entry(form, textvariable=cost_var, width=12).grid(
            row=2, column=1, sticky=tk.W, padx=4)

        ttk.Label(form, text="Color:").grid(row=3, column=0, sticky=tk.E, padx=8, pady=6)
        color_var = tk.StringVar()
        color_row = ttk.Frame(form)
        color_row.grid(row=3, column=1, columnspan=2, sticky=tk.W, padx=4)
        swatch = tk.Label(color_row, bg="#888888", width=4, height=1,
                           relief=tk.RAISED, cursor="hand2")
        swatch.pack(side=tk.LEFT, padx=(0, 6))
        color_label = ttk.Label(color_row, textvariable=color_var, width=10,
                                 font=("Consolas", 10))
        color_label.pack(side=tk.LEFT)

        def pick_color(_event=None):
            c = colorchooser.askcolor(
                initialcolor=color_var.get() or "#888888",
                title="Cable color (used to color wires on the map)")
            if c and c[1]:
                color_var.set(c[1].upper())
                swatch.config(bg=c[1].upper())
        # Click directly on the swatch (or its label) to open the picker.
        swatch.bind("<Button-1>", pick_color)
        color_label.bind("<Button-1>", pick_color)
        ttk.Button(color_row, text="Pick color…", command=pick_color).pack(side=tk.LEFT, padx=8)

        # Detail-form state
        editing_id: list[str | None] = [None]    # None when adding
        is_dirty: list[bool] = [False]

        def update_swatch(*_):
            try:
                swatch.config(bg=color_var.get() or "#888888")
            except tk.TclError:
                pass
        color_var.trace_add("write", update_swatch)

        def mark_dirty(*_):
            is_dirty[0] = True
        for v in (id_var, label_var, cost_var, color_var):
            v.trace_add("write", mark_dirty)

        # ---------------- repopulation of list
        def repop(select_id: str | None = None) -> None:
            for it in tree.get_children():
                tree.delete(it)
            for cid, ct in sorted(self.cable_types.items()):
                color = ct.get("color", "#888888")
                tree.insert("", tk.END, iid=cid,
                            values=(cid, ct.get("label", cid),
                                    f"${ct.get('cost_per_ft', 0):.4f}",
                                    color),
                            tags=(f"c_{cid}",))
                # Color swatch via tag-coloring on the swatch column
                tree.tag_configure(f"c_{cid}", background=color,
                                    foreground=_text_for_bg(color))
            if select_id and select_id in self.cable_types:
                tree.selection_set(select_id)
                tree.see(select_id)

        # ---------------- form load/save
        def load_into_form(cid: str | None) -> None:
            editing_id[0] = cid
            if cid is None:
                id_var.set("")
                label_var.set("")
                cost_var.set("0.10")
                color_var.set("#888888")
                id_entry.config(state="normal")
                id_help.grid()
                form.config(text="Add new cable")
            else:
                ct = self.cable_types.get(cid, {})
                id_var.set(cid)
                label_var.set(ct.get("label", ""))
                cost_var.set(f"{ct.get('cost_per_ft', 0):.4f}")
                color_var.set(ct.get("color", "#888888"))
                id_entry.config(state="disabled")
                id_help.grid_remove()
                form.config(text=f"Editing: {cid}")
            is_dirty[0] = False

        def commit_form() -> bool:
            new_id = id_var.get().strip()
            if not new_id:
                messagebox.showerror("Required", "Cable ID is required")
                id_entry.focus_set()
                return False
            try:
                cost = float(cost_var.get())
            except (ValueError, tk.TclError):
                messagebox.showerror("Invalid", "Cost / ft must be a number")
                return False
            color = (color_var.get() or "#888888").upper()
            label = label_var.get().strip() or new_id
            old_id = editing_id[0]
            if old_id is None and new_id in self.cable_types:
                if not messagebox.askyesno(
                    "Overwrite?",
                    f"A cable named '{new_id}' already exists. Overwrite it?"):
                    return False
            self.cable_types[new_id] = {
                "label": label, "color": color, "cost_per_ft": cost,
            }
            editing_id[0] = new_id
            is_dirty[0] = False
            self._mark_dirty()
            self._refresh_bom_tab()
            self._refresh_symbol_list()
            self._redraw()
            repop(select_id=new_id)
            return True

        def on_select(_e=None):
            sel = tree.selection()
            if not sel:
                return
            if is_dirty[0] and editing_id[0] is not None:
                # Quietly persist the unsaved row before switching away
                if not commit_form():
                    return
            load_into_form(sel[0])
        tree.bind("<<TreeviewSelect>>", on_select)

        # ---------------- buttons
        button_row = ttk.Frame(dlg)
        button_row.pack(fill=tk.X, padx=12, pady=(4, 12))
        ttk.Button(button_row, text="+ Add new cable",
                   command=lambda: (load_into_form(None), id_entry.focus_set())
                   ).pack(side=tk.LEFT)
        save_btn = ttk.Button(button_row, text="Save changes",
                               command=commit_form)
        save_btn.pack(side=tk.LEFT, padx=8)

        def on_delete():
            cid = editing_id[0]
            if cid is None or cid not in self.cable_types:
                return
            if not messagebox.askyesno(
                "Delete cable",
                f"Delete cable type '{cid}'?\n\n"
                "Wires that use this cable will fall back to their device's "
                "default cable in the BOM."):
                return
            self.cable_types.pop(cid, None)
            self._mark_dirty()
            self._refresh_bom_tab()
            self._refresh_symbol_list()
            self._redraw()
            repop()
            # Show the first remaining cable, or empty form
            if self.cable_types:
                first = sorted(self.cable_types.keys())[0]
                tree.selection_set(first)
                load_into_form(first)
            else:
                load_into_form(None)
        ttk.Button(button_row, text="Delete", command=on_delete).pack(side=tk.LEFT, padx=4)
        ttk.Button(button_row, text="Close", command=dlg.destroy).pack(side=tk.RIGHT)

        # Enter in any form field commits
        for w in (id_entry, ):  # easy fallthrough
            pass
        dlg.bind("<Return>", lambda _e: commit_form())

        # Initial state — show the first cable selected
        repop()
        if self.cable_types:
            first = sorted(self.cable_types.keys())[0]
            tree.selection_set(first)
            load_into_form(first)
        else:
            load_into_form(None)

    # ============================================================ FOV / DORI editor
    def _open_fov_dialog_for_selected(self) -> None:
        if self.selected_device is None:
            messagebox.showinfo("Select", "Select a camera device first")
            return
        info = self.symbol_types.get(self.selected_device["type"], {})
        if not info.get("is_camera"):
            messagebox.showinfo("Not a camera",
                                f"{self.selected_device['id']} is not flagged as a camera.")
            return
        self._open_fov_dialog(self.selected_device)

    # ============================================================ Device-list context menu
    def _on_dev_listbox_rclick(self, event) -> None:
        """Right-click anywhere in the Devices list — context menu.

        Preserves multi-selection: if the user right-clicks a row that's
        already part of the multi-selection, we don't replace it. If the
        clicked row isn't in the selection, we switch to single-select on
        that row (standard Windows behavior).
        """
        idx = self.dev_listbox.nearest(event.y)
        if idx < 0 or not hasattr(self, "_dev_listbox_rows"):
            return
        if idx >= len(self._dev_listbox_rows):
            return
        device = self._dev_listbox_rows[idx]
        if device is None:
            # Group header → offer "Select all in group"
            self._show_dev_group_menu(event, idx)
            return
        existing_sel = self.dev_listbox.curselection()
        if idx in existing_sel:
            # Clicked-on row is already part of the multi-selection — keep it
            self._primary_selected = device
        else:
            self.dev_listbox.selection_clear(0, tk.END)
            self.dev_listbox.selection_set(idx)
            self.selected_device = device
        self._redraw()

        info = self.symbol_types.get(device["type"], {})
        wires = [w for w in self.wire_routes if w.get("device_id") == device["id"]]
        wire_total_in = sum(w.get("length_in", 0) or 0 for w in wires)

        m = tk.Menu(self.root, tearoff=0)
        m.add_command(label=f"{device['id']} ({device['type']} – {info.get('label','?')})",
                       state=tk.DISABLED)
        m.add_separator()
        m.add_command(label="📐  Draw wire from here…",
                       command=lambda d=device: self._draw_wire_from(d))
        if wires:
            m.add_command(label=f"🔁  Re-route last wire ({len(wires)} run(s), "
                                 f"{self._format_length(wire_total_in)})",
                           command=lambda d=device: self._reroute_last_wire_from(d))
            m.add_command(label=f"🗑  Clear all wires for this device "
                                 f"({len(wires)} run(s))",
                           command=lambda d=device: self._clear_wires_for_device(d))
        else:
            m.add_command(label="(no wires assigned yet)", state=tk.DISABLED)
        m.add_separator()
        m.add_command(label="🎯  Center on map",
                       command=self._center_on_selected)
        if info.get("is_camera"):
            m.add_command(label="📷  Edit FOV / DORI…",
                           command=lambda d=device: self._open_fov_dialog(d))
        m.add_separator()
        del_label = (f"🗑  Delete {len(self.selected_devices)} selected devices"
                     if len(self.selected_devices) > 1
                     else "🗑  Delete device")
        m.add_command(label=del_label, command=self._on_delete_key,
                       accelerator="Del")
        try:
            m.tk_popup(event.x_root, event.y_root)
        finally:
            m.grab_release()

    def _show_wire_group_menu(self, event, header_idx: int) -> None:
        """Right-click on a group header in the Wires listbox → bulk actions."""
        # Collect contiguous non-None route indices following the header
        group_indices: list[int] = []
        group_rows: list[int] = []
        for i in range(header_idx + 1, len(self._wire_listbox_rows)):
            row = self._wire_listbox_rows[i]
            if row is None:
                break
            group_indices.append(row)
            group_rows.append(i)
        if not group_indices:
            return
        header_text = self.wire_listbox.get(header_idx).strip()
        m = tk.Menu(self.root, tearoff=0)
        m.add_command(label=header_text, state=tk.DISABLED)
        m.add_separator()
        m.add_command(
            label=f"Select all {len(group_indices)} wire(s) in this group",
            command=lambda r=group_rows: self._select_wire_rows(r))
        m.add_command(
            label=f"Delete all {len(group_indices)} wire(s) in this group",
            command=lambda r=group_rows: self._delete_selected_wires_multi_force(r))
        try:
            m.tk_popup(event.x_root, event.y_root)
        finally:
            m.grab_release()

    def _select_wire_rows(self, rows: list[int]) -> None:
        self.wire_listbox.selection_clear(0, tk.END)
        for r in rows:
            self.wire_listbox.selection_set(r)
        if rows:
            self.wire_listbox.see(rows[0])
        self._redraw()

    def _delete_selected_wires_multi_force(self, rows: list[int]) -> None:
        """Same as _delete_selected_wires_multi but skips the per-call
        confirmation since we just collected the group via right-click."""
        self._delete_selected_wires_multi(rows)

    def _show_dev_group_menu(self, event, header_idx: int) -> None:
        """Right-click on a group header in the Devices listbox →
        actions that operate on the whole group."""
        # Find which devices belong to this group: contiguous non-None
        # entries in _dev_listbox_rows immediately after header_idx.
        group_devs: list[dict] = []
        for i in range(header_idx + 1, len(self._dev_listbox_rows)):
            row = self._dev_listbox_rows[i]
            if row is None:
                break
            group_devs.append(row)
        if not group_devs:
            return
        header_text = self.dev_listbox.get(header_idx).strip()
        m = tk.Menu(self.root, tearoff=0)
        m.add_command(label=header_text, state=tk.DISABLED)
        m.add_separator()
        m.add_command(
            label=f"Select all {len(group_devs)} in this group",
            command=lambda devs=group_devs: self._select_devices_group(devs))
        m.add_command(
            label=f"Zoom to fit this group",
            command=lambda devs=group_devs: self._zoom_to_devices(devs))
        try:
            m.tk_popup(event.x_root, event.y_root)
        finally:
            m.grab_release()

    def _select_devices_group(self, devs: list[dict]) -> None:
        self.selected_devices = list(devs)
        self._primary_selected = devs[-1]
        self._flash_status(
            f"Selected all {len(devs)} device(s) in this group.")
        self._redraw()

    def _zoom_to_devices(self, devs: list[dict]) -> None:
        if not devs:
            return
        xs0 = [d["x"] * self.dx_scale for d in devs]
        xs1 = [(d["x"] + d["w"]) * self.dx_scale for d in devs]
        ys0 = [d["y"] * self.dy_scale for d in devs]
        ys1 = [(d["y"] + d["h"]) * self.dy_scale for d in devs]
        self._zoom_to_image_bbox(min(xs0), min(ys0),
                                  max(xs1), max(ys1), pad=120)

    def _clear_wires_for_device(self, device: dict) -> None:
        """Remove every wire associated with this device (undoable)."""
        wires = [w for w in self.wire_routes if w.get("device_id") == device["id"]]
        if not wires:
            return
        total_in = sum(w.get("length_in", 0) or 0 for w in wires)
        if not messagebox.askyesno(
            "Clear wires?",
            f"Remove all {len(wires)} wire run(s) for {device['id']} "
            f"({device['type']}) — total {self._format_length(total_in)}?\n\n"
            "This is undoable (Ctrl+Z)."):
            return
        self._execute(ClearDeviceWiresCmd(self, device["id"]))
        self._post_command_refresh()
        self.status_label.config(
            text=f"Cleared {len(wires)} wire run(s) on {device['id']}. "
                 "Ctrl+Z to undo.")

    # ============================================================ Direct wire entry
    def _draw_wire_from(self, device: dict) -> None:
        """Switch to Wire Mode with `device` as the source, with first point
        already placed at the device's center. User just clicks subsequent
        route points and right-clicks to finish."""
        self.selected_device = device
        self._set_mode(Mode.WIRE)
        # Seed first point at device center (in image coords)
        cx, cy = self._drawio_to_img(device["x"] + device["w"] / 2,
                                     device["y"] + device["h"] / 2)
        self.current_wire_points = [(cx, cy)]
        info = self.symbol_types.get(device["type"], {})
        self.status_label.config(
            text=f"Drawing wire from {device['id']} ({info.get('label','?')}) — "
                 f"click route points, right-click to finish")
        self._redraw()
        self.canvas.focus_set()

    def _reroute_last_wire_from(self, device: dict) -> None:
        """Find this device's most recent wire, delete it, and start a fresh
        wire draw from the device. Useful when you want to revise a route."""
        wires = [(i, w) for i, w in enumerate(self.wire_routes)
                 if w.get("device_id") == device["id"]]
        if not wires:
            messagebox.showinfo(
                "No wire yet",
                f"No wire is associated with {device['id']}. "
                "Use 'Draw wire from here…' to create one.")
            self._draw_wire_from(device)
            return
        idx, route = wires[-1]
        if not messagebox.askyesno(
            "Replace wire?",
            f"Delete the most recent wire on {device['id']} "
            f"({len(route['points'])} points, "
            f"{self._format_length(route.get('length_in') or 0)}) "
            "and start a new one?"):
            return
        self._execute(DeleteWireCmd(self, idx, route))
        self._post_command_refresh()
        self._draw_wire_from(device)

    # ============================================================ FOV bulk actions
    def _apply_default_fov_all_cameras(self) -> None:
        """Set the per-type default FOV on every camera that doesn't
        already have one. Existing per-camera FOVs are preserved."""
        camera_count = 0
        applied = 0
        skipped = 0
        for d in self.devices:
            info = self.symbol_types.get(d["type"], {})
            if not info.get("is_camera"):
                continue
            camera_count += 1
            if d.get("fov"):
                skipped += 1
                continue
            d["fov"] = default_fov_for_type(d["type"])
            applied += 1
        if applied == 0 and skipped > 0:
            self._flash_status(
                f"All {camera_count} cameras already have FOV configured.",
                ttl_ms=3500)
        elif applied == 0:
            self._flash_status(
                "No cameras on this floor — place some cameras first.",
                ttl_ms=3500)
        else:
            self._mark_dirty()
            self._show_fov.set(True)   # auto-enable so the user sees them
            self._redraw()
            msg = f"Applied default FOV to {applied} camera(s)."
            if skipped:
                msg += f" Kept existing FOV on {skipped}."
            self._flash_status(msg, ttl_ms=3500)

    def _clear_fov_all_cameras(self) -> None:
        """Remove all per-camera FOV configurations (cones revert to
        type defaults via the fallback path in _draw_fov_cones)."""
        n_have = sum(1 for d in self.devices if d.get("fov"))
        if n_have == 0:
            self._flash_status("No camera-specific FOVs to clear.")
            return
        if not messagebox.askyesno(
            "Clear FOV configs?",
            f"Remove FOV configuration from {n_have} camera(s)?\n\n"
            "(They'll revert to the type defaults — cones still show "
            "when 'Show FOV Cones' is on.)"):
            return
        for d in self.devices:
            d.pop("fov", None)
        self._mark_dirty()
        self._redraw()
        self._flash_status(f"Cleared FOV from {n_have} camera(s).")

    def _apply_default_fov_to_one(self, device: dict) -> None:
        """Apply the type-default FOV to a single camera, overwriting any
        existing per-camera FOV (so this also acts as 'reset to default')."""
        info = self.symbol_types.get(device["type"], {})
        if not info.get("is_camera"):
            return
        device["fov"] = default_fov_for_type(device["type"])
        self._show_fov.set(True)
        self._mark_dirty()
        self._redraw()
        self._flash_status(
            f"Applied default FOV to {device['id']} ({device['type']}). "
            f"Right-click → Edit FOV to fine-tune.")

    def _toggle_camera_fov(self, device: dict) -> None:
        fov = device.get("fov") or dict(lenses_mod.DEFAULT_FOV)
        fov["show"] = not fov.get("show", True)
        device["fov"] = fov
        self._show_fov.set(True)
        self._mark_dirty()
        self._redraw()

    def _open_fov_dialog(self, device: dict) -> None:
        existing = device.get("fov") or dict(lenses_mod.DEFAULT_FOV)
        fov = dict(existing)

        dlg = tk.Toplevel(self.root)
        dlg.title(f"FOV / DORI — {device['id']}")
        dlg.geometry("560x540")
        dlg.transient(self.root); dlg.grab_set()

        ttk.Label(dlg, text=f"Camera {device['id']} ({device['type']})",
                  font=("Segoe UI", 11, "bold")).pack(anchor=tk.W, padx=10, pady=(10, 4))

        form = ttk.Frame(dlg); form.pack(fill=tk.X, padx=10, pady=4)
        row = 0

        ttk.Label(form, text="Lens (mm):").grid(row=row, column=0, sticky=tk.W, pady=2)
        lens_var = tk.DoubleVar(value=float(fov.get("lens_mm", 4.0)))
        lens_combo = ttk.Combobox(form, textvariable=lens_var, width=10,
                                   values=[str(v) for v in lenses_mod.LENS_PRESETS_MM])
        lens_combo.grid(row=row, column=1, sticky=tk.W, padx=4); row += 1

        ttk.Label(form, text="Sensor:").grid(row=row, column=0, sticky=tk.W, pady=2)
        sensor_var = tk.StringVar(value=fov.get("sensor", '1/2.8"'))
        ttk.Combobox(form, textvariable=sensor_var, width=10,
                     values=list(lenses_mod.SENSORS.keys()),
                     state="readonly").grid(row=row, column=1, sticky=tk.W, padx=4); row += 1

        ttk.Label(form, text="Resolution:").grid(row=row, column=0, sticky=tk.W, pady=2)
        res_var = tk.StringVar(value=next(
            (k for k, v in lenses_mod.RESOLUTIONS.items()
             if list(v) == list(fov.get("resolution") or [])), "4K (UHD)"))
        ttk.Combobox(form, textvariable=res_var, width=12,
                     values=list(lenses_mod.RESOLUTIONS.keys()),
                     state="readonly").grid(row=row, column=1, sticky=tk.W, padx=4); row += 1

        ttk.Label(form, text="Mount height (ft):").grid(row=row, column=0, sticky=tk.W, pady=2)
        mh_var = tk.DoubleVar(value=float(fov.get("mount_height_ft", 9.0)))
        ttk.Entry(form, textvariable=mh_var, width=10).grid(row=row, column=1, sticky=tk.W, padx=4); row += 1

        ttk.Label(form, text="Tilt (° down):").grid(row=row, column=0, sticky=tk.W, pady=2)
        tilt_var = tk.DoubleVar(value=float(fov.get("tilt_deg", 15.0)))
        ttk.Entry(form, textvariable=tilt_var, width=10).grid(row=row, column=1, sticky=tk.W, padx=4); row += 1

        ttk.Label(form, text="Azimuth (°, 0=up):").grid(row=row, column=0, sticky=tk.W, pady=2)
        az_var = tk.DoubleVar(value=float(fov.get("azimuth_deg", 0.0)))
        ttk.Scale(form, from_=0, to=360, variable=az_var, orient=tk.HORIZONTAL,
                  length=200).grid(row=row, column=1, sticky=tk.W, padx=4)
        ttk.Entry(form, textvariable=az_var, width=8).grid(row=row, column=2, padx=4); row += 1

        show_var = tk.BooleanVar(value=bool(fov.get("show", True)))
        ttk.Checkbutton(form, text="Show this camera's FOV cone on map",
                        variable=show_var).grid(row=row, column=0, columnspan=3, sticky=tk.W, pady=4); row += 1

        # DORI distance preview
        preview = ttk.LabelFrame(dlg, text="DORI distances (computed live)")
        preview.pack(fill=tk.X, padx=10, pady=8)
        preview_label = ttk.Label(preview, text="—", font=("Consolas", 10),
                                   justify=tk.LEFT)
        preview_label.pack(anchor=tk.W, padx=8, pady=6)

        def update_preview(*_):
            try:
                cur = {
                    "lens_mm": float(lens_var.get()),
                    "sensor": sensor_var.get(),
                    "resolution": list(lenses_mod.RESOLUTIONS.get(res_var.get(),
                                                                  [1920, 1080])),
                    "mount_height_ft": float(mh_var.get()),
                    "azimuth_deg": float(az_var.get()),
                }
                cone = fov_mod.compute_fov(cur)
                lines = [f"Horizontal AOV: {cone.aov_h_deg:.1f}°    "
                         f"Vertical AOV: {cone.aov_v_deg:.1f}°"]
                for r in ("D", "O", "R", "I"):
                    d_in = cone.distances_in[r]
                    d_ft = d_in / 12
                    lines.append(f"  {r}  {fov_mod.DORI_LABELS[r]:14s}  {d_ft:6.1f} ft")
                preview_label.config(text="\n".join(lines))
            except (ValueError, tk.TclError):
                preview_label.config(text="(invalid input)")
        for v in (lens_var, sensor_var, res_var, mh_var, az_var):
            v.trace_add("write", update_preview)
        update_preview()

        # Quick presets
        presets_frame = ttk.LabelFrame(dlg, text="Quick presets")
        presets_frame.pack(fill=tk.X, padx=10, pady=4)
        for i, (name, p) in enumerate(lenses_mod.INSTALL_PRESETS.items()):
            def apply_preset(pp=p):
                lens_var.set(pp["lens_mm"])
                tilt_var.set(pp["tilt_deg"])
                mh_var.set(pp["mount_height_ft"])
            ttk.Button(presets_frame, text=name, command=apply_preset).grid(
                row=i // 4, column=i % 4, padx=4, pady=2, sticky=tk.W)

        btns = ttk.Frame(dlg); btns.pack(fill=tk.X, padx=10, pady=8)
        def _settings_dict(include_az: bool = True) -> dict:
            d = {
                "enabled": True,
                "show": show_var.get(),
                "lens_mm": float(lens_var.get()),
                "sensor": sensor_var.get(),
                "resolution": list(lenses_mod.RESOLUTIONS.get(res_var.get(),
                                                              [1920, 1080])),
                "mount_height_ft": float(mh_var.get()),
                "tilt_deg": float(tilt_var.get()),
            }
            if include_az:
                d["azimuth_deg"] = float(az_var.get())
            return d

        def apply():
            old_fov = dict(device["fov"]) if device.get("fov") else None
            new_fov = _settings_dict(include_az=True)
            self._execute(EditFovCmd(self, [(device, old_fov, new_fov)]))
            self._show_fov.set(True)
            self._post_command_refresh()
            dlg.destroy()

        def apply_to_type():
            """Push the lens/sensor/resolution/mount/tilt settings to ALL
            cameras of this type. Preserves each camera's own azimuth_deg
            (which is intentionally per-camera since they point different
            ways). Toggling 'show' is also per-camera."""
            same_type = [d for d in self.devices if d["type"] == device["type"]]
            if len(same_type) <= 1:
                apply()  # nothing else to propagate to; just save this one
                return
            if not messagebox.askyesno(
                "Apply to all?",
                f"Update FOV settings for ALL {len(same_type)} cameras of "
                f"type {device['type']}?\n\n"
                "(Lens, sensor, resolution, mount-height, and tilt will be "
                "pushed. Each camera keeps its own azimuth and show-flag.)"):
                return
            shared = _settings_dict(include_az=False)
            entries: list[tuple[dict, dict | None, dict]] = []
            for d in same_type:
                old_raw = d.get("fov")
                old_snapshot = dict(old_raw) if old_raw else None
                base = old_raw or default_fov_for_type(d["type"])
                merged = {**base, **shared}
                merged["azimuth_deg"] = base.get("azimuth_deg", 0.0)
                if "show" in base:
                    merged["show"] = base["show"]
                if d is device:
                    merged["azimuth_deg"] = float(az_var.get())
                entries.append((d, old_snapshot, merged))
            self._execute(EditFovCmd(self, entries))
            self._show_fov.set(True)
            self._post_command_refresh()
            self._flash_status(
                f"Applied FOV settings to all {len(same_type)} "
                f"{device['type']} cameras. Ctrl+Z to undo.",
                ttl_ms=3500)
            dlg.destroy()

        ttk.Button(btns, text="Apply", command=apply).pack(side=tk.LEFT)
        if any(d["type"] == device["type"] and d["id"] != device["id"]
                for d in self.devices):
            ttk.Button(
                btns,
                text=f"Apply to all {device['type']} cameras…",
                command=apply_to_type,
            ).pack(side=tk.LEFT, padx=4)
        ttk.Button(btns, text="Cancel", command=dlg.destroy).pack(side=tk.LEFT, padx=4)

    # ============================================================ Cable resolution
    def _resolve_cable_for_pair(self, src: dict, dst: dict | None) -> str:
        """Pick the cable type for a wire run between two endpoints.

        Rule: prefer the *device's* cable over a run point's cable. If
        neither endpoint is a run point, prefer the source's. If both
        are run points, prefer the source's (arbitrary but consistent).
        """
        src_info = self.symbol_types.get(src["type"], {})
        src_cable = (src_info.get("cable_type")
                     or bom_mod.default_cable_for(src_info))
        if dst is None:
            return src_cable
        dst_info = self.symbol_types.get(dst["type"], {})
        dst_cable = (dst_info.get("cable_type")
                     or bom_mod.default_cable_for(dst_info))
        src_rp = bool(src_info.get("is_run_point"))
        dst_rp = bool(dst_info.get("is_run_point"))
        if src_rp and not dst_rp:
            return dst_cable    # the actual device wins over the run point
        if dst_rp and not src_rp:
            return src_cable
        return src_cable        # both regular OR both run points → source

    # ============================================================ Multi-floor
    def _refresh_floor_menu(self) -> None:
        if not hasattr(self, "_floor_menu"):
            return
        m = self._floor_menu
        m.delete(0, tk.END)
        proj = self._project
        if proj is None or not proj.floors:
            m.add_command(label="(single-floor mode)", state=tk.DISABLED)
            m.add_separator()
        else:
            for f in proj.floors:
                marker = "● " if f.id == proj.active_floor else "  "
                m.add_command(label=f"{marker}{f.id} — {f.name}",
                              command=lambda fid=f.id: self._switch_floor(fid))
            m.add_separator()
        m.add_command(label="Add Floor…", command=self._add_floor_dialog)
        m.add_command(label="Align Floor to Reference…",
                      command=self._open_alignment_dialog)
        m.add_command(label="Manage Floors…", command=self._open_floor_manager)

    def _add_floor_dialog(self) -> None:
        path = filedialog.askopenfilename(
            title="Pick .drawio for new floor",
            initialdir=os.path.dirname(self.drawio_path),
            filetypes=[("Draw.io files", "*.drawio")])
        if not path:
            return
        if self._project is None:
            # Bootstrap into multi-floor mode by capturing current floor as F1
            self._project = mf_mod.ProjectState(
                floors=[mf_mod.FloorState(
                    id="F1", name="First Floor", elevation_ft=0,
                    drawio_file=os.path.basename(self.drawio_path),
                )],
            )
        existing_ids = {f.id for f in self._project.floors}
        n = 2
        while f"F{n}" in existing_ids:
            n += 1
        new_id = f"F{n}"

        dlg = tk.Toplevel(self.root); dlg.transient(self.root); dlg.grab_set()
        dlg.title("New floor"); dlg.geometry("360x180")
        ttk.Label(dlg, text=f"Floor ID: {new_id}").pack(anchor=tk.W, padx=10, pady=4)
        ttk.Label(dlg, text="Name:").pack(anchor=tk.W, padx=10)
        name_var = tk.StringVar(value=os.path.splitext(os.path.basename(path))[0])
        ttk.Entry(dlg, textvariable=name_var, width=40).pack(padx=10, pady=2)
        ttk.Label(dlg, text="Elevation (ft):").pack(anchor=tk.W, padx=10)
        elev_var = tk.DoubleVar(value=14.0 * (n - 1))
        ttk.Entry(dlg, textvariable=elev_var, width=10).pack(anchor=tk.W, padx=10, pady=2)

        # Make path relative if possible (project file's directory)
        try:
            rel = os.path.relpath(path, os.path.dirname(self.drawio_path))
        except ValueError:
            rel = path

        def add():
            new_floor = mf_mod.FloorState(
                id=new_id, name=name_var.get().strip() or new_id,
                elevation_ft=float(elev_var.get()),
                drawio_file=rel,
            )
            self._project.floors.append(new_floor)
            self._mark_dirty()
            self._refresh_floor_menu()
            dlg.destroy()
            if messagebox.askyesno("Switch?", f"Switch to {new_id} now?"):
                self._switch_floor(new_id)

        ttk.Button(dlg, text="Add", command=add).pack(pady=8)

    def _switch_floor(self, floor_id: str) -> None:
        if self._project is None:
            return
        target = self._project.get_floor(floor_id)
        if target is None or floor_id == self._project.active_floor:
            return

        if self._dirty and not self._confirm_discard():
            return

        # Save active floor's wires back to project
        active = self._project.get_floor(self._project.active_floor)
        if active is not None:
            active.wire_routes = []
            for wr in self.wire_routes:
                e = {"points": [list(p) for p in wr["points"]],
                     "device_id": wr.get("device_id")}
                if wr.get("cable_type"):
                    e["cable_type"] = wr["cable_type"]
                active.wire_routes.append(e)

        # Resolve target's drawio path (relative or absolute)
        if os.path.isabs(target.drawio_file):
            new_path = target.drawio_file
        else:
            new_path = os.path.normpath(
                os.path.join(os.path.dirname(self.drawio_path), target.drawio_file))
        if not os.path.exists(new_path):
            messagebox.showerror("Missing file",
                                 f"Drawio file not found:\n{new_path}")
            return

        # Reload — relaunch the tool with the new file (cleaner than reparenting state)
        self._save_project()  # persist the v2 project
        # Hand the active_floor change off to the new instance via the project file:
        self._project.active_floor = floor_id
        try:
            mf_mod.save_project(self._project, self.project_path)
        except OSError:
            pass
        self.root.destroy()
        new_root = tk.Tk()
        apply_theme(new_root)
        new_root.title("Camera Wire Estimation Tool")
        CameraWireTool(new_root, new_path)
        new_root.mainloop()

    def _open_alignment_dialog(self) -> None:
        if self._project is None or len(self._project.floors) < 2:
            messagebox.showinfo("Alignment",
                                "Add a second floor first (Floors menu → Add Floor).")
            return
        active = self._project.get_floor(self._project.active_floor)
        if active is None:
            return

        dlg = tk.Toplevel(self.root); dlg.transient(self.root); dlg.grab_set()
        dlg.title(f"Align floor {active.id} to reference")
        dlg.geometry("520x320")

        ttk.Label(dlg, text=
            f"Pick a reference floor and click 2 anchor points (e.g. NW and NE\n"
            f"corners of the building) on each. The transform that maps this floor's\n"
            f"anchors to the reference's anchors becomes its alignment.",
            justify=tk.LEFT).pack(anchor=tk.W, padx=10, pady=8)

        ref_var = tk.StringVar(value=self._project.floors[0].id
                               if self._project.floors[0].id != active.id
                               else self._project.floors[1].id)
        ttk.Label(dlg, text="Reference floor:").pack(anchor=tk.W, padx=10)
        ttk.Combobox(dlg, textvariable=ref_var,
                     values=[f.id for f in self._project.floors if f.id != active.id],
                     state="readonly", width=20).pack(anchor=tk.W, padx=10, pady=2)

        info_label = ttk.Label(
            dlg,
            text="Status: anchors not picked.\n\n"
                 "Tip: anchor points are stored per-floor. To re-align, just pick "
                 "new ones. The transform updates automatically.",
            font=("Segoe UI", 9), justify=tk.LEFT)
        info_label.pack(anchor=tk.W, padx=10, pady=8)

        def show_current():
            a = active.alignment
            info_label.config(
                text=f"Current alignment for {active.id}:\n"
                     f"  translation: ({a['tx']:.1f}, {a['ty']:.1f}) px\n"
                     f"  scale:       {a['scale']:.4f}\n"
                     f"  rotation:    {a['rotation_deg']:.2f}°\n"
                     f"  anchors:     {len(active.anchor_points)} stored")
        show_current()

        def reset():
            active.alignment = dict(mf_mod.IDENTITY_ALIGNMENT)
            active.anchor_points = []
            self._mark_dirty()
            show_current()
            messagebox.showinfo("Reset", f"{active.id} reset to identity transform.")

        def manual():
            # Quick manual nudge dialog using arrow keys
            n = tk.Toplevel(dlg); n.transient(dlg); n.grab_set()
            n.title("Manual nudge")
            n.geometry("320x180")
            ttk.Label(n, text="Use these buttons to nudge the alignment.\n"
                              "1px steps; ×10 with shift modifier.").pack(padx=10, pady=8)
            grid = ttk.Frame(n); grid.pack(pady=6)
            def nudge(dx, dy):
                active.alignment["tx"] += dx
                active.alignment["ty"] += dy
                self._mark_dirty()
                show_current()
            ttk.Button(grid, text="↑",  width=4, command=lambda: nudge(0, -1)).grid(row=0, column=1)
            ttk.Button(grid, text="←",  width=4, command=lambda: nudge(-1, 0)).grid(row=1, column=0)
            ttk.Button(grid, text="↓",  width=4, command=lambda: nudge(0, 1)).grid(row=1, column=1)
            ttk.Button(grid, text="→",  width=4, command=lambda: nudge(1, 0)).grid(row=1, column=2)
            ttk.Button(grid, text="↑10", width=4, command=lambda: nudge(0, -10)).grid(row=2, column=1)
            ttk.Button(grid, text="←10", width=4, command=lambda: nudge(-10, 0)).grid(row=3, column=0)
            ttk.Button(grid, text="↓10", width=4, command=lambda: nudge(0, 10)).grid(row=3, column=1)
            ttk.Button(grid, text="→10", width=4, command=lambda: nudge(10, 0)).grid(row=3, column=2)

        def pick_anchors():
            ref = self._project.get_floor(ref_var.get())
            if ref is None:
                messagebox.showerror("Error", "Pick a valid reference floor"); return
            messagebox.showinfo(
                "Alignment via anchors",
                f"On {active.id}, click two reference points on the map. Then on "
                f"{ref.id} (you'll be prompted to switch), click the SAME two "
                f"physical points. The system computes the alignment transform.")
            # Capture mode for current floor
            self._floor_anchors_picking = []
            self._floor_anchor_target = (active, ref)
            self.status_label.config(
                text=f"ANCHOR: click 2 reference points on {active.id}")
            dlg.destroy()

        btns = ttk.Frame(dlg); btns.pack(side=tk.BOTTOM, pady=10)
        ttk.Button(btns, text="Pick anchor points (this floor)",
                   command=pick_anchors).pack(side=tk.LEFT, padx=4)
        ttk.Button(btns, text="Manual nudge…", command=manual).pack(side=tk.LEFT, padx=4)
        ttk.Button(btns, text="Reset", command=reset).pack(side=tk.LEFT, padx=4)
        ttk.Button(btns, text="Close", command=dlg.destroy).pack(side=tk.LEFT, padx=4)

    def _open_floor_manager(self) -> None:
        if self._project is None or not self._project.floors:
            messagebox.showinfo("Floors", "No floors yet — use Add Floor.")
            return
        dlg = tk.Toplevel(self.root); dlg.transient(self.root); dlg.grab_set()
        dlg.title("Manage floors"); dlg.geometry("520x320")
        cols = ("id", "name", "elevation", "drawio")
        tree = ttk.Treeview(dlg, columns=cols, show="headings", height=8)
        for c, w, h in (("id", 60, "ID"), ("name", 140, "Name"),
                         ("elevation", 90, "Elev. (ft)"), ("drawio", 220, "Drawio")):
            tree.heading(c, text=h); tree.column(c, width=w)
        for f in self._project.floors:
            tree.insert("", tk.END, iid=f.id,
                        values=(f.id, f.name, f"{f.elevation_ft:.1f}",
                                f.drawio_file))
        tree.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        ttk.Button(dlg, text="Close", command=dlg.destroy).pack(pady=6)

    # ============================================================ Export
    def _export_bom_csv(self) -> None:
        path = filedialog.asksaveasfilename(
            title="Export BOM as CSV",
            defaultextension=".csv",
            initialfile=f"{os.path.splitext(os.path.basename(self.drawio_path))[0]}_bom.csv",
            filetypes=[("CSV", "*.csv")])
        if not path:
            return
        try:
            bom_mod.write_csv(self._build_bom_report(), path,
                              currency=self._bom_currency_var.get() or "$")
            messagebox.showinfo("Exported", f"BOM written:\n{path}")
        except OSError as e:
            messagebox.showerror("Export failed", str(e))

    def _export_csv_bundle(self) -> None:
        if not _HAS_EXPORT:
            messagebox.showwarning("Unavailable", "Export module not available.")
            return
        directory = filedialog.askdirectory(title="Export CSV bundle into folder")
        if not directory:
            return
        try:
            paths = export_mod.write_bundle_csv(
                directory,
                devices=self.devices,
                symbol_types=self.symbol_types,
                wire_routes=self.wire_routes,
                bom_report=self._build_bom_report(),
                format_length=self._format_length,
                currency=self._bom_currency_var.get() or "$",
            )
            messagebox.showinfo("Exported", "Wrote:\n" + "\n".join(paths))
        except OSError as e:
            messagebox.showerror("Export failed", str(e))

    def _export_map_png(self) -> None:
        """Save a PNG snapshot of the canvas — what's currently visible
        on the map (devices + wires + legend, but not the mode banner /
        mini-map / hover popups)."""
        path = filedialog.asksaveasfilename(
            title="Save map snapshot",
            defaultextension=".png",
            initialfile=f"{os.path.splitext(os.path.basename(self.drawio_path))[0]}_map.png",
            filetypes=[("PNG", "*.png")],
        )
        if not path:
            return
        # Ephemerally hide on-canvas chrome so the snapshot is clean
        self.canvas.delete("hover_info")
        was_minimap_built = self._minimap_built
        try:
            self._minimap_photo = None       # skip mini-map render
            self._minimap_built = True
            saved_show_walls = False         # (no walls to hide here, just safe)
            self._redraw()
            # Use Win32 PrintWindow against the canvas widget so we capture
            # exactly what's on the canvas, even if other windows overlap.
            try:
                import ctypes
                from PIL import Image
                hwnd = ctypes.windll.user32.GetParent(self.canvas.winfo_id())
                # Dimensions of the Tk root, since canvas is embedded
                rect_root = self._win_rect(hwnd)
                # Compute the canvas's screen position to crop after capture
                cx = self.canvas.winfo_rootx()
                cy = self.canvas.winfo_rooty()
                cw = self.canvas.winfo_width()
                ch = self.canvas.winfo_height()
                full = self._capture_root_window()
                if full is None:
                    raise RuntimeError("PrintWindow returned no bitmap")
                # Translate root-window coords → bitmap coords
                root_x = rect_root[0]; root_y = rect_root[1]
                cb = full.crop((cx - root_x, cy - root_y,
                                 cx - root_x + cw, cy - root_y + ch))
                cb.save(path)
            except Exception as e:
                log_error("Map snapshot failed", e)
                messagebox.showerror("Export failed", str(e))
                return
        finally:
            self._minimap_built = was_minimap_built
            self._build_minimap_image()
            self._redraw()
        self._flash_status(f"✓ Saved map snapshot to {os.path.basename(path)}",
                            ttl_ms=3500)

    @staticmethod
    def _win_rect(hwnd):
        import ctypes
        from ctypes import wintypes
        class R(ctypes.Structure):
            _fields_ = [("L", wintypes.LONG), ("T", wintypes.LONG),
                        ("R", wintypes.LONG), ("B", wintypes.LONG)]
        r = R()
        ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(r))
        return (r.L, r.T, r.R, r.B)

    def _capture_root_window(self):
        """Snapshot the Tk root window via Win32 PrintWindow — works even
        when other apps overlap. Returns a PIL.Image or None."""
        try:
            import ctypes
            from PIL import Image
            user32 = ctypes.windll.user32
            gdi32 = ctypes.windll.gdi32
            hwnd_root = user32.GetParent(self.canvas.winfo_id())
            l, t, r, b = self._win_rect(hwnd_root)
            w, h = r - l, b - t
            if w <= 0 or h <= 0:
                return None
            hdc = user32.GetDC(0)
            hdc_mem = gdi32.CreateCompatibleDC(hdc)
            hbm = gdi32.CreateCompatibleBitmap(hdc, w, h)
            gdi32.SelectObject(hdc_mem, hbm)
            user32.PrintWindow(hwnd_root, hdc_mem, 2)

            class BITMAPINFOHEADER(ctypes.Structure):
                _fields_ = [
                    ("biSize", ctypes.c_uint32),
                    ("biWidth", ctypes.c_int32),
                    ("biHeight", ctypes.c_int32),
                    ("biPlanes", ctypes.c_uint16),
                    ("biBitCount", ctypes.c_uint16),
                    ("biCompression", ctypes.c_uint32),
                    ("biSizeImage", ctypes.c_uint32),
                    ("biXPelsPerMeter", ctypes.c_int32),
                    ("biYPelsPerMeter", ctypes.c_int32),
                    ("biClrUsed", ctypes.c_uint32),
                    ("biClrImportant", ctypes.c_uint32),
                ]
            class BMI(ctypes.Structure):
                _fields_ = [("h", BITMAPINFOHEADER), ("c", ctypes.c_uint32 * 3)]
            bmi = BMI()
            bmi.h.biSize = ctypes.sizeof(BITMAPINFOHEADER)
            bmi.h.biWidth = w; bmi.h.biHeight = -h
            bmi.h.biPlanes = 1; bmi.h.biBitCount = 32
            buf = (ctypes.c_uint8 * (w * h * 4))()
            gdi32.GetDIBits(hdc_mem, hbm, 0, h, buf, ctypes.byref(bmi), 0)
            img = Image.frombuffer("RGBA", (w, h), bytes(buf), "raw", "BGRA", 0, 1)
            gdi32.DeleteObject(hbm)
            gdi32.DeleteDC(hdc_mem)
            user32.ReleaseDC(0, hdc)
            return img
        except Exception as e:
            log_error("Capture-root-window failed", e)
            return None

    def _export_pdf(self) -> None:
        if not _HAS_EXPORT or not export_mod._HAS_REPORTLAB:
            messagebox.showwarning(
                "PDF unavailable",
                "PDF export requires reportlab. `pip install reportlab` then restart.")
            return
        path = filedialog.asksaveasfilename(
            title="Export PDF report",
            defaultextension=".pdf",
            initialfile=f"{os.path.splitext(os.path.basename(self.drawio_path))[0]}_report.pdf",
            filetypes=[("PDF", "*.pdf")])
        if not path:
            return
        try:
            project_name = (self._project.project_name if self._project else "")
            project_name = project_name or os.path.splitext(
                os.path.basename(self.drawio_path))[0]
            export_mod.write_pdf_report(
                path,
                project_name=project_name,
                devices=self.devices,
                symbol_types=self.symbol_types,
                wire_routes=self.wire_routes,
                bom_report=self._build_bom_report(),
                risers=(self._project.risers if self._project else []),
                floor_label=(self._project.active_floor if self._project else None),
                scale_factor=self.scale_factor,
                format_length=self._format_length,
                currency=self._bom_currency_var.get() or "$",
            )
            messagebox.showinfo("Exported", f"PDF written:\n{path}")
        except Exception as e:
            log_error("PDF export failed", e)
            messagebox.showerror("Export failed", str(e))

    # ============================================================ FOV rendering helper
    def _draw_fov_cones(self) -> None:
        """Render DORI cones for any cameras with `fov` configured.

        Uses inches_per_pixel = 1/scale_factor to convert DORI radii (inches)
        into image-pixel coords. Falls back to 0.5 if no scale set.
        """
        # Skip rendering entirely at very low zoom — cones become unreadable
        # noise and dominate render time. User can zoom in to see them.
        if self.zoom < 0.18:
            self._fov_handles = {}
            return
        # Reset handle cache; it gets repopulated as we draw cones.
        self._fov_handles = {}
        ipp = (1.0 / self.scale_factor) if self.scale_factor else 0.5
        # 'Only selected' filter: when active and there's a single primary
        # selection that's a camera, render only that one's cone.
        only_selected = self._show_only_selected_fov.get()
        sel_id = (self._primary_selected["id"]
                  if self._primary_selected is not None else None)
        # Viewport culling — skip cones whose camera is far outside the
        # visible canvas region. Cones extend FROM the camera, so we still
        # render any camera that could project a cone into the view.
        cw = self.canvas.winfo_width() or 800
        ch = self.canvas.winfo_height() or 600
        ix0, iy0 = self._canvas_to_img(0, 0)
        ix1, iy1 = self._canvas_to_img(cw, ch)
        slack = max((ix1 - ix0), (iy1 - iy0))
        for d in self.devices:
            info = self.symbol_types.get(d["type"], {})
            if not info.get("is_camera"):
                continue
            if only_selected and (sel_id is None or d["id"] != sel_id):
                continue
            cam_ix = (d["x"] + d["w"]/2) * self.dx_scale
            cam_iy = (d["y"] + d["h"]/2) * self.dy_scale
            if not (ix0 - slack <= cam_ix <= ix1 + slack
                    and iy0 - slack <= cam_iy <= iy1 + slack):
                continue
            f = d.get("fov")
            # If the camera doesn't have its own FOV configured, fall back
            # to the per-type preset so toggling 'Show FOV' shows ALL
            # cameras' coverage immediately, even on a fresh project.
            if not f:
                f = default_fov_for_type(d["type"])
            elif not f.get("show", True):
                continue
            cone = fov_mod.compute_fov(f)
            # Camera apex in image coords
            apex_ix, apex_iy = self._drawio_to_img(
                d["x"] + d["w"]/2, d["y"] + d["h"]/2)
            # Draw rings outermost-to-innermost so inner rings overlay correctly
            for ring in ("D", "O", "R", "I"):
                if ring != "D" and not self._show_dori.get():
                    # When DORI rings disabled, draw only Detection (the outer cone)
                    continue
                radius_in = cone.distances_in[ring]
                if radius_in <= 0:
                    continue
                poly_img = fov_mod.cone_polygon(
                    cone,
                    center_x=apex_ix, center_y=apex_iy,
                    radius_in=radius_in,
                    inches_per_pixel=ipp,
                    azimuth_deg=cone.azimuth_deg,
                )
                if len(poly_img) < 3:
                    continue
                pts_canvas: list[float] = []
                for ix, iy in poly_img:
                    cx, cy = self._img_to_canvas(ix, iy)
                    pts_canvas.extend([cx, cy])
                color = fov_mod.DORI_COLORS[ring]
                self.canvas.create_polygon(
                    pts_canvas, fill=color, outline=color, width=1,
                    stipple="gray25",  # semi-transparent feel
                    tags=("fov", f"fov_{d['id']}"),
                )
            # Draw the rotation handle — only on the SELECTED camera (or
            # when "Show only selected" is on, which already filtered above).
            # Handle sits at the tip of the cone's center axis, at the
            # Detection radius. Drag it to rotate.
            sel_id_now = (self._primary_selected["id"]
                          if self._primary_selected is not None else None)
            if d["id"] == sel_id_now or only_selected:
                d_radius_in = cone.distances_in.get("D", 0)
                if d_radius_in > 0:
                    radius_px = d_radius_in / ipp
                    az_rad = math.radians(cone.azimuth_deg)
                    tip_ix = apex_ix + math.sin(az_rad) * radius_px
                    tip_iy = apex_iy - math.cos(az_rad) * radius_px
                    cx_, cy_ = self._img_to_canvas(tip_ix, tip_iy)
                    apex_cx, apex_cy = self._img_to_canvas(apex_ix, apex_iy)
                    # Line from apex to handle, then a circle handle
                    self.canvas.create_line(
                        apex_cx, apex_cy, cx_, cy_,
                        fill="#FFFFFF", width=2, dash=(4, 3),
                        tags=("fov", "fov_axis", f"fov_axis_{d['id']}"),
                    )
                    self.canvas.create_oval(
                        cx_-9, cy_-9, cx_+9, cy_+9,
                        fill="#FFFFFF", outline="#202060", width=2,
                        tags=("fov", "fov_handle", f"fov_handle_{d['id']}"),
                    )
                    # Cache the handle position (image coords + camera apex)
                    # for hit-testing in _on_left_press.
                    self._fov_handles[d["id"]] = (cx_, cy_, apex_cx, apex_cy)
                    # Floating azimuth readout
                    dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
                    compass = dirs[round(cone.azimuth_deg / 45) % 8]
                    self.canvas.create_text(
                        cx_+14, cy_+14,
                        text=f"{int(cone.azimuth_deg)}° ({compass})",
                        fill="#FFFFFF", font=("Segoe UI", 9, "bold"),
                        anchor=tk.W,
                        tags=("fov", f"fov_label_{d['id']}"),
                    )


# ============================================================ Entry point
def _discover_drawio_files() -> list[str]:
    """Search the standard locations (data/, then script dir) for .drawio files."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    candidates: list[str] = []
    for sub in ("data", "."):
        d = os.path.join(script_dir, sub)
        if not os.path.isdir(d):
            continue
        for f in sorted(os.listdir(d)):
            if f.endswith(".drawio"):
                candidates.append(os.path.normpath(os.path.join(d, f)))
    return candidates


def main() -> None:
    root = tk.Tk()
    apply_theme(root)
    root.title("Camera Wire Estimation Tool")

    drawio_files = _discover_drawio_files()
    script_dir = os.path.dirname(os.path.abspath(__file__))

    if len(drawio_files) == 1:
        drawio_path = drawio_files[0]
    elif len(drawio_files) > 1:
        initial = os.path.dirname(drawio_files[0])
        drawio_path = filedialog.askopenfilename(
            title="Select .drawio file", initialdir=initial,
            filetypes=[("Draw.io files", "*.drawio")])
        if not drawio_path:
            sys.exit(0)
    else:
        drawio_path = filedialog.askopenfilename(
            title="Select .drawio file", initialdir=script_dir,
            filetypes=[("Draw.io files", "*.drawio")])
        if not drawio_path:
            sys.exit(0)

    CameraWireTool(root, drawio_path)
    root.mainloop()


if __name__ == "__main__":
    main()
