"""At the minimum window size the live totals, the whole next-step guide
and the map stay usable: the status hint gives way first, the guide
switches to short wording, and the mini-map steps aside."""

from __future__ import annotations

from tests.gui_support import pump, settle


def _right_edge(w):
    return w.winfo_rootx() + w.winfo_width()


def _narrow(app):
    root = app.root
    root.geometry("900x600+0+0")
    settle(root, 300)
    pump(root, 5)


def test_status_totals_stay_visible_when_narrow(gui_app):
    app = gui_app
    _narrow(app)
    lbl = app.total_toolbar_label
    assert lbl.winfo_ismapped()
    assert lbl.winfo_width() >= lbl.winfo_reqwidth() - 2
    assert _right_edge(lbl) <= _right_edge(app.root)


def test_step_guide_fits_when_narrow(gui_app):
    app = gui_app
    _narrow(app)
    bar = app._steps_bar
    last = app._step_btns[-1]
    assert _right_edge(last) <= _right_edge(bar)
    app.root.geometry("1600x800+0+0")
    settle(app.root, 300)
    assert "Export PDF" in last.cget("text")


def test_minimap_hides_when_the_map_is_narrow(gui_app):
    app = gui_app
    app._show_minimap.set(True)
    app.root.geometry("1600x800+0+0")
    settle(app.root, 300)
    assert app.minimap.winfo_ismapped()
    _narrow(app)
    assert not app.minimap.winfo_ismapped()


def test_minimap_takes_the_bottom_corner_that_covers_least(gui_app):
    app = gui_app
    app.root.geometry("1600x800+0+0")
    app._show_minimap.set(True)
    pump(app.root, 6)
    cw, ch = app.canvas.winfo_width(), app.canvas.winfo_height()
    app.zoom = 1.0
    # Plan in the bottom-right corner: the mini-map moves to bottom-left.
    app.offset_x, app.offset_y = cw - app.img_w, ch - app.img_h
    assert app._minimap_corner() == "sw"
    # Plan in the bottom-left corner: it stays bottom-right.
    app.offset_x, app.offset_y = 0, ch - app.img_h
    assert app._minimap_corner() == "se"
    # No overlap either way: bottom-right, as before.
    app.offset_x, app.offset_y = cw / 2 - 20, 0
    app.zoom = 0.1
    assert app._minimap_corner() == "se"
