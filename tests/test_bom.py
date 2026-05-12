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


def test_rollup_coerces_non_numeric_length_in():
    """A hand-edited project file might carry length_in='12in' or None.
    Rollup should treat malformed lengths as zero, not crash with
    TypeError when adding to the running total."""
    devs = [{"id": "s1", "type": "MD"}, {"id": "s2", "type": "MD"}]
    routes = [
        {"device_id": "s1", "length_in": "twelve"},   # non-numeric
        {"device_id": "s1", "length_in": None},        # None
        {"device_id": "s2", "length_in": 120.0},       # well-formed
    ]
    rep = bom.rollup(routes, devs, SYMBOL_TYPES, CABLE_TYPES)
    cat6 = next(l for l in rep.lines if l.cable_id == "Cat6")
    # Only the well-formed 120in (10ft) wire contributes — the others
    # are silently treated as zero rather than blowing up.
    assert abs(cat6.total_ft - 10.0) < 1e-6
    # Run count includes all three (they're real wire entries, just with
    # bad/missing lengths)
    assert cat6.runs == 3


def test_rollup_handles_malformed_riser_entries():
    """Hand-edited risers may carry non-numeric vertical_ft, a string in
    place of the dict, or missing fields. Rollup should skip those rather
    than crash with ValueError."""
    devs = [{"id": "s1", "type": "MD"}]
    routes = []
    risers = [
        {"vertical_ft": "fourteen", "horizontal_ft": 6, "cable_type": "Cat6"},
        "not a dict",
        {"vertical_ft": None, "horizontal_ft": 0, "cable_type": "Cat6"},
        {"vertical_ft": 14.0, "horizontal_ft": 6.0, "cable_type": "Cat6"},
    ]
    rep = bom.rollup(routes, devs, SYMBOL_TYPES, CABLE_TYPES,
                      waste_factor=1.0, tax_rate=0.0, risers=risers)
    # Only the well-formed riser should contribute (20 ft)
    assert len(rep.lines) == 1
    assert abs(rep.lines[0].total_ft - 20.0) < 1e-6


def test_device_inventory_basic():
    devs = [
        {"id": "s1", "type": "MD"},
        {"id": "s2", "type": "MD"},
        {"id": "s3", "type": "MD"},
        {"id": "s4", "type": "CR"},
    ]
    routes = [
        {"device_id": "s1", "length_in": 120.0},
        {"device_id": "s2", "length_in":  60.0},
        {"device_id": "s4", "length_in": 240.0},
    ]
    inv = bom.device_inventory(devs, SYMBOL_TYPES, routes, CABLE_TYPES)
    by_code = {l.code: l for l in inv}
    assert by_code["MD"].count == 3
    assert by_code["MD"].runs == 2
    assert abs(by_code["MD"].total_in - 180.0) < 1e-6
    assert by_code["MD"].cable_id == "Cat6"
    assert by_code["CR"].count == 1
    assert by_code["CR"].runs == 1
    assert abs(by_code["CR"].total_in - 240.0) < 1e-6


def test_device_inventory_cameras_only():
    devs = [
        {"id": "s1", "type": "MD"},
        {"id": "s2", "type": "CR"},   # not a camera
        {"id": "s3", "type": "M"},    # not a camera
    ]
    routes = []
    inv = bom.device_inventory(devs, SYMBOL_TYPES, routes, CABLE_TYPES,
                                cameras_only=True)
    codes = {l.code for l in inv}
    assert codes == {"MD"}


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


def test_rollup_skips_non_dict_wire_route():
    """A stray string in wire_routes should be skipped, not crash on
    route.get(...). Companion to the earlier non-dict-cable-type and
    non-dict-symbol guards."""
    devs = [{"id": "s1", "type": "MD"}]
    routes = [
        "this should have been a dict",          # bad
        {"device_id": "s1", "length_in": 120.0},  # good
    ]
    rep = bom.rollup(routes, devs, SYMBOL_TYPES, CABLE_TYPES,
                      waste_factor=1.0, tax_rate=0.0)
    cat6 = next(l for l in rep.lines if l.cable_id == "Cat6")
    # Only the well-formed route contributes (10ft @ $0.20 = $2)
    assert cat6.runs == 1
    assert abs(cat6.total_ft - 10.0) < 1e-6


def test_rollup_skips_non_dict_device():
    """A non-dict entry in `devices` shouldn't crash devices_by_id
    construction. Skip it; well-formed devices still resolve."""
    devs = [
        "definitely not a dict",
        {"id": "s1", "type": "MD"},
        {"no_id_here": True},                 # missing 'id' key
    ]
    routes = [{"device_id": "s1", "length_in": 60.0}]
    rep = bom.rollup(routes, devs, SYMBOL_TYPES, CABLE_TYPES,
                      waste_factor=1.0, tax_rate=0.0)
    assert len(rep.lines) == 1
    assert rep.lines[0].runs == 1


def test_default_cable_for_non_dict_symbol():
    """A corrupted symbol_types entry (string instead of dict) should
    return the generic Cat5e fallback, not AttributeError on .get()."""
    assert bom.default_cable_for("not a dict") == "Cat5e"
    assert bom.default_cable_for(None) == "Cat5e"


def test_cable_for_route_handles_corrupted_symbol_entry():
    """A non-dict entry in symbol_types should be treated as empty,
    not crash with AttributeError on sym.get('cable_type'). The
    inferred default for an empty/unknown symbol is Cat5e."""
    sym = {"MD": "this should have been a dict"}   # corrupted
    devs = [{"id": "s1", "type": "MD"}]
    route = {"device_id": "s1"}
    # Should not raise. default_cable_for empties out to Cat5e for
    # unknown-shape symbols (no is_camera, no color) — that's our return.
    cable = bom.cable_for_route(route, {"s1": devs[0]}, sym, CABLE_TYPES)
    assert cable == "Cat5e"


def test_cable_for_route_handles_corrupted_device_entry():
    """A non-dict device entry (string in place of the dict) shouldn't
    crash on .get('type'). Falls through to the empty-symbol Cat5e."""
    route = {"device_id": "s1"}
    cable = bom.cable_for_route(
        route, {"s1": "not a dict"}, SYMBOL_TYPES, CABLE_TYPES)
    assert cable == "Cat5e"


def test_rollup_handles_corrupted_cable_type_entry():
    """A non-dict entry in cable_types (corrupted project file) used to
    crash rollup with AttributeError. Now treated as empty."""
    bad_cable_types = {
        "Cat6": "this should have been a dict",   # corrupted
        "Cat5e": {"label": "Cat5e", "color": "#3060A0", "cost_per_ft": 0.10},
    }
    devs = [{"id": "s1", "type": "MD"}]
    routes = [
        {"device_id": "s1", "length_in": 120.0, "cable_type": "Cat6"},
    ]
    # Should not raise
    rep = bom.rollup(routes, devs, SYMBOL_TYPES, bad_cable_types)
    # Cat6 line still appears, just with default 0 cost
    cat6 = next((l for l in rep.lines if l.cable_id == "Cat6"), None)
    assert cat6 is not None
    assert cat6.cost_per_ft == 0.0


def test_write_csv_is_atomic_no_partial_file_on_failure(tmp_path, monkeypatch):
    """Force os.replace to raise after the tmp write, and confirm the
    destination doesn't exist + the staging tmp gets cleaned up.
    Validates the tmp+rename + cleanup-on-failure contract."""
    devs = [{"id": "s1", "type": "MD"}]
    routes = [{"device_id": "s1", "length_in": 1200.0}]
    rep = bom.rollup(routes, devs, SYMBOL_TYPES, CABLE_TYPES,
                      waste_factor=1.10, tax_rate=0.08)
    target = tmp_path / "bom.csv"
    # Patch os.replace as imported by cwt.bom so the rename fails
    def boom(_src, _dst):
        raise OSError("simulated rename failure")
    monkeypatch.setattr(bom.os, "replace", boom)
    raised = False
    try:
        bom.write_csv(rep, str(target))
    except OSError:
        raised = True
    assert raised, "expected the simulated rename failure to surface"
    # Neither the destination nor the staging tmp should survive
    assert not target.exists(), "destination must not exist after failure"
    assert not (tmp_path / "bom.csv.tmp").exists(), "tmp must be cleaned up"


def test_write_csv_round_trip(tmp_path):
    """Sanity check — atomic write actually produces a valid CSV at the
    destination path (regression for the tmp+rename refactor)."""
    devs = [{"id": "s1", "type": "MD"}]
    routes = [{"device_id": "s1", "length_in": 1200.0}]
    rep = bom.rollup(routes, devs, SYMBOL_TYPES, CABLE_TYPES,
                      waste_factor=1.10, tax_rate=0.08)
    target = tmp_path / "bom.csv"
    bom.write_csv(rep, str(target), currency="€")
    assert target.exists()
    contents = target.read_text(encoding="utf-8")
    assert "GRAND TOTAL" in contents
    assert "€" in contents
