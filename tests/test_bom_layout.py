"""BOM tab: the grand total is at the top where a laptop screen shows it,
the numeric columns are never the ones cut off, and the tax box takes
8 or 0.08 for 8 %."""

from __future__ import annotations

import json

import camera_wire_tool as cwt
from tests.gui_support import pump


def test_as_tax_rate():
    f = cwt.CameraWireTool._as_tax_rate
    assert f(0.08) == 0.08
    assert f(0) == 0
    assert abs(f(8) - 0.08) < 1e-12
    assert abs(f(8.25) - 0.0825) < 1e-12


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
        total = sum(int(tree.column(c, "width")) for c in tree.cget("columns"))
        assert total <= tree.winfo_width(), (str(tree), total, tree.winfo_width())
