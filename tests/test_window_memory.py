"""The window reopens where the user left it: size and position (or
maximised), side-panel width and the open tab. New optional settings
keys; a settings file without them still gives today's behaviour."""

from __future__ import annotations

import json

import camera_wire_tool as cwt
from tests.gui_support import pump, settle, write_drawio


def _settings():
    return json.loads(cwt.SETTINGS_PATH.read_text(encoding="utf-8"))


def test_placement_is_saved_and_restored(tk_root, tmp_path):
    path = write_drawio(tmp_path / "Plan.drawio")
    app = cwt.CameraWireTool(tk_root, path)
    # The user un-maximises and moves the window.
    try:
        tk_root.state("normal")
        tk_root.attributes("-zoomed", False)      # X11
    except cwt.tk.TclError:
        pass
    tk_root.geometry("1100x700+30+40")
    settle(tk_root)
    app._notebook.select(3)
    pump(tk_root)
    app._flush_pending_settings()
    s = _settings()
    assert s["window_zoomed"] is False
    assert s["window_geometry"].startswith("1100x700")
    assert s["active_tab"] == 3
    assert s["side_width"] > 100
    # Old keys are still written as before.
    assert "unit" in s and "tax_rate" in s

    for w in tk_root.winfo_children():
        w.destroy()
    app2 = cwt.CameraWireTool(tk_root, path)
    settle(tk_root)
    assert tk_root.geometry().startswith("1100x700")
    assert app2._notebook.index("current") == 3


def test_off_screen_placement_is_ignored(tk_root, tmp_path):
    cwt.SETTINGS_PATH.write_text(json.dumps({
        "window_zoomed": False, "window_geometry": "1200x800+99999+0",
        "side_width": "junk", "active_tab": 42}), encoding="utf-8")
    path = write_drawio(tmp_path / "Plan.drawio")
    app = cwt.CameraWireTool(tk_root, path)
    settle(tk_root)
    assert not tk_root.geometry().endswith("+99999+0")
    assert app._notebook.index("current") == 0


def test_settings_without_new_keys_still_load(tk_root, tmp_path):
    cwt.SETTINGS_PATH.write_text(json.dumps({"unit": "ft-in", "tax_rate": 0.05}),
                                 encoding="utf-8")
    path = write_drawio(tmp_path / "Plan.drawio")
    app = cwt.CameraWireTool(tk_root, path)
    settle(tk_root)
    assert abs(app._bom_tax_var.get() - 0.05) < 1e-12
    assert app._notebook.index("current") == 0
