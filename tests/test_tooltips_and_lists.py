"""Tooltips stay on screen and next to what they describe, a shortened
label still reports its full text, and a short recent-projects list
does not leave the start screen half empty."""

from __future__ import annotations

import camera_wire_tool as cwt
from tests.gui_support import pump, settle, write_drawio


# ------------------------------------------------ tooltip placement
def test_place_on_screen_keeps_the_tip_inside():
    f = cwt.Tooltip.place_on_screen
    # plenty of room: below the anchor, where asked
    assert f(100, 200, 180, size=(300, 50), screen=(1280, 720)) == (100, 200)
    # would run off the right edge: pulled left
    x, y = f(1100, 200, 180, size=(614, 50), screen=(1280, 720))
    assert x + 614 <= 1280 and y == 200
    # would run off the bottom: flipped above the anchor
    x, y = f(100, 700, 680, size=(300, 60), screen=(1280, 720))
    assert (x, y) == (100, 620)
    # never off the left edge either
    assert f(2, 10, 0, size=(50, 20), screen=(1280, 720))[0] == 4
    # an anchor on a second monitor (beyond the primary) is left alone
    assert f(1500, 200, 180, size=(300, 50), screen=(1280, 720)) == (1500, 200)


def _tip_box(tip):
    tip.tip.update_idletasks()
    geo = tip.tip.wm_geometry()          # WxH+X+Y
    size, x, y = geo.split("+")[0], geo.split("+")[1], geo.split("+")[2]
    w, h = (int(v) for v in size.split("x"))
    return int(x), int(y), w, h


def test_long_tooltip_near_the_right_edge_stays_on_screen(tk_root):
    sw = tk_root.winfo_screenwidth()
    tk_root.geometry(f"200x100+{sw - 200}+100")
    btn = cwt.ttk.Button(tk_root, text="Export")
    btn.pack(side="right")
    settle(tk_root, 200)
    tip = cwt.Tooltip(btn, "A long help text " * 12)
    tip._show()
    x, _y, w, _h = _tip_box(tip)
    assert x >= 0 and x + w <= sw, (x, w, sw)
    tip._hide()


def test_tooltip_near_the_bottom_flips_above(tk_root):
    sh = tk_root.winfo_screenheight()
    tk_root.geometry(f"300x60+100+{sh - 60}")
    btn = cwt.ttk.Button(tk_root, text="Save")
    btn.pack(side="bottom")
    settle(tk_root, 200)
    tip = cwt.Tooltip(btn, "Save the project\nline 2\nline 3")
    tip._show()
    _x, y, _w, h = _tip_box(tip)
    assert y + h <= btn.winfo_rooty(), (y, h, btn.winfo_rooty())
    tip._hide()


def test_row_tooltip_appears_by_the_pointer(gui_app, monkeypatch):
    """The BOM row tooltip used to appear under the whole table (172 px
    below the hovered row, over the Export buttons)."""
    app = gui_app
    app._notebook.select(3)
    pump(app.root, 4)
    tree = app._bom_devices_tree
    tip = tree._row_tip
    px = tree.winfo_rootx() + 40
    py = tree.winfo_rooty() + 30
    monkeypatch.setattr(tree, "winfo_pointerxy", lambda: (px, py))
    tip.text = "Card Reader Door"
    tip._show()
    x, y, _w, _h = _tip_box(tip)
    assert abs(x - (px + 12)) <= 2 and abs(y - (py + 20)) <= 2
    assert y < tree.winfo_rooty() + tree.winfo_height()
    tip._hide()


def test_eliding_label_configure_reports_the_full_text(tk_root):
    lbl = cwt.ElidingLabel(tk_root, text="short")
    lbl.pack()
    lbl.configure(text="A long status message that will not fit " * 3)
    lbl.configure(width=5)
    tk_root.geometry("120x40+0+0")
    pump(tk_root, 3)
    full = "A long status message that will not fit " * 3
    assert lbl.cget("text") == full
    assert lbl.configure("text")[-1] == full
    assert lbl.config("text")[-1] == full


# ------------------------------------------------ start screen
def test_short_recent_list_is_sized_to_its_content(tk_root, tmp_path):
    tk_root.minsize(900, 600)
    for n in range(2):
        cwt.push_recent_file(write_drawio(tmp_path / f"Plan{n}.drawio"))
    start = cwt.StartScreen(tk_root, initial_dir=str(tmp_path))
    pump(tk_root, 4)
    assert len(start.items) == 2
    assert tk_root.winfo_height() < 600
    assert tk_root.winfo_height() <= start.frame.winfo_reqheight() + 2
    start.close()
    assert tuple(tk_root.minsize()) == (900, 600)


def test_long_folder_is_elided_and_full_path_on_hover(tk_root, tmp_path):
    deep = tmp_path.joinpath(*[f"Customer folder {i}" for i in range(8)])
    deep.mkdir(parents=True)
    plan = write_drawio(deep / "Site.drawio")
    cwt.push_recent_file(plan)
    start = cwt.StartScreen(tk_root, initial_dir=str(tmp_path))
    pump(tk_root, 4)
    start._elide_folders()
    shown = start.tree.set("0", "folder")
    assert shown.startswith("…") and shown.endswith("Customer folder 7")
    assert start.tree._row_tip.row_text("0") == plan
