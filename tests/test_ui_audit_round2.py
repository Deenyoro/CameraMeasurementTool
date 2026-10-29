"""UI/UX audit, round 2: waste box validation, side-panel buttons that
wrap instead of clipping, mode banners that fit the map, a legend box
that fits its labels, calibration that refuses "inf", and .drawio files
with a DTD are refused."""

from __future__ import annotations

import math
import tkinter as tk

import pytest

import camera_wire_tool as cwt
from tests.gui_support import pump, write_drawio


# ---------------- waste factor: 1.10, 10 and 0.10 all mean +10 %
@pytest.mark.parametrize("typed, factor", [
    (1.10, 1.10), ("1.1", 1.10), (10, 1.10), (0.10, 1.10), (15, 1.15),
    (0, 1.0), (1, 1.0), (1.5, 1.5), (100, 2.0),
])
def test_as_waste_factor(typed, factor):
    assert cwt.CameraWireTool._as_waste_factor(typed) == pytest.approx(factor)


@pytest.mark.parametrize("typed", ["abc", "", None, -1, float("inf"),
                                   float("nan"), "1e999", 250])
def test_as_waste_factor_rejects(typed):
    assert cwt.CameraWireTool._as_waste_factor(typed) is None


def test_waste_typed_as_percent_does_not_multiply_cost(gui_app):
    app = gui_app
    app._bom_waste_var.set(10)
    assert app._build_bom_report().waste_factor == pytest.approx(1.10)
    app._normalize_waste_entry()
    assert app._bom_waste_var.get() == pytest.approx(1.10)
    app.root.setvar(str(app._bom_waste_var), "abc")
    assert app._build_bom_report().waste_factor == pytest.approx(1.10)


@pytest.mark.parametrize("typed", [-5, float("nan"), float("inf")])
def test_bad_tax_counts_as_zero(gui_app, typed):
    gui_app._bom_tax_var.set(typed)
    assert gui_app._tax_rate() == 0.0


def test_bad_settings_file_values_still_open(isolated_home, tk_root, tmp_path):
    cwt.SETTINGS_PATH.write_text(
        '{"waste_factor": "lots", "tax_rate": "NaN"}', encoding="utf-8")
    app = cwt.CameraWireTool(tk_root, write_drawio(tmp_path / "P.drawio"))
    assert app._waste_factor() == pytest.approx(1.10)
    assert app._tax_rate() == 0.0


# ---------------- button rows wrap instead of clipping
def test_flow_rows():
    assert cwt.flow_rows([50, 50, 50], 200) == [0, 0, 0]
    assert cwt.flow_rows([80, 80, 80], 200) == [0, 0, 1]
    assert cwt.flow_rows([300, 50], 200) == [0, 1]
    assert cwt.flow_rows([], 100) == []


def _inside(widget, container) -> bool:
    x = widget.winfo_rootx() - container.winfo_rootx()
    return (widget.winfo_ismapped() and x >= 0
            and x + widget.winfo_width() <= container.winfo_width() + 1)


def test_wire_tab_buttons_all_visible_on_a_narrow_panel(gui_app):
    app = gui_app
    app.root.geometry("1280x720+0+0")
    app._notebook.select(1)
    pump(app.root, 8)
    tab = app._notebook.nametowidget(app._notebook.select())
    buttons = [w for w in tab.winfo_children()
               if isinstance(w, cwt.FlowFrame)][0]._items
    assert len(buttons) == 5          # 4 buttons + "Show wires on map"
    for b in buttons:
        assert _inside(b, tab), b.cget("text")


# ---------------- mode banner fits the map
def test_wire_banner_fits_a_laptop_map(gui_app):
    app = gui_app
    app.root.geometry("1280x720+0+0")
    pump(app.root, 6)
    app._set_mode(cwt.Mode.WIRE)
    pump(app.root, 3)
    item = app.canvas.find_withtag("mode_banner")[-1]
    x0, _y0, x1, _y1 = app.canvas.bbox(item)
    assert x0 >= 0 and x1 <= app.canvas.winfo_width()
    assert "right-click" in app.canvas.itemcget(item, "text").lower()


def test_banner_text_shortens_then_elides(gui_app):
    app = gui_app
    msgs = ["a long message " * 10, "short one"]
    assert app._fit_banner_text(msgs, 5000) == msgs[0]
    assert app._fit_banner_text(msgs, 200) == "short one"
    tiny = app._fit_banner_text(msgs, 30)
    assert tiny.endswith("…") and len(tiny) < len("short one") + 1


# ---------------- legend box grows to fit its labels
def test_legend_box_fits_long_labels(gui_app):
    app = gui_app
    code = next(iter(app.symbol_types))
    app.symbol_types[code]["label"] = "A very long device label " * 3
    app._redraw()
    pump(app.root, 2)
    rect = [i for i in app.canvas.find_withtag("legend")
            if app.canvas.type(i) == "rectangle"][0]
    rx1 = app.canvas.coords(rect)[2]
    for i in app.canvas.find_withtag("legend"):
        if app.canvas.type(i) == "text":
            assert app.canvas.bbox(i)[2] <= rx1 + 1


def test_exported_legend_box_fits_long_labels(gui_app):
    app = gui_app
    code = next(iter(app.symbol_types))
    app.symbol_types[code]["label"] = "A very long device label " * 3
    img = app._render_map_image()
    assert img is not None
    # The widened legend box runs past the plan's right edge here, so the
    # export page grows to keep it (and its labels) whole.
    assert img.width > app.img_w


# ---------------- calibration refuses infinite distances
@pytest.mark.parametrize("typed", ["inf", "nan", "1e999"])
def test_calibration_refuses_non_finite(gui_app, typed):
    app = gui_app
    root = app.root
    app._start_calibration()
    app.cal_points = [(20.0, 230.0), (120.0, 230.0)]
    app._finish_calibration()
    pump(root, 5)
    dlg = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)][-1]
    entry = root.focus_get()
    entry.insert(0, typed)
    dlg.event_generate("<Return>")
    pump(root, 3)
    assert app.scale_factor is None or math.isfinite(app.scale_factor)
    if dlg.winfo_exists():
        dlg.destroy()


# ---------------- safe XML parsing of .drawio files
_XXE = """<?xml version="1.0"?>
<!DOCTYPE mxfile [<!ENTITY xxe SYSTEM "file:///etc/hostname">]>
<mxfile><diagram><mxGraphModel><root>
<mxCell id="0"/><mxCell id="s1" value="&xxe;" vertex="1" parent="1">
<mxGeometry x="1" y="1" width="2" height="2" as="geometry"/></mxCell>
</root></mxGraphModel></diagram></mxfile>
"""

_LAUGHS = """<?xml version="1.0"?>
<!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;&lol;">]>
<mxfile><diagram><mxGraphModel><root><mxCell id="0" value="&lol2;"/>
</root></mxGraphModel></diagram></mxfile>
"""


@pytest.mark.parametrize("xml", [_XXE, _LAUGHS])
def test_drawio_with_dtd_is_refused(tmp_path, xml):
    p = tmp_path / "evil.drawio"
    p.write_text(xml, encoding="utf-8")
    with pytest.raises(RuntimeError, match="DTD or entity"):
        cwt.parse_drawio(str(p))


def test_drawio_dtd_refused_without_defusedxml(tmp_path, monkeypatch):
    monkeypatch.setattr(cwt, "_safe_et", None)
    p = tmp_path / "evil.drawio"
    p.write_text(_XXE, encoding="utf-8")
    with pytest.raises(RuntimeError, match="DTD or entity"):
        cwt.parse_drawio(str(p))


def test_normal_drawio_still_parses(tmp_path, monkeypatch):
    path = write_drawio(tmp_path / "ok.drawio")
    assert len(cwt.parse_drawio(path)[3]) == 4
    monkeypatch.setattr(cwt, "_safe_et", None)
    assert len(cwt.parse_drawio(path)[3]) == 4


def test_compressed_drawio_explains_how_to_fix(tmp_path):
    p = tmp_path / "c.drawio"
    p.write_text('<mxfile><diagram id="x">7VdNb6MwEP01HCuBCSQ5Ntnu'
                 '</diagram></mxfile>', encoding="utf-8")
    with pytest.raises(RuntimeError, match="Compressed"):
        cwt.parse_drawio(str(p))
