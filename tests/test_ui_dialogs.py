"""Dialogs: sized from their content (no fixed pixel geometry), buttons
never cut off, calibration dialog ready to type and forgiving input."""

from __future__ import annotations

import os
import shutil
import tkinter as tk

import pytest

import camera_wire_tool as cwt
from tests.gui_support import pump


@pytest.mark.parametrize(("text", "metric", "inches"), [
    ("30", False, 360.0),
    ("30 6", False, 366.0),
    ("30'-6\"", False, 366.0),
    ("30' 6\"", False, 366.0),
    ("30 ft 6 in", False, 366.0),
    ("30’ 6”", False, 366.0),
    ("6 in", False, 6.0),
    ("2.5 ft", False, 30.0),
    ("1 m", False, 39.37007874015748),
    ("3 50", True, 3 * 39.37007874015748 + 50 * 0.39370078740157477),
    ("350 cm", True, 350 * 0.39370078740157477),
    ("3,5 m", True, 3.5 * 39.37007874015748),
])
def test_parse_distance(text, metric, inches):
    assert cwt.parse_distance_in(text, metric=metric) == pytest.approx(inches)


@pytest.mark.parametrize("text", ["", "abc", "30 ft banana", "1 2 3", None])
def test_parse_distance_rejects_garbage(text):
    assert cwt.parse_distance_in(text) is None


def _newest_toplevel(root):
    tops = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)]
    return tops[-1]


def _fits(dlg):
    dlg.update_idletasks()
    return (dlg.winfo_reqwidth() <= dlg.winfo_width() + 2
            and dlg.winfo_reqheight() <= dlg.winfo_height() + 2)


@pytest.mark.parametrize("opener", ["_open_preferences_dialog", "_show_about",
                                    "_show_shortcuts", "_open_cable_types_editor"])
def test_dialogs_fit_their_content_at_150_percent(gui_app, opener):
    root = gui_app.root
    root.tk.call("tk", "scaling", 2.0)
    cwt._init_fonts(root)
    getattr(gui_app, opener)()
    pump(root, 5)
    dlg = _newest_toplevel(root)
    assert _fits(dlg), (dlg.title(), dlg.winfo_reqheight(), dlg.winfo_height())
    dlg.destroy()


def test_calibration_dialog_focus_and_free_form_distance(gui_app):
    app = gui_app
    root = app.root
    app._start_calibration()
    app.cal_points = [(20.0, 230.0), (120.0, 230.0)]     # 100 px apart
    app._finish_calibration()
    pump(root, 5)
    dlg = _newest_toplevel(root)
    focused = root.focus_get()
    assert isinstance(focused, cwt.ttk.Entry)
    assert focused.get() == ""                            # no "0" to delete
    focused.insert(0, "30'-6\"")
    dlg.event_generate("<Return>")
    pump(root, 3)
    assert app.scale_factor == pytest.approx(366.0 / 100.0)


def test_export_dialog_fits_a_1366x768_laptop_at_150_percent(tk_root, tmp_path):
    """The Export PDF dialog of the 2-floor sample must fit the work area
    of a 1366x768 screen at 150 % (it asked for 1764 px of width)."""
    if not (cwt._HAS_EXPORT and getattr(cwt.export_mod, "_HAS_REPORTLAB", False)):
        pytest.skip("reportlab not installed")
    data = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "data")
    for f in ("Floor1_Security_Measurements.drawio",
              "Floor1_Security_Measurements.wiremap.json",
              "Floor2_Security.drawio"):
        shutil.copyfile(os.path.join(data, f), tmp_path / f)
    tk_root.tk.call("tk", "scaling", 2.0)
    cwt._init_fonts(tk_root)
    app = cwt.CameraWireTool(
        tk_root, str(tmp_path / "Floor1_Security_Measurements.drawio"))
    tk_root.geometry("1366x705+0+0")
    pump(tk_root, 5)
    app._open_export_dialog()
    pump(tk_root, 5)
    dlg = _newest_toplevel(tk_root)
    dlg.update_idletasks()
    assert dlg.title() == "Export to PDF"
    work_w, work_h = 1366, 768 - app._px(80)     # taskbar + title bar
    assert dlg.winfo_reqwidth() <= work_w, dlg.winfo_reqwidth()
    assert dlg.winfo_reqheight() <= work_h, dlg.winfo_reqheight()
    dlg.destroy()

