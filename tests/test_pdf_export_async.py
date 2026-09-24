"""The map-PDF export runs off the Tk thread.

Per-floor state is captured BY VALUE on the Tk thread (MapRenderSnapshot):
a lost earlier attempt shared the live project with the worker and other
floors rendered with the wrong scale/alignment. These tests pin that the
snapshot renders exactly like the live app, stays correct after the app
switches floors, and that the export finishes (or cancels) while the
event loop keeps running."""

from __future__ import annotations

import io
import os
import shutil
import threading
import time

import pytest
from PIL import Image

import camera_wire_tool as cwt
from cwt import bom
from tests.gui_support import settle

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(REPO, "data")

pytestmark = pytest.mark.skipif(
    not (cwt._HAS_EXPORT and getattr(cwt.export_mod, "_HAS_REPORTLAB", False)),
    reason="reportlab not installed")


@pytest.fixture
def two_floor_app(tk_root, tmp_path):
    for f in ("Floor1_Security_Measurements.drawio",
              "Floor1_Security_Measurements.wiremap.json",
              "Floor2_Security.drawio"):
        shutil.copyfile(os.path.join(DATA, f), tmp_path / f)
    app = cwt.CameraWireTool(
        tk_root, str(tmp_path / "Floor1_Security_Measurements.drawio"))
    tk_root.geometry("1200x700+0+0")
    settle(tk_root)
    if app._project is None or len(app._project.floors) < 2:
        pytest.skip("sample project is not multi-floor")
    return app


def _same(a, b) -> bool:
    return a.size == b.size and a.tobytes() == b.tobytes()


def test_snapshot_matches_live_render_on_every_floor(two_floor_app):
    app = two_floor_app
    app._show_fov.set(True)
    start = app._project.active_floor
    snaps = {}
    for fl in app._project.floors:
        if fl.id != app._project.active_floor:
            app._switch_floor(fl.id)
        assert app._project.active_floor == fl.id
        live = app._render_map_image(symbol_scale=1.5)
        snap = cwt.MapRenderSnapshot(app)
        assert _same(snap.render(symbol_scale=1.5), live), fl.id
        snaps[fl.id] = (snap, live)
    # Back on the first floor, every other floor's snapshot must still
    # render THAT floor (its own scale, alignment and devices).
    app._switch_floor(start)
    for fid, (snap, live) in snaps.items():
        assert snap.floor_id == fid
        assert _same(snap.render(symbol_scale=1.5), live), fid


def test_snapshot_is_independent_of_the_live_app(two_floor_app):
    app = two_floor_app
    snap = cwt.MapRenderSnapshot(app, hide_types={"CR"}, hide_legend=True)
    assert snap.devices is not app.devices
    assert snap.devices[0] is not app.devices[0]
    assert snap.wire_routes is not app.wire_routes
    assert "CR" in snap._hidden_types and "CR" not in app._hidden_types
    assert snap._show_legend.get() is False
    assert app._show_legend.get() is True           # live flag untouched
    assert not hasattr(snap, "root") and not hasattr(snap, "canvas")


def _run_until(root, pred, timeout=60.0):
    """Pump the Tk loop until pred() is true; return the longest gap
    between two loop iterations (a frozen UI shows up as a long gap)."""
    worst, last = 0.0, time.monotonic()
    end = last + timeout
    while not pred() and time.monotonic() < end:
        root.update()
        now = time.monotonic()
        worst = max(worst, now - last)
        last = now
        time.sleep(0.005)
    assert pred(), "export did not finish in time"
    return worst


def test_export_all_floors_runs_off_the_ui_thread(two_floor_app, tmp_path,
                                                  monkeypatch):
    app = two_floor_app
    render_threads = []
    orig = cwt.MapRenderSnapshot.render

    def spy(self, **kw):
        render_threads.append(threading.current_thread())
        return orig(self, **kw)
    monkeypatch.setattr(cwt.MapRenderSnapshot, "render", spy)
    out = tmp_path / "all.pdf"
    results = []
    start_floor = app._project.active_floor
    specs = [{"label": lbl, "show_wires": w, "show_fov": f}
             for (lbl, w, f, _d) in app._EXPORT_VIEWS]
    app._run_pdf_map_export(
        str(out), page_specs=specs, include_summary=True, hide_types=set(),
        hide_legend=False, hide_drops=False,
        floor_ids=[f.id for f in app._project.floors],
        on_finished=results.append)
    _run_until(app.root, lambda: bool(results))
    assert "path" in results[0], results
    assert out.exists() and out.stat().st_size > 0
    assert not (tmp_path / "all.pdf.tmp").exists()
    assert render_threads and all(
        t is not threading.main_thread() for t in render_threads)
    assert app._project.active_floor == start_floor
    assert app._show_wires.get() is True and app._show_fov.get() is False


def test_export_cancel_writes_nothing(two_floor_app, tmp_path):
    app = two_floor_app
    out = tmp_path / "cancelled.pdf"
    results = []
    specs = [{"label": lbl, "show_wires": w, "show_fov": f}
             for (lbl, w, f, _d) in app._EXPORT_VIEWS]
    app._run_pdf_map_export(
        str(out), page_specs=specs, include_summary=True, hide_types=set(),
        hide_legend=False, hide_drops=False,
        floor_ids=[f.id for f in app._project.floors],
        on_finished=results.append)
    # The progress window's Cancel button
    app.root.update()
    dlg = [w for w in app.root.winfo_children()
           if isinstance(w, cwt.tk.Toplevel)][-1]
    assert dlg.title() == "Export to PDF"
    dlg.event_generate("<Escape>")
    _run_until(app.root, lambda: bool(results))
    assert results[0].get("cancelled") is True
    assert not out.exists()
    assert not (tmp_path / "cancelled.pdf.tmp").exists()


def test_map_png_snapshot_renders_off_screen(two_floor_app, tmp_path,
                                             monkeypatch):
    # Used to be a Win32 screen grab: always "Could not capture the map
    # image." on Linux. Now rendered off-screen on a worker thread.
    app = two_floor_app
    out = tmp_path / "snap.png"
    monkeypatch.setattr(cwt.filedialog, "asksaveasfilename",
                        lambda **_k: str(out))
    app._export_map_png()
    _run_until(app.root, lambda: getattr(app, "_toast", None) is not None)
    with Image.open(out) as im:
        assert im.size[0] >= app.bg_image.size[0]


def test_write_map_pdf_renders_pages_lazily_into_a_stream():
    calls = []

    def page(n):
        def _r():
            calls.append(n)
            return Image.new("RGB", (800, 500), (200, 200, 210))
        return _r
    rep = bom.rollup([], [], {}, {}, waste_factor=1.0, tax_rate=0.0)
    buf = io.BytesIO()
    cwt.export_mod.write_map_pdf(
        buf, project_name="T",
        map_pages=[{"render": page(1), "caption": "a"},
                   {"render": page(2), "caption": "b"}],
        devices=[], symbol_types={}, wire_routes=[], bom_report=rep,
        include_summary=False)
    assert calls == [1, 2]
    assert buf.getvalue().startswith(b"%PDF")


def test_pdf_snapshot_layers_follow_the_export_dialog(two_floor_app):
    # The PDF export's legend/drops follow the dialog's own "hide" boxes,
    # not whatever the on-screen Legend/Drops toggles happen to be.
    app = two_floor_app
    app._show_legend.set(False)
    app._show_drops.set(False)
    snap = cwt.MapRenderSnapshot(app, live_layers=False)
    assert snap._show_legend.get() is True and snap._drops_visible is True
    snap = cwt.MapRenderSnapshot(app, hide_legend=True, hide_drops=True,
                                 live_layers=False)
    assert snap._show_legend.get() is False and snap._drops_visible is False
    # A PNG snapshot shows the map as it is on screen.
    snap = cwt.MapRenderSnapshot(app)
    assert snap._show_legend.get() is False and snap._drops_visible is False
