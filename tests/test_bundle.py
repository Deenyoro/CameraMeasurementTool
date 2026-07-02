"""Tests for the .zsmt project bundle: revision history, content-addressed
drawio dedup, pruning + blob GC, and extract/round-trip."""

from __future__ import annotations

import json
import os
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cwt import bundle


def _drawio(tmp_path, name, content):
    p = tmp_path / name
    p.write_bytes(content if isinstance(content, bytes) else content.encode())
    return str(p)


def _wiremap(active="F1"):
    return {
        "version": 2, "active_floor": active,
        "floors": [
            {"id": "F1", "name": "First", "elevation_ft": 0.0,
             "drawio_file": "F1.drawio",
             "alignment": {"tx": 0, "ty": 0, "scale": 1.0, "rotation_deg": 0}},
            {"id": "F2", "name": "Second", "elevation_ft": 25.0,
             "drawio_file": "F2.drawio",
             "alignment": {"tx": -627.1, "ty": -856.2, "scale": 1.886,
                           "rotation_deg": 0}},
        ],
    }


def test_is_bundle():
    assert bundle.is_bundle("x.zsmt")
    assert bundle.is_bundle("X.ZSMT")
    assert not bundle.is_bundle("x.wiremap.json")
    assert not bundle.is_bundle(None)


def test_create_and_read_current(tmp_path):
    f1 = _drawio(tmp_path, "F1.drawio", "<floor1/>")
    f2 = _drawio(tmp_path, "F2.drawio", "<floor2/>")
    bpath = str(tmp_path / "proj.zsmt")
    rid = bundle.commit_revision(bpath, _wiremap(), {"F1": f1, "F2": f2},
                                 note="initial", app_version="1.0")
    assert os.path.exists(bpath)
    wm, floors, rev = bundle.read_revision(bpath)
    assert rev["id"] == rid
    assert wm["floors"][1]["alignment"]["scale"] == 1.886
    assert floors["F1"] == b"<floor1/>"
    assert floors["F2"] == b"<floor2/>"


def test_revisions_accumulate_newest_last(tmp_path):
    f1 = _drawio(tmp_path, "F1.drawio", "<a/>")
    bpath = str(tmp_path / "p.zsmt")
    r1 = bundle.commit_revision(bpath, _wiremap("F1"), {"F1": f1}, note="one")
    r2 = bundle.commit_revision(bpath, _wiremap("F2"), {"F1": f1}, note="two")
    revs = bundle.list_revisions(bpath)
    assert [r["id"] for r in revs] == [r1, r2]
    assert revs[-1]["note"] == "two"
    # current == newest
    wm, _, rev = bundle.read_revision(bpath)
    assert rev["id"] == r2 and wm["active_floor"] == "F2"


def test_unchanged_drawio_is_deduped(tmp_path):
    f1 = _drawio(tmp_path, "F1.drawio", "<same/>")
    bpath = str(tmp_path / "p.zsmt")
    for _ in range(5):
        bundle.commit_revision(bpath, _wiremap(), {"F1": f1})
    with zipfile.ZipFile(bpath) as z:
        blobs = [n for n in z.namelist() if n.startswith("blobs/")]
    # Five revisions, identical drawio → exactly one blob.
    assert len(blobs) == 1


def test_changed_drawio_adds_blob(tmp_path):
    bpath = str(tmp_path / "p.zsmt")
    f = tmp_path / "F1.drawio"
    f.write_text("<v1/>")
    bundle.commit_revision(bpath, _wiremap(), {"F1": str(f)})
    f.write_text("<v2/>")
    bundle.commit_revision(bpath, _wiremap(), {"F1": str(f)})
    with zipfile.ZipFile(bpath) as z:
        blobs = [n for n in z.namelist() if n.startswith("blobs/")]
    assert len(blobs) == 2


def test_prune_keeps_last_n_and_gcs_blobs(tmp_path):
    bpath = str(tmp_path / "p.zsmt")
    f = tmp_path / "F1.drawio"
    ids = []
    for i in range(6):
        f.write_text(f"<v{i}/>")   # unique content each time → unique blob
        ids.append(bundle.commit_revision(bpath, _wiremap(), {"F1": str(f)},
                                           keep=3))
    revs = bundle.list_revisions(bpath)
    assert [r["id"] for r in revs] == ids[-3:]   # only last 3 kept
    with zipfile.ZipFile(bpath) as z:
        blobs = [n for n in z.namelist() if n.startswith("blobs/")]
    assert len(blobs) == 3   # orphaned blobs from dropped revisions GC'd


def test_read_specific_revision(tmp_path):
    bpath = str(tmp_path / "p.zsmt")
    f = tmp_path / "F1.drawio"
    f.write_text("<old/>")
    r1 = bundle.commit_revision(bpath, _wiremap("F1"), {"F1": str(f)})
    f.write_text("<new/>")
    bundle.commit_revision(bpath, _wiremap("F2"), {"F1": str(f)})
    wm, floors, rev = bundle.read_revision(bpath, r1)
    assert rev["id"] == r1
    assert wm["active_floor"] == "F1"
    assert floors["F1"] == b"<old/>"


def test_extract_revision_writes_folder_project(tmp_path):
    src = tmp_path / "F1.drawio"
    src.write_text("<floor/>")
    f2 = tmp_path / "F2.drawio"
    f2.write_text("<floor2/>")
    bpath = str(tmp_path / "p.zsmt")
    bundle.commit_revision(bpath, _wiremap(), {"F1": str(src), "F2": str(f2)})
    dest = tmp_path / "extracted"
    wmp, floor_paths, rev = bundle.extract_revision(bpath, str(dest))
    assert os.path.exists(wmp)
    wm = json.loads(open(wmp, encoding="utf-8").read())
    # Flat layout: drawio_file normalized to the basename, sibling of wiremap.
    f1_entry = next(f for f in wm["floors"] if f["id"] == "F1")
    assert f1_entry["drawio_file"] == "F1.drawio"
    assert os.path.dirname(floor_paths["F1"]) == os.path.dirname(wmp)
    assert open(floor_paths["F1"], encoding="utf-8").read() == "<floor/>"
    # Alignment survived the round-trip.
    f2_entry = next(f for f in wm["floors"] if f["id"] == "F2")
    assert f2_entry["alignment"]["scale"] == 1.886


def test_extract_revision_uniquifies_colliding_basenames(tmp_path):
    """Two floors whose drawio_file flattens to the same basename
    (e.g. a/Plan.drawio + b/Plan.drawio) must NOT overwrite each other
    on extract — the second gets a _2 suffix and its floor entry is
    updated to the actual written name."""
    f1 = _drawio(tmp_path, "one.drawio", "<floor1/>")
    f2 = _drawio(tmp_path, "two.drawio", "<floor2/>")
    wm = {
        "version": 2, "active_floor": "F1",
        "floors": [
            {"id": "F1", "name": "First", "elevation_ft": 0.0,
             "drawio_file": "a/Plan.drawio"},
            {"id": "F2", "name": "Second", "elevation_ft": 12.0,
             "drawio_file": "b/Plan.drawio"},
        ],
    }
    bpath = str(tmp_path / "p.zsmt")
    bundle.commit_revision(bpath, wm, {"F1": f1, "F2": f2})
    dest = tmp_path / "extracted"
    wmp, floor_paths, _rev = bundle.extract_revision(bpath, str(dest))
    # Distinct files on disk, each with its own floor's content.
    assert floor_paths["F1"] != floor_paths["F2"]
    with open(floor_paths["F1"], encoding="utf-8") as fh:
        assert fh.read() == "<floor1/>"
    with open(floor_paths["F2"], encoding="utf-8") as fh:
        assert fh.read() == "<floor2/>"
    assert os.path.basename(floor_paths["F1"]) == "Plan.drawio"
    assert os.path.basename(floor_paths["F2"]) == "Plan_2.drawio"
    # The wiremap's drawio_file fields point at the names actually written.
    with open(wmp, encoding="utf-8") as fh:
        out = json.load(fh)
    by_id = {f["id"]: f["drawio_file"] for f in out["floors"]}
    assert by_id["F1"] == os.path.basename(floor_paths["F1"])
    assert by_id["F2"] == os.path.basename(floor_paths["F2"])
    # No staging leftovers from the atomic writes.
    assert not [n for n in os.listdir(dest) if n.endswith(".tmp")]


def test_extract_revision_atomic_no_partial_wiremap(tmp_path, monkeypatch):
    """extract_revision writes via tmp+os.replace. If the replace blows
    up, neither a half-written destination nor a stray .tmp survives."""
    f1 = _drawio(tmp_path, "F1.drawio", "<floor/>")
    bpath = str(tmp_path / "p.zsmt")
    bundle.commit_revision(bpath, _wiremap(), {"F1": f1})
    dest = tmp_path / "extracted"

    def boom(_src, _dst):
        raise OSError("simulated rename failure")
    monkeypatch.setattr(bundle.os, "replace", boom)
    raised = False
    try:
        bundle.extract_revision(bpath, str(dest))
    except OSError:
        raised = True
    assert raised, "expected the simulated rename failure to surface"
    assert not [n for n in os.listdir(dest)
                if n.endswith(".tmp") or n.endswith(".drawio")
                or n.endswith(".json")]


def test_read_revision_missing_id_raises(tmp_path):
    bpath = str(tmp_path / "p.zsmt")
    f = tmp_path / "F1.drawio"
    f.write_text("<x/>")
    bundle.commit_revision(bpath, _wiremap(), {"F1": str(f)})
    try:
        bundle.read_revision(bpath, "rev_nope")
        raise AssertionError("expected KeyError")
    except KeyError:
        pass
