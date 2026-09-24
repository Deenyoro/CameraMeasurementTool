"""Main-window layout: the status bar (live totals, save feedback) and the
mode switch must stay on screen at common laptop sizes and at the
declared minimum window size."""

from __future__ import annotations

import camera_wire_tool as cwt
from tests.gui_support import pump


def _inside(widget, root) -> bool:
    """True when the widget is mapped and fully inside the root window."""
    if not widget.winfo_ismapped():
        return False
    x = widget.winfo_rootx() - root.winfo_rootx()
    y = widget.winfo_rooty() - root.winfo_rooty()
    return (x >= 0 and y >= 0
            and x + widget.winfo_width() <= root.winfo_width() + 1
            and y + widget.winfo_height() <= root.winfo_height() + 1)


def test_status_bar_visible_on_768px_laptop(gui_app):
    root = gui_app.root
    root.geometry("1366x705+0+0")      # maximised client area on 1366x768
    pump(root, 6)
    assert _inside(gui_app._statusbar, root)
    assert _inside(gui_app.total_toolbar_label, root)


def test_status_bar_visible_when_short(gui_app):
    root = gui_app.root
    root.minsize(1, 1)
    root.geometry("1000x420+0+0")
    pump(root, 6)
    assert _inside(gui_app._statusbar, root)


def test_mode_switch_and_scale_visible_at_minimum_size(gui_app):
    root = gui_app.root
    root.geometry("900x600+0+0")
    pump(root, 6)
    for m in (cwt.Mode.MOVE, cwt.Mode.WIRE):
        assert _inside(gui_app._mode_buttons[m], root), m
    assert _inside(gui_app.scale_label, root)
    assert _inside(gui_app.zoom_indicator, root)
    # the right-hand cluster wrapped onto its own row instead of clipping
    assert gui_app._toolbar_wrapped is True


def test_toolbar_single_row_when_wide(gui_app):
    root = gui_app.root
    root.geometry("1900x900+0+0")
    pump(root, 6)
    assert gui_app._toolbar_wrapped is False


def test_active_mode_button_is_accented(gui_app):
    app = gui_app
    app._set_mode(cwt.Mode.WIRE)
    pump(app.root)
    assert str(app._mode_buttons[cwt.Mode.WIRE].cget("style")) == "Accent.TButton"
    assert str(app._mode_buttons[cwt.Mode.MOVE].cget("style")) == "TButton"


def test_step_strip_tracks_progress(gui_app):
    app = gui_app
    texts = lambda: [b.cget("text") for b in app._step_btns]  # noqa: E731
    assert texts()[0].startswith("1")
    app.scale_factor = 1.0
    app._refresh_scale_label()
    assert texts()[0].startswith("✓")
    assert "0 of 2 cameras wired" in texts()[1]


def test_settings_write_keeps_unknown_keys(gui_app):
    cwt.save_settings({"unit": "ft-in", "from_a_newer_version": 42})
    gui_app._do_persist_user_settings()
    data = cwt.load_settings()
    assert data["from_a_newer_version"] == 42
    assert data["show_steps"] is True
