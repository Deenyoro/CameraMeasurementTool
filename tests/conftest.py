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


# `tk scaling` of the first Tk root in this process (100 % here).
_INITIAL_SCALING: dict = {}


def _unzoom_before_resizing(root) -> None:
    """The app maximises its window ("zoomed"). On Windows, geometry() on
    a zoomed window does not resize it, so the size-dependent GUI tests
    would measure a maximised window there (the Windows CI job runs them).
    Make root.geometry("WxH...") leave the zoomed state first, as a user
    un-maximising the window would. No effect on X11, which has no
    "zoomed" state."""
    orig = root.geometry

    def geometry(new_geometry=None):
        if new_geometry and new_geometry[:1].isdigit():
            with contextlib.suppress(tk.TclError):
                if root.state() == "zoomed":
                    root.state("normal")
        return orig(new_geometry)

    root.geometry = root.wm_geometry = geometry


@pytest.fixture
def tk_root(isolated_home):
    try:
        root = tk.Tk()
    except tk.TclError as e:
        pytest.skip(f"no display for Tk: {e}")
    # X11 Tk keeps `tk scaling` per display, so a test that set 150 %
    # would leave every later test at 150 %: start each test at 100 %.
    scaling = _INITIAL_SCALING.setdefault(
        "v", float(root.tk.call("tk", "scaling")))
    root.tk.call("tk", "scaling", scaling)
    cwt.apply_theme(root)
    _unzoom_before_resizing(root)
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


@pytest.fixture
def cad_backends(monkeypatch):
    """Act as a copy of the app that has the DXF/SVG importers, whether
    or not this test environment installed them (the CI test job does
    not). Tests using it stub the renderer itself. Afterwards, wait for
    a render the test left running (closing during an import does not
    wait for it), so the next test is not told "still stopping"."""
    monkeypatch.setattr(cwt._CadFormats, "exts", (".dxf", ".svg"))
    yield
    t = cwt._CAD_RENDER.get("thread")
    if t is not None:
        t.join(15)


@pytest.fixture
def no_cad_backends(monkeypatch):
    """Act as the Windows .exe, which ships without the CAD importers."""
    for m in ("ezdxf", "matplotlib", "fitz", "pymupdf"):
        monkeypatch.setitem(sys.modules, m, None)
    monkeypatch.setattr(cwt._CadFormats, "exts", None)
