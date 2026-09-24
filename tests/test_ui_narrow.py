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
