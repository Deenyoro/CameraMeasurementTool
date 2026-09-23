"""packaging/check_bundle.py: required-entry check across OS path styles."""

from __future__ import annotations

import importlib.util
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location(
    "check_bundle", os.path.join(_ROOT, "packaging", "check_bundle.py"))
cb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cb)

MODULES = set(cb.REQUIRED_MODULES) | {"camera_wire_tool", "PIL.Image"}


def test_linux_style_names_pass():
    names = set(cb.REQUIRED_DATA) | {"libtcl8.6.so", "PYZ.pyz"}
    assert cb.check(names, MODULES) == []


def test_windows_style_names_pass():
    # A Windows CArchive stores data entries with backslashes.
    names = {d.replace("/", "\\") for d in cb.REQUIRED_DATA} | {"tcl86t.dll", "PYZ.pyz"}
    assert cb.check(names, MODULES) == []


def test_missing_data_entry_fails_on_either_style():
    for sep in ("/", "\\"):
        names = {d.replace("/", sep) for d in cb.REQUIRED_DATA
                 if not d.startswith("reportlab/")}
        assert cb.check(names, MODULES) == ["reportlab/fonts/Vera.ttf"]


def test_missing_module_fails():
    mods = MODULES - {"reportlab.pdfgen.canvas"}
    assert cb.check(set(cb.REQUIRED_DATA), mods) == ["reportlab.pdfgen.canvas"]
