"""Regression: the BOM tab showed "(no wire runs yet)" and a $0 total
after wires were drawn on the map, because finishing a wire never marked
the lazily-refreshed BOM stale."""

from __future__ import annotations

import types

import camera_wire_tool as cwt
from tests.gui_support import click, device_img_center, pump


def _rows(tree):
    return [tree.item(i, "values") for i in tree.get_children()]


def test_execute_invalidates_bom_even_when_caller_forgets():
    calls = []
    app = types.SimpleNamespace(
        history=[], future=[], _bom_tab_stale=False,
        _mark_dirty=lambda: calls.append("dirty"),
        _update_undo_redo_state=lambda: None,
    )
    app._invalidate_bom = lambda: cwt.CameraWireTool._invalidate_bom(app)
    cmd = types.SimpleNamespace(do=lambda: calls.append("do"))
    cwt.CameraWireTool._execute(app, cmd)
    assert calls == ["do", "dirty"]
    assert app._bom_tab_stale is True


def test_bom_tab_shows_wire_drawn_on_canvas(gui_app, monkeypatch):
    """Finish the wire the way users do: right-click on the map."""
    app = gui_app
    monkeypatch.setattr(cwt.tk.Menu, "tk_popup", lambda *a, **k: None)
    root = app.root
    app.scale_factor = 1.0
    # Look at the (empty) BOM first, as a user touring the tabs does:
    # that consumes the start-up "stale" flag.
    app._notebook.select(3)
    pump(root)
    app._notebook.select(0)
    pump(root)
    click(app, *device_img_center(app, "s1"))
    app._set_mode(cwt.Mode.WIRE)
    for p in ((50, 120), (200, 120), (340, 200)):
        click(app, *p)
    pump(root)
    x, y = app._img_to_canvas(340, 200)
    app.canvas.event_generate("<ButtonPress-3>", x=int(x), y=int(y))
    app.canvas.event_generate("<ButtonRelease-3>", x=int(x), y=int(y))
    pump(root)
    assert len(app.wire_routes) == 1
    app._notebook.select(1)
    pump(root)
    app._notebook.select(3)
    pump(root)
    rows = _rows(app._bom_tree)
    assert rows and rows[0][0] != "—", rows
    assert rows[0][2] == "1"


def test_bom_visible_tab_follows_new_wire(gui_app):
    app = gui_app
    root = app.root
    app.scale_factor = 1.0
    app._notebook.select(3)
    pump(root)
    click(app, *device_img_center(app, "s2"))
    app._set_mode(cwt.Mode.WIRE)
    for p in ((310, 120), (340, 200)):
        click(app, *p)
    app._finish_wire_route()
    pump(root)
    rows = _rows(app._bom_tree)
    assert rows and rows[0][2] == "1", rows
