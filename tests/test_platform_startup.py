"""Linux/AppImage start-up behaviour: window maximise fallback and the
frozen-build project lookup (no display needed)."""

from __future__ import annotations

import os
import sys
import tkinter as tk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import camera_wire_tool as cwt


class _FakeWin:
    def __init__(self, zoomed_ok: bool, attr_ok: bool = True):
        self.zoomed_ok, self.attr_ok = zoomed_ok, attr_ok
        self.calls: list[tuple] = []

    def state(self, s):
        self.calls.append(("state", s))
        if not self.zoomed_ok:
            raise tk.TclError('bad argument "zoomed": must be normal, iconic, or withdrawn')

    def attributes(self, *a):
        self.calls.append(("attributes",) + a)
        if not self.attr_ok:
            raise tk.TclError("bad attribute")


def test_maximize_uses_zoomed_state_when_supported():  # Windows / macOS
    w = _FakeWin(zoomed_ok=True)
    cwt._maximize_window(w)
    assert w.calls == [("state", "zoomed")]


def test_maximize_falls_back_to_x11_attribute():  # Linux / X11
    w = _FakeWin(zoomed_ok=False)
    cwt._maximize_window(w)
    assert w.calls == [("state", "zoomed"), ("attributes", "-zoomed", True)]


def test_maximize_never_raises():
    w = _FakeWin(zoomed_ok=False, attr_ok=False)
    cwt._maximize_window(w)  # must not raise
    assert len(w.calls) == 2


def test_base_dir_frozen_uses_appimage_location(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", "/tmp/.mount_CameraXYZ/usr/bin/CameraWireTool")
    monkeypatch.setenv("APPIMAGE", str(tmp_path / "CameraWireTool.AppImage"))
    assert cwt._app_base_dir() == str(tmp_path)


def test_base_dir_frozen_without_appimage_uses_executable(monkeypatch, tmp_path):
    exe = tmp_path / "CameraWireTool.exe"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    monkeypatch.delenv("APPIMAGE", raising=False)
    assert cwt._app_base_dir() == str(tmp_path)


def test_base_dir_from_source_ignores_appimage(monkeypatch, tmp_path):
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setenv("APPIMAGE", str(tmp_path / "x.AppImage"))
    assert cwt._app_base_dir() == os.path.dirname(os.path.abspath(cwt.__file__))
