"""After a wire is finished the tool stays in Wire mode, but a stray click
on the map must not silently start another wire credited to the device
that was just wired (it used to). The next wire starts on a device."""

from __future__ import annotations

import camera_wire_tool as cwt
from tests.gui_support import click, device_img_center, pump


def _draw_wire_from(app, dev_id, route):
    click(app, *device_img_center(app, dev_id))
    for p in route:
        click(app, *p)
    app._finish_wire_route()
    pump(app.root)


def test_click_on_empty_map_after_finishing_does_not_start_a_wire(gui_app):
    app = gui_app
    app.scale_factor = 1.0
    app._set_mode(cwt.Mode.WIRE)
    _draw_wire_from(app, "s1", [(50, 120), (340, 200)])
    assert len(app.wire_routes) == 1
    assert app.mode == cwt.Mode.WIRE
    assert "next camera" in app.status_label.cget("text")
    click(app, 200, 120)              # empty floor, not a device
    pump(app.root)
    assert app.current_wire_points == []
    # Clicking the next camera starts its wire as before.
    click(app, *device_img_center(app, "s2"))
    assert len(app.current_wire_points) == 1
    assert app.selected_device["id"] == "s2"
    for p in ((310, 120), (340, 200)):
        click(app, *p)
    app._finish_wire_route()
    assert [w["device_id"] for w in app.wire_routes] == ["s1", "s2"]


def test_w_key_in_wire_mode_starts_from_the_selection(gui_app):
    app = gui_app
    app.scale_factor = 1.0
    app._set_mode(cwt.Mode.WIRE)
    _draw_wire_from(app, "s1", [(50, 120), (340, 200)])
    app._kbd_mode_wire(None)
    assert len(app.current_wire_points) == 1
    assert app._wire_awaiting_source is False


def test_leaving_wire_mode_clears_the_guard(gui_app):
    app = gui_app
    app.scale_factor = 1.0
    app._set_mode(cwt.Mode.WIRE)
    _draw_wire_from(app, "s1", [(50, 120), (340, 200)])
    app._set_mode(cwt.Mode.MOVE)
    assert app._wire_awaiting_source is False


def test_step_guide_counts_the_new_wire_right_away(gui_app):
    app = gui_app
    app.scale_factor = 1.0
    app._set_mode(cwt.Mode.WIRE)
    _draw_wire_from(app, "s1", [(50, 120), (340, 200)])
    texts = [b.cget("text") for b in app._step_btns]
    assert "1 of" in texts[1], texts
