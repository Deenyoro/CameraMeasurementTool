"""Smoke test: every cwt/* module imports cleanly + key public symbols
are present. Acts as a cheap canary against syntax errors / missing
exports / circular import regressions when refactoring the package."""

from __future__ import annotations

import importlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_cwt_bom_imports_with_key_symbols():
    m = importlib.import_module("cwt.bom")
    for name in ("rollup", "write_csv", "cable_for_route",
                  "device_inventory", "default_cable_for",
                  "DEFAULT_CABLE_TYPES", "BomReport", "BomLine"):
        assert hasattr(m, name), f"cwt.bom missing {name}"


def test_cwt_fov_imports_with_key_symbols():
    m = importlib.import_module("cwt.fov")
    for name in ("compute_fov", "cone_polygon", "horizontal_aov",
                  "vertical_aov", "dori_distance_meters", "project_to_floor",
                  "FOVCone", "DORI_PPM", "DORI_COLORS", "DORI_LABELS"):
        assert hasattr(m, name), f"cwt.fov missing {name}"


def test_cwt_lenses_imports_with_key_symbols():
    m = importlib.import_module("cwt.lenses")
    for name in ("SENSORS", "RESOLUTIONS", "LENS_PRESETS_MM",
                  "INSTALL_PRESETS", "DEFAULT_FOV"):
        assert hasattr(m, name), f"cwt.lenses missing {name}"


def test_cwt_multifloor_imports_with_key_symbols():
    m = importlib.import_module("cwt.multifloor")
    for name in ("ProjectState", "FloorState",
                  "load_project", "save_project",
                  "compute_alignment", "apply_alignment",
                  "inverse_alignment", "IDENTITY_ALIGNMENT"):
        assert hasattr(m, name), f"cwt.multifloor missing {name}"


def test_cwt_export_imports():
    m = importlib.import_module("cwt.export")
    for name in ("write_pdf_report", "write_bundle_csv",
                  "write_devices_csv", "write_wires_csv"):
        assert hasattr(m, name), f"cwt.export missing {name}"
