"""DXF/SVG import renders on a worker thread: the window keeps
responding (no "Not Responding" on a dense plan), errors still show a
dialog, and Cancel returns at once without opening anything."""

from __future__ import annotations

import threading
import time

from PIL import Image

import camera_wire_tool as cwt
from tests.gui_support import pump


def _xml() -> str:
    return cwt.cad_mod.image_to_drawio_xml(Image.new("RGB", (40, 30), "white"))


def test_conversion_runs_off_the_tk_thread(gui_app, tmp_path, monkeypatch):
    app = gui_app
    seen = {}

    def slow_render(src):
        seen["thread"] = threading.current_thread()
        time.sleep(0.6)
        return _xml()
    monkeypatch.setattr(cwt.cad_mod, "render_to_drawio_xml", slow_render)
    ticks = []

    def tick():
        ticks.append(time.monotonic())
        if len(ticks) < 50:
            app.root.after(40, tick)
    app.root.after(40, tick)
    out = app._convert_cad_drawing(str(tmp_path / "Level2.dxf"))
    assert out == str(tmp_path / "Level2.drawio")
    assert cwt._preflight_drawio(out) is None     # written on success
    assert seen["thread"] is not threading.main_thread()
    assert len(ticks) >= 5          # the event loop kept running meanwhile


def test_conversion_error_shows_a_dialog(gui_app, tmp_path, monkeypatch):
    app = gui_app
    errors = []
    monkeypatch.setattr(cwt.messagebox, "showerror",
                        lambda *a, **k: errors.append(a))

    def bad(src):
        raise cwt.cad_mod.CadImportError("no entities")
    monkeypatch.setattr(cwt.cad_mod, "render_to_drawio_xml", bad)
    assert app._convert_cad_drawing(str(tmp_path / "x.dxf")) is None
    assert errors and errors[0][0] == "Couldn't import drawing"
    assert "no entities" in errors[0][1]


def test_cancel_returns_without_waiting(gui_app, tmp_path, monkeypatch):
    app = gui_app
    release = threading.Event()

    def stuck(src):
        release.wait(10)
        return _xml()
    monkeypatch.setattr(cwt.cad_mod, "render_to_drawio_xml", stuck)

    def press_cancel():
        tops = [w for w in app.root.winfo_children()
                if isinstance(w, cwt.tk.Toplevel)]
        tops[-1].event_generate("<Escape>")
    app.root.after(300, press_cancel)
    t0 = time.monotonic()
    assert app._convert_cad_drawing(str(tmp_path / "x.svg")) is None
    assert time.monotonic() - t0 < 5
    release.set()
    app._cad_thread.join(5)
    pump(app.root)
    # The cancelled render finished later, but nothing was written.
    assert list(tmp_path.glob("x*.drawio")) == []



def _press_cancel_later(app, ms=300):
    def press():
        tops = [w for w in app.root.winfo_children()
                if isinstance(w, cwt.tk.Toplevel)]
        tops[-1].event_generate("<Escape>")
    app.root.after(ms, press)


def test_one_import_at_a_time(gui_app, tmp_path, monkeypatch):
    """A cancelled render keeps running until it finishes; a second
    import meanwhile is refused instead of running two renderers."""
    app = gui_app
    release = threading.Event()
    calls = []

    def stuck(src):
        calls.append(src)
        release.wait(10)
        return _xml()
    monkeypatch.setattr(cwt.cad_mod, "render_to_drawio_xml", stuck)
    infos = []
    monkeypatch.setattr(cwt.messagebox, "showinfo",
                        lambda *a, **k: infos.append(a))
    _press_cancel_later(app)
    assert app._convert_cad_drawing(str(tmp_path / "a.dxf")) is None
    assert app._convert_cad_drawing(str(tmp_path / "b.dxf")) is None
    assert len(calls) == 1
    assert infos and "Still stopping" in infos[0][0]
    release.set()
    app._cad_thread.join(5)


def test_closing_during_import_never_reopens_the_app(gui_app, tmp_path,
                                                     monkeypatch):
    """Closing during a DXF import (Yes to "Stop it and close the app?")
    used to reopen the app with the imported drawing when the render
    finished first."""
    app = gui_app
    dxf = tmp_path / "Level2.dxf"
    dxf.write_text("0\nEOF\n")

    def slow_render(src):
        time.sleep(0.5)
        return _xml()
    monkeypatch.setattr(cwt.cad_mod, "render_to_drawio_xml", slow_render)
    monkeypatch.setattr(cwt.filedialog, "askopenfilename",
                        lambda **k: str(dxf))
    relaunched = []
    monkeypatch.setattr(cwt, "_relaunch",
                        lambda *a, **k: relaunched.append(a))
    quits = []
    monkeypatch.setattr(app.root, "quit", lambda: quits.append(1))
    monkeypatch.setattr(cwt.messagebox, "askyesno", lambda *a, **k: True)
    app._dirty = False
    app.root.after(150, app._on_close)
    app._file_open()
    end = time.monotonic() + 5
    while not quits and time.monotonic() < end:
        pump(app.root, 1)
        time.sleep(0.02)
    assert relaunched == []
    assert quits == [1]
    assert list(tmp_path.glob("Level2*.drawio")) == []


def test_closing_during_add_floor_import_adds_nothing(gui_app, tmp_path,
                                                      monkeypatch):
    app = gui_app
    dxf = tmp_path / "Level3.dxf"
    dxf.write_text("0\nEOF\n")

    def slow_render(src):
        time.sleep(0.5)
        return _xml()
    monkeypatch.setattr(cwt.cad_mod, "render_to_drawio_xml", slow_render)
    monkeypatch.setattr(cwt.filedialog, "askopenfilename",
                        lambda **k: str(dxf))
    monkeypatch.setattr(app.root, "quit", lambda: None)
    monkeypatch.setattr(cwt.messagebox, "askyesno", lambda *a, **k: True)
    app.root.after(150, app._on_close)
    app._add_floor_dialog()
    pump(app.root)
    assert not any(isinstance(w, cwt.tk.Toplevel) and w.winfo_exists()
                   and w.title() == "New floor"
                   for w in app.root.winfo_children())
