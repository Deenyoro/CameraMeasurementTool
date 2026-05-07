"""Tests for cwt.bom — cable resolution + rollup math."""

from __future__ import annotations

import os
import sys
import tempfile
import csv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cwt import bom


CABLE_TYPES = {
    "Cat6":    {"label": "Cat6 PoE",   "color": "#0080FF", "cost_per_ft": 0.20},
    "Cat5e":   {"label": "Cat5e",      "color": "#3060A0", "cost_per_ft": 0.10},
    "RG59":    {"label": "RG59 Coax",  "color": "#101010", "cost_per_ft": 0.50},
    "Alarm":   {"label": "22/4 Stranded","color": "#80C040","cost_per_ft": 0.10},
}

SYMBOL_TYPES = {
    "MD":  {"label": "Minidome", "shape": "ellipse", "color": "#FFFF00",
            "is_camera": True},
    "CR":  {"label": "Card Reader", "shape": "rect", "color": "#00E080",
            "is_camera": False},
    "M":   {"label": "Motion", "shape": "hexagon", "color": "#FF80FF",
            "is_camera": False},
}


def test_default_cable_for_camera():
    assert bom.default_cable_for(SYMBOL_TYPES["MD"]) == "Cat6"


def test_default_cable_for_card_reader():
    assert bom.default_cable_for(SYMBOL_TYPES["CR"]) == "Cat6"  # green = access


def test_default_cable_for_motion_sensor():
    assert bom.default_cable_for(SYMBOL_TYPES["M"]) == "Alarm"  # pink = intrusion


def test_cable_for_route_explicit():
    devs = [{"id": "s1", "type": "MD"}]
    route = {"device_id": "s1", "cable_type": "RG59"}
    assert bom.cable_for_route(route, {"s1": devs[0]}, SYMBOL_TYPES, CABLE_TYPES) == "RG59"


def test_cable_for_route_via_symbol():
    sym = dict(SYMBOL_TYPES)
    sym["MD"] = {**sym["MD"], "cable_type": "OM4"}
    cables = {**CABLE_TYPES, "OM4": {"label": "Fiber", "color": "#FF8000",
                                      "cost_per_ft": 1.0}}
    devs = [{"id": "s1", "type": "MD"}]
    route = {"device_id": "s1"}
    assert bom.cable_for_route(route, {"s1": devs[0]}, sym, cables) == "OM4"


def test_cable_for_route_falls_back_to_default():
    devs = [{"id": "s1", "type": "MD"}]
    route = {"device_id": "s1"}
    assert bom.cable_for_route(route, {"s1": devs[0]}, SYMBOL_TYPES, CABLE_TYPES) == "Cat6"


def test_rollup_simple():
    devs = [{"id": "s1", "type": "MD"}, {"id": "s2", "type": "CR"}]
    routes = [
        {"device_id": "s1", "length_in": 120.0},   # 10 ft on Cat6
        {"device_id": "s2", "length_in":  60.0},   # 5 ft on Cat6
        {"device_id": None, "length_in":  24.0},   # 2 ft, unassigned
    ]
    rep = bom.rollup(routes, devs, SYMBOL_TYPES, CABLE_TYPES,
                      waste_factor=1.0, tax_rate=0.0)
    assert len(rep.lines) == 1
    line = rep.lines[0]
    assert line.cable_id == "Cat6"
    assert line.runs == 3
    assert abs(line.total_ft - (120 + 60 + 24) / 12) < 1e-6
    assert abs(line.subtotal - line.total_ft * 0.20) < 1e-6
    assert rep.unassigned_runs == 1
    assert abs(rep.unassigned_inches - 24.0) < 1e-6


def test_rollup_waste_and_tax():
    devs = [{"id": "s1", "type": "MD"}]
    routes = [{"device_id": "s1", "length_in": 1200.0}]   # 100 ft Cat6 @ $0.20
    rep = bom.rollup(routes, devs, SYMBOL_TYPES, CABLE_TYPES,
                      waste_factor=1.10, tax_rate=0.08)
    # subtotal: 100 * 0.20 = 20
    assert abs(rep.subtotal - 20.0) < 1e-6
    # waste: 10% extra material cost = 2
    assert abs(rep.waste_added - 2.0) < 1e-6
    # tax on 22 = 1.76
    assert abs(rep.tax_amount - 1.76) < 1e-2
    # grand total = 22 + 1.76 = 23.76
    assert abs(rep.grand_total - 23.76) < 1e-2


def test_rollup_includes_risers():
    devs = [{"id": "s1", "type": "MD"}]
    routes = []
    risers = [{"vertical_ft": 14.0, "horizontal_ft": 6.0, "cable_type": "Cat6"}]
    rep = bom.rollup(routes, devs, SYMBOL_TYPES, CABLE_TYPES,
                      waste_factor=1.0, tax_rate=0.0, risers=risers)
    assert len(rep.lines) == 1
    line = rep.lines[0]
    # 20 ft total at $0.20 = $4.00
    assert abs(line.total_ft - 20.0) < 1e-6
    assert abs(line.subtotal - 4.0) < 1e-6


def test_csv_roundtrip(tmp_path):
    devs = [{"id": "s1", "type": "MD"}]
    routes = [{"device_id": "s1", "length_in": 1200.0}]
    rep = bom.rollup(routes, devs, SYMBOL_TYPES, CABLE_TYPES,
                      waste_factor=1.10, tax_rate=0.08)
    p = tmp_path / "bom.csv"
    bom.write_csv(rep, str(p))
    rows = list(csv.reader(p.open(encoding="utf-8")))
    assert rows[0][0] == "Cable"
    assert any("GRAND TOTAL" in r for r in (row for row in rows))
