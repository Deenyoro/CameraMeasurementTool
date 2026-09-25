"""The BOM shows all three tables at once, each under its own title,
when the panel is tall enough, and the table with more rows gets more
of the height; on a short panel it keeps the one-table switch."""

from __future__ import annotations

import pytest

import camera_wire_tool as cwt
from tests.gui_support import pump, settle, write_drawio


# ------------------------------------------------ BOM: stacked when tall
@pytest.fixture
def app_150(tk_root, tmp_path):
    tk_root.tk.call("tk", "scaling", 2.0)
    cwt.apply_theme(tk_root)
    path = write_drawio(tmp_path / "Plan.drawio")
    app = cwt.CameraWireTool(tk_root, path)
    settle(tk_root)
    return app


def test_tall_panel_shows_all_three_tables(app_150):
    """1920x1080 at 150 % left the Cables table with ~400 px of empty
    rows while Device types and Models hid behind the switch."""
    app = app_150
    root = app.root
    root.geometry("1920x1020+0+0")
    app._notebook.select(3)
    pump(root, 10)
    assert app._bom_stacked is True
    assert not app._bom_switch.winfo_ismapped()
    app._refresh_bom_tab(force=True)
    pump(root, 6)
    rowh = int(cwt.ttk.Style(root).lookup("Treeview", "rowheight"))
    tab = app._bom_pill_total.master
    bottom = tab.winfo_rooty() + tab.winfo_height()
    for key, tree in (("runs", app._bom_tree),
                      ("types", app._bom_devices_tree),
                      ("models", app._bom_models_tree)):
        assert tree.winfo_ismapped(), key
        assert app._bom_pane_titles[key].winfo_ismapped(), key
        # the heading plus its rows, up to 4 (a one-row table needs less
        # room than a long one; the height is shared by row count)
        rows = min(max(1, len(tree.get_children())),
                   app._BOM_STACK_MIN_ROWS)
        assert tree.winfo_height() >= (1 + rows) * rowh, (
            key, tree.winfo_height(), rows)
        assert tree.winfo_rooty() + tree.winfo_height() <= bottom, key


def test_short_panel_keeps_the_switch_and_goes_back(app_150):
    app = app_150
    root = app.root
    root.geometry("1920x1020+0+0")
    app._notebook.select(3)
    pump(root, 10)
    assert app._bom_stacked is True
    root.geometry("1280x720+0+0")
    pump(root, 10)
    assert app._bom_stacked is False
    assert app._bom_switch.winfo_ismapped()
    assert not app._bom_pane_titles["runs"].winfo_ismapped()
    app._bom_view_var.set("models")
    app._show_bom_view()
    pump(root, 3)
    assert app._bom_models_tree.winfo_ismapped()


def test_stacked_titles_show_row_counts(app_150):
    app = app_150
    app.root.geometry("1920x1020+0+0")
    app._notebook.select(3)
    app._refresh_bom_tab(force=True)
    pump(app.root, 8)
    assert app._bom_pane_titles["types"].cget("text") == "Device types (3)"


def test_stacked_height_follows_row_counts(app_150):
    """The table with more rows gets more of the height."""
    app = app_150
    root = app.root
    root.geometry("1920x1020+0+0")
    app._notebook.select(3)
    pump(root, 10)
    tree = app._bom_models_tree
    for n in range(12):
        tree.insert("", "end", values=(f"Cam {n}", "X", 1, "$1", "$1"),
                    tags=("model",))
    app._bom_fit_stacked_rows()
    pump(root, 6)
    assert tree.winfo_height() > app._bom_tree.winfo_height()
