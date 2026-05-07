"""
Building Schematic Measurement Tool
====================================
Load a building schematic image and measure distances using a calibrated scale.

Usage:
1. Set the scale by clicking two points of a known dimension, then entering the real-world distance.
2. Draw measurement lines on the schematic.
3. View individual and total lengths in feet and inches.

Controls:
- Left-click: place/complete a measurement line (click start, then click end)
- Right-click: cancel current line being drawn
- Mouse wheel: zoom in/out
- Middle-click drag (or Ctrl+left drag): pan the view
"""

import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from PIL import Image, ImageTk
import math
import os
import sys


class MeasurementApp:
    def __init__(self, root, image_path):
        self.root = root
        self.root.title("Schematic Measurement Tool")
        self.root.state("zoomed")

        # --- state ---
        self.scale_factor = None  # real-world inches per pixel
        self.calibration_mode = False
        self.cal_points = []  # [(x,y), (x,y)] in image coords
        self.lines = []  # list of dicts: {start, end, px_len, real_in}
        self.current_start = None  # image coords of line start
        self.preview_line_id = None

        # pan / zoom
        self.zoom = 1.0
        self.offset_x = 0.0
        self.offset_y = 0.0
        self.pan_start = None

        # display unit
        self.unit_var = tk.StringVar(value="ft-in")

        # --- load full-res image ---
        self.pil_image = Image.open(image_path)
        self.pil_image.load()  # force load into RAM
        self.img_w, self.img_h = self.pil_image.size

        self._tk_ref = None  # prevent GC of current PhotoImage
        self._redraw_pending = False

        # --- build UI ---
        self._build_ui()

        # Defer initial render until window is actually visible
        self.root.after(150, self._initial_render)

    def _initial_render(self):
        self.root.update_idletasks()
        self._fit_image()
        self._redraw()

        # --- bindings ---
        self.canvas.bind("<ButtonPress-1>", self._on_left_click)
        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<ButtonPress-3>", self._on_right_click)
        self.canvas.bind("<MouseWheel>", self._on_mousewheel)
        self.canvas.bind("<ButtonPress-2>", self._on_pan_start)
        self.canvas.bind("<B2-Motion>", self._on_pan_move)
        self.canvas.bind("<ButtonRelease-2>", self._on_pan_end)
        # ctrl+left for pan on systems without middle button
        self.canvas.bind("<Control-ButtonPress-1>", self._on_pan_start)
        self.canvas.bind("<Control-B1-Motion>", self._on_pan_move)
        self.canvas.bind("<Control-ButtonRelease-1>", self._on_pan_end)
        self.root.bind("<Delete>", self._delete_selected)
        self.root.bind("<Escape>", lambda e: self._cancel_current())
        self.canvas.bind("<Configure>", self._on_canvas_resize)

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        # toolbar
        toolbar = ttk.Frame(self.root)
        toolbar.pack(side=tk.TOP, fill=tk.X)

        ttk.Button(toolbar, text="Set Scale (Calibrate)", command=self._start_calibration).pack(side=tk.LEFT, padx=4, pady=2)
        ttk.Button(toolbar, text="Undo Last Line", command=self._undo_last).pack(side=tk.LEFT, padx=4, pady=2)
        ttk.Button(toolbar, text="Clear All", command=self._clear_all).pack(side=tk.LEFT, padx=4, pady=2)
        ttk.Button(toolbar, text="Fit View", command=lambda: (self._fit_image(), self._redraw())).pack(side=tk.LEFT, padx=4, pady=2)

        ttk.Separator(toolbar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=6)

        ttk.Label(toolbar, text="Units:").pack(side=tk.LEFT, padx=(4, 0))
        ttk.Radiobutton(toolbar, text="Feet-Inches", variable=self.unit_var, value="ft-in", command=self._refresh_list).pack(side=tk.LEFT)
        ttk.Radiobutton(toolbar, text="Inches", variable=self.unit_var, value="inches", command=self._refresh_list).pack(side=tk.LEFT)
        ttk.Radiobutton(toolbar, text="Feet (decimal)", variable=self.unit_var, value="feet-dec", command=self._refresh_list).pack(side=tk.LEFT)

        self.scale_label = ttk.Label(toolbar, text="  Scale: not set", foreground="red")
        self.scale_label.pack(side=tk.LEFT, padx=10)

        self.status_label = ttk.Label(toolbar, text="")
        self.status_label.pack(side=tk.RIGHT, padx=10)

        # main area: canvas + side panel
        main = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        main.pack(fill=tk.BOTH, expand=True)

        # canvas
        canvas_frame = ttk.Frame(main)
        self.canvas = tk.Canvas(canvas_frame, bg="#2b2b2b", cursor="crosshair")
        self.canvas.pack(fill=tk.BOTH, expand=True)
        main.add(canvas_frame, weight=4)

        # side panel
        side = ttk.Frame(main, width=280)
        main.add(side, weight=1)

        ttk.Label(side, text="Measurements", font=("Segoe UI", 11, "bold")).pack(anchor=tk.W, padx=6, pady=(6, 2))

        list_frame = ttk.Frame(side)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=4, pady=2)

        self.line_listbox = tk.Listbox(list_frame, font=("Consolas", 10), selectmode=tk.SINGLE)
        sb = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.line_listbox.yview)
        self.line_listbox.config(yscrollcommand=sb.set)
        self.line_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)

        self.line_listbox.bind("<<ListboxSelect>>", self._on_list_select)

        ttk.Separator(side, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=4, pady=4)

        self.total_label = ttk.Label(side, text="Total: --", font=("Segoe UI", 12, "bold"))
        self.total_label.pack(anchor=tk.W, padx=6, pady=4)

        ttk.Button(side, text="Delete Selected", command=self._delete_selected).pack(anchor=tk.W, padx=6, pady=2)

    # --------------------------------------------------------- coordinate transforms
    def _img_to_canvas(self, ix, iy):
        cx = ix * self.zoom + self.offset_x
        cy = iy * self.zoom + self.offset_y
        return cx, cy

    def _canvas_to_img(self, cx, cy):
        ix = (cx - self.offset_x) / self.zoom
        iy = (cy - self.offset_y) / self.zoom
        return ix, iy

    # --------------------------------------------------------- rendering
    def _fit_image(self):
        cw = self.canvas.winfo_width() or 800
        ch = self.canvas.winfo_height() or 600
        zx = cw / self.img_w
        zy = ch / self.img_h
        self.zoom = min(zx, zy) * 0.95
        self.offset_x = (cw - self.img_w * self.zoom) / 2
        self.offset_y = (ch - self.img_h * self.zoom) / 2

    def _get_viewport_image(self):
        """Crop the full-res image to only the visible viewport, then resize.
        This keeps full detail at every zoom level without resizing the entire image."""
        cw = self.canvas.winfo_width() or 800
        ch = self.canvas.winfo_height() or 600

        # Determine which region of the source image is visible
        img_left, img_top = self._canvas_to_img(0, 0)
        img_right, img_bot = self._canvas_to_img(cw, ch)

        # Clamp to image bounds
        src_x0 = max(0, int(math.floor(img_left)))
        src_y0 = max(0, int(math.floor(img_top)))
        src_x1 = min(self.img_w, int(math.ceil(img_right)))
        src_y1 = min(self.img_h, int(math.ceil(img_bot)))

        if src_x1 <= src_x0 or src_y1 <= src_y0:
            # Image is fully off-screen
            return None, 0, 0

        # Crop just the visible portion from the full-res image
        cropped = self.pil_image.crop((src_x0, src_y0, src_x1, src_y1))

        # Size the crop should be on screen
        dst_w = max(1, int((src_x1 - src_x0) * self.zoom))
        dst_h = max(1, int((src_y1 - src_y0) * self.zoom))

        # Cap to reasonable size for display (screen can't show more pixels than this)
        max_dim = 4000
        if dst_w > max_dim or dst_h > max_dim:
            scale = max_dim / max(dst_w, dst_h)
            dst_w = max(1, int(dst_w * scale))
            dst_h = max(1, int(dst_h * scale))

        resized = cropped.resize((dst_w, dst_h), Image.LANCZOS)
        tk_img = ImageTk.PhotoImage(resized)

        # Where to place this crop on the canvas
        place_x, place_y = self._img_to_canvas(src_x0, src_y0)
        return tk_img, place_x, place_y

    def _redraw(self):
        self.canvas.delete("all")

        tk_img, px, py = self._get_viewport_image()
        if tk_img is not None:
            self.canvas.create_image(px, py, anchor=tk.NW, image=tk_img, tags="img")
            self._tk_ref = tk_img  # prevent GC

        # draw measurement lines
        for i, line in enumerate(self.lines):
            self._draw_line(line, i)

        # draw calibration points
        for pt in self.cal_points:
            cx, cy = self._img_to_canvas(*pt)
            self.canvas.create_oval(cx - 5, cy - 5, cx + 5, cy + 5, fill="yellow", outline="black", width=2, tags="cal")

    def _schedule_redraw(self):
        """Debounced redraw for smooth panning/zooming."""
        if not self._redraw_pending:
            self._redraw_pending = True
            self.root.after(16, self._do_deferred_redraw)  # ~60fps cap

    def _do_deferred_redraw(self):
        self._redraw_pending = False
        self._redraw()

    def _draw_line(self, line, idx):
        x1, y1 = self._img_to_canvas(*line["start"])
        x2, y2 = self._img_to_canvas(*line["end"])
        color = "#00ff00"
        # highlight selected
        sel = self.line_listbox.curselection()
        if sel and sel[0] == idx:
            color = "#ff4444"

        self.canvas.create_line(x1, y1, x2, y2, fill=color, width=2, tags=f"line_{idx}")
        # endpoints
        for ex, ey in [(x1, y1), (x2, y2)]:
            self.canvas.create_oval(ex - 4, ey - 4, ex + 4, ey + 4, fill=color, outline="white", tags=f"line_{idx}")
        # label
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        label = self._format_length(line["real_in"]) if line["real_in"] is not None else f"{line['px_len']:.0f}px"
        self.canvas.create_text(mx, my - 10, text=label, fill="yellow", font=("Consolas", 9, "bold"),
                                tags=f"line_{idx}")

    # --------------------------------------------------------- formatting
    def _format_length(self, inches):
        if inches is None:
            return "--"
        unit = self.unit_var.get()
        if unit == "inches":
            return f'{inches:.2f}"'
        elif unit == "feet-dec":
            return f"{inches / 12:.3f} ft"
        else:  # ft-in
            feet = int(inches // 12)
            rem = inches % 12
            whole_in = int(rem)
            frac = rem - whole_in
            # express fraction as nearest 1/16
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
            if feet > 0:
                return f"{feet}'-{whole_in}{frac_str}\""
            else:
                return f'{whole_in}{frac_str}"'

    # --------------------------------------------------------- calibration
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

        # ask user for real distance
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
            # recalculate all existing lines
            for line in self.lines:
                line["real_in"] = line["px_len"] * self.scale_factor
            self._refresh_list()
            self._redraw()
            dlg.destroy()

        ttk.Button(dlg, text="Apply", command=apply).pack(pady=8)
        self.calibration_mode = False
        self.cal_points = []
        self.status_label.config(text="", foreground="black")

    # --------------------------------------------------------- mouse handlers
    def _on_left_click(self, event):
        # ignore if ctrl held (pan)
        if event.state & 0x4:
            return
        ix, iy = self._canvas_to_img(event.x, event.y)
        # clamp to image bounds
        ix = max(0, min(ix, self.img_w))
        iy = max(0, min(iy, self.img_h))

        if self.calibration_mode:
            self.cal_points.append((ix, iy))
            cx, cy = self._img_to_canvas(ix, iy)
            self.canvas.create_oval(cx - 5, cy - 5, cx + 5, cy + 5, fill="yellow", outline="black", width=2, tags="cal")
            if len(self.cal_points) == 1:
                self.status_label.config(text="CALIBRATE: Click the second point")
            elif len(self.cal_points) == 2:
                self._finish_calibration()
            return

        if self.current_start is None:
            # start a new line
            self.current_start = (ix, iy)
            self.status_label.config(text="Click to place end point (Right-click to cancel)")
        else:
            # finish line
            start = self.current_start
            end = (ix, iy)
            px_len = math.dist(start, end)
            real_in = px_len * self.scale_factor if self.scale_factor else None
            self.lines.append({"start": start, "end": end, "px_len": px_len, "real_in": real_in})
            self.current_start = None
            if self.preview_line_id:
                self.canvas.delete(self.preview_line_id)
                self.preview_line_id = None
            self._refresh_list()
            self._redraw()
            self.status_label.config(text="")

    def _on_motion(self, event):
        if self.current_start is not None:
            if self.preview_line_id:
                self.canvas.delete(self.preview_line_id)
            sx, sy = self._img_to_canvas(*self.current_start)
            self.preview_line_id = self.canvas.create_line(sx, sy, event.x, event.y,
                                                           fill="#00ff00", width=2, dash=(4, 4))
            # show live distance in status
            ix, iy = self._canvas_to_img(event.x, event.y)
            px_dist = math.dist(self.current_start, (ix, iy))
            if self.scale_factor:
                real = px_dist * self.scale_factor
                self.status_label.config(text=f"Length: {self._format_length(real)}  |  Right-click to cancel")
            else:
                self.status_label.config(text=f"Length: {px_dist:.0f}px  |  Right-click to cancel")

    def _on_right_click(self, event):
        self._cancel_current()

    def _cancel_current(self):
        self.current_start = None
        if self.preview_line_id:
            self.canvas.delete(self.preview_line_id)
            self.preview_line_id = None
        self.status_label.config(text="")

    def _on_mousewheel(self, event):
        # zoom toward cursor
        factor = 1.15 if event.delta > 0 else 1 / 1.15
        new_zoom = max(0.02, min(self.zoom * factor, 15.0))

        # adjust offset so the point under cursor stays fixed
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
        self._redraw()  # final crisp render

    def _on_canvas_resize(self, event):
        self._schedule_redraw()

    # --------------------------------------------------------- list management
    def _refresh_list(self):
        self.line_listbox.delete(0, tk.END)
        total = 0.0
        for i, line in enumerate(self.lines):
            label = self._format_length(line["real_in"]) if line["real_in"] is not None else f"{line['px_len']:.0f}px"
            self.line_listbox.insert(tk.END, f"  Line {i + 1}:  {label}")
            if line["real_in"] is not None:
                total += line["real_in"]

        if self.scale_factor and self.lines:
            self.total_label.config(text=f"Total: {self._format_length(total)}")
        elif self.lines:
            total_px = sum(l["px_len"] for l in self.lines)
            self.total_label.config(text=f"Total: {total_px:.0f}px (set scale to see real units)")
        else:
            self.total_label.config(text="Total: --")

    def _on_list_select(self, event):
        self._redraw()

    def _delete_selected(self, event=None):
        sel = self.line_listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        self.lines.pop(idx)
        self._refresh_list()
        self._redraw()

    def _undo_last(self):
        if self.lines:
            self.lines.pop()
            self._refresh_list()
            self._redraw()

    def _clear_all(self):
        if self.lines and not messagebox.askyesno("Confirm", "Clear all measurement lines?"):
            return
        self.lines.clear()
        self._refresh_list()
        self._redraw()


def main():
    # default image path
    default_img = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schematic.png")

    root = tk.Tk()

    if os.path.exists(default_img):
        image_path = default_img
    else:
        image_path = filedialog.askopenfilename(
            title="Select schematic image",
            filetypes=[("Images", "*.png *.jpg *.jpeg *.bmp *.tiff")]
        )
        if not image_path:
            sys.exit(0)

    app = MeasurementApp(root, image_path)
    root.mainloop()


if __name__ == "__main__":
    main()
