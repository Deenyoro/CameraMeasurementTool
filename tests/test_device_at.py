"""Tests for CameraWireTool._device_at — the canvas hit-test that runs
on every mouse move. Has to tolerate a corrupted device entry without
crashing (a single bad row would otherwise take down hover, click, and
context-menu paths together)."""

from __future__ import annotations

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import camera_wire_tool as cwt


def _stub(devices, *, camera_check=lambda t: False, hidden_types=()):
    app = types.SimpleNamespace(devices=devices)
    app.dx_scale = 1.0
    app.dy_scale = 1.0
    app._drawio_to_img = lambda x, y: (x, y)
    app._is_camera_type = camera_check
    app._hidden_types = set(hidden_types)
    return app


def test_hits_within_bounds():
    devices = [{"id": "s1", "type": "MD", "x": 0, "y": 0, "w": 10, "h": 10}]
    app = _stub(devices)
    assert cwt.CameraWireTool._device_at(app, 5, 5) is devices[0]


def test_misses_outside_bounds():
    devices = [{"id": "s1", "type": "MD", "x": 0, "y": 0, "w": 10, "h": 10}]
    app = _stub(devices)
    assert cwt.CameraWireTool._device_at(app, 500, 500) is None


def test_skips_non_dict_device():
    """A stray string in self.devices used to crash on d['x']."""
    devices = ["not a device", {"id": "s1", "type": "MD",
                                  "x": 0, "y": 0, "w": 10, "h": 10}]
    app = _stub(devices)
    # The good device should still hit.
    assert cwt.CameraWireTool._device_at(app, 5, 5) is devices[1]


def test_skips_device_with_missing_coords():
    """A device missing x/y/w/h used to KeyError mid-hit-test."""
    devices = [{"id": "s_bad", "type": "MD"},
               {"id": "s1", "type": "MD", "x": 0, "y": 0, "w": 10, "h": 10}]
    app = _stub(devices)
    assert cwt.CameraWireTool._device_at(app, 5, 5) is devices[1]


def test_skips_device_with_non_numeric_coords():
    """A device with stringified coords (hand-edited project file) used
    to crash on the float multiplication."""
    devices = [{"id": "s_bad", "type": "MD", "x": "junk", "y": 0,
                "w": 10, "h": 10},
               {"id": "s1", "type": "MD", "x": 0, "y": 0, "w": 10, "h": 10}]
    app = _stub(devices)
    assert cwt.CameraWireTool._device_at(app, 5, 5) is devices[1]


def test_no_devices_returns_none():
    app = _stub([])
    assert cwt.CameraWireTool._device_at(app, 5, 5) is None


def test_hidden_type_is_not_hittable():
    """A device whose type is in _hidden_types is non-interactive — the
    hit-test skips it so clicks fall through to whatever's beneath."""
    devices = [{"id": "s1", "type": "CR", "x": 0, "y": 0, "w": 10, "h": 10}]
    app = _stub(devices, hidden_types={"CR"})
    assert cwt.CameraWireTool._device_at(app, 5, 5) is None


def test_hidden_type_falls_through_to_visible_device():
    """With two overlapping devices, hiding the top type lets the hit-test
    find the still-visible one underneath."""
    devices = [
        {"id": "cr1", "type": "CR", "x": 0, "y": 0, "w": 20, "h": 20},
        {"id": "md1", "type": "MD", "x": 0, "y": 0, "w": 20, "h": 20},
    ]
    app = _stub(devices, hidden_types={"CR"})
    assert cwt.CameraWireTool._device_at(app, 5, 5) is devices[1]


def test_camera_preferred_over_non_camera_overlap():
    """When a camera and non-camera both contain the hit point, prefer
    the camera (matches the existing behavior of the wires/FOV logic
    which expects camera devices to win the click)."""
    devices = [
        {"id": "n1", "type": "MD", "x": 0, "y": 0, "w": 20, "h": 20},
        {"id": "c1", "type": "CAM", "x": 0, "y": 0, "w": 20, "h": 20},
    ]
    app = _stub(devices, camera_check=lambda t: t == "CAM")
    assert cwt.CameraWireTool._device_at(app, 5, 5) is devices[1]
