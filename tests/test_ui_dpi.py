"""DPI consistency: every font the UI uses must be point-sized so Tk
scales it with the display, and fixed pixel sizes go through _px()."""

from __future__ import annotations

import tkinter.font as tkfont

import camera_wire_tool as cwt


def test_named_fonts_exist_in_points(tk_root):
    for name, size, *_ in cwt._APP_FONTS:
        f = tkfont.nametofont(name, root=tk_root)
        assert f.cget("size") == size       # positive = points
    if cwt._HAS_SVTTK:
        for name in cwt._SV_FONT_POINTS:
            size = int(tk_root.tk.call("font", "configure", name, "-size"))
            assert size > 0, (name, size)   # was -14 (pixels): never scaled


def test_px_follows_tk_scaling(gui_app):
    root = gui_app.root
    root.tk.call("tk", "scaling", 2.0)      # 150 % of 96 dpi
    assert gui_app._px(100) == 150
    root.tk.call("tk", "scaling", 96 / 72)
    assert gui_app._px(100) == 100


def test_treeview_rows_fit_the_font(tk_root):
    style = cwt.ttk.Style(tk_root)
    body = ("SunValleyBodyFont" if cwt._HAS_SVTTK else "TkDefaultFont")
    line = int(tk_root.tk.call("font", "metrics", body, "-linespace"))
    assert int(style.lookup("Treeview", "rowheight")) >= line


def test_window_icon_from_source_checkout(tk_root):
    # Running from source: the PNG in packaging/icons is used for every
    # window (frozen Windows builds use the icon embedded in the exe).
    assert cwt._app_icon_path() is not None
    assert getattr(tk_root, "_app_icon_ref", None) is not None
