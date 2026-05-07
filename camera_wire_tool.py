"""
Camera Wire Estimation Tool
============================
Loads a .drawio building schematic with security device icons, lets you:
- Calibrate scale (feet/inches)
- Drag device icons to new positions
- Draw wire routes and estimate total wire length
- Edit symbol types (rename shortcodes, change colors/shapes/labels)
  — changes propagate to EVERY instance on the map and auto-regenerate the legend
- Add new symbol types and place instances
- Save everything back to the .drawio file

Controls:
- Left-click on device: select (drag to move in Move Mode)
- Left-click on empty space (Wire Mode): place wire route points
- Right-click: finish wire / cancel
- Mouse wheel: zoom
- Middle-click drag (or Ctrl+left drag): pan
"""

import tkinter as tk
from tkinter import ttk, messagebox, filedialog, colorchooser
from PIL import Image, ImageTk
import math
import os
import sys
import xml.etree.ElementTree as ET
import base64
import io
import re
import copy
import json

# ---------------------------------------------------------------- available shapes
SHAPE_OPTIONS = ["ellipse", "rect", "roundrect", "hexagon", "halfrect", "step"]

# style templates for .drawio export
SHAPE_STYLES = {
    "ellipse":   "ellipse;html=1;fillColor={color};strokeColor=#0000FF;strokeWidth=2;fontSize={fs};fontStyle=1;fontColor=#000000",
    "rect":      "rounded=0;html=1;fillColor={color};strokeColor=#0000FF;strokeWidth=1.5;fontSize={fs};fontStyle=1;fontColor=#000000",
    "roundrect": "rounded=1;html=1;fillColor={color};strokeColor=#0000FF;strokeWidth=1.5;fontSize={fs};fontStyle=1;fontColor=#000000",
    "hexagon":   "shape=hexagon;html=1;fillColor={color};strokeColor=#0000FF;strokeWidth=1.5;fontSize={fs};fontStyle=1;fontColor=#000000",
    "halfrect":  "shape=mxgraph.bootstrap.halfRect;html=1;direction=south;fillColor={color};strokeColor=#0000FF;strokeWidth=1.5;fontSize={fs};fontStyle=1;fontColor=#000000",
    "step":      "shape=step;html=1;fillColor={color};strokeColor=#0000FF;strokeWidth=1.5;fontSize={fs};fontStyle=1;fontColor=#000000",
}


def detect_shape(style):
    """Detect shape type from a drawio style string."""
    if "ellipse" in style:
        return "ellipse"
    elif "hexagon" in style:
        return "hexagon"
    elif "halfRect" in style:
        return "halfrect"
    elif "step" in style:
        return "step"
    elif "rounded=1" in style:
        return "roundrect"
    else:
        return "rect"


def detect_color(style):
    m = re.search(r'fillColor=(#[0-9A-Fa-f]{6})', style)
    return m.group(1) if m else "#FFFF00"


def parse_drawio(filepath):
    """Parse a .drawio file. Returns (bg_image, page_w, page_h, devices, symbol_types, legend_box_info)."""
    tree = ET.parse(filepath)
    root = tree.getroot()
    diagram = root.find("diagram")
    model = diagram.find("mxGraphModel")

    page_w = int(model.get("pageWidth", "3024"))
    page_h = int(model.get("pageHeight", "2160"))

    cells = model.findall(".//mxCell")

    bg_image = None
    devices = []
    legend_box_info = None
    # Collect legend icon->label pairs to build symbol_types
    legend_icons = {}   # shortcode -> {shape, color}
    legend_labels = {}  # index -> label text (matched by y-position order)
    legend_icon_list = []
    legend_text_list = []

    for cell in cells:
        cell_id = cell.get("id", "")
        style = cell.get("style", "")
        value = cell.get("value", "")
        geom = cell.find("mxGeometry")
        parent = cell.get("parent", "")

        # background image
        if "shape=image;image=data:image/png," in style and geom is not None:
            b64_data = style.split("data:image/png,", 1)[1]
            if ";" in b64_data:
                b64_data = b64_data.split(";")[0]
            img_bytes = base64.b64decode(b64_data)
            bg_image = Image.open(io.BytesIO(img_bytes))
            bg_image.load()
            continue

        # legend box
        if cell_id == "legbox" and geom is not None:
            legend_box_info = {
                "x": float(geom.get("x", "0")),
                "y": float(geom.get("y", "0")),
                "w": float(geom.get("width", "340")),
                "h": float(geom.get("height", "740")),
                "title": value,
            }
            continue

        # legend children
        if parent == "legbox" and geom is not None:
            y = float(geom.get("y", "0"))
            if cell_id.startswith("legi"):
                legend_icon_list.append({"value": value.strip(), "y": y,
                                         "shape": detect_shape(style),
                                         "color": detect_color(style)})
            elif cell_id.startswith("legt"):
                legend_text_list.append({"value": value, "y": y})
            continue

        if cell_id in ("0", "1"):
            continue

        # device cell — accept ANY cell with geometry and a non-empty short value
        if geom is not None and value.strip():
            val = value.strip()
            # skip cells with very long values (likely text boxes, not device icons)
            if len(val) > 6:
                continue
            x = float(geom.get("x", "0"))
            y = float(geom.get("y", "0"))
            w = float(geom.get("width", "22"))
            h = float(geom.get("height", "22"))
            devices.append({
                "id": cell_id,
                "type": val,
                "x": x, "y": y, "w": w, "h": h,
                "orig_style": style,
            })

    # Build symbol_types from legend icons matched with legend texts by y-position
    legend_icon_list.sort(key=lambda e: e["y"])
    legend_text_list.sort(key=lambda e: e["y"])

    symbol_types = {}  # shortcode -> {label, shape, color, is_camera}

    # Camera shortcodes (yellow fill = camera by convention)
    camera_codes = {"MD", "360", "PTZ", "BC", "180", "HS", "MI"}

    for i, icon in enumerate(legend_icon_list):
        code = icon["value"]
        label = legend_text_list[i]["value"] if i < len(legend_text_list) else code
        symbol_types[code] = {
            "label": label,
            "shape": icon["shape"],
            "color": icon["color"],
            "is_camera": code in camera_codes or icon["color"].upper() == "#FFFF00",
        }

    # Also discover types from placed devices that might not be in the legend
    for dev in devices:
        if dev["type"] not in symbol_types:
            symbol_types[dev["type"]] = {
                "label": dev["type"],
                "shape": detect_shape(dev["orig_style"]),
                "color": detect_color(dev["orig_style"]),
                "is_camera": detect_color(dev["orig_style"]).upper() == "#FFFF00",
            }

    return bg_image, page_w, page_h, devices, symbol_types, legend_box_info


class CameraWireTool:
    def __init__(self, root, drawio_path):
        self.root = root
        self.root.title("Camera Wire Estimation Tool")
        self.root.state("zoomed")
        self.drawio_path = drawio_path

        # parse
        (self.bg_image, self.page_w, self.page_h,
         self.devices, self.symbol_types, self.legend_box) = parse_drawio(drawio_path)

        if self.bg_image is None:
            messagebox.showerror("Error", "No background image found in .drawio file")
            sys.exit(1)

        self.img_w, self.img_h = self.bg_image.size
        self.dx_scale = self.img_w / self.page_w
        self.dy_scale = self.img_h / self.page_h

        # If no legend box existed, create one at default position
        if self.legend_box is None:
            self.legend_box = {"x": 50, "y": 1400, "w": 340, "h": 740, "title": "Symbols"}

        # project file path (companion to .drawio)
        self.project_path = os.path.splitext(drawio_path)[0] + ".wiremap.json"

        # state
        self.scale_factor = None
        self.calibration_mode = False
        self.cal_points = []
        # wire routes: list of {points, length_px, length_in, device_id (str or None)}
        self.wire_routes = []
        self.current_wire_points = []
        self.wire_mode = False
        self.move_mode = True
        self.dragging_device = None
        self.drag_offset = (0, 0)
        self.selected_device = None
        self.zoom = 1.0
        self.offset_x = 0.0
        self.offset_y = 0.0
        self.pan_start = None
        self.unit_var = tk.StringVar(value="ft-in")
        self._tk_ref = None
        self._redraw_pending = False
        self._next_device_id = 100  # for adding new devices

        # figure out next available device ID from existing devices
        for d in self.devices:
            m = re.match(r's(\d+)', d["id"])
            if m:
                self._next_device_id = max(self._next_device_id, int(m.group(1)) + 1)

        # load project file if it exists
        self._load_project()

        self._build_ui()
        self.root.after(150, self._initial_render)

    # ================================================================ project file (JSON)
    def _load_project(self):
        """Load wire routes, scale, and state from companion .wiremap.json."""
        if not os.path.exists(self.project_path):
            return
        try:
            with open(self.project_path, "r") as f:
                data = json.load(f)
            self.scale_factor = data.get("scale_factor")
            self._loaded_unit = data.get("unit", "ft-in")
            for wr in data.get("wire_routes", []):
                pts = [tuple(p) for p in wr["points"]]
                px_len = self._calc_wire_length(pts)
                real_in = px_len * self.scale_factor if self.scale_factor else None
                self.wire_routes.append({
                    "points": pts,
                    "length_px": px_len,
                    "length_in": real_in,
                    "device_id": wr.get("device_id"),
                })
            nid = data.get("next_device_id")
            if nid:
                self._next_device_id = nid
        except Exception:
            pass  # ignore corrupt project files

    def _save_project(self):
        """Save wire routes, scale, and state to companion .wiremap.json."""
        data = {
            "drawio_file": os.path.basename(self.drawio_path),
            "scale_factor": self.scale_factor,
            "unit": self.unit_var.get() if isinstance(self.unit_var, tk.StringVar) else "ft-in",
            "next_device_id": self._next_device_id,
            "wire_routes": [],
        }
        for wr in self.wire_routes:
            data["wire_routes"].append({
                "points": [list(p) for p in wr["points"]],
                "device_id": wr.get("device_id"),
            })

        # summary section for quick reference
        summary = {}
        for wr in self.wire_routes:
            did = wr.get("device_id") or "unassigned"
            length = wr.get("length_in", 0) or 0
            if did not in summary:
                summary[did] = {"runs": 0, "total_inches": 0}
            summary[did]["runs"] += 1
            summary[did]["total_inches"] += length
        data["summary_by_device"] = summary

        with open(self.project_path, "w") as f:
            json.dump(data, f, indent=2)

    def _initial_render(self):
        self.root.update_idletasks()
        # apply loaded unit preference
        if hasattr(self, "_loaded_unit"):
            self.unit_var.set(self._loaded_unit)
        if self.scale_factor:
            self.scale_label.config(text=f"  Scale: 1px = {self.scale_factor:.4f}\"", foreground="green")
        self._fit_image()
        self._refresh_wire_list()
        self._redraw()
        self._bind_events()

    def _bind_events(self):
        self.canvas.bind("<ButtonPress-1>", self._on_left_press)
        self.canvas.bind("<B1-Motion>", self._on_left_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_left_release)
        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<ButtonPress-3>", self._on_right_click)
        self.canvas.bind("<MouseWheel>", self._on_mousewheel)
        self.canvas.bind("<ButtonPress-2>", self._on_pan_start)
        self.canvas.bind("<B2-Motion>", self._on_pan_move)
        self.canvas.bind("<ButtonRelease-2>", self._on_pan_end)
        self.canvas.bind("<Control-ButtonPress-1>", self._on_pan_start)
        self.canvas.bind("<Control-B1-Motion>", self._on_pan_move)
        self.canvas.bind("<Control-ButtonRelease-1>", self._on_pan_end)
        self.root.bind("<Delete>", self._delete_selected_wire)
        self.root.bind("<Escape>", lambda e: self._cancel_current())
        self.canvas.bind("<Configure>", lambda e: self._schedule_redraw())

    # ================================================================ UI
    def _build_ui(self):
        # ---- Menu bar ----
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)

        # File menu
        file_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="File", menu=file_menu)
        file_menu.add_command(label="Open .drawio...", command=self._file_open, accelerator="Ctrl+O")
        file_menu.add_separator()
        file_menu.add_command(label="Save", command=self._save_drawio, accelerator="Ctrl+S")
        file_menu.add_command(label="Save As...", command=self._file_save_as, accelerator="Ctrl+Shift+S")
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.root.quit)

        self.root.bind("<Control-s>", lambda e: self._save_drawio())
        self.root.bind("<Control-S>", lambda e: self._file_save_as())
        self.root.bind("<Control-o>", lambda e: self._file_open())

        # Edit menu
        edit_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Edit", menu=edit_menu)
        edit_menu.add_command(label="Undo Last Wire", command=self._undo_wire, accelerator="Ctrl+Z")
        edit_menu.add_command(label="Delete Selected Wire", command=self._delete_selected_wire)
        edit_menu.add_separator()
        edit_menu.add_command(label="Clear All Wires...", command=self._clear_wires)

        self.root.bind("<Control-z>", lambda e: self._undo_wire())

        # View menu
        view_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="View", menu=view_menu)
        view_menu.add_command(label="Fit View", command=lambda: (self._fit_image(), self._redraw()), accelerator="Home")
        view_menu.add_separator()
        view_menu.add_command(label="Set Scale (Calibrate)...", command=self._start_calibration)

        self.root.bind("<Home>", lambda e: (self._fit_image(), self._redraw()))

        # ---- Toolbar ----
        toolbar = ttk.Frame(self.root)
        toolbar.pack(side=tk.TOP, fill=tk.X)

        ttk.Button(toolbar, text="Set Scale", command=self._start_calibration).pack(side=tk.LEFT, padx=4, pady=2)
        ttk.Separator(toolbar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=4)

        self.move_btn = ttk.Button(toolbar, text="Move Mode [ON]", command=self._toggle_move)
        self.move_btn.pack(side=tk.LEFT, padx=4, pady=2)
        self.wire_btn = ttk.Button(toolbar, text="Wire Mode [OFF]", command=self._toggle_wire)
        self.wire_btn.pack(side=tk.LEFT, padx=4, pady=2)

        ttk.Separator(toolbar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=4)
        ttk.Button(toolbar, text="Fit View", command=lambda: (self._fit_image(), self._redraw())).pack(side=tk.LEFT, padx=4, pady=2)

        ttk.Separator(toolbar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=4)
        ttk.Label(toolbar, text="Units:").pack(side=tk.LEFT, padx=(4, 0))
        ttk.Radiobutton(toolbar, text="Ft-In", variable=self.unit_var, value="ft-in", command=self._refresh_wire_list).pack(side=tk.LEFT)
        ttk.Radiobutton(toolbar, text="Inches", variable=self.unit_var, value="inches", command=self._refresh_wire_list).pack(side=tk.LEFT)
        ttk.Radiobutton(toolbar, text="Feet", variable=self.unit_var, value="feet-dec", command=self._refresh_wire_list).pack(side=tk.LEFT)

        self.scale_label = ttk.Label(toolbar, text="  Scale: not set", foreground="red")
        self.scale_label.pack(side=tk.LEFT, padx=10)
        self.status_label = ttk.Label(toolbar, text="")
        self.status_label.pack(side=tk.RIGHT, padx=10)

        # main layout
        main = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        main.pack(fill=tk.BOTH, expand=True)

        canvas_frame = ttk.Frame(main)
        self.canvas = tk.Canvas(canvas_frame, bg="#2b2b2b", cursor="crosshair")
        self.canvas.pack(fill=tk.BOTH, expand=True)
        main.add(canvas_frame, weight=4)

        # side panel with notebook tabs
        side = ttk.Frame(main, width=320)
        main.add(side, weight=1)

        notebook = ttk.Notebook(side)
        notebook.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)

        # --- Tab 1: Devices & Wires ---
        tab_devices = ttk.Frame(notebook)
        notebook.add(tab_devices, text="Devices & Wires")

        ttk.Label(tab_devices, text="Placed Devices", font=("Segoe UI", 10, "bold")).pack(anchor=tk.W, padx=6, pady=(6, 2))
        dev_frame = ttk.Frame(tab_devices)
        dev_frame.pack(fill=tk.BOTH, expand=True, padx=4, pady=2)
        self.dev_listbox = tk.Listbox(dev_frame, font=("Consolas", 9), selectmode=tk.SINGLE)
        sb1 = ttk.Scrollbar(dev_frame, orient=tk.VERTICAL, command=self.dev_listbox.yview)
        self.dev_listbox.config(yscrollcommand=sb1.set)
        self.dev_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb1.pack(side=tk.RIGHT, fill=tk.Y)
        self.dev_listbox.bind("<<ListboxSelect>>", self._on_dev_select)

        ttk.Separator(tab_devices, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=4, pady=4)

        ttk.Label(tab_devices, text="Wire Runs", font=("Segoe UI", 10, "bold")).pack(anchor=tk.W, padx=6, pady=(2, 2))
        wire_frame = ttk.Frame(tab_devices)
        wire_frame.pack(fill=tk.BOTH, expand=True, padx=4, pady=2)
        self.wire_listbox = tk.Listbox(wire_frame, font=("Consolas", 9), selectmode=tk.SINGLE)
        sb2 = ttk.Scrollbar(wire_frame, orient=tk.VERTICAL, command=self.wire_listbox.yview)
        self.wire_listbox.config(yscrollcommand=sb2.set)
        self.wire_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb2.pack(side=tk.RIGHT, fill=tk.Y)
        self.wire_listbox.bind("<<ListboxSelect>>", self._on_wire_select)

        ttk.Button(tab_devices, text="Delete Selected Wire", command=self._delete_selected_wire).pack(anchor=tk.W, padx=6, pady=2)

        ttk.Separator(tab_devices, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=4, pady=4)
        self.total_label = ttk.Label(tab_devices, text="Total Wire: --", font=("Segoe UI", 12, "bold"))
        self.total_label.pack(anchor=tk.W, padx=6, pady=4)

        # --- Tab 2: Symbol Editor ---
        tab_symbols = ttk.Frame(notebook)
        notebook.add(tab_symbols, text="Symbol Editor")

        ttk.Label(tab_symbols, text="Symbol Types", font=("Segoe UI", 10, "bold")).pack(anchor=tk.W, padx=6, pady=(6, 2))

        sym_frame = ttk.Frame(tab_symbols)
        sym_frame.pack(fill=tk.BOTH, expand=True, padx=4, pady=2)
        self.sym_listbox = tk.Listbox(sym_frame, font=("Consolas", 9), selectmode=tk.SINGLE)
        sb3 = ttk.Scrollbar(sym_frame, orient=tk.VERTICAL, command=self.sym_listbox.yview)
        self.sym_listbox.config(yscrollcommand=sb3.set)
        self.sym_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb3.pack(side=tk.RIGHT, fill=tk.Y)

        btn_frame = ttk.Frame(tab_symbols)
        btn_frame.pack(fill=tk.X, padx=4, pady=4)
        ttk.Button(btn_frame, text="Edit Selected", command=self._edit_symbol_type).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="Add New Type", command=self._add_symbol_type).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="Delete Type", command=self._delete_symbol_type).pack(side=tk.LEFT, padx=2)

        ttk.Separator(tab_symbols, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=4, pady=4)

        ttk.Label(tab_symbols, text="Place Device", font=("Segoe UI", 10, "bold")).pack(anchor=tk.W, padx=6, pady=(2, 2))
        place_frame = ttk.Frame(tab_symbols)
        place_frame.pack(fill=tk.X, padx=6, pady=2)
        ttk.Label(place_frame, text="Type:").pack(side=tk.LEFT)
        self.place_type_var = tk.StringVar()
        self.place_combo = ttk.Combobox(place_frame, textvariable=self.place_type_var, width=8, state="readonly")
        self.place_combo.pack(side=tk.LEFT, padx=4)
        ttk.Button(place_frame, text="Place on Map", command=self._start_place_device).pack(side=tk.LEFT, padx=4)
        self.placing_device = False

        # populate
        self._refresh_symbol_list()
        self._refresh_device_list()

    # ================================================================ symbol list helpers
    def _refresh_symbol_list(self):
        self.sym_listbox.delete(0, tk.END)
        # count instances
        counts = {}
        for d in self.devices:
            counts[d["type"]] = counts.get(d["type"], 0) + 1

        self._sym_keys = sorted(self.symbol_types.keys())
        for code in self._sym_keys:
            info = self.symbol_types[code]
            n = counts.get(code, 0)
            cam_tag = " [cam]" if info["is_camera"] else ""
            self.sym_listbox.insert(tk.END,
                f'  {code:5s} | {info["label"][:22]:22s} | {info["shape"]:9s} | x{n}{cam_tag}')

        # update place combobox
        self.place_combo["values"] = self._sym_keys
        if self._sym_keys and not self.place_type_var.get():
            self.place_type_var.set(self._sym_keys[0])

    def _refresh_device_list(self):
        self.dev_listbox.delete(0, tk.END)
        self.devices.sort(key=lambda d: d["id"])

        # calculate wire totals per device
        wire_by_device = {}
        for wr in self.wire_routes:
            did = wr.get("device_id")
            if did:
                if did not in wire_by_device:
                    wire_by_device[did] = {"runs": 0, "total_in": 0.0}
                wire_by_device[did]["runs"] += 1
                wire_by_device[did]["total_in"] += (wr.get("length_in") or 0)

        for d in self.devices:
            info = self.symbol_types.get(d["type"], {})
            label = info.get("label", d["type"])
            wire_info = wire_by_device.get(d["id"])
            if wire_info:
                wire_str = f' [{wire_info["runs"]}w: {self._format_length(wire_info["total_in"])}]'
            else:
                wire_str = ""
            self.dev_listbox.insert(tk.END, f'  {d["id"]:6s} {d["type"]:5s}{wire_str}')

    # ================================================================ symbol editor dialog
    def _edit_symbol_type(self):
        sel = self.sym_listbox.curselection()
        if not sel:
            messagebox.showinfo("Select", "Select a symbol type to edit")
            return
        old_code = self._sym_keys[sel[0]]
        info = self.symbol_types[old_code]
        self._open_type_editor(old_code, info, is_new=False)

    def _add_symbol_type(self):
        info = {"label": "New Device", "shape": "ellipse", "color": "#FFFF00", "is_camera": False}
        self._open_type_editor("NEW", info, is_new=True)

    def _open_type_editor(self, old_code, info, is_new=False):
        dlg = tk.Toplevel(self.root)
        dlg.title("Edit Symbol Type" if not is_new else "Add Symbol Type")
        dlg.geometry("420x340")
        dlg.transient(self.root)
        dlg.grab_set()
        dlg.resizable(False, False)

        pad = {"padx": 10, "pady": 4}
        row = 0

        ttk.Label(dlg, text="Shortcode (shown inside icon):").grid(row=row, column=0, sticky=tk.W, **pad)
        code_var = tk.StringVar(value=old_code if not is_new else "")
        code_entry = ttk.Entry(dlg, textvariable=code_var, width=10)
        code_entry.grid(row=row, column=1, sticky=tk.W, **pad)
        row += 1

        ttk.Label(dlg, text="Label (legend description):").grid(row=row, column=0, sticky=tk.W, **pad)
        label_var = tk.StringVar(value=info["label"])
        ttk.Entry(dlg, textvariable=label_var, width=30).grid(row=row, column=1, sticky=tk.W, **pad)
        row += 1

        ttk.Label(dlg, text="Shape:").grid(row=row, column=0, sticky=tk.W, **pad)
        shape_var = tk.StringVar(value=info["shape"])
        ttk.Combobox(dlg, textvariable=shape_var, values=SHAPE_OPTIONS, state="readonly", width=12).grid(row=row, column=1, sticky=tk.W, **pad)
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
            c = colorchooser.askcolor(initialcolor=color_var.get(), title="Pick color")
            if c[1]:
                color_var.set(c[1].upper())
                color_swatch.config(bg=c[1])
                color_hex_label.config(text=c[1].upper())

        ttk.Button(color_frame, text="Pick...", command=pick_color).pack(side=tk.LEFT)
        row += 1

        cam_var = tk.BooleanVar(value=info["is_camera"])
        ttk.Checkbutton(dlg, text="Is a camera (counts toward camera totals)", variable=cam_var).grid(
            row=row, column=0, columnspan=2, sticky=tk.W, **pad)
        row += 1

        if not is_new:
            count = sum(1 for d in self.devices if d["type"] == old_code)
            ttk.Label(dlg, text=f"Instances on map: {count}", foreground="gray").grid(
                row=row, column=0, columnspan=2, sticky=tk.W, **pad)
            row += 1

        # preview
        preview_canvas = tk.Canvas(dlg, width=60, height=60, bg="white", relief=tk.SUNKEN)
        preview_canvas.grid(row=0, column=2, rowspan=3, padx=10, pady=10)

        def update_preview(*_):
            preview_canvas.delete("all")
            s = shape_var.get()
            c = color_var.get()
            cx, cy, r = 30, 30, 18
            if s == "ellipse":
                preview_canvas.create_oval(cx-r, cy-r, cx+r, cy+r, fill=c, outline="#0000FF", width=2)
            elif s in ("rect", "roundrect"):
                preview_canvas.create_rectangle(cx-r, cy-r, cx+r, cy+r, fill=c, outline="#0000FF", width=2)
            elif s == "hexagon":
                pts = []
                for a in range(0, 360, 60):
                    rad = math.radians(a - 30)
                    pts.extend([cx + r*math.cos(rad), cy + r*math.sin(rad)])
                preview_canvas.create_polygon(pts, fill=c, outline="#0000FF", width=2)
            elif s == "halfrect":
                preview_canvas.create_arc(cx-r, cy-r, cx+r, cy+r, start=0, extent=180, fill=c, outline="#0000FF", width=2)
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
                messagebox.showerror("Error", "Shortcode cannot be empty")
                return
            if len(new_code) > 5:
                messagebox.showerror("Error", "Shortcode max 5 characters")
                return

            # check for conflict if code changed
            if new_code != old_code and new_code in self.symbol_types:
                messagebox.showerror("Error", f'Shortcode "{new_code}" already exists')
                return

            new_info = {
                "label": label_var.get().strip() or new_code,
                "shape": shape_var.get(),
                "color": color_var.get().upper(),
                "is_camera": cam_var.get(),
            }

            if is_new:
                self.symbol_types[new_code] = new_info
            else:
                # Rename: update the type registry
                if new_code != old_code:
                    del self.symbol_types[old_code]
                    # Update ALL device instances
                    for d in self.devices:
                        if d["type"] == old_code:
                            d["type"] = new_code
                self.symbol_types[new_code] = new_info

            self._refresh_symbol_list()
            self._refresh_device_list()
            self._redraw()
            dlg.destroy()

        ttk.Button(dlg, text="Apply", command=apply).grid(row=row, column=0, columnspan=3, pady=12)

    def _delete_symbol_type(self):
        sel = self.sym_listbox.curselection()
        if not sel:
            return
        code = self._sym_keys[sel[0]]
        count = sum(1 for d in self.devices if d["type"] == code)
        if count > 0:
            if not messagebox.askyesno("Delete Type",
                    f'Delete symbol type "{code}"?\n'
                    f'This will also remove all {count} instances from the map.'):
                return
            self.devices = [d for d in self.devices if d["type"] != code]
        del self.symbol_types[code]
        self._refresh_symbol_list()
        self._refresh_device_list()
        self._redraw()

    # ================================================================ place new device
    def _start_place_device(self):
        code = self.place_type_var.get()
        if not code or code not in self.symbol_types:
            messagebox.showinfo("Select", "Pick a symbol type first")
            return
        self.placing_device = True
        self._place_type = code
        self.status_label.config(text=f"Click on map to place a '{code}' device", foreground="green")

    def _do_place_device(self, ix, iy):
        code = self._place_type
        dx, dy = self._img_to_drawio(ix, iy)
        new_id = f"s{self._next_device_id}"
        self._next_device_id += 1
        info = self.symbol_types[code]
        self.devices.append({
            "id": new_id,
            "type": code,
            "x": dx, "y": dy, "w": 22, "h": 22,
            "orig_style": SHAPE_STYLES.get(info["shape"], SHAPE_STYLES["ellipse"]).format(
                color=info["color"], fs=8),
        })
        self.placing_device = False
        self._refresh_symbol_list()
        self._refresh_device_list()
        self._redraw()
        self.status_label.config(text=f"Placed {code} ({new_id})")

    # ================================================================ mode toggles
    def _toggle_move(self):
        self.move_mode = True
        self.wire_mode = False
        self._cancel_current()
        self.move_btn.config(text="Move Mode [ON]")
        self.wire_btn.config(text="Wire Mode [OFF]")
        self.canvas.config(cursor="hand2")

    def _toggle_wire(self):
        self.wire_mode = True
        self.move_mode = False
        self.wire_btn.config(text="Wire Mode [ON]")
        self.move_btn.config(text="Move Mode [OFF]")
        self.canvas.config(cursor="crosshair")
        if self.selected_device:
            dev = self.selected_device
            info = self.symbol_types.get(dev["type"], {})
            self.status_label.config(
                text=f'Wire Mode for {dev["id"]} ({dev["type"]} - {info.get("label","")}) — click to route, right-click to finish',
                foreground="cyan")
        else:
            self.status_label.config(
                text="Wire Mode (no device selected — select a device first, or wire will be unassigned)",
                foreground="orange")

    # ================================================================ coords
    def _drawio_to_img(self, dx, dy):
        return dx * self.dx_scale, dy * self.dy_scale

    def _img_to_drawio(self, ix, iy):
        return ix / self.dx_scale, iy / self.dy_scale

    def _img_to_canvas(self, ix, iy):
        return ix * self.zoom + self.offset_x, iy * self.zoom + self.offset_y

    def _canvas_to_img(self, cx, cy):
        return (cx - self.offset_x) / self.zoom, (cy - self.offset_y) / self.zoom

    # ================================================================ rendering
    def _fit_image(self):
        cw = self.canvas.winfo_width() or 800
        ch = self.canvas.winfo_height() or 600
        zx = cw / self.img_w
        zy = ch / self.img_h
        self.zoom = min(zx, zy) * 0.95
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
        max_dim = 4000
        if dst_w > max_dim or dst_h > max_dim:
            sc = max_dim / max(dst_w, dst_h)
            dst_w = max(1, int(dst_w * sc))
            dst_h = max(1, int(dst_h * sc))
        resized = cropped.resize((dst_w, dst_h), Image.LANCZOS)
        tk_img = ImageTk.PhotoImage(resized)
        place_x, place_y = self._img_to_canvas(src_x0, src_y0)
        return tk_img, place_x, place_y

    def _schedule_redraw(self):
        if not self._redraw_pending:
            self._redraw_pending = True
            self.root.after(16, self._do_deferred_redraw)

    def _do_deferred_redraw(self):
        self._redraw_pending = False
        self._redraw()

    def _redraw(self):
        self.canvas.delete("all")
        tk_img, px, py = self._get_viewport_image()
        if tk_img is not None:
            self.canvas.create_image(px, py, anchor=tk.NW, image=tk_img, tags="img")
            self._tk_ref = tk_img

        for i, route in enumerate(self.wire_routes):
            self._draw_wire_route(route, i)

        if self.current_wire_points:
            pts_canvas = [self._img_to_canvas(*p) for p in self.current_wire_points]
            for j in range(len(pts_canvas) - 1):
                self.canvas.create_line(pts_canvas[j][0], pts_canvas[j][1],
                                        pts_canvas[j+1][0], pts_canvas[j+1][1],
                                        fill="#00BFFF", width=2, dash=(6, 3), tags="wip_wire")
            for cx, cy in pts_canvas:
                self.canvas.create_oval(cx-3, cy-3, cx+3, cy+3, fill="#00BFFF", outline="white", tags="wip_wire")

        for d in self.devices:
            self._draw_device(d)

        self._draw_legend()

        for pt in self.cal_points:
            cx, cy = self._img_to_canvas(*pt)
            self.canvas.create_oval(cx-5, cy-5, cx+5, cy+5, fill="yellow", outline="black", width=2, tags="cal")

    # ---------------------------------------------------------------- draw legend (auto-generated from symbol_types)
    def _draw_legend(self):
        box = self.legend_box
        bx, by = self._drawio_to_img(box["x"], box["y"])
        bw = box["w"] * self.dx_scale
        bh_per_row = 38  # drawio units per legend row
        sorted_codes = sorted(self.symbol_types.keys())
        n_types = len(sorted_codes)
        # auto-size box height
        needed_h = (n_types * bh_per_row + 50) * self.dy_scale
        cx, cy = self._img_to_canvas(bx, by)
        cw = bw * self.zoom
        ch = needed_h * self.zoom

        self.canvas.create_rectangle(cx, cy, cx + cw, cy + ch,
                                     fill="white", outline="#0000FF", width=2, tags="legend")

        title_fs = max(7, min(16, int(14 * self.zoom * self.dx_scale)))
        self.canvas.create_text(cx + cw / 2, cy + 10 * self.zoom * self.dy_scale,
                                text=box["title"], font=("Segoe UI", title_fs, "bold"),
                                fill="black", anchor=tk.N, tags="legend")

        icon_fs = max(5, min(12, int(8 * self.zoom * self.dx_scale)))
        text_fs = max(6, min(13, int(11 * self.zoom * self.dx_scale)))

        for i, code in enumerate(sorted_codes):
            info = self.symbol_types[code]
            # icon at x=20, text at x=55 (drawio units relative to box)
            icon_dx = box["x"] + 20
            icon_dy = box["y"] + 38 + i * bh_per_row
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

            # label text
            text_dx = box["x"] + 55
            text_dy = box["y"] + 38 + i * bh_per_row
            tx, ty = self._drawio_to_img(text_dx, text_dy)
            tcx, tcy = self._img_to_canvas(tx, ty)
            count = sum(1 for d in self.devices if d["type"] == code)
            label_text = f'{info["label"]}  ({count})'
            self.canvas.create_text(tcx, tcy + ich / 2, text=label_text,
                                    font=("Segoe UI", text_fs), fill="black",
                                    anchor=tk.W, tags="legend")

    def _draw_shape(self, shape, color, center_x, center_y, r, bx, by, bw, bh, tag):
        if shape == "ellipse":
            self.canvas.create_oval(center_x-r, center_y-r, center_x+r, center_y+r,
                                    fill=color, outline="#0000FF", width=1.5, tags=tag)
        elif shape in ("rect", "roundrect"):
            self.canvas.create_rectangle(bx, by, bx+bw, by+bh,
                                         fill=color, outline="#0000FF", width=1.5, tags=tag)
        elif shape == "hexagon":
            pts = []
            for a in range(0, 360, 60):
                rad = math.radians(a - 30)
                pts.extend([center_x + r*math.cos(rad), center_y + r*math.sin(rad)])
            self.canvas.create_polygon(pts, fill=color, outline="#0000FF", width=1.5, tags=tag)
        elif shape == "halfrect":
            self.canvas.create_arc(center_x-r, center_y-r, center_x+r, center_y+r,
                                   start=0, extent=180, fill=color, outline="#0000FF", width=1.5, tags=tag)
        elif shape == "step":
            pts = [bx, by, bx+bw*0.7, by, bx+bw, by+bh*0.5, bx+bw*0.7, by+bh, bx, by+bh]
            self.canvas.create_polygon(pts, fill=color, outline="#0000FF", width=1.5, tags=tag)
        else:
            self.canvas.create_oval(center_x-r, center_y-r, center_x+r, center_y+r,
                                    fill=color, outline="#0000FF", width=1.5, tags=tag)

    # ---------------------------------------------------------------- draw device
    def _draw_device(self, dev):
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

        is_selected = (self.selected_device is not None and self.selected_device["id"] == dev["id"])
        outline_color = "#FF0000" if is_selected else "#0000FF"
        outline_width = 3 if is_selected else 2
        tag = f'dev_{dev["id"]}'

        shape = info["shape"]
        color = info["color"]

        if shape == "ellipse":
            self.canvas.create_oval(center_x-r, center_y-r, center_x+r, center_y+r,
                                    fill=color, outline=outline_color, width=outline_width, tags=tag)
        elif shape in ("rect", "roundrect"):
            self.canvas.create_rectangle(cx, cy, cx+cw, cy+ch,
                                         fill=color, outline=outline_color, width=outline_width, tags=tag)
        elif shape == "hexagon":
            pts = []
            for a in range(0, 360, 60):
                rad = math.radians(a - 30)
                pts.extend([center_x + r*math.cos(rad), center_y + r*math.sin(rad)])
            self.canvas.create_polygon(pts, fill=color, outline=outline_color, width=outline_width, tags=tag)
        elif shape == "halfrect":
            self.canvas.create_arc(center_x-r, center_y-r, center_x+r, center_y+r,
                                   start=0, extent=180, fill=color, outline=outline_color, width=outline_width, tags=tag)
        elif shape == "step":
            pts = [cx, cy, cx+cw*0.7, cy, cx+cw, cy+ch*0.5, cx+cw*0.7, cy+ch, cx, cy+ch]
            self.canvas.create_polygon(pts, fill=color, outline=outline_color, width=outline_width, tags=tag)
        else:
            self.canvas.create_oval(center_x-r, center_y-r, center_x+r, center_y+r,
                                    fill=color, outline=outline_color, width=outline_width, tags=tag)

        font_size = max(6, min(14, int(8 * self.zoom * self.dx_scale)))
        self.canvas.create_text(center_x, center_y, text=dev["type"],
                                font=("Arial", font_size, "bold"), fill="#000000", tags=tag)

    # ---------------------------------------------------------------- wire drawing
    def _draw_wire_route(self, route, idx):
        if len(route["points"]) < 2:
            return
        sel = self.wire_listbox.curselection()
        is_selected = sel and sel[0] == idx

        # use device color if assigned, otherwise cyan
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
            self.canvas.create_oval(cx-3, cy-3, cx+3, cy+3, fill=color, outline="white", tags=f"wire_{idx}")
        mid_idx = len(pts_canvas) // 2
        mx, my = pts_canvas[mid_idx]
        length_in = route.get("length_in")
        length_str = self._format_length(length_in) if length_in is not None else f'{route["length_px"]:.0f}px'
        dev_tag = f'{did}: ' if did else ''
        label = f'{dev_tag}{length_str}'
        self.canvas.create_text(mx, my - 12, text=label, fill="#00FFFF",
                                font=("Consolas", 9, "bold"), tags=f"wire_{idx}")

    # ================================================================ formatting
    def _format_length(self, inches):
        if inches is None:
            return "--"
        unit = self.unit_var.get()
        if unit == "inches":
            return f'{inches:.2f}"'
        elif unit == "feet-dec":
            return f"{inches / 12:.2f} ft"
        else:
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

    # ================================================================ hit testing
    def _device_at(self, ix, iy):
        hit = None
        for d in self.devices:
            dx, dy = self._drawio_to_img(d["x"], d["y"])
            dw, dh = d["w"] * self.dx_scale, d["h"] * self.dy_scale
            margin = max(dw, dh) * 0.3
            if dx - margin <= ix <= dx + dw + margin and dy - margin <= iy <= dy + dh + margin:
                info = self.symbol_types.get(d["type"], {})
                if info.get("is_camera"):
                    return d
                hit = d
        return hit

    # ================================================================ calibration
    def _start_calibration(self):
        self.calibration_mode = True
        self.cal_points = []
        self.status_label.config(text="CALIBRATE: Click two points of a known distance", foreground="orange")

    def _finish_calibration(self):
        p1, p2 = self.cal_points
        px_dist = math.dist(p1, p2)
        if px_dist < 1:
            messagebox.showerror("Error", "Points too close together")
            self.cal_points = []
            return
        dlg = tk.Toplevel(self.root)
        dlg.title("Enter Known Distance")
        dlg.geometry("340x150")
        dlg.transient(self.root)
        dlg.grab_set()
        ttk.Label(dlg, text="Enter the real-world distance between the two points:").pack(pady=(12, 4))
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
            self.scale_factor = total_in / px_dist
            self.scale_label.config(text=f"  Scale: 1px = {self.scale_factor:.4f}\"", foreground="green")
            self._recalc_all_wires()
            self._refresh_wire_list()
            self._redraw()
            dlg.destroy()

        ttk.Button(dlg, text="Apply", command=apply).pack(pady=8)
        self.calibration_mode = False
        self.cal_points = []
        self.status_label.config(text="", foreground="black")

    # ================================================================ wire math
    def _calc_wire_length(self, points):
        total = 0.0
        for i in range(len(points) - 1):
            total += math.dist(points[i], points[i+1])
        return total

    def _recalc_all_wires(self):
        for route in self.wire_routes:
            route["length_px"] = self._calc_wire_length(route["points"])
            route["length_in"] = route["length_px"] * self.scale_factor if self.scale_factor else None

    # ================================================================ mouse handlers
    def _on_left_press(self, event):
        if event.state & 0x4:
            return
        ix, iy = self._canvas_to_img(event.x, event.y)

        if self.calibration_mode:
            self.cal_points.append((ix, iy))
            cx, cy = self._img_to_canvas(ix, iy)
            self.canvas.create_oval(cx-5, cy-5, cx+5, cy+5, fill="yellow", outline="black", width=2, tags="cal")
            if len(self.cal_points) == 1:
                self.status_label.config(text="CALIBRATE: Click the second point")
            elif len(self.cal_points) == 2:
                self._finish_calibration()
            return

        if self.placing_device:
            self._do_place_device(ix, iy)
            return

        if self.move_mode:
            dev = self._device_at(ix, iy)
            if dev is not None:
                self.selected_device = dev
                self.dragging_device = dev
                dx, dy = self._drawio_to_img(dev["x"], dev["y"])
                self.drag_offset = (ix - dx, iy - dy)
                # show wire info for selected device
                dev_wires = [w for w in self.wire_routes if w.get("device_id") == dev["id"]]
                if dev_wires:
                    total_in = sum(w.get("length_in", 0) or 0 for w in dev_wires)
                    self.status_label.config(
                        text=f'Selected {dev["id"]} ({dev["type"]}) — {len(dev_wires)} wire run(s), total: {self._format_length(total_in)}')
                else:
                    self.status_label.config(
                        text=f'Selected {dev["id"]} ({dev["type"]}) — no wires (switch to Wire Mode to add)')
                self._redraw()
                return
            self.selected_device = None
            self.dragging_device = None
            self.status_label.config(text="")
            self._redraw()

        if self.wire_mode:
            self.current_wire_points.append((ix, iy))
            self._redraw()
            if len(self.current_wire_points) == 1:
                self.status_label.config(text="Wire: click to add points, right-click to finish route")

    def _on_left_drag(self, event):
        if event.state & 0x4:
            return
        if self.dragging_device is not None:
            ix, iy = self._canvas_to_img(event.x, event.y)
            new_ix = ix - self.drag_offset[0]
            new_iy = iy - self.drag_offset[1]
            new_dx, new_dy = self._img_to_drawio(new_ix, new_iy)
            self.dragging_device["x"] = new_dx
            self.dragging_device["y"] = new_dy
            self._schedule_redraw()

    def _on_left_release(self, event):
        if self.dragging_device is not None:
            self.dragging_device = None
            self._redraw()

    def _on_motion(self, event):
        if self.wire_mode and self.current_wire_points:
            self.canvas.delete("wire_preview")
            last = self.current_wire_points[-1]
            sx, sy = self._img_to_canvas(*last)
            self.canvas.create_line(sx, sy, event.x, event.y,
                                    fill="#00BFFF", width=2, dash=(4, 4), tags="wire_preview")
            ix, iy = self._canvas_to_img(event.x, event.y)
            all_pts = self.current_wire_points + [(ix, iy)]
            total_px = self._calc_wire_length(all_pts)
            if self.scale_factor:
                total_in = total_px * self.scale_factor
                self.status_label.config(text=f"Wire route: {self._format_length(total_in)}  |  Right-click to finish")
            else:
                self.status_label.config(text=f"Wire route: {total_px:.0f}px  |  Right-click to finish")

    def _on_right_click(self, event):
        if self.wire_mode and len(self.current_wire_points) >= 2:
            self._finish_wire_route()
        else:
            self._cancel_current()

    def _finish_wire_route(self):
        if len(self.current_wire_points) < 2:
            self._cancel_current()
            return
        px_len = self._calc_wire_length(self.current_wire_points)
        real_in = px_len * self.scale_factor if self.scale_factor else None
        device_id = self.selected_device["id"] if self.selected_device else None
        self.wire_routes.append({
            "points": list(self.current_wire_points),
            "length_px": px_len,
            "length_in": real_in,
            "device_id": device_id,
        })
        self.current_wire_points = []
        self._refresh_wire_list()
        self._refresh_device_list()
        self._redraw()
        dev_label = f' for {device_id}' if device_id else ' (unassigned)'
        self.status_label.config(text=f"Wire route saved{dev_label}. Click to start a new one.")

    def _cancel_current(self):
        self.current_wire_points = []
        self.placing_device = False
        self.canvas.delete("wire_preview")
        self.canvas.delete("wip_wire")
        self.status_label.config(text="")

    def _on_mousewheel(self, event):
        factor = 1.15 if event.delta > 0 else 1 / 1.15
        new_zoom = max(0.02, min(self.zoom * factor, 15.0))
        ix, iy = self._canvas_to_img(event.x, event.y)
        self.zoom = new_zoom
        self.offset_x = event.x - ix * self.zoom
        self.offset_y = event.y - iy * self.zoom
        self._schedule_redraw()

    def _on_pan_start(self, event):
        self.pan_start = (event.x, event.y)

    def _on_pan_move(self, event):
        if self.pan_start:
            dx = event.x - self.pan_start[0]
            dy = event.y - self.pan_start[1]
            self.offset_x += dx
            self.offset_y += dy
            self.pan_start = (event.x, event.y)
            self._schedule_redraw()

    def _on_pan_end(self, event):
        self.pan_start = None
        self._redraw()

    # ================================================================ list management
    def _refresh_wire_list(self):
        self.wire_listbox.delete(0, tk.END)
        total = 0.0
        for i, route in enumerate(self.wire_routes):
            if route["length_in"] is not None:
                label = self._format_length(route["length_in"])
                total += route["length_in"]
            else:
                label = f'{route["length_px"]:.0f}px'
            n_segs = len(route["points"]) - 1
            did = route.get("device_id") or "---"
            self.wire_listbox.insert(tk.END, f"  {did:6s} | {n_segs} seg | {label}")
        if self.scale_factor and self.wire_routes:
            self.total_label.config(text=f"Total Wire: {self._format_length(total)}")
        elif self.wire_routes:
            total_px = sum(r["length_px"] for r in self.wire_routes)
            self.total_label.config(text=f"Total Wire: {total_px:.0f}px (calibrate first)")
        else:
            self.total_label.config(text="Total Wire: --")

    def _on_dev_select(self, event):
        sel = self.dev_listbox.curselection()
        if sel:
            self.selected_device = self.devices[sel[0]]
            if self.wire_mode:
                dev = self.selected_device
                info = self.symbol_types.get(dev["type"], {})
                self.status_label.config(
                    text=f'Wire Mode for {dev["id"]} ({dev["type"]}) — click to route, right-click to finish',
                    foreground="cyan")
            self._redraw()

    def _on_wire_select(self, event):
        self._redraw()

    def _delete_selected_wire(self, event=None):
        sel = self.wire_listbox.curselection()
        if not sel:
            return
        self.wire_routes.pop(sel[0])
        self._refresh_wire_list()
        self._refresh_device_list()
        self._redraw()

    def _undo_wire(self):
        if self.wire_routes:
            self.wire_routes.pop()
            self._refresh_wire_list()
            self._refresh_device_list()
            self._redraw()

    def _clear_wires(self):
        if self.wire_routes and not messagebox.askyesno("Confirm", "Clear all wire routes?"):
            return
        self.wire_routes.clear()
        self._refresh_wire_list()
        self._refresh_device_list()
        self._redraw()

    # ================================================================ file operations
    def _file_open(self):
        path = filedialog.askopenfilename(
            title="Open .drawio file",
            initialdir=os.path.dirname(self.drawio_path),
            filetypes=[("Draw.io files", "*.drawio")])
        if not path:
            return
        # relaunch with new file
        self.root.destroy()
        new_root = tk.Tk()
        CameraWireTool(new_root, path)
        new_root.mainloop()

    def _file_save_as(self):
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
        self.root.title(f"Camera Wire Estimation Tool — {os.path.basename(path)}")
        self._save_drawio()

    def _save_drawio(self):
        """Write ALL changes back to the .drawio file:
        - Updated device positions and renamed shortcodes
        - Updated device styles (shape/color changes)
        - Regenerated legend from symbol_types
        """
        tree = ET.parse(self.drawio_path)
        xml_root = tree.getroot()
        root_elem = xml_root.find(".//root")
        if root_elem is None:
            root_elem = xml_root.find(".//mxGraphModel/root")

        # --- Update existing device cells and track which IDs exist in XML ---
        xml_cells = root_elem.findall("mxCell")
        existing_ids = set()

        for cell in xml_cells:
            cid = cell.get("id", "")
            existing_ids.add(cid)

        # Build device map
        dev_map = {d["id"]: d for d in self.devices}

        # Update or remove existing cells
        cells_to_remove = []
        for cell in xml_cells:
            cid = cell.get("id", "")
            parent = cell.get("parent", "")

            # Update device cells
            if cid in dev_map:
                dev = dev_map[cid]
                info = self.symbol_types.get(dev["type"], {})
                cell.set("value", dev["type"])
                # Update style
                fs = 7 if len(dev["type"]) >= 3 else 8
                new_style = SHAPE_STYLES.get(info.get("shape", "ellipse"), SHAPE_STYLES["ellipse"]).format(
                    color=info.get("color", "#FFFF00"), fs=fs)
                cell.set("style", new_style)
                geom = cell.find("mxGeometry")
                if geom is not None:
                    geom.set("x", f"{dev['x']:.1f}")
                    geom.set("y", f"{dev['y']:.1f}")

            # Mark old legend children for removal
            if parent == "legbox":
                cells_to_remove.append(cell)
            if cid == "legbox":
                cells_to_remove.append(cell)

        for cell in cells_to_remove:
            root_elem.remove(cell)

        # --- Add new device cells (ones that don't exist in XML yet) ---
        for dev in self.devices:
            if dev["id"] not in existing_ids:
                info = self.symbol_types.get(dev["type"], {})
                fs = 7 if len(dev["type"]) >= 3 else 8
                style = SHAPE_STYLES.get(info.get("shape", "ellipse"), SHAPE_STYLES["ellipse"]).format(
                    color=info.get("color", "#FFFF00"), fs=fs)
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

        # --- Remove device cells that were deleted from self.devices ---
        current_ids = {d["id"] for d in self.devices}
        for cell in list(root_elem.findall("mxCell")):
            cid = cell.get("id", "")
            if cid.startswith("s") and cid not in current_ids and cid not in ("0", "1"):
                # Only remove if it looks like a device cell
                val = cell.get("value", "")
                if val and len(val) <= 6:
                    root_elem.remove(cell)

        # --- Regenerate legend ---
        box = self.legend_box
        sorted_codes = sorted(self.symbol_types.keys())
        row_h = 38
        total_h = len(sorted_codes) * row_h + 50

        legbox = ET.SubElement(root_elem, "mxCell")
        legbox.set("id", "legbox")
        legbox.set("value", box["title"])
        legbox.set("style", "rounded=0;html=1;fillColor=#FFFFFF;strokeColor=#0000FF;strokeWidth=2;fontSize=14;fontStyle=1;verticalAlign=top;align=center")
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

            # icon cell
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

            # text cell
            text_cell = ET.SubElement(root_elem, "mxCell")
            text_cell.set("id", f"legt{i}")
            text_cell.set("value", info["label"])
            text_cell.set("style", "text;html=1;align=left;verticalAlign=middle;fontSize=11;fontStyle=0")
            text_cell.set("vertex", "1")
            text_cell.set("parent", "legbox")
            tg = ET.SubElement(text_cell, "mxGeometry")
            tg.set("x", "55")
            tg.set("y", str(y_offset - 2))
            tg.set("width", "275")
            tg.set("height", "26")
            tg.set("as", "geometry")

        tree.write(self.drawio_path, xml_declaration=True, encoding="utf-8")

        # also save project file with wire routes
        self._save_project()

        messagebox.showinfo("Saved",
            f"Saved:\n"
            f"  Layout: {os.path.basename(self.drawio_path)}\n"
            f"  Wires:  {os.path.basename(self.project_path)}")


def main():
    root = tk.Tk()
    script_dir = os.path.dirname(os.path.abspath(__file__))
    drawio_files = [f for f in os.listdir(script_dir) if f.endswith(".drawio")]

    if len(drawio_files) == 1:
        drawio_path = os.path.join(script_dir, drawio_files[0])
    elif len(drawio_files) > 1:
        drawio_path = filedialog.askopenfilename(
            title="Select .drawio file", initialdir=script_dir,
            filetypes=[("Draw.io files", "*.drawio")])
        if not drawio_path:
            sys.exit(0)
    else:
        drawio_path = filedialog.askopenfilename(
            title="Select .drawio file",
            filetypes=[("Draw.io files", "*.drawio")])
        if not drawio_path:
            sys.exit(0)

    app = CameraWireTool(root, drawio_path)
    root.mainloop()


if __name__ == "__main__":
    main()
