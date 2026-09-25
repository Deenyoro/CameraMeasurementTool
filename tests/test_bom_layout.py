"""BOM tab: the grand total is at the top where a laptop screen shows it,
the numeric columns are never the ones cut off, and the tax box takes
8 or 0.08 for 8 %."""

from __future__ import annotations

import json

import pytest

import camera_wire_tool as cwt
from tests.gui_support import pump, settle, write_drawio


def test_as_tax_rate():
    f = cwt.CameraWireTool._as_tax_rate
    assert f(0.08) == 0.08
    assert f(0) == 0
    assert abs(f(8) - 0.08) < 1e-12
    assert abs(f(8.25) - 0.0825) < 1e-12
    assert abs(f(1) - 0.01) < 1e-12     # "1" is 1 %, not 100 % tax


def test_tax_typed_as_percent_is_used_and_stored_as_decimal(gui_app):
    app = gui_app
    app._bom_tax_var.set(8)
    assert abs(app._build_bom_report().tax_rate - 0.08) < 1e-12
    app._normalize_tax_entry()
    assert abs(app._bom_tax_var.get() - 0.08) < 1e-12
    app._do_persist_user_settings()
    saved = json.loads(cwt.SETTINGS_PATH.read_text(encoding="utf-8"))
    assert abs(saved["tax_rate"] - 0.08) < 1e-12


def test_total_band_sits_above_the_tables_and_is_visible(gui_app):
    app = gui_app
    root = app.root
    root.geometry("1366x705+0+0")
    app._notebook.select(3)
    pump(root, 6)
    band = app._bom_pill_total
    assert band.winfo_ismapped()
    assert band.winfo_rooty() < app._bom_tree.winfo_rooty()
    tab = band.master
    assert band.winfo_rooty() + band.winfo_height() <= (
        tab.winfo_rooty() + tab.winfo_height())
    assert "Wiring total" in band.cget("text")


def test_tree_columns_fit_the_panel(gui_app):
    app = gui_app
    root = app.root
    root.geometry("1366x705+0+0")
    app._notebook.select(3)
    pump(root, 6)
    for tree in (app._bom_tree, app._bom_devices_tree, app._bom_models_tree):
        total = sum(int(tree.column(c, "width")) for c in _shown(tree))
        assert total <= tree.winfo_width(), (str(tree), total, tree.winfo_width())


def _shown(tree):
    """The columns on screen (a narrow panel hides the least useful)."""
    dc = tree.tk.splitlist(tree.cget("displaycolumns"))
    return list(tree.cget("columns")) if tuple(dc) == ("#all",) else list(dc)


@pytest.fixture
def app_150(tk_root, tmp_path):
    """The app built at 150 % display scaling (tk scaling 2.0)."""
    tk_root.tk.call("tk", "scaling", 2.0)
    cwt.apply_theme(tk_root)            # re-derive fonts and row height
    path = write_drawio(tmp_path / "Plan.drawio")
    app = cwt.CameraWireTool(tk_root, path)
    settle(tk_root)
    return app


def test_one_table_at_a_time_gets_the_height(app_150):
    """At 150 % on a 1280x720 window the BOM used to show three tables
    with a header and zero data rows each. Now one table is shown and it
    has room for several rows; the switch picks which."""
    app = app_150
    root = app.root
    root.geometry("1280x720+0+0")
    app._notebook.select(3)
    pump(root, 8)
    assert app._bom_compact is True
    rowh = int(cwt.ttk.Style(root).lookup("Treeview", "rowheight"))
    for key, tree in (("runs", app._bom_tree),
                      ("types", app._bom_devices_tree),
                      ("models", app._bom_models_tree)):
        app._bom_view_var.set(key)
        app._show_bom_view()
        pump(root, 3)
        # header + at least 4 data rows fit
        assert tree.winfo_height() >= 5 * rowh, (key, tree.winfo_height(), rowh)
    # buttons and total stay on screen
    tab = app._bom_pill_total.master
    bottom = tab.winfo_rooty() + tab.winfo_height()
    assert app._bom_pill_total.winfo_ismapped()
    assert app._bom_devices_tree.winfo_rooty() + app._bom_devices_tree.winfo_height() <= bottom


def test_full_totals_layout_on_a_tall_panel(gui_app):
    app = gui_app
    app.root.geometry("1600x1000+0+0")
    app._notebook.select(3)
    pump(app.root, 8)
    assert app._bom_compact is False
    assert app._bom_totals_frame.winfo_ismapped()
    assert not app._bom_breakdown_line.winfo_ismapped()


def test_switch_labels_show_row_counts(gui_app):
    app = gui_app
    app._notebook.select(3)
    app._refresh_bom_tab(force=True)
    pump(app.root)
    assert app._bom_view_btns["types"].cget("text") == "Device types (3)"
    assert app._bom_view_btns["runs"].cget("text") == "Cables"


def test_name_column_keeps_readable_width_when_narrow(app_150):
    """Numeric columns give up width before the name column is cut to a
    few characters ("Card Rea")."""
    app = app_150
    root = app.root
    root.geometry("1280x720+0+0")
    app._notebook.select(3)
    app._bom_view_var.set("types")
    app._show_bom_view()
    pump(root, 8)
    tree = app._bom_devices_tree
    body = cwt.ttk.Style(tree).lookup("Treeview", "font") or "TkDefaultFont"
    total = sum(int(tree.column(c, "width")) for c in _shown(tree))
    assert total <= tree.winfo_width()
    # "Door Position Switch" keeps its first two words (was "Door Posi")
    need = int(root.tk.call("font", "measure", body, "Door Position"))
    assert int(tree.column("label", "width")) >= need


def test_tax_hint_reads_back_the_rate(gui_app):
    app = gui_app
    app._notebook.select(3)
    app._bom_tax_var.set(1)             # 1 means 1 %, not 100 %
    app._refresh_bom_tab(force=True)
    assert app._bom_tax_hint.cget("text") == "= 1 %"
    app._bom_tax_var.set(0.5)           # 0.5 is 50 %: shown, and flagged
    app._refresh_bom_tab(force=True)
    assert app._bom_tax_hint.cget("text") == "= 50 %"
    assert str(app._bom_tax_hint.cget("foreground")) == "#FF8C42"


def test_numbers_are_never_cut_to_fit_the_name(gui_app):
    """Shrinking the numeric columns stops at the width their values
    need ("232.3" for 232.33 ft, "$0.18" for $0.1800 was the bug)."""
    app = gui_app
    app.root.geometry("1366x705+0+0")
    app._notebook.select(3)
    app.scale_factor = 1.0
    app.wire_routes.append({"device_id": "s1", "points": [(0, 0), (4000, 0)]})
    app._refresh_bom_tab(force=True)
    pump(app.root, 6)
    tree = app._bom_tree
    rows = tree.get_children()
    assert rows
    body = cwt.ttk.Style(tree).lookup("Treeview", "font") or "TkDefaultFont"
    cols = list(tree.cget("columns"))
    for i, c in enumerate(cols):
        if c == "label" or c not in _shown(tree):
            continue
        for iid in rows:
            text = str(tree.item(iid, "values")[i])
            need = int(tree.tk.call("font", "measure", body, text))
            assert int(tree.column(c, "width")) >= need, (c, text)


def test_least_useful_columns_hide_before_numbers_are_cut(app_150):
    """Too narrow for every column even after shrinking: hide the least
    useful ones (model number, unit price…) instead of cutting the
    subtotal off at the right edge; they return when they fit again."""
    app = app_150
    root = app.root
    root.geometry("1280x720+0+0")
    app._notebook.select(3)
    pump(root, 6)                   # (selecting the tab rebuilds the rows)
    tree = app._bom_models_tree
    for n in range(3):
        tree.insert("", "end", values=(f"Hanwha {n}", "PNM-C16083RVQ-LONG",
                                       46, "$11,283.00", "$123,849.00"),
                    tags=("model",))
    tree._refit()
    pump(root, 4)
    shown = _shown(tree)
    # "Model #" goes first (the name usually carries it; the row tooltip
    # shows it), then the unit price.
    assert "number" not in shown and "subtotal" in shown and "name" in shown
    widths = sum(int(tree.column(c, "width")) for c in shown)
    assert widths <= tree.winfo_width()
    # Short values again: every column comes back.
    for iid in tree.get_children():
        tree.delete(iid)
    tree.insert("", "end", values=("Cam", "X1", 1, "$9.00", "$9.00"),
                tags=("model",))
    tree._refit()
    assert root.tk.splitlist(tree.cget("displaycolumns")) == ("#all",)


def test_fit_column_widths_pure():
    nat = {"a": 100, "b": 100}
    mins = {"a": 40, "b": 60}
    # plenty of room: natural widths
    assert cwt.fit_column_widths(500, nat, mins, 100) == ([], {"a": 100, "b": 100})
    # tight: both shrink in proportion to their slack
    hidden, w = cwt.fit_column_widths(250, nat, mins, 100)
    assert hidden == [] and sum(w.values()) == 150 and w["a"] < w["b"]
    # too tight even at minimums: hide in drop order
    hidden, w = cwt.fit_column_widths(180, nat, mins, 100, drop_order=("a", "b"))
    assert hidden == ["a"] and w == {"b": 80}


@pytest.mark.parametrize("geometry", ["1280x720+0+0", "1920x1080+0+0"])
def test_tables_show_their_scrollbar(app_150, geometry):
    """The scrollbars were packed after an expanding tree and never got
    any width: rows below the fold (MD with 46 cameras, the subtotals)
    could only be reached with the mouse wheel, with no sign they
    existed."""
    app = app_150
    root = app.root
    root.geometry(geometry)
    app._notebook.select(3)
    pump(root, 6)
    for key, tree in (("runs", app._bom_tree),
                      ("types", app._bom_devices_tree),
                      ("models", app._bom_models_tree)):
        app._bom_view_var.set(key)
        app._show_bom_view()
        pump(root, 4)
        sb = next(w for w in tree.master.winfo_children()
                  if isinstance(w, cwt.ttk.Scrollbar))
        assert sb.winfo_ismapped(), key
        assert sb.winfo_width() >= 8, (key, sb.winfo_width())
        assert (tree.winfo_rootx() + tree.winfo_width()
                <= sb.winfo_rootx()), key


def test_cut_off_row_text_shows_in_full_on_hover(app_150):
    app = app_150
    root = app.root
    root.geometry("1280x720+0+0")
    app._notebook.select(3)
    app._bom_view_var.set("models")
    app._show_bom_view()
    pump(root, 6)
    tree = app._bom_models_tree
    long_name = "Hanwha PNM-C16083RVQ 4x5MP multi-sensor " * 3
    iid = tree.insert("", "end", values=(long_name, "PNM-C16083RVQ", 2,
                                         "$1.00", "$2.00"), tags=("model",))
    short = tree.insert("", "end", values=("Cam", "X1", 1, "$9.00", "$9.00"),
                        tags=("model",))
    tree._refit()
    pump(root, 3)
    tip = tree._row_tip
    text = tip.row_text(iid)
    assert long_name.strip() in text and "PNM-C16083RVQ" in text
    if "number" in _shown(tree):
        assert tip.row_text(short) == ""
