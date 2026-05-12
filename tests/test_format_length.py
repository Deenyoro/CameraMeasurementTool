"""ft-in formatter boundary cases.

The rollover bug fixed by this test: 23.999" used to format as
"1'-12\"" because the 11+16/16 → 12 inches bump didn't cascade into
the next foot. Should be "2'-0\"".
"""

from __future__ import annotations

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import camera_wire_tool as cwt


def _stub(unit: str = "ft-in"):
    """Minimal stand-in for the bits of CameraWireTool that _format_length
    actually touches. Lets us call the method without spinning up Tk."""
    return types.SimpleNamespace(
        unit_var=types.SimpleNamespace(get=lambda: unit),
        _IN_PER_M=39.3701,
        _IN_PER_CM=0.3937007874,
    )


def _fmt(inches, unit="ft-in"):
    return cwt.CameraWireTool._format_length(_stub(unit), inches)


def test_zero_inches():
    assert _fmt(0) == '0"'


def test_exact_inches():
    assert _fmt(5) == '5"'
    assert _fmt(11) == '11"'


def test_exact_feet():
    assert _fmt(12) == '1\'-0"'
    assert _fmt(24) == '2\'-0"'
    assert _fmt(60) == '5\'-0"'


def test_feet_plus_inches():
    assert _fmt(13) == '1\'-1"'
    assert _fmt(25) == '2\'-1"'


def test_fractional_sixteenths():
    # 1/16 — coprime
    assert _fmt(0.0625) == '0 1/16"'
    # 8/16 → 1/2
    assert _fmt(0.5) == '0 1/2"'
    # 12/16 → 3/4
    assert _fmt(0.75) == '0 3/4"'


def test_near_inch_boundary_rolls_into_next_inch():
    """11.99" rounds to 12/16 of inch 11, which then bumps to inch 12.
    Should cascade into 1 foot 0 inches, NOT print '1'-12\"'."""
    assert _fmt(11.99) == '1\'-0"'


def test_near_foot_boundary_rolls_into_next_foot():
    """The fixed bug: 23.999\" used to format as '1'-12\"'. Should be
    '2'-0\"' after the rollover cascade."""
    assert _fmt(23.999) == '2\'-0"'


def test_none_inches():
    assert _fmt(None) == "--"


def test_other_units():
    assert _fmt(12, unit="inches") == '12.00"'
    assert _fmt(12, unit="feet-dec") == "1.00 ft"
    # 39.3701 inches = 1 m
    assert _fmt(39.3701, unit="meters").startswith("1.000")
    # 100 cm = ~39.37 inches
    assert _fmt(39.3701, unit="cm").startswith("100")
    # m-cm: 250 cm
    out = _fmt(39.3701 * 2.5, unit="m-cm")
    assert "2 m" in out and "50" in out
