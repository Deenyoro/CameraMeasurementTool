"""Tests for cwt.export.write_map_pdf — the configurable map+summary PDF.

The summary must always be a single page (KeepInFrame shrink), and the
map+summary combination produces map page(s) + exactly one summary page.
Skipped when reportlab (or pypdf, for page counting) isn't installed."""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cwt import bom, export

pytestmark = pytest.mark.skipif(
    not export._HAS_REPORTLAB, reason="reportlab not installed")

SYM = {"MD": {"label": "Minidome", "is_camera": True},
       "CR": {"label": "Card Reader", "is_camera": False}}
CAB = {"Cat6": {"label": "Cat6", "color": "#0080FF", "cost_per_ft": 0.18},
       "Cat5e": {"label": "Cat5e", "color": "#3060A0", "cost_per_ft": 0.11}}


def _report():
    devs = [{"id": "s1", "type": "MD"}, {"id": "s2", "type": "MD"},
            {"id": "s3", "type": "CR"}]
    routes = [{"device_id": "s1", "length_in": 1200.0},
              {"device_id": "s2", "length_in": 600.0}]
    rep = bom.rollup(routes, devs, SYM, CAB, waste_factor=1.1, tax_rate=0.08)
    models = bom.model_inventory(
        [{"id": "s1"}, {"id": "s2"}],
        lambda d: {"id": "MA", "name": "QNV-C8011R",
                   "model_number": "QNV-C8011R", "price": 250.0})
    return devs, routes, rep, models


def _pages(path):
    try:
        from pypdf import PdfReader
    except ImportError:
        pytest.skip("pypdf not installed")
    return len(PdfReader(path).pages)


def _img(tmp_path, name, size=(1600, 900)):
    pytest.importorskip("PIL")
    from PIL import Image
    p = str(tmp_path / name)
    Image.new("RGB", size, (230, 230, 235)).save(p)
    return p


def test_summary_only_is_one_page(tmp_path):
    devs, routes, rep, models = _report()
    p = str(tmp_path / "summary.pdf")
    export.write_map_pdf(
        p, project_name="T", map_pages=None, devices=devs,
        symbol_types=SYM, wire_routes=routes, bom_report=rep,
        model_lines=models, include_summary=True, scale_factor=0.5)
    assert os.path.getsize(p) > 0
    assert _pages(p) == 1


def test_map_plus_summary_is_two_pages(tmp_path):
    devs, routes, rep, models = _report()
    img = _img(tmp_path, "map.png")
    p = str(tmp_path / "full.pdf")
    export.write_map_pdf(
        p, project_name="T",
        map_pages=[{"image": img, "caption": "Devices + wires"}],
        devices=devs, symbol_types=SYM, wire_routes=routes, bom_report=rep,
        model_lines=models, include_summary=True, floor_label="F1",
        scale_factor=0.5)
    assert _pages(p) == 2


def test_multiple_map_pages(tmp_path):
    devs, routes, rep, models = _report()
    pages = [
        {"image": _img(tmp_path, "a.png"), "caption": "Devices + wires"},
        {"image": _img(tmp_path, "b.png"), "caption": "No wires"},
        {"image": _img(tmp_path, "c.png"), "caption": "Devices + FOV"},
    ]
    p = str(tmp_path / "multi.pdf")
    export.write_map_pdf(
        p, project_name="T", map_pages=pages, devices=devs, symbol_types=SYM,
        wire_routes=routes, bom_report=rep, model_lines=models,
        include_summary=True)
    assert _pages(p) == 4   # 3 map pages + 1 summary


def test_map_only_is_one_page(tmp_path):
    devs, routes, rep, models = _report()
    img = _img(tmp_path, "map.png", size=(1200, 800))
    p = str(tmp_path / "maponly.pdf")
    export.write_map_pdf(
        p, project_name="T",
        map_pages=[{"image": img, "caption": ""}],
        devices=devs, symbol_types=SYM, wire_routes=routes, bom_report=rep,
        model_lines=models, include_summary=False)
    assert _pages(p) == 1


def test_summary_without_models(tmp_path):
    devs, routes, rep, _ = _report()
    p = str(tmp_path / "nomodels.pdf")
    export.write_map_pdf(
        p, project_name="T", map_pages=None, devices=devs,
        symbol_types=SYM, wire_routes=routes, bom_report=rep,
        model_lines=None, include_summary=True)
    assert _pages(p) == 1
