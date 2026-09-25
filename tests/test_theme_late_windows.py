"""Windows built after start-up get the dark theme's background too."""

from __future__ import annotations

import pytest

import camera_wire_tool as cwt


def test_window_background_is_themed_from_the_start(tk_root):
    """Content built after start-up (the start screen after a failed
    open, then the import from it) showed Tk's light grey behind its
    dark-themed widgets: sv-ttk only recolours windows that exist when
    its theme-changed event is handled."""
    if not cwt._HAS_SVTTK:
        pytest.skip("sv-ttk not installed")
    root = tk_root
    cwt.apply_theme(root)
    assert str(root.cget("background")).lower() == "#1c1c1c"
    top = cwt.tk.Toplevel(root)
    assert str(top.cget("background")).lower() == "#1c1c1c"
    top.destroy()
