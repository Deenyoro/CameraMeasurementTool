"""Start-up and opening projects: a start screen instead of a bare file
dialog, files passed on the command line, recents that keep .zsmt
bundles, and a drawing without a floor-plan image no longer closing the
app."""

from __future__ import annotations

import json
import os
import shutil

import pytest

import camera_wire_tool as cwt
from tests.gui_support import pump, write_drawio

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_path_from_argv_ignores_options_and_missing(tmp_path):
    f = tmp_path / "Plan.drawio"
    f.write_text("x")
    assert cwt._path_from_argv(["cwt"]) is None
    assert cwt._path_from_argv(["cwt", "--verbose"]) is None
    assert cwt._path_from_argv(["cwt", str(tmp_path / "nope.drawio")]) is None
    assert cwt._path_from_argv(["cwt", "-x", str(f)]) == str(f)


def test_bundle_for_work_drawing(tmp_path):
    work = tmp_path / "Site_work"
    work.mkdir()
    d = work / "Floor1.drawio"
    d.write_text("x")
    assert cwt._bundle_for_work_drawing(str(d)) is None      # no bundle
    (tmp_path / "Site.zsmt").write_text("x")
    assert cwt._bundle_for_work_drawing(str(d)) == str(tmp_path / "Site.zsmt")
    assert cwt._bundle_for_work_drawing(str(tmp_path / "Site.zsmt")) is None


def test_preflight_rejects_drawing_without_image(tmp_path):
    ok = write_drawio(tmp_path / "Ok.drawio")
    bad = write_drawio(tmp_path / "Bad.drawio", with_image=False)
    assert cwt._preflight_drawio(ok) is None
    assert "no floor-plan image" in cwt._preflight_drawio(bad)
    notxml = tmp_path / "NotXml.drawio"
    notxml.write_text("not xml")
    assert "Can't be opened" in cwt._preflight_drawio(str(notxml))


def test_constructor_raises_instead_of_exiting(tk_root, tmp_path):
    bad = write_drawio(tmp_path / "Bad.drawio", with_image=False)
    with pytest.raises(cwt.ProjectOpenError):
        cwt.CameraWireTool(tk_root, bad)


def test_start_screen_lists_recents_and_opens_one(tk_root, tmp_path):
    plan = write_drawio(tmp_path / "Plan.drawio")
    cwt.push_recent_file(plan)
    start = cwt.StartScreen(tk_root, initial_dir=str(tmp_path))
    pump(tk_root)
    assert start.tree is not None
    assert start.tree.item("0", "values")[0] == "Plan.drawio"
    start.tree.selection_set("0")
    start.open_selected()
    pump(tk_root)
    assert not start.frame.winfo_exists()
    assert tk_root.title().startswith("Camera Wire Tool")


def test_start_screen_stays_when_a_file_cannot_open(tk_root, tmp_path,
                                                    monkeypatch):
    errors = []
    monkeypatch.setattr(cwt.messagebox, "showerror",
                        lambda *a, **k: errors.append(a))
    bad = write_drawio(tmp_path / "Bad.drawio", with_image=False)
    start = cwt.StartScreen(tk_root, initial_dir=str(tmp_path))
    assert start.open_path(bad) is False
    assert errors and "no floor-plan image" in errors[0][1]
    assert start.frame.winfo_exists()


def test_cancelling_the_open_dialog_keeps_the_start_screen(tk_root, tmp_path,
                                                          monkeypatch):
    monkeypatch.setattr(cwt.filedialog, "askopenfilename", lambda **_k: "")
    start = cwt.StartScreen(tk_root, initial_dir=str(tmp_path))
    start.open_dialog()
    assert start.frame.winfo_exists()


def test_opening_a_bundle_records_the_bundle_in_recents(tk_root, tmp_path):
    src = os.path.join(REPO, "data", "Floor1_Security_Measurements.zsmt")
    if not os.path.exists(src):
        pytest.skip("sample bundle not present")
    zs = tmp_path / "Sample.zsmt"
    shutil.copyfile(src, zs)
    start = cwt.StartScreen(tk_root, initial_dir=str(tmp_path))
    assert start.open_path(str(zs)) is True
    pump(tk_root)
    recents = json.loads(cwt.RECENT_FILES_PATH.read_text(encoding="utf-8"))
    assert recents[0] == str(zs)
