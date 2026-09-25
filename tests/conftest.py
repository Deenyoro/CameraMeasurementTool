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


# `tk scaling` for 100 % of 96 dpi. Every GUI test starts here, whatever
# the display's own dpi is, so the size checks mean the same everywhere.
_SCALING_96_DPI = 96 / 72

# The Windows release job (build-windows) runs the whole suite on a
# runner whose desktop size, display scale and Tk version nobody picks.
# The GUI tests measure pixel layouts and were written against Xvfb, so
# there they skip unless asked for (CAMERA_TOOL_GUI_TESTS=1). The Linux
# CI test job has no display and skips them anyway.
_SKIP_GUI_ON_WINDOWS_CI = (
    sys.platform == "win32"
    and bool(os.environ.get("GITLAB_CI") or os.environ.get("CI"))
    and os.environ.get("CAMERA_TOOL_GUI_TESTS") != "1")


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
            # A window larger than the desktop gets shrunk on Windows (Tk
            # keeps it within `wm maxsize`), so the test would measure a
            # different size than it asked for: skip it there instead.
            w, _, rest = new_geometry.partition("x")
            h = rest.split("+")[0].split("-")[0]
            if w.isdigit() and h.isdigit() and (
                    int(w) > root.winfo_screenwidth()
                    or int(h) > root.winfo_screenheight()):
                pytest.skip(f"window {w}x{h} does not fit this desktop")
            with contextlib.suppress(tk.TclError):
                if root.state() == "zoomed":
                    root.state("normal")
        return orig(new_geometry)

    root.geometry = root.wm_geometry = geometry


@pytest.fixture
def tk_root(isolated_home):
    if _SKIP_GUI_ON_WINDOWS_CI:
        pytest.skip("GUI layout tests do not run on the Windows CI runner "
                    "(set CAMERA_TOOL_GUI_TESTS=1 to run them)")
    try:
        root = tk.Tk()
    except tk.TclError as e:
        pytest.skip(f"no display for Tk: {e}")
    # X11 Tk keeps `tk scaling` per display, so a test that set 150 %
    # would leave every later test at 150 %: start each test at 100 %.
    root.tk.call("tk", "scaling", _SCALING_96_DPI)
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
