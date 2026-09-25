"""DXF/SVG import renders on a worker thread: the window keeps
responding (no "Not Responding" on a dense plan), errors still show a
dialog, and Cancel returns at once without opening anything."""

from __future__ import annotations

import threading
import time

import camera_wire_tool as cwt
from tests.gui_support import write_drawio


def test_conversion_runs_off_the_tk_thread(gui_app, tmp_path, monkeypatch):
    app = gui_app
    out = write_drawio(tmp_path / "Level2.drawio")
    seen = {}

    def slow_convert(src):
        seen["thread"] = threading.current_thread()
        time.sleep(0.6)
        return out
    monkeypatch.setattr(cwt.cad_mod, "convert_to_drawio", slow_convert)
    ticks = []

    def tick():
        ticks.append(time.monotonic())
        if len(ticks) < 50:
            app.root.after(40, tick)
    app.root.after(40, tick)
    assert app._convert_cad_drawing(str(tmp_path / "Level2.dxf")) == out
    assert seen["thread"] is not threading.main_thread()
    assert len(ticks) >= 5          # the event loop kept running meanwhile


def test_conversion_error_shows_a_dialog(gui_app, tmp_path, monkeypatch):
    app = gui_app
    errors = []
    monkeypatch.setattr(cwt.messagebox, "showerror",
                        lambda *a, **k: errors.append(a))

    def bad(src):
        raise cwt.cad_mod.CadImportError("no entities")
    monkeypatch.setattr(cwt.cad_mod, "convert_to_drawio", bad)
    assert app._convert_cad_drawing(str(tmp_path / "x.dxf")) is None
    assert errors and errors[0][0] == "Couldn't import drawing"
    assert "no entities" in errors[0][1]


def test_cancel_returns_without_waiting(gui_app, tmp_path, monkeypatch):
    app = gui_app
    release = threading.Event()

    def stuck(src):
        release.wait(10)
        return str(tmp_path / "late.drawio")
    monkeypatch.setattr(cwt.cad_mod, "convert_to_drawio", stuck)

    def press_cancel():
        tops = [w for w in app.root.winfo_children()
                if isinstance(w, cwt.tk.Toplevel)]
        tops[-1].event_generate("<Escape>")
    app.root.after(300, press_cancel)
    t0 = time.monotonic()
    assert app._convert_cad_drawing(str(tmp_path / "x.svg")) is None
    assert time.monotonic() - t0 < 5
    release.set()
