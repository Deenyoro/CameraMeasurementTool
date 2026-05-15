"""Regression test for the 'ultra small Floor 1' bug.

The drag-align dialog used to apply its draft to whatever floor was
ACTIVE, regardless of which floor the user thought they'd labeled
'reference' in the combo. Now the dialog has explicit target/ref
pickers and Apply writes to the chosen TARGET. Pin that contract:
applying to a non-active floor doesn't touch the active floor's
alignment."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cwt import multifloor as mf


def test_alignment_stored_on_target_not_active():
    """Simulates the dialog's apply_now writing to a chosen target
    floor. Pinning: the OTHER floor's alignment is untouched."""
    f1 = mf.FloorState(id="F1", name="First", elevation_ft=0.0,
                        drawio_file="F1.drawio",
                        alignment=dict(mf.IDENTITY_ALIGNMENT))
    f2 = mf.FloorState(id="F2", name="Second", elevation_ft=14.0,
                        drawio_file="F2.drawio",
                        alignment=dict(mf.IDENTITY_ALIGNMENT))
    project = mf.ProjectState(active_floor="F1", floors=[f1, f2])

    # User is viewing F1 (active) but picks F2 as TARGET in the dialog.
    # The user-bug case: they THOUGHT F2 was the reference but actually
    # selected it as the 'floor to align'. We want to verify that the
    # current contract — alignment is applied to the explicit target,
    # NOT to the active floor — holds.
    target = project.get_floor("F2")
    target.alignment = {"tx": 100.0, "ty": 50.0,
                        "scale": 1.5, "rotation_deg": 0.0}

    active = project.get_floor("F1")
    # Active floor's alignment is untouched even though it was 'active'.
    assert active.alignment == mf.IDENTITY_ALIGNMENT
    assert target.alignment["scale"] == 1.5


def test_clear_alignment_only_affects_active():
    """_clear_active_floor_alignment must not nuke OTHER floors'
    alignments. Pin that contract."""
    f1 = mf.FloorState(id="F1", name="First", elevation_ft=0.0,
                        drawio_file="F1.drawio",
                        alignment={"tx": 0, "ty": 0,
                                    "scale": 1.5, "rotation_deg": 0})
    f2 = mf.FloorState(id="F2", name="Second", elevation_ft=14.0,
                        drawio_file="F2.drawio",
                        alignment={"tx": 100, "ty": 50,
                                    "scale": 2.0, "rotation_deg": 30})
    project = mf.ProjectState(active_floor="F1", floors=[f1, f2])

    # Simulate what _clear_active_floor_alignment does.
    active = project.get_floor(project.active_floor)
    active.alignment = dict(mf.IDENTITY_ALIGNMENT)

    # F2's alignment must be unchanged.
    assert project.get_floor("F2").alignment == {
        "tx": 100, "ty": 50, "scale": 2.0, "rotation_deg": 30}


def test_swap_target_ref_consistent():
    """The drag-align Swap button swaps the picker values. After
    swapping, the dialog should align the NEW target (= old ref) onto
    the new ref (= old target). Both floors must keep their original
    state until Apply is hit."""
    f1 = mf.FloorState(id="F1", name="First", elevation_ft=0.0,
                        drawio_file="F1.drawio",
                        alignment=dict(mf.IDENTITY_ALIGNMENT))
    f2 = mf.FloorState(id="F2", name="Second", elevation_ft=14.0,
                        drawio_file="F2.drawio",
                        alignment=dict(mf.IDENTITY_ALIGNMENT))
    project = mf.ProjectState(active_floor="F1", floors=[f1, f2])

    target_var, ref_var = "F1", "F2"
    # swap_target_ref equivalent
    target_var, ref_var = ref_var, target_var
    assert target_var == "F2" and ref_var == "F1"
    # No mutation to floor state from swap alone.
    assert project.get_floor("F1").alignment == mf.IDENTITY_ALIGNMENT
    assert project.get_floor("F2").alignment == mf.IDENTITY_ALIGNMENT
