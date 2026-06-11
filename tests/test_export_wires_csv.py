"""Tests for cwt.export.write_wires_csv — effective-cable resolution.

The Cable column used to print only the explicit `cable_type` override,
leaving auto-resolved rows blank. With devices/symbol_types/cable_types
passed in, the column now matches the BOM (with an '(auto)' suffix when
falling back to a default)."""

from __future__ import annotations

import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cwt import export


SYMBOL_TYPES = {
    "MD": {"label": "Minidome", "shape": "ellipse", "color": "#FFFF00",
            "is_camera": True},
    "CR": {"label": "Card Reader", "shape": "rect", "color": "#00E080"},
}

CABLE_TYPES = {
    "Cat6":  {"label": "Cat6 PoE",  "color": "#0080FF", "cost_per_ft": 0.20},
    "Cat5e": {"label": "Cat5e",     "color": "#3060A0", "cost_per_ft": 0.10},
    "RG59":  {"label": "RG59 Coax", "color": "#101010", "cost_per_ft": 0.50},
}


def _read(path):
    with open(path, encoding="utf-8") as f:
        return list(csv.reader(f))


def test_legacy_call_still_writes_explicit_only(tmp_path):
    """No devices/symbol_types/cable_types passed → cable column is the
    raw explicit value (empty for auto-resolved rows). Backwards-compat
    contract for older callers."""
    routes = [
        {"device_id": "s1", "cable_type": "RG59", "points": [(0, 0), (1, 1)],
         "length_in": 12.0},
        {"device_id": "s2",                        "points": [(0, 0), (1, 1)],
         "length_in": 12.0},
    ]
    path = tmp_path / "wires.csv"
    export.write_wires_csv(routes, lambda inches: f"{inches:.0f}in", str(path))
    rows = _read(path)
    # Header + 2 rows
    assert rows[0][2] == "Cable"
    assert rows[1][2] == "RG59"
    assert rows[2][2] == ""


def test_effective_cable_resolves_via_symbol_default(tmp_path):
    """When devices+symbol_types+cable_types ARE passed, auto-resolved
    wires get the cable label with '(auto)' suffix."""
    devices = [{"id": "s1", "type": "MD"}, {"id": "s2", "type": "MD"}]
    routes = [
        # explicit override stays without '(auto)'
        {"device_id": "s1", "cable_type": "RG59", "points": [(0, 0), (1, 1)],
         "length_in": 12.0},
        # no override → resolves to MD's default (camera → Cat6) with '(auto)'
        {"device_id": "s2",                        "points": [(0, 0), (1, 1)],
         "length_in": 12.0},
    ]
    path = tmp_path / "wires.csv"
    export.write_wires_csv(
        routes, lambda inches: f"{inches:.0f}in", str(path),
        devices=devices, symbol_types=SYMBOL_TYPES, cable_types=CABLE_TYPES)
    rows = _read(path)
    assert rows[1][2] == "RG59"
    assert rows[2][2] == "Cat6 (auto)"


def test_write_wires_csv_skips_malformed_entries(tmp_path):
    """Corrupted wire entries from a hand-edited project file
    (stray string, missing 'points', non-numeric length_in) should be
    handled cleanly rather than crashing the export."""
    routes = [
        "not a dict",                                       # skipped
        {"device_id": "s1", "points": [(0, 0), (1, 1)],
         "length_in": "twelve"},                            # length coerced to 0
        {"device_id": "s2", "points": None,
         "length_in": 12.0},                                # n_segs coerced to 0
        {"device_id": "s3", "points": [(0, 0), (1, 1), (2, 2)],
         "length_in": 24.0},                                # well-formed (2 segs)
    ]
    path = tmp_path / "wires.csv"
    export.write_wires_csv(routes, lambda i: f"{i:.0f}in", str(path))
    rows = _read(path)
    # Header + 3 data rows (the string entry was skipped)
    assert len(rows) == 4
    # Row 1 (originally [1]): length 'twelve' → 0
    assert rows[1][4] == "0.00"
    # Row 2 (originally [2]): points None → n_segs 0
    assert rows[2][3] == "0"
    # Row 3 (originally [3]): well-formed → 2 segments, length 24
    assert rows[3][3] == "2"
    assert rows[3][4] == "24.00"


def test_write_wires_csv_skips_malformed_devices(tmp_path):
    """Malformed entries in the DEVICES list (non-dict, missing id) must
    not crash the index build when effective-cable resolution is on."""
    devices = [
        "not a dict",                       # skipped
        {"type": "MD"},                     # no id — skipped
        None,                               # skipped
        {"id": "s1", "type": "MD"},         # well-formed
    ]
    routes = [{"device_id": "s1", "points": [(0, 0), (1, 1)],
               "length_in": 12.0}]
    path = tmp_path / "wires.csv"
    export.write_wires_csv(
        routes, lambda i: f"{i:.0f}in", str(path),
        devices=devices, symbol_types=SYMBOL_TYPES, cable_types=CABLE_TYPES)
    rows = _read(path)
    assert len(rows) == 2
    assert rows[1][2] == "Cat6 (auto)"


def test_write_devices_csv_skips_malformed_entries(tmp_path):
    """Corrupted device entries / non-numeric x,y should not crash the
    devices CSV — bad rows are skipped (no id) or render '—' for the
    missing coord."""
    devs = [
        "not a dict",                                  # skipped (no id)
        {"no_id_here": True},                          # skipped
        {"id": "s1", "type": "MD", "x": "ten",         # coerced positions
         "y": None},
        {"id": "s2", "type": "MD", "x": 0.0,
         "y": 0.0},                                    # well-formed
    ]
    routes = []
    path = tmp_path / "devices.csv"
    export.write_devices_csv(
        devs, SYMBOL_TYPES, routes,
        lambda i: f"{i:.0f}in", str(path))
    rows = _read(path)
    # Header + 2 data rows
    assert len(rows) == 3
    # First data row: id=s1, both x and y were non-numeric → '—'
    assert rows[1][0] == "s1"
    assert rows[1][3] == "—"
    assert rows[1][4] == "—"
    # Second data row: id=s2, well-formed positions
    assert rows[2][0] == "s2"
    assert rows[2][3] == "0.0"


def test_bundle_csv_wires_use_effective_cable(tmp_path):
    """write_bundle_csv now forwards cable_types so the wires.csv
    matches the BOM. Spot-check the wires file inside the bundle."""
    from cwt import bom
    # write_devices_csv reads x/y/w/h — supply them so the bundle write
    # doesn't choke. cable resolution only needs id+type.
    devices = [{"id": "s1", "type": "MD", "x": 0, "y": 0, "w": 30, "h": 30}]
    routes = [{"device_id": "s1", "points": [(0, 0), (1, 1)],
                "length_in": 12.0}]
    rep = bom.rollup(routes, devices, SYMBOL_TYPES, CABLE_TYPES)
    out_dir = tmp_path / "bundle"
    paths = export.write_bundle_csv(
        str(out_dir),
        devices=devices, symbol_types=SYMBOL_TYPES, wire_routes=routes,
        bom_report=rep, format_length=lambda i: f"{i:.0f}in",
        cable_types=CABLE_TYPES,
    )
    wires_path = next(p for p in paths if p.endswith("wires.csv"))
    rows = _read(wires_path)
    assert rows[1][2] == "Cat6 (auto)"
