"""Layout-PDF support: door-list parsing, number ranges, building grouping,
and the device_tags / pdf_note / print_area project-file round trip."""
import os

import pytest
from PIL import Image

from cwt import layout_pdf as lp
from cwt import multifloor as mf


def test_parse_door_list_requires_title_and_reads_rows():
    title, rows = lp.parse_door_list(
        "Expansion Building Door List\n1. Flower Room 1\n 2) Flower Room 2\nnot a row\n17. Server")
    assert title == "Expansion Building Door List"
    assert [t for t, _ in rows] == ["1", "2", "17"]
    assert rows[0][1] == "1. Flower Room 1"
    assert lp.parse_door_list("Just a note\n1. thing") is None
    assert lp.parse_door_list(None) is None


def test_number_ranges():
    assert lp.number_ranges(["1", "2", "3", "5", "7", "8"]) == "1-3, 5, 7-8"
    assert lp.number_ranges(["18", "15", "19"]) == "15, 18-19"
    assert lp.number_ranges(["A", "2"]) == "2, A"
    assert lp.number_ranges([]) == "none"


def test_building_and_short_names():
    assert mf.building_of("New Bldg - 1st Floor") == "New Bldg"
    assert mf.floor_short_name("New Bldg - 1st Floor") == "1st Floor"
    assert mf.building_of("First Floor") == ""
    assert mf.floor_short_name("First Floor") == "First Floor"


def test_coerce_print_area():
    assert mf.coerce_print_area([10, 20, 5, 40]) == [5.0, 20.0, 10.0, 40.0]
    assert mf.coerce_print_area([0, 0, 0, 10]) is None
    assert mf.coerce_print_area("junk") is None
    assert mf.coerce_print_area([0, 0, float("nan"), 1]) is None


def test_coerce_device_tags():
    assert mf.coerce_device_tags({"s1": " 7 ", "s2": "", "s3": 12, "s4": None}) == {
        "s1": "7", "s3": "12"}
    assert mf.coerce_device_tags([1, 2]) == {}


def test_project_round_trip_keeps_tags_note_print_area(tmp_path):
    p = mf.ProjectState(floors=[
        mf.FloorState(id="F1", name="A - 1st", elevation_ft=0, drawio_file="a.drawio",
                      print_area=[1, 2, 300, 400]),
        mf.FloorState(id="F2", name="A - 2nd", elevation_ft=10, drawio_file="b.drawio")])
    p.device_tags = {"s1": "16"}
    p.pdf_note = "Reader removed"
    path = str(tmp_path / "proj.wiremap.json")
    mf.save_project(p, path)
    q = mf.load_project(path, str(tmp_path / "a.drawio"))
    assert q.device_tags == {"s1": "16"}
    assert q.pdf_note == "Reader removed"
    assert q.get_floor("F1").print_area == [1.0, 2.0, 300.0, 400.0]
    assert q.get_floor("F2").print_area is None


@pytest.mark.skipif(not lp._HAS_REPORTLAB, reason="reportlab not installed")
def test_write_layout_pdf_smoke(tmp_path):
    img = tmp_path / "f.png"
    Image.new("RGB", (400, 300), "white").save(img)
    pages = [{"id": "F1", "name": "A - 1st", "building": "A", "short": "1st",
              "image": str(img), "size": (400, 300), "counts": {"CR": 2},
              "tags": {"1": "CR", "2": "CR"}},
             {"id": "F2", "name": "A - 2nd", "building": "A", "short": "2nd",
              "image": str(img), "size": (400, 300), "counts": {"CR": 1},
              "tags": {"3": "CR"}}]
    out = tmp_path / "o.pdf"
    for size in lp.PAGE_SIZES:
        lp.write_layout_pdf(
            str(out), title="T", pages=pages,
            symbols={"CR": {"label": "Card Reader", "shape": "rect", "color": "#2F6DB5"}},
            categories={"CR": "Access Control"},
            door_lists={"A": {"title": "A Door List",
                              "rows": [("1", "1. a"), ("2", "2. b"), ("3", "3. c"),
                                       ("4", "4. gone")]}},
            summary_note="note", page_size=size)
        assert os.path.getsize(out) > 2000
