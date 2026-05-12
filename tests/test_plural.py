"""Tests for CameraWireTool._plural — the tiny helper that kills the
'1 device(s)' awkwardness in flash messages."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camera_wire_tool import CameraWireTool


def test_singular():
    assert CameraWireTool._plural(1, "device") == "1 device"


def test_default_plural_appends_s():
    assert CameraWireTool._plural(0, "device") == "0 devices"
    assert CameraWireTool._plural(2, "device") == "2 devices"
    assert CameraWireTool._plural(99, "wire") == "99 wires"


def test_multi_word_singular():
    """'wire run' → 'wire runs' — only the last token gets the 's'.
    Default 'add s to the whole string' is correct for the simple cases
    we use; multi-word irregulars need explicit plural."""
    assert CameraWireTool._plural(1, "wire run") == "1 wire run"
    assert CameraWireTool._plural(3, "wire run") == "3 wire runs"


def test_explicit_irregular_plural():
    """Pass `plural` for English irregulars (mouse → mice, etc.)."""
    assert CameraWireTool._plural(1, "mouse", "mice") == "1 mouse"
    assert CameraWireTool._plural(5, "mouse", "mice") == "5 mice"


def test_zero_pluralizes():
    """English: zero takes the plural form ('0 items', not '0 item')."""
    assert CameraWireTool._plural(0, "item") == "0 items"
