"""Shared fixtures for tests that drive the real Tk UI.

They need a display (Windows/macOS always have one; on Linux run under
Xvfb). Without one, `tk_root` skips the test, so the headless CI test
job still passes. Every GUI test redirects the settings, recent-files and
log paths into tmp_path so it never touches the real home folder."""

from __future__ import annotations

import contextlib
import os
import sys
import tkinter as tk

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import camera_wire_tool as cwt
from tests.gui_support import settle, write_drawio


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(cwt, "SETTINGS_PATH", home / ".camera_wire_tool_settings.json")
    monkeypatch.setattr(cwt, "RECENT_FILES_PATH", home / ".camera_wire_tool_recent.json")
    monkeypatch.setattr(cwt, "LOG_PATH", home / ".camera_wire_tool.log")
    # Never block a test on a modal box.
    for name in ("showinfo", "showwarning", "showerror"):
        monkeypatch.setattr(cwt.messagebox, name, lambda *a, **k: None)
    monkeypatch.setattr(cwt.messagebox, "askyesno", lambda *a, **k: True)
    monkeypatch.setattr(cwt.messagebox, "askokcancel", lambda *a, **k: True)
    monkeypatch.setattr(cwt.messagebox, "askyesnocancel", lambda *a, **k: False)
    return home


@pytest.fixture
def tk_root(isolated_home):
    try:
        root = tk.Tk()
    except tk.TclError as e:
        pytest.skip(f"no display for Tk: {e}")
    cwt.apply_theme(root)
    yield root
    with contextlib.suppress(tk.TclError):
        for aid in root.tk.splitlist(root.tk.call("after", "info")):
            root.after_cancel(aid)
    try:
        root.destroy()
    except tk.TclError:
        # Python-side command bookkeeping can trip over a command Tk has
        # already dropped; make sure the window is gone and the next test
        # gets a fresh default root.
        with contextlib.suppress(tk.TclError):
            root.tk.call("destroy", ".")
    if getattr(tk, "_default_root", None) is root:
        tk._default_root = None


@pytest.fixture
def gui_app(tk_root, tmp_path):
    """A CameraWireTool on a tiny synthetic drawing, with the UI built."""
    path = write_drawio(tmp_path / "Plan.drawio")
    app = cwt.CameraWireTool(tk_root, path)
    tk_root.geometry("1200x700+0+0")
    settle(tk_root)
    return app
