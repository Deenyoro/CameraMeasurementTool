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
    from cwt import routing as routing_mod
    _HAS_ROUTING = True
except ImportError:        # numpy missing
    _HAS_ROUTING = False
    routing_mod = None  # type: ignore

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
    AUTO_ROUTE = auto()        # click two devices, A* draws optimal path


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
    def __init__(self, app: "CameraWireTool", device: dict, index: int):
        self.app = app
        self.device = device
        self.index = index

    def do(self):
        if self.device in self.app.devices:
            self.app.devices.remove(self.device)

    def undo(self):
        idx = min(self.index, len(self.app.devices))
        self.app.devices.insert(idx, self.device)


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
    def __init__(self, app: "CameraWireTool", code: str, info: dict, removed_devices: list[dict]):
        self.app = app
        self.code = code
        self.info = dict(info)
        self.removed_devices = list(removed_devices)

    def do(self):
        self.app.symbol_types.pop(self.code, None)
        self.app.devices = [d for d in self.app.devices if d["type"] != self.code]

    def undo(self):
        self.app.symbol_types[self.code] = self.info
        self.app.devices.extend(self.removed_devices)


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
        self.selected_device: dict | None = None
        self.zoom = 1.0
        self.offset_x = 0.0
        self.offset_y = 0.0
        self.pan_start: tuple[int, int] | None = None
        self._space_pan = False           # space-bar held for temporary pan
        self.unit_var = tk.StringVar(value="ft-in")
        self._tk_ref: ImageTk.PhotoImage | None = None
        self._redraw_pending = False
        self._next_device_id = 100
        self._place_type: str | None = None
        self._show_device_ids = tk.BooleanVar(value=False)
        self._show_fov = tk.BooleanVar(value=False)
        self._show_dori = tk.BooleanVar(value=True)

        # Cable types catalog (project-level)
        self.cable_types: dict = {k: dict(v) for k, v in
                                   bom_mod.DEFAULT_CABLE_TYPES.items()}

        # Multi-floor project (created lazily; single-floor by default)
        # active_floor_id is "F1" for legacy single-floor; multi-floor mode
        # populates self._project (ProjectState) and tracks floors there.
        self._project: mf_mod.ProjectState | None = None
        self._floor_anchors_picking: list[tuple[float, float]] | None = None

        # Routing context (built lazily on first auto-route)
        self._routing_ctx = None
        self._auto_route_first: dict | None = None

        # BOM tab inputs
        self._bom_waste_var = tk.DoubleVar(value=1.10)
        self._bom_tax_var = tk.DoubleVar(value=0.0)
        self._bom_currency_var = tk.StringVar(value="$")

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
        if self.scale_factor:
            self.scale_label.config(
                text=f"  Scale: 1px = {self.scale_factor:.4f}\"", foreground="#3ddc84",
            )
        self._fit_image()
        self._refresh_wire_list()
        self._redraw()
        self._bind_events()

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
        self.canvas.bind("<Control-ButtonPress-1>", self._on_pan_start)
        self.canvas.bind("<Control-B1-Motion>", self._on_pan_move)
        self.canvas.bind("<Control-ButtonRelease-1>", self._on_pan_end)

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
        b("<Home>", lambda _e: (self._fit_image(), self._redraw()))
        b("<plus>", lambda _e: self._zoom_button(1))
        b("<equal>", lambda _e: self._zoom_button(1))
        b("<minus>", lambda _e: self._zoom_button(-1))
        b("<bracketleft>", lambda _e: self._cycle_device(-1))
        b("<bracketright>", lambda _e: self._cycle_device(1))
        # Single-key mode shortcuts (only when canvas-focused, never inside Entry)
        b("<KeyPress-m>", self._kbd_mode_move)
        b("<KeyPress-M>", self._kbd_mode_move)
        b("<KeyPress-w>", self._kbd_mode_wire)
        b("<KeyPress-W>", self._kbd_mode_wire)
        b("<KeyPress-c>", self._kbd_mode_cal)
        b("<KeyPress-C>", self._kbd_mode_cal)
        b("<KeyPress-a>", self._kbd_mode_autoroute)
        b("<KeyPress-A>", self._kbd_mode_autoroute)
        b("<KeyPress-space>", self._kbd_space_press)
        b("<KeyRelease-space>", self._kbd_space_release)
        self.canvas.bind("<Configure>", lambda _e: self._schedule_redraw())
        # Right-click: finish/cancel wire, otherwise show context menu
        self.canvas.bind("<Button-3>", self._on_right_click_with_menu)

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

        # Mode
        mode_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Mode", menu=mode_menu)
        mode_menu.add_radiobutton(label="Move", variable=self.mode_var, value=Mode.MOVE.name,
                                  accelerator="M",
                                  command=lambda: self._set_mode(Mode.MOVE))
        mode_menu.add_radiobutton(label="Wire", variable=self.mode_var, value=Mode.WIRE.name,
                                  accelerator="W",
                                  command=lambda: self._set_mode(Mode.WIRE))
        mode_menu.add_radiobutton(label="Auto-route (A*)", variable=self.mode_var,
                                  value=Mode.AUTO_ROUTE.name, accelerator="A",
                                  command=lambda: self._set_mode(Mode.AUTO_ROUTE))
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

        # Export submenu inside File
        file_menu.add_separator()
        export_menu = tk.Menu(file_menu, tearoff=0)
        file_menu.add_cascade(label="Export", menu=export_menu)
        export_menu.add_command(label="BOM (CSV)…", command=self._export_bom_csv)
        export_menu.add_command(label="All CSVs (BOM + Devices + Wires)…",
                                command=self._export_csv_bundle)
        export_menu.add_command(label="Project PDF Report…",
                                command=self._export_pdf)

        # Help
        help_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Help", menu=help_menu)
        help_menu.add_command(label="Keyboard Shortcuts", command=self._show_shortcuts)
        help_menu.add_command(label="About", command=self._show_about)

        # Toolbar
        toolbar = ttk.Frame(self.root)
        toolbar.pack(side=tk.TOP, fill=tk.X)

        def _ttkbtn(text, cmd, tip):
            b = ttk.Button(toolbar, text=text, command=cmd, width=4)
            b.pack(side=tk.LEFT, padx=2, pady=4)
            Tooltip(b, tip)
            return b

        _ttkbtn("📁", self._file_open, "Open .drawio (Ctrl+O)")
        _ttkbtn("💾", self._save_drawio, "Save (Ctrl+S)")
        ttk.Separator(toolbar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=4, pady=4)

        self._undo_btn = _ttkbtn("↶", self._undo, "Undo (Ctrl+Z)")
        self._redo_btn = _ttkbtn("↷", self._redo, "Redo (Ctrl+Y)")
        ttk.Separator(toolbar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=4, pady=4)

        _ttkbtn("📏", self._start_calibration, "Set scale by clicking a known dimension (C)")
        _ttkbtn("🔍", lambda: (self._fit_image(), self._redraw()), "Fit to view (Home)")
        _ttkbtn("➕", lambda: self._zoom_button(1), "Zoom in (+)")
        _ttkbtn("➖", lambda: self._zoom_button(-1), "Zoom out (-)")
        ttk.Separator(toolbar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=4, pady=4)

        # Mode segmented control
        seg = ttk.Frame(toolbar)
        seg.pack(side=tk.LEFT, padx=4, pady=2)
        self._mode_buttons: dict[Mode, ttk.Radiobutton] = {}
        for m, label, tip in (
            (Mode.MOVE,       "Move",       "Move/select devices  (M)"),
            (Mode.WIRE,       "Wire",       "Draw wire routes  (W)"),
            (Mode.AUTO_ROUTE, "Auto-route", "A* shortest path between two devices  (A)"),
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
        for txt, val in (("Ft-In", "ft-in"), ("In", "inches"), ("Ft", "feet-dec")):
            ttk.Radiobutton(
                toolbar, text=txt, variable=self.unit_var, value=val,
                command=self._on_unit_change,
            ).pack(side=tk.LEFT)

        self.scale_label = ttk.Label(toolbar, text="  Scale: not set", foreground="#ff6b6b")
        self.scale_label.pack(side=tk.LEFT, padx=10)

        self.total_toolbar_label = ttk.Label(toolbar, text="Total: --", font=("Segoe UI", 10, "bold"))
        self.total_toolbar_label.pack(side=tk.RIGHT, padx=10)

        # Main split
        main = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        main.pack(fill=tk.BOTH, expand=True)

        # Canvas
        canvas_frame = ttk.Frame(main)
        canvas_bg = "#1e1e1e" if _HAS_SVTTK else "#2b2b2b"
        self.canvas = tk.Canvas(canvas_frame, bg=canvas_bg, highlightthickness=0,
                                cursor="hand2")
        self.canvas.pack(fill=tk.BOTH, expand=True)
        main.add(canvas_frame, weight=4)

        # Side panel
        side = ttk.Frame(main, width=340)
        main.add(side, weight=1)

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
        self.dev_listbox = tk.Listbox(df, font=("Consolas", 9), selectmode=tk.SINGLE,
                                       activestyle="dotbox")
        sb1 = ttk.Scrollbar(df, orient=tk.VERTICAL, command=self.dev_listbox.yview)
        self.dev_listbox.config(yscrollcommand=sb1.set)
        self.dev_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb1.pack(side=tk.RIGHT, fill=tk.Y)
        self.dev_listbox.bind("<<ListboxSelect>>", self._on_dev_select)
        self.dev_listbox.bind("<Double-Button-1>", lambda _e: self._center_on_selected())

        ttk.Button(tab_dev, text="Center on Selected (Enter)",
                   command=self._center_on_selected).pack(anchor=tk.W, padx=6, pady=4)

        # --- Tab 2: Wires ---
        tab_wire = ttk.Frame(notebook)
        notebook.add(tab_wire, text="Wires")
        ttk.Label(tab_wire, text="Wire Runs",
                  font=("Segoe UI", 10, "bold")).pack(anchor=tk.W, padx=6, pady=(6, 2))

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
        self.wire_listbox = tk.Listbox(wf, font=("Consolas", 9), selectmode=tk.SINGLE,
                                        activestyle="dotbox")
        sb2 = ttk.Scrollbar(wf, orient=tk.VERTICAL, command=self.wire_listbox.yview)
        self.wire_listbox.config(yscrollcommand=sb2.set)
        self.wire_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb2.pack(side=tk.RIGHT, fill=tk.Y)
        self.wire_listbox.bind("<<ListboxSelect>>", self._on_wire_select)

        wbtns = ttk.Frame(tab_wire)
        wbtns.pack(fill=tk.X, padx=6, pady=4)
        ttk.Button(wbtns, text="Delete Wire", command=self._delete_selected_wire).pack(side=tk.LEFT)
        ttk.Button(wbtns, text="Reassign…", command=self._reassign_selected_wire).pack(side=tk.LEFT, padx=4)

        ttk.Separator(tab_wire, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=4, pady=4)
        self.total_label = ttk.Label(tab_wire, text="Total Wire: --",
                                     font=("Segoe UI", 12, "bold"))
        self.total_label.pack(anchor=tk.W, padx=6, pady=4)

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
        self.sym_listbox.bind("<Double-Button-1>", lambda _e: self._edit_symbol_type())

        sym_btns = ttk.Frame(tab_sym)
        sym_btns.pack(fill=tk.X, padx=4, pady=4)
        ttk.Button(sym_btns, text="Edit", command=self._edit_symbol_type).pack(side=tk.LEFT, padx=2)
        ttk.Button(sym_btns, text="Add", command=self._add_symbol_type).pack(side=tk.LEFT, padx=2)
        ttk.Button(sym_btns, text="Delete", command=self._delete_symbol_type).pack(side=tk.LEFT, padx=2)

        ttk.Separator(tab_sym, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=4, pady=4)
        ttk.Label(tab_sym, text="Place Device",
                  font=("Segoe UI", 10, "bold")).pack(anchor=tk.W, padx=6, pady=(2, 2))
        place_frame = ttk.Frame(tab_sym)
        place_frame.pack(fill=tk.X, padx=6, pady=2)
        ttk.Label(place_frame, text="Type:").pack(side=tk.LEFT)
        self.place_type_var = tk.StringVar()
        self.place_combo = ttk.Combobox(place_frame, textvariable=self.place_type_var,
                                        width=8, state="readonly")
        self.place_combo.pack(side=tk.LEFT, padx=4)
        ttk.Button(place_frame, text="Place on Map",
                   command=self._start_place_device).pack(side=tk.LEFT, padx=4)

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
        ttk.Button(adj, text="Refresh", command=self._refresh_bom_tab).pack(side=tk.RIGHT)

        # BOM table (Treeview)
        bom_frame = ttk.Frame(tab_bom)
        bom_frame.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)
        cols = ("cable", "label", "runs", "ft", "rate", "subtotal")
        self._bom_tree = ttk.Treeview(bom_frame, columns=cols, show="headings",
                                       height=10)
        for c, w, h in (
            ("cable", 60, "Cable"),
            ("label", 130, "Description"),
            ("runs", 50, "Runs"),
            ("ft", 80, "Length (ft)"),
            ("rate", 70, "$/ft"),
            ("subtotal", 80, "Subtotal"),
        ):
            self._bom_tree.heading(c, text=h)
            self._bom_tree.column(c, width=w, anchor=tk.W if c in ("cable", "label") else tk.E)
        sb_bom = ttk.Scrollbar(bom_frame, orient=tk.VERTICAL,
                                command=self._bom_tree.yview)
        self._bom_tree.config(yscrollcommand=sb_bom.set)
        self._bom_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb_bom.pack(side=tk.RIGHT, fill=tk.Y)

        # Totals
        self._bom_totals_label = ttk.Label(
            tab_bom, text="Subtotal: --\nGrand total: --",
            font=("Consolas", 10),
        )
        self._bom_totals_label.pack(anchor=tk.W, padx=8, pady=(4, 0))

        bom_btns = ttk.Frame(tab_bom)
        bom_btns.pack(fill=tk.X, padx=6, pady=6)
        ttk.Button(bom_btns, text="Cable Types…",
                   command=self._open_cable_types_editor).pack(side=tk.LEFT)
        ttk.Button(bom_btns, text="Export CSV",
                   command=self._export_bom_csv).pack(side=tk.LEFT, padx=4)
        if _HAS_EXPORT:
            ttk.Button(bom_btns, text="Export PDF",
                       command=self._export_pdf).pack(side=tk.LEFT, padx=4)

        # Refresh once on input change
        for var in (self._bom_waste_var, self._bom_tax_var, self._bom_currency_var):
            var.trace_add("write", lambda *_: self._refresh_bom_tab())

        # Bottom breadcrumb status bar
        self._statusbar = ttk.Frame(self.root)
        self._statusbar.pack(side=tk.BOTTOM, fill=tk.X)
        self.status_label = ttk.Label(self._statusbar, text="", anchor=tk.W,
                                       font=("Segoe UI", 9))
        self.status_label.pack(side=tk.LEFT, padx=8, pady=2, fill=tk.X, expand=True)
        self.cursor_label = ttk.Label(self._statusbar, text="", anchor=tk.E,
                                       font=("Consolas", 9), foreground="#888888")
        self.cursor_label.pack(side=tk.RIGHT, padx=8, pady=2)

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
        self._cancel_current()
        self.mode = new_mode
        self.mode_var.set(new_mode.name)
        self._refresh_mode_ui()

    def _refresh_mode_ui(self) -> None:
        cursor = {
            Mode.MOVE: "hand2",
            Mode.WIRE: "crosshair",
            Mode.CALIBRATE: "tcross",
            Mode.PLACE_DEVICE: "tcross",
            Mode.AUTO_ROUTE: "plus",
        }.get(self.mode, "arrow")
        try:
            self.canvas.config(cursor=cursor)
        except tk.TclError:
            pass
        self.mode_var.set(self.mode.name)
        self._update_status()

    def _kbd_mode_move(self, event):
        if self._is_text_focus(event):
            return
        self._set_mode(Mode.MOVE)

    def _kbd_mode_wire(self, event):
        if self._is_text_focus(event):
            return
        self._set_mode(Mode.WIRE)

    def _kbd_mode_cal(self, event):
        if self._is_text_focus(event):
            return
        self._start_calibration()

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
            return
        cmd = self.history.pop()
        cmd.undo()
        self.future.append(cmd)
        self._mark_dirty()
        self._post_command_refresh()

    def _redo(self) -> None:
        if not self.future:
            return
        cmd = self.future.pop()
        cmd.do()
        self.history.append(cmd)
        self._mark_dirty()
        self._post_command_refresh()

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
        self._update_title()

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
        for code in self._sym_keys:
            info = self.symbol_types[code]
            if query and query not in code.lower() and query not in info.get("label", "").lower():
                continue
            n = counts.get(code, 0)
            cam_tag = " [cam]" if info["is_camera"] else ""
            self.sym_listbox.insert(
                tk.END,
                f'  {code:5s} | {info["label"][:22]:22s} | {info["shape"]:9s} | x{n}{cam_tag}',
            )
        # update place combobox
        self.place_combo["values"] = self._sym_keys
        if self._sym_keys and not self.place_type_var.get():
            self.place_type_var.set(self._sym_keys[0])

    def _refresh_device_list(self) -> None:
        self.dev_listbox.delete(0, tk.END)
        self.devices.sort(key=lambda d: d["id"])

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

        self._filtered_devices: list[dict] = []
        for d in self.devices:
            info = self.symbol_types.get(d["type"], {})
            label = info.get("label", d["type"])
            if query and not (
                query in d["id"].lower()
                or query in d["type"].lower()
                or query in label.lower()
            ):
                continue
            self._filtered_devices.append(d)
            wire_info = wire_by_device.get(d["id"])
            wire_str = (
                f' [{wire_info["runs"]}w: {self._format_length(wire_info["total_in"])}]'
                if wire_info else ""
            )
            self.dev_listbox.insert(tk.END, f'  {d["id"]:6s} {d["type"]:5s}{wire_str}')

    # ============================================================ Symbol editor dialog
    def _edit_symbol_type(self) -> None:
        sel = self.sym_listbox.curselection()
        if not sel:
            messagebox.showinfo("Select", "Select a symbol type to edit")
            return
        # Use the listbox's displayed order, which after filtering may not match _sym_keys.
        text = self.sym_listbox.get(sel[0]).strip()
        old_code = text.split("|")[0].strip()
        if old_code not in self.symbol_types:
            return
        info = self.symbol_types[old_code]
        self._open_type_editor(old_code, info, is_new=False)

    def _add_symbol_type(self) -> None:
        info = {"label": "New Device", "shape": "ellipse", "color": "#FFFF00", "is_camera": False}
        self._open_type_editor("NEW", info, is_new=True)

    def _open_type_editor(self, old_code: str, info: dict, is_new: bool = False) -> None:
        dlg = tk.Toplevel(self.root)
        dlg.title("Edit Symbol Type" if not is_new else "Add Symbol Type")
        dlg.geometry("440x360")
        dlg.transient(self.root)
        dlg.grab_set()
        dlg.resizable(False, False)

        pad = {"padx": 10, "pady": 4}
        row = 0

        ttk.Label(dlg, text="Shortcode (shown inside icon):").grid(
            row=row, column=0, sticky=tk.W, **pad)
        code_var = tk.StringVar(value=old_code if not is_new else "")
        ttk.Entry(dlg, textvariable=code_var, width=10).grid(row=row, column=1, sticky=tk.W, **pad)
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
        color_swatch = tk.Label(color_frame, bg=info["color"], width=4, height=1, relief=tk.RAISED)
        color_swatch.pack(side=tk.LEFT, padx=(0, 6))
        color_hex_label = ttk.Label(color_frame, text=info["color"])
        color_hex_label.pack(side=tk.LEFT, padx=(0, 6))

        def pick_color():
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

        ttk.Button(color_frame, text="Pick…", command=pick_color).pack(side=tk.LEFT)
        row += 1

        cam_var = tk.BooleanVar(value=info["is_camera"])
        ttk.Checkbutton(dlg, text="Is a camera (counts toward camera totals)",
                        variable=cam_var).grid(row=row, column=0, columnspan=2, sticky=tk.W, **pad)
        row += 1

        if not is_new:
            count = sum(1 for d in self.devices if d["type"] == old_code)
            ttk.Label(dlg, text=f"Instances on map: {count}", foreground="gray").grid(
                row=row, column=0, columnspan=2, sticky=tk.W, **pad)
            row += 1

        # Preview canvas
        preview_canvas = tk.Canvas(dlg, width=60, height=60, bg="white", relief=tk.SUNKEN)
        preview_canvas.grid(row=0, column=2, rowspan=3, padx=10, pady=10)

        def update_preview(*_):
            preview_canvas.delete("all")
            s = shape_var.get()
            c = color_var.get()
            cx, cy, r = 30, 30, 18
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
            preview_canvas.create_text(cx, cy, text=cv, font=("Arial", 9, "bold"))

        shape_var.trace_add("write", update_preview)
        color_var.trace_add("write", update_preview)
        code_var.trace_add("write", update_preview)
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
            }
            cmd = EditSymbolCmd(self, old_code, info, new_code, new_info, is_new)
            self._execute(cmd)
            self._post_command_refresh()
            dlg.destroy()

        btns = ttk.Frame(dlg)
        btns.grid(row=row, column=0, columnspan=3, pady=12)
        ttk.Button(btns, text="Apply", command=apply).pack(side=tk.LEFT, padx=4)
        ttk.Button(btns, text="Cancel", command=dlg.destroy).pack(side=tk.LEFT, padx=4)
        dlg.bind("<Return>", lambda _e: apply())
        dlg.bind("<Escape>", lambda _e: dlg.destroy())

    def _delete_symbol_type(self) -> None:
        sel = self.sym_listbox.curselection()
        if not sel:
            return
        text = self.sym_listbox.get(sel[0]).strip()
        code = text.split("|")[0].strip()
        if code not in self.symbol_types:
            return
        info = self.symbol_types[code]
        removed = [d for d in self.devices if d["type"] == code]
        if removed and not messagebox.askyesno(
            "Delete Type",
            f'Delete symbol type "{code}"?\n'
            f'This will also remove all {len(removed)} instances from the map.',
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
        dx, dy = self._img_to_drawio(ix, iy)
        new_id = f"s{self._next_device_id}"
        self._next_device_id += 1
        info = self.symbol_types[code]
        device = {
            "id": new_id, "type": code,
            "x": dx, "y": dy,
            "w": C.DEVICE_DEFAULT_W, "h": C.DEVICE_DEFAULT_H,
            "orig_style": SHAPE_STYLES.get(info["shape"], SHAPE_STYLES["ellipse"]).format(
                color=info["color"], fs=8,
            ),
        }
        self._execute(AddDeviceCmd(self, device))
        self._set_mode(Mode.MOVE)
        self._post_command_refresh()
        self.status_label.config(text=f"Placed {code} ({new_id})")

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
        resized = cropped.resize((dst_w, dst_h), Image.LANCZOS)
        tk_img = ImageTk.PhotoImage(resized)
        place_x, place_y = self._img_to_canvas(src_x0, src_y0)
        return tk_img, place_x, place_y

    def _schedule_redraw(self) -> None:
        if not self._redraw_pending:
            self._redraw_pending = True
            self.root.after(C.DEBOUNCE_MS, self._do_deferred_redraw)

    def _do_deferred_redraw(self) -> None:
        self._redraw_pending = False
        self._redraw()

    def _redraw(self) -> None:
        self.canvas.delete("all")
        tk_img, px, py = self._get_viewport_image()
        if tk_img is not None:
            self.canvas.create_image(px, py, anchor=tk.NW, image=tk_img, tags="img")
            self._tk_ref = tk_img

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

        self._draw_legend()

        for pt in self.cal_points:
            cx, cy = self._img_to_canvas(*pt)
            self.canvas.create_oval(cx-5, cy-5, cx+5, cy+5,
                                    fill="yellow", outline="black", width=2, tags="cal")

    def _draw_legend(self) -> None:
        box = self.legend_box
        bx, by = self._drawio_to_img(box["x"], box["y"])
        bw = box["w"] * self.dx_scale
        sorted_codes = sorted(self.symbol_types.keys())
        n_types = len(sorted_codes)
        needed_h = (n_types * C.LEGEND_ROW_H + 50) * self.dy_scale
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
        center_x = cx + cw / 2
        center_y = cy + ch / 2
        r = max(cw, ch) / 2

        is_selected = (self.selected_device is not None
                       and self.selected_device["id"] == dev["id"])
        outline_color = "#FF3030" if is_selected else "#0000FF"
        outline_width = 3 if is_selected else 2
        tag = f'dev_{dev["id"]}'

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
        if did:
            dev = next((d for d in self.devices if d["id"] == did), None)
            if dev:
                info = self.symbol_types.get(dev["type"], {})
                wire_color = info.get("color", "#00BFFF")

        color = "#FF4444" if is_selected else wire_color
        width = 3 if is_selected else 2
        pts_canvas = [self._img_to_canvas(*p) for p in route["points"]]
        for j in range(len(pts_canvas) - 1):
            self.canvas.create_line(pts_canvas[j][0], pts_canvas[j][1],
                                    pts_canvas[j+1][0], pts_canvas[j+1][1],
                                    fill=color, width=width, tags=f"wire_{idx}")
        for cx, cy in pts_canvas:
            self.canvas.create_oval(cx-3, cy-3, cx+3, cy+3, fill=color,
                                    outline="white", tags=f"wire_{idx}")
        mid_idx = len(pts_canvas) // 2
        mx, my = pts_canvas[mid_idx]
        length_in = route.get("length_in")
        length_str = (self._format_length(length_in) if length_in is not None
                      else f'{route["length_px"]:.0f}px')
        dev_tag = f"{did}: " if did else ""
        label = f"{dev_tag}{length_str}"
        self.canvas.create_text(mx, my - 12, text=label, fill="#00FFFF",
                                font=("Consolas", 9, "bold"), tags=f"wire_{idx}")

    def _wire_index_for_listbox(self, list_index: int) -> int:
        # If the wire list is filtered, the listbox index doesn't match wire_routes index.
        return self._filtered_wire_indices[list_index] if hasattr(self, "_filtered_wire_indices") and list_index < len(self._filtered_wire_indices) else list_index

    # ============================================================ Formatting
    def _format_length(self, inches) -> str:
        if inches is None:
            return "--"
        unit = self.unit_var.get()
        if unit == "inches":
            return f'{inches:.2f}"'
        if unit == "feet-dec":
            return f"{inches / 12:.2f} ft"
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
        dlg.geometry("360x170")
        dlg.transient(self.root)
        dlg.grab_set()
        ttk.Label(dlg, text="Enter the real-world distance between the two points:").pack(
            pady=(12, 4))
        input_frame = ttk.Frame(dlg)
        input_frame.pack(pady=4)
        feet_var = tk.StringVar(value="0")
        inches_var = tk.StringVar(value="0")
        ttk.Label(input_frame, text="Feet:").grid(row=0, column=0, padx=4)
        ttk.Entry(input_frame, textvariable=feet_var, width=8).grid(row=0, column=1, padx=4)
        ttk.Label(input_frame, text="Inches:").grid(row=0, column=2, padx=4)
        ttk.Entry(input_frame, textvariable=inches_var, width=8).grid(row=0, column=3, padx=4)

        def apply():
            try:
                ft = float(feet_var.get())
                inc = float(inches_var.get())
            except ValueError:
                messagebox.showerror("Error", "Enter valid numbers")
                return
            total_in = ft * 12 + inc
            if total_in <= 0:
                messagebox.showerror("Error", "Distance must be positive")
                return
            new_scale = total_in / px_dist
            self._execute(CalibrateCmd(self, self.scale_factor, new_scale))
            self.scale_label.config(
                text=f"  Scale: 1px = {self.scale_factor:.4f}\"", foreground="#3ddc84",
            )
            self._refresh_wire_list()
            self._refresh_device_list()
            self._redraw()
            dlg.destroy()

        def cancel():
            dlg.destroy()

        btns = ttk.Frame(dlg)
        btns.pack(pady=8)
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

        if self.mode == Mode.AUTO_ROUTE:
            dev = self._device_at(ix, iy)
            if dev is None:
                self.status_label.config(
                    text="Auto-route: click directly on a device icon. Esc to cancel.")
                return
            if self._auto_route_first is None:
                self._auto_route_first = dev
                self.selected_device = dev
                self._redraw()
                self.status_label.config(
                    text=f"Auto-route: now click destination "
                         f"(started at {dev['id']})")
                return
            else:
                if dev["id"] == self._auto_route_first["id"]:
                    self.status_label.config(text="Pick a DIFFERENT destination device.")
                    return
                self._do_auto_route(self._auto_route_first, dev)
                self._auto_route_first = None
                # stay in auto-route mode so user can chain runs
                return

        if self.mode == Mode.MOVE:
            dev = self._device_at(ix, iy)
            if dev is not None:
                self.selected_device = dev
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
            self.selected_device = None
            self.dragging_device = None
            self._update_status()
            self._redraw()
            return

        if self.mode == Mode.WIRE:
            self.current_wire_points.append((ix, iy))
            self._redraw()
            if len(self.current_wire_points) == 1:
                self.status_label.config(
                    text="Wire: click to add points, right-click to finish route",
                )

    def _on_left_drag(self, event) -> None:
        if (event.state & 0x4) or self._space_pan or self.pan_start is not None:
            self._on_pan_move(event); return
        if self.dragging_device is not None:
            ix, iy = self._canvas_to_img(event.x, event.y)
            new_ix = ix - self.drag_offset[0]
            new_iy = iy - self.drag_offset[1]
            new_dx, new_dy = self._img_to_drawio(new_ix, new_iy)
            self.dragging_device["x"] = new_dx
            self.dragging_device["y"] = new_dy
            self._schedule_redraw()

    def _on_left_release(self, event) -> None:
        if self.pan_start is not None:
            self._on_pan_end(event); return
        if self.dragging_device is not None:
            # Record undoable command if device actually moved
            if self._drag_start_pos is not None:
                old_x, old_y = self._drag_start_pos
                if (abs(old_x - self.dragging_device["x"]) > 0.01
                        or abs(old_y - self.dragging_device["y"]) > 0.01):
                    cmd = MoveDeviceCmd(
                        self, self.dragging_device,
                        old_x, old_y,
                        self.dragging_device["x"], self.dragging_device["y"],
                    )
                    # We've already mutated; record without re-doing
                    self.history.append(cmd)
                    if len(self.history) > C.HISTORY_LIMIT:
                        self.history.pop(0)
                    self.future.clear()
                    self._mark_dirty()
                    self._update_undo_redo_state()
            self._drag_start_pos = None
            self.dragging_device = None
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

        if self.mode == Mode.WIRE and self.current_wire_points:
            self.canvas.delete("wire_preview")
            last = self.current_wire_points[-1]
            sx, sy = self._img_to_canvas(*last)
            self.canvas.create_line(sx, sy, event.x, event.y,
                                    fill="#00BFFF", width=2, dash=(4, 4),
                                    tags="wire_preview")
            ix2, iy2 = self._canvas_to_img(event.x, event.y)
            all_pts = self.current_wire_points + [(ix2, iy2)]
            total_px = self._calc_wire_length(all_pts)
            if self.scale_factor:
                total_in = total_px * self.scale_factor
                self.status_label.config(
                    text=f"Wire route: {self._format_length(total_in)}  |  Right-click to finish",
                )
            else:
                self.status_label.config(
                    text=f"Wire route: {total_px:.0f}px  |  Right-click to finish",
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
        real_in = px_len * self.scale_factor if self.scale_factor else None
        device_id = self.selected_device["id"] if self.selected_device else None
        route = {
            "points": list(self.current_wire_points),
            "length_px": px_len,
            "length_in": real_in,
            "device_id": device_id,
        }
        self._execute(AddWireCmd(self, route))
        self.current_wire_points = []
        self._refresh_wire_list()
        self._refresh_device_list()
        self._redraw()
        dev_label = f" for {device_id}" if device_id else " (unassigned)"
        self.status_label.config(text=f"Wire route saved{dev_label}. Click to start a new one.")

    def _cancel_current(self) -> None:
        self.current_wire_points = []
        if self.mode in (Mode.PLACE_DEVICE, Mode.CALIBRATE, Mode.AUTO_ROUTE):
            self._set_mode(Mode.MOVE)
        self.canvas.delete("wire_preview")
        self.canvas.delete("wip_wire")
        self.canvas.delete("anchor")
        self.cal_points = []
        self._auto_route_first = None
        self._floor_anchors_picking = None
        self._update_status()

    def _on_mousewheel(self, event) -> None:
        factor = C.ZOOM_FACTOR if event.delta > 0 else 1 / C.ZOOM_FACTOR
        new_zoom = max(C.ZOOM_MIN, min(self.zoom * factor, C.ZOOM_MAX))
        ix, iy = self._canvas_to_img(event.x, event.y)
        self.zoom = new_zoom
        self.offset_x = event.x - ix * self.zoom
        self.offset_y = event.y - iy * self.zoom
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
        self._redraw()

    def _on_delete_key(self, _event=None) -> None:
        # Prefer wire if focused on wire list, otherwise selected device
        if self.wire_listbox.curselection():
            self._delete_selected_wire(); return
        if self.selected_device is not None:
            dev = self.selected_device
            try:
                idx = self.devices.index(dev)
            except ValueError:
                return
            if not messagebox.askyesno("Delete device", f"Delete {dev['id']} ({dev['type']})?"):
                return
            self.selected_device = None
            self._execute(DeleteDeviceCmd(self, dev, idx))
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

    def _center_on_selected(self) -> None:
        sel_idx = self.dev_listbox.curselection()
        if sel_idx and hasattr(self, "_filtered_devices") and sel_idx[0] < len(self._filtered_devices):
            self.selected_device = self._filtered_devices[sel_idx[0]]
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
        total = 0.0
        query = (
            self._wire_search_var.get() if hasattr(self, "_wire_search_var") else ""
        ).lower().strip()
        self._filtered_wire_indices: list[int] = []
        for i, route in enumerate(self.wire_routes):
            did = route.get("device_id") or "---"
            if query and query not in did.lower():
                continue
            self._filtered_wire_indices.append(i)
            if route["length_in"] is not None:
                label = self._format_length(route["length_in"])
                total += route["length_in"]
            else:
                label = f'{route["length_px"]:.0f}px'
            n_segs = len(route["points"]) - 1
            self.wire_listbox.insert(tk.END, f"  {did:6s} | {n_segs} seg | {label}")

        # All routes (regardless of filter) for total
        if self.scale_factor:
            grand_total_in = sum((r.get("length_in") or 0) for r in self.wire_routes)
            total_text = f"Total Wire: {self._format_length(grand_total_in)}"
        elif self.wire_routes:
            total_px = sum(r["length_px"] for r in self.wire_routes)
            total_text = f"Total Wire: {total_px:.0f}px (calibrate first)"
        else:
            total_text = "Total Wire: --"
        self.total_label.config(text=total_text)
        self.total_toolbar_label.config(text=total_text)

    def _on_dev_select(self, _event) -> None:
        sel = self.dev_listbox.curselection()
        if sel and hasattr(self, "_filtered_devices") and sel[0] < len(self._filtered_devices):
            self.selected_device = self._filtered_devices[sel[0]]
            if self.mode == Mode.WIRE:
                dev = self.selected_device
                info = self.symbol_types.get(dev["type"], {})
                self.status_label.config(
                    text=f'Wire Mode for {dev["id"]} ({dev["type"]} - {info.get("label", "")}) — '
                         f'click to route, right-click to finish',
                )
            self._redraw()

    def _on_wire_select(self, _event) -> None:
        self._redraw()

    def _on_unit_change(self) -> None:
        self._refresh_wire_list()
        self._refresh_device_list()
        self._redraw()
        self._mark_dirty()

    def _delete_selected_wire(self, _event=None) -> None:
        sel = self.wire_listbox.curselection()
        if not sel:
            return
        if not hasattr(self, "_filtered_wire_indices") or sel[0] >= len(self._filtered_wire_indices):
            return
        idx = self._filtered_wire_indices[sel[0]]
        route = self.wire_routes[idx]
        self._execute(DeleteWireCmd(self, idx, route))
        self._post_command_refresh()

    def _reassign_selected_wire(self) -> None:
        sel = self.wire_listbox.curselection()
        if not sel:
            messagebox.showinfo("Select", "Select a wire to reassign")
            return
        if not hasattr(self, "_filtered_wire_indices"):
            return
        idx = self._filtered_wire_indices[sel[0]]
        route = self.wire_routes[idx]

        dlg = tk.Toplevel(self.root)
        dlg.title("Reassign wire")
        dlg.geometry("320x140")
        dlg.transient(self.root); dlg.grab_set()

        ttk.Label(dlg, text="Assign this wire to:").pack(pady=(10, 4))
        choices = ["(unassigned)"] + [f'{d["id"]} ({d["type"]})' for d in self.devices]
        var = tk.StringVar(value=choices[0] if route.get("device_id") is None
                           else f'{route["device_id"]} ({next((d["type"] for d in self.devices if d["id"] == route["device_id"]), "?")})')
        cb = ttk.Combobox(dlg, textvariable=var, values=choices, state="readonly", width=30)
        cb.pack(pady=4)

        def apply():
            choice = var.get()
            new_did = None if choice == "(unassigned)" else choice.split(" ")[0]
            old_did = route.get("device_id")
            if new_did == old_did:
                dlg.destroy(); return
            route["device_id"] = new_did
            self._mark_dirty()
            self._refresh_wire_list()
            self._refresh_device_list()
            self._redraw()
            dlg.destroy()

        btns = ttk.Frame(dlg); btns.pack(pady=8)
        ttk.Button(btns, text="Apply", command=apply).pack(side=tk.LEFT, padx=4)
        ttk.Button(btns, text="Cancel", command=dlg.destroy).pack(side=tk.LEFT, padx=4)

    def _clear_wires(self) -> None:
        if not self.wire_routes:
            return
        if not messagebox.askyesno("Confirm", "Clear all wire routes?"):
            return
        self._execute(ClearWiresCmd(self, self.wire_routes))
        self._post_command_refresh()

    # ============================================================ Status
    def _update_status(self) -> None:
        mode_str = {
            Mode.MOVE: "Move",
            Mode.WIRE: "Wire",
            Mode.CALIBRATE: "Calibrate",
            Mode.PLACE_DEVICE: f"Place {self._place_type or '?'}",
        }[self.mode]
        sel_str = ""
        if self.selected_device is not None:
            d = self.selected_device
            info = self.symbol_types.get(d["type"], {})
            sel_str = f"  •  Selected: {d['id']} ({d['type']} – {info.get('label','?')})"
            wires = [w for w in self.wire_routes if w.get("device_id") == d["id"]]
            if wires:
                tot = sum(w.get("length_in", 0) or 0 for w in wires)
                sel_str += f"  •  {len(wires)} run(s), {self._format_length(tot)}"
        self.status_label.config(text=f"Mode: {mode_str}{sel_str}")

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
        push_recent_file(self.drawio_path)
        self._refresh_recent_menu()
        messagebox.showinfo("Saved",
            f"Saved:\n  Layout: {os.path.basename(self.drawio_path)}\n"
            f"  Wires:  {os.path.basename(self.project_path)}",
        )

    # ============================================================ Help dialogs
    def _show_shortcuts(self) -> None:
        msg = (
            "Modes\n"
            "  M / W / C       Move / Wire / Calibrate\n"
            "\n"
            "Edit\n"
            "  Ctrl+Z / Ctrl+Y  Undo / Redo\n"
            "  Delete           Delete selected wire or device\n"
            "  Esc              Cancel current action\n"
            "\n"
            "Navigation\n"
            "  Mouse wheel      Zoom (toward cursor)\n"
            "  Middle-drag      Pan\n"
            "  Ctrl+drag        Pan\n"
            "  Space (hold)     Temporary pan\n"
            "  Home             Fit to view\n"
            "  + / -            Zoom in / out\n"
            "  [ / ]            Cycle through devices\n"
            "\n"
            "File\n"
            "  Ctrl+O           Open\n"
            "  Ctrl+S           Save\n"
            "  Ctrl+Shift+S     Save As\n"
            "\n"
            "Other\n"
            "  Ctrl+F           Focus search box on active tab\n"
        )
        messagebox.showinfo("Keyboard Shortcuts", msg)

    def _show_about(self) -> None:
        messagebox.showinfo(
            "About Camera Wire Tool",
            "Camera Wire Estimation Tool\n"
            "v1.0.0 — killer features release\n\n"
            "Tkinter app for estimating security-camera cable runs on draw.io floor plans.\n\n"
            "Features:\n"
            "  • Cable types + BOM with waste & tax\n"
            "  • Camera FOV / DORI overlays (IEC 62676-4)\n"
            "  • A* auto-routing between devices\n"
            "  • Multi-floor with anchor-point alignment + risers\n"
            "  • PDF + CSV report export\n\n"
            "See PLAYBOOK.md for full documentation.\n"
            "MIT License.",
        )

    # ============================================================ keyboard handlers (extra)
    def _kbd_mode_autoroute(self, event):
        if self._is_text_focus(event):
            return
        if not _HAS_ROUTING:
            messagebox.showwarning(
                "Auto-route unavailable",
                "Auto-routing requires numpy. `pip install numpy` then restart.")
            return
        self._set_mode(Mode.AUTO_ROUTE)

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
        if self.mode in (Mode.CALIBRATE, Mode.PLACE_DEVICE, Mode.AUTO_ROUTE):
            self._cancel_current()
            return
        ix, iy = self._canvas_to_img(event.x, event.y)
        dev = self._device_at(ix, iy)
        menu = tk.Menu(self.root, tearoff=0)
        if dev is not None:
            self.selected_device = dev
            self._redraw()
            info = self.symbol_types.get(dev["type"], {})
            menu.add_command(label=f"Selected: {dev['id']} ({dev['type']})",
                             state=tk.DISABLED)
            menu.add_separator()
            if info.get("is_camera"):
                menu.add_command(label="Edit FOV / DORI…",
                                 command=lambda d=dev: self._open_fov_dialog(d))
                menu.add_command(
                    label="Toggle FOV cone for this camera",
                    command=lambda d=dev: self._toggle_camera_fov(d),
                )
                menu.add_separator()
            if _HAS_ROUTING:
                menu.add_command(
                    label="Auto-route from here…",
                    command=lambda d=dev: self._auto_route_from(d),
                )
                menu.add_separator()
            menu.add_command(label="Delete device",
                             command=self._on_delete_key)
        else:
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
        for line in report.lines:
            self._bom_tree.insert(
                "", tk.END,
                values=(line.cable_id, line.label, line.runs,
                        f"{line.total_ft:,.2f}",
                        f"{cur}{line.cost_per_ft:.4f}",
                        f"{cur}{line.subtotal:,.2f}"),
            )
        unassigned = ""
        if report.unassigned_runs:
            unassigned = (
                f"\n  ⚠ {report.unassigned_runs} unassigned wire(s) — "
                f"{self._format_length(report.unassigned_inches)}"
            )
        self._bom_totals_label.config(text=(
            f"Subtotal:    {cur}{report.subtotal:,.2f}\n"
            f"Waste added: {cur}{report.waste_added:,.2f} (×{report.waste_factor:.2f})\n"
            f"Tax:         {cur}{report.tax_amount:,.2f} ({report.tax_rate*100:.2f}%)\n"
            f"GRAND TOTAL: {cur}{report.grand_total:,.2f}"
            + unassigned
        ))

    # ============================================================ Cable types editor
    def _open_cable_types_editor(self) -> None:
        dlg = tk.Toplevel(self.root)
        dlg.title("Cable Types")
        dlg.geometry("520x420")
        dlg.transient(self.root); dlg.grab_set()

        ttk.Label(dlg, text="Project cable catalog. Per-symbol cable assignment "
                  "is in the Symbol editor.",
                  font=("Segoe UI", 9)).pack(anchor=tk.W, padx=10, pady=(8, 4))

        cols = ("id", "label", "color", "cost")
        tree = ttk.Treeview(dlg, columns=cols, show="headings", height=10)
        for c, w, h in (("id", 70, "ID"), ("label", 200, "Label"),
                         ("color", 80, "Color"), ("cost", 80, "$/ft")):
            tree.heading(c, text=h)
            tree.column(c, width=w, anchor=tk.W)
        tree.pack(fill=tk.BOTH, expand=True, padx=10, pady=4)

        def repop():
            for it in tree.get_children():
                tree.delete(it)
            for cid, ct in sorted(self.cable_types.items()):
                tree.insert("", tk.END, iid=cid,
                            values=(cid, ct["label"], ct["color"],
                                    f"{ct.get('cost_per_ft', 0):.4f}"))
        repop()

        def edit_dialog(cid: str | None):
            edit = tk.Toplevel(dlg); edit.transient(dlg); edit.grab_set()
            edit.title("Cable" if cid else "New Cable")
            edit.geometry("340x230")
            row = 0
            ttk.Label(edit, text="ID:").grid(row=row, column=0, sticky=tk.W, padx=8, pady=4)
            id_var = tk.StringVar(value=cid or "")
            ttk.Entry(edit, textvariable=id_var, width=20,
                      state=("disabled" if cid else "normal")).grid(row=row, column=1, sticky=tk.W)
            row += 1
            ttk.Label(edit, text="Label:").grid(row=row, column=0, sticky=tk.W, padx=8, pady=4)
            label_var = tk.StringVar(value=self.cable_types.get(cid, {}).get("label", "") if cid else "")
            ttk.Entry(edit, textvariable=label_var, width=28).grid(row=row, column=1, sticky=tk.W)
            row += 1
            ttk.Label(edit, text="Color:").grid(row=row, column=0, sticky=tk.W, padx=8, pady=4)
            color_var = tk.StringVar(value=self.cable_types.get(cid, {}).get("color", "#888888") if cid else "#888888")
            color_frame = ttk.Frame(edit); color_frame.grid(row=row, column=1, sticky=tk.W)
            sw = tk.Label(color_frame, bg=color_var.get(), width=4, height=1, relief=tk.RAISED)
            sw.pack(side=tk.LEFT, padx=(0, 4))
            ttk.Label(color_frame, textvariable=color_var, width=10).pack(side=tk.LEFT)
            def pick():
                c = colorchooser.askcolor(initialcolor=color_var.get())
                if c and c[1]:
                    color_var.set(c[1].upper()); sw.config(bg=c[1])
            ttk.Button(color_frame, text="Pick…", command=pick).pack(side=tk.LEFT, padx=4)
            row += 1
            ttk.Label(edit, text="Cost/ft ($):").grid(row=row, column=0, sticky=tk.W, padx=8, pady=4)
            cost_var = tk.DoubleVar(value=self.cable_types.get(cid, {}).get("cost_per_ft", 0.10) if cid else 0.10)
            ttk.Entry(edit, textvariable=cost_var, width=10).grid(row=row, column=1, sticky=tk.W)
            row += 1
            def save():
                new_id = id_var.get().strip()
                if not new_id:
                    messagebox.showerror("Error", "ID required"); return
                self.cable_types[new_id] = {
                    "label": label_var.get().strip() or new_id,
                    "color": color_var.get().upper(),
                    "cost_per_ft": float(cost_var.get()),
                }
                self._mark_dirty()
                repop()
                self._refresh_bom_tab()
                self._redraw()
                edit.destroy()
            ttk.Button(edit, text="Save", command=save).grid(row=row, column=0, columnspan=2, pady=10)

        btns = ttk.Frame(dlg); btns.pack(fill=tk.X, padx=10, pady=8)
        ttk.Button(btns, text="Add", command=lambda: edit_dialog(None)).pack(side=tk.LEFT)
        def on_edit():
            sel = tree.selection()
            if sel: edit_dialog(sel[0])
        ttk.Button(btns, text="Edit", command=on_edit).pack(side=tk.LEFT, padx=4)
        def on_del():
            sel = tree.selection()
            if not sel: return
            cid = sel[0]
            if messagebox.askyesno("Delete", f"Delete cable type '{cid}'?"):
                self.cable_types.pop(cid, None)
                self._mark_dirty(); repop()
                self._refresh_bom_tab(); self._redraw()
        ttk.Button(btns, text="Delete", command=on_del).pack(side=tk.LEFT, padx=4)
        ttk.Button(btns, text="Close", command=dlg.destroy).pack(side=tk.RIGHT)

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
        def apply():
            device["fov"] = {
                "enabled": True,
                "show": show_var.get(),
                "lens_mm": float(lens_var.get()),
                "sensor": sensor_var.get(),
                "resolution": list(lenses_mod.RESOLUTIONS.get(res_var.get(),
                                                              [1920, 1080])),
                "mount_height_ft": float(mh_var.get()),
                "tilt_deg": float(tilt_var.get()),
                "azimuth_deg": float(az_var.get()),
            }
            self._show_fov.set(True)
            self._mark_dirty()
            self._redraw()
            dlg.destroy()
        ttk.Button(btns, text="Apply", command=apply).pack(side=tk.LEFT)
        ttk.Button(btns, text="Cancel", command=dlg.destroy).pack(side=tk.LEFT, padx=4)

    # ============================================================ Auto-routing (A*)
    def _build_routing_ctx(self):
        if self._routing_ctx is not None:
            return self._routing_ctx
        if not _HAS_ROUTING or routing_mod is None:
            return None
        try:
            self.status_label.config(text="Building obstacle grid…")
            self.root.update_idletasks()
            # cell_px=25 + threshold=80 + no dilation tuned for typical
            # architectural floor plans (lots of fine detail; dilation makes
            # everything a wall).
            grid = routing_mod.build_obstacle_grid(self.bg_image, cell_px=25,
                                                    threshold=80, dilate_iters=0)
            self._routing_ctx = routing_mod.RoutingContext(grid=grid, cell_px=25)
            self._update_status()
        except Exception as e:
            log_error("Failed to build routing grid", e)
            messagebox.showerror("Auto-route", f"Could not build obstacle grid:\n{e}")
            return None
        return self._routing_ctx

    def _start_auto_route(self):
        if not _HAS_ROUTING:
            messagebox.showwarning(
                "Auto-route unavailable",
                "Auto-routing requires numpy. `pip install numpy` then restart.")
            return
        self._set_mode(Mode.AUTO_ROUTE)
        self._auto_route_first = None
        self.status_label.config(
            text="Auto-route: click first device, then click second device. Esc to cancel.")

    def _auto_route_from(self, device: dict):
        self._start_auto_route()
        self._auto_route_first = device
        self.status_label.config(
            text=f"Auto-route: now click destination device (started at {device['id']})")

    def _do_auto_route(self, src: dict, dst: dict):
        ctx = self._build_routing_ctx()
        if ctx is None:
            return
        sx, sy = self._drawio_to_img(src["x"] + src["w"]/2, src["y"] + src["h"]/2)
        gx, gy = self._drawio_to_img(dst["x"] + dst["w"]/2, dst["y"] + dst["h"]/2)
        try:
            waypoints = routing_mod.route_between(ctx, (sx, sy), (gx, gy))
        except Exception as e:
            log_error("A* failed", e)
            messagebox.showerror("Auto-route", f"Route failed:\n{e}")
            return
        if not waypoints:
            messagebox.showwarning("Auto-route",
                                   "No path found (all routes blocked by walls)")
            return
        px_len = self._calc_wire_length(waypoints)
        real_in = px_len * self.scale_factor if self.scale_factor else None
        route = {
            "points": [tuple(p) for p in waypoints],
            "length_px": px_len,
            "length_in": real_in,
            "device_id": src["id"],
        }
        self._execute(AddWireCmd(self, route))
        self.selected_device = src
        self._post_command_refresh()
        self.status_label.config(
            text=f"Auto-routed {src['id']} → {dst['id']}: "
                 f"{len(waypoints)} waypoints, "
                 f"{self._format_length(real_in) if real_in else f'{px_len:.0f}px'}")

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
        ipp = (1.0 / self.scale_factor) if self.scale_factor else 0.5
        for d in self.devices:
            f = d.get("fov")
            if not f or not f.get("show", True):
                continue
            info = self.symbol_types.get(d["type"], {})
            if not info.get("is_camera"):
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
