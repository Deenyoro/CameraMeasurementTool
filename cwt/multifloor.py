"""Multi-floor project model + cross-floor anchor-point alignment.

The user's hard requirement: when switching floors, the same physical
location on the building maps to the same canvas position. Achieved by
storing per-floor `alignment` (translation + scale + rotation) computed
from two anchor points that the user clicks on each floor.

A "project" is the (Floor1.drawio, Floor2.drawio, ...) bundle plus the
shared cable catalog, wire routes per floor, and risers.

File format (v2 .wiremap.json):
{
  "version": 2,
  "project_name": "...",
  "active_floor": "F1",
  "floors": [
      {"id": "F1", "name": "First Floor", "elevation_ft": 0,
       "drawio_file": "Floor1.drawio",
       "alignment": {"tx": 0, "ty": 0, "scale": 1.0, "rotation_deg": 0},
       "anchor_points": [[ix1, iy1], [ix2, iy2]]
      },
      ...
  ],
  "cable_types": {...},
  "wire_routes_by_floor": {"F1": [...], ...},
  "risers": [...],
  "scale_factor": 0.59913,
  "unit": "ft-in"
}

A v1 file (single floor) auto-migrates by wrapping into a one-floor v2.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field


IDENTITY_ALIGNMENT = {"tx": 0.0, "ty": 0.0, "scale": 1.0, "rotation_deg": 0.0}


# ---------------- Anchor-point alignment math
def compute_alignment(
    floor_a_anchors: list[tuple[float, float]],
    floor_b_anchors: list[tuple[float, float]],
) -> dict:
    """Solve a 2-point similarity transform that maps floor B → floor A.

    Given two corresponding pairs (a1, b1) and (a2, b2), find translation,
    rotation, and uniform scale so that applying the transform to b1, b2
    yields a1, a2. Standard computer-vision similarity transform.

    Returns: {"tx", "ty", "scale", "rotation_deg"}
    """
    if len(floor_a_anchors) < 2 or len(floor_b_anchors) < 2:
        return dict(IDENTITY_ALIGNMENT)

    a1x, a1y = floor_a_anchors[0]
    a2x, a2y = floor_a_anchors[1]
    b1x, b1y = floor_b_anchors[0]
    b2x, b2y = floor_b_anchors[1]

    # Vectors from anchor 1 to anchor 2 in each frame
    avx, avy = a2x - a1x, a2y - a1y
    bvx, bvy = b2x - b1x, b2y - b1y

    a_len = math.hypot(avx, avy)
    b_len = math.hypot(bvx, bvy)
    if b_len == 0 or a_len == 0:
        return dict(IDENTITY_ALIGNMENT)

    scale = a_len / b_len
    a_ang = math.atan2(avy, avx)
    b_ang = math.atan2(bvy, bvx)
    rotation_rad = a_ang - b_ang

    # Apply rotation+scale to b1, then translate so b1 lands on a1
    cos_r = math.cos(rotation_rad)
    sin_r = math.sin(rotation_rad)
    rb1x = scale * (cos_r * b1x - sin_r * b1y)
    rb1y = scale * (sin_r * b1x + cos_r * b1y)
    tx = a1x - rb1x
    ty = a1y - rb1y

    return {
        "tx": tx, "ty": ty,
        "scale": scale,
        "rotation_deg": math.degrees(rotation_rad),
    }


def _safe_align_field(alignment: dict, key: str, default: float) -> float:
    """Read a numeric alignment field with a fallback. Public-API
    callers (apply_alignment / inverse_alignment) used to KeyError on
    a manually-constructed partial dict; the loader normalize covers
    file-load callers, but library users get the same robustness."""
    if not isinstance(alignment, dict):
        return default
    raw = alignment.get(key, default)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def apply_alignment(
    pt: tuple[float, float], alignment: dict,
) -> tuple[float, float]:
    """Apply (rotate around origin, scale, translate) to an image-pixel point."""
    rot = _safe_align_field(alignment, "rotation_deg", 0.0)
    s   = _safe_align_field(alignment, "scale",        1.0)
    tx  = _safe_align_field(alignment, "tx",           0.0)
    ty  = _safe_align_field(alignment, "ty",           0.0)
    cos_r = math.cos(math.radians(rot))
    sin_r = math.sin(math.radians(rot))
    x = s * (cos_r * pt[0] - sin_r * pt[1]) + tx
    y = s * (sin_r * pt[0] + cos_r * pt[1]) + ty
    return x, y


def inverse_alignment(alignment: dict) -> dict:
    """Inverse of a similarity transform — useful when projecting from
    project-frame back to a floor's local image-pixel coords."""
    s = _safe_align_field(alignment, "scale", 1.0)
    if s == 0:
        return dict(IDENTITY_ALIGNMENT)
    inv_s = 1.0 / s
    inv_rot = -_safe_align_field(alignment, "rotation_deg", 0.0)
    cos_r = math.cos(math.radians(inv_rot))
    sin_r = math.sin(math.radians(inv_rot))
    # Inverse translation: -inv_rot/inv_s * t
    tx = _safe_align_field(alignment, "tx", 0.0)
    ty = _safe_align_field(alignment, "ty", 0.0)
    new_tx = -inv_s * (cos_r * tx - sin_r * ty)
    new_ty = -inv_s * (sin_r * tx + cos_r * ty)
    return {"tx": new_tx, "ty": new_ty, "scale": inv_s, "rotation_deg": inv_rot}


# ---------------- Project file IO + v1 → v2 migration
@dataclass
class FloorState:
    id: str
    name: str
    elevation_ft: float
    drawio_file: str       # relative to project file
    alignment: dict = field(default_factory=lambda: dict(IDENTITY_ALIGNMENT))
    anchor_points: list[list[float]] = field(default_factory=list)
    wire_routes: list[dict] = field(default_factory=list)
    annotations: list[dict] = field(default_factory=list)


@dataclass
class ProjectState:
    version: int = 2
    project_name: str = ""
    active_floor: str = "F1"
    floors: list[FloorState] = field(default_factory=list)
    cable_types: dict = field(default_factory=dict)
    risers: list[dict] = field(default_factory=list)
    scale_factor: float | None = None
    unit: str = "ft-in"
    next_device_id: int = 100

    def get_floor(self, fid: str) -> FloorState | None:
        return next((f for f in self.floors if f.id == fid), None)


def load_project(project_path: str, drawio_path: str) -> ProjectState:
    """Load a project file. Auto-migrates from v1 (single-floor) format."""
    if not os.path.exists(project_path):
        return _make_empty_project(drawio_path)

    try:
        with open(project_path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return _make_empty_project(drawio_path)

    version = int(data.get("version", 1))
    if version >= 2:
        return _load_v2(data, project_path)
    return _migrate_from_v1(data, drawio_path)


def _make_empty_project(drawio_path: str) -> ProjectState:
    floor = FloorState(
        id="F1", name="First Floor", elevation_ft=0.0,
        drawio_file=os.path.basename(drawio_path),
    )
    return ProjectState(floors=[floor], active_floor="F1")


def _migrate_from_v1(data: dict, drawio_path: str) -> ProjectState:
    """Wrap a v1 (single-floor) wiremap into a v2 project. Preserves
    catalog-level state (cable_types, annotations) that older files
    may have stored alongside the wire routes — silently dropping them
    on first save would lose the user's customizations."""
    # Coerce container fields the same way _load_v2 does, so a malformed
    # v1 file doesn't crash the migration.
    raw_wires = data.get("wire_routes", [])
    raw_anns  = data.get("annotations", [])
    floor = FloorState(
        id="F1", name="First Floor", elevation_ft=0.0,
        drawio_file=os.path.basename(drawio_path),
        wire_routes=list(raw_wires) if isinstance(raw_wires, list) else [],
        annotations=list(raw_anns) if isinstance(raw_anns, list) else [],
    )
    try:
        ndid = int(data.get("next_device_id", 100) or 100)
    except (TypeError, ValueError):
        ndid = 100
    try:
        sf = data.get("scale_factor")
        sf = float(sf) if sf is not None else None
    except (TypeError, ValueError):
        sf = None
    raw_unit = data.get("unit", "ft-in")
    unit = raw_unit if isinstance(raw_unit, str) and raw_unit else "ft-in"
    raw_ct = data.get("cable_types", {})
    cable_types = dict(raw_ct) if isinstance(raw_ct, dict) else {}
    return ProjectState(
        version=2, active_floor="F1",
        floors=[floor],
        cable_types=cable_types,
        scale_factor=sf,
        unit=unit,
        next_device_id=ndid,
    )


def _load_v2(data: dict, project_path: str) -> ProjectState:
    floors: list[FloorState] = []
    # Container fields should be dicts/lists; treat anything else as empty.
    raw_wbf = data.get("wire_routes_by_floor", {})
    wires_by_floor = raw_wbf if isinstance(raw_wbf, dict) else {}
    raw_abf = data.get("annotations_by_floor", {})
    annotations_by_floor = raw_abf if isinstance(raw_abf, dict) else {}
    raw_floors = data.get("floors", [])
    if not isinstance(raw_floors, list):
        raw_floors = []
    for f in raw_floors:
        # Defensive — id + drawio_file are required, but a corrupted
        # project file may have neither. Skip the floor entry rather
        # than crashing the entire load.
        if not isinstance(f, dict):
            continue
        fid = f.get("id")
        drawio_file = f.get("drawio_file")
        # Both id + drawio_file must be non-empty strings. Without the
        # isinstance check on drawio_file, a hand-edited integer (or
        # path-like Path) would survive into FloorState and then break
        # os.path operations downstream (e.g. relative path resolve).
        if (not fid or not isinstance(fid, str)
                or not drawio_file or not isinstance(drawio_file, str)):
            continue
        try:
            elev = float(f.get("elevation_ft", 0))
        except (TypeError, ValueError):
            elev = 0.0
        # alignment may have been hand-edited to a non-dict OR be a
        # partial dict missing keys. Normalize: start from IDENTITY, then
        # overlay any well-typed numeric fields the user provided.
        # apply_alignment / inverse_alignment do raw dict[] indexing on
        # all four keys, so missing one would KeyError downstream.
        alignment = dict(IDENTITY_ALIGNMENT)
        raw_align = f.get("alignment")
        if isinstance(raw_align, dict):
            for k in ("tx", "ty", "scale", "rotation_deg"):
                if k in raw_align:
                    try:
                        alignment[k] = float(raw_align[k])
                    except (TypeError, ValueError):
                        pass
        # anchor_points: list of [x, y] pairs; skip malformed entries
        # instead of crashing the load on one bad row.
        raw_anchors = f.get("anchor_points", [])
        anchors: list[list[float]] = []
        if isinstance(raw_anchors, list):
            for p in raw_anchors:
                try:
                    anchors.append([float(p[0]), float(p[1])])
                except (TypeError, ValueError, IndexError):
                    continue
        # wire/annotation per-floor lists default to [] when not present
        # OR not list-shaped.
        raw_wires = wires_by_floor.get(fid, [])
        raw_anns  = annotations_by_floor.get(fid, [])
        floors.append(FloorState(
            id=fid,
            name=f.get("name", fid) if isinstance(f.get("name", fid), str) else fid,
            elevation_ft=elev,
            drawio_file=drawio_file,
            alignment=alignment,
            anchor_points=anchors,
            wire_routes=list(raw_wires) if isinstance(raw_wires, list) else [],
            annotations=list(raw_anns) if isinstance(raw_anns, list) else [],
        ))
    # next_device_id may be a hand-edited string in legacy files —
    # coerce safely instead of letting int() raise.
    try:
        ndid = int(data.get("next_device_id", 100) or 100)
    except (TypeError, ValueError):
        ndid = 100
    try:
        sf = data.get("scale_factor")
        sf = float(sf) if sf is not None else None
    except (TypeError, ValueError):
        sf = None
    # Defensive coerce for fields a hand-edit could turn into the wrong
    # type. project_name + unit downstream consumers (StringVar, title
    # bar) assume strings — coerce or fall back.
    raw_name = data.get("project_name", "")
    project_name = raw_name if isinstance(raw_name, str) else ""
    raw_unit = data.get("unit", "ft-in")
    unit = raw_unit if isinstance(raw_unit, str) and raw_unit else "ft-in"
    raw_active = data.get("active_floor", floors[0].id if floors else "F1")
    active_floor = (raw_active if isinstance(raw_active, str) and raw_active
                    else (floors[0].id if floors else "F1"))
    # cable_types must be dict-shaped; risers must be list-shaped.
    raw_ct = data.get("cable_types", {})
    cable_types = dict(raw_ct) if isinstance(raw_ct, dict) else {}
    raw_risers = data.get("risers", [])
    risers = list(raw_risers) if isinstance(raw_risers, list) else []
    return ProjectState(
        version=2,
        project_name=project_name,
        active_floor=active_floor,
        floors=floors,
        cable_types=cable_types,
        risers=risers,
        scale_factor=sf,
        unit=unit,
        next_device_id=ndid,
    )


def _serialize_wire(wr: dict) -> dict | None:
    """Build the on-disk wire entry from an in-memory route. Returns
    None when the route is too malformed to round-trip safely (skipped
    by caller) so we don't write garbage into the project file."""
    if not isinstance(wr, dict):
        return None
    raw_pts = wr.get("points")
    if not isinstance(raw_pts, list):
        return None
    points: list[list[float]] = []
    for p in raw_pts:
        try:
            points.append([float(p[0]), float(p[1])])
        except (TypeError, ValueError, IndexError):
            continue
    if len(points) < 2:
        return None
    out = {"points": points, "device_id": wr.get("device_id")}
    if wr.get("cable_type"):
        out["cable_type"] = wr["cable_type"]
    return out


def save_project(project: ProjectState, project_path: str) -> None:
    """Write a v2 project file."""
    data = {
        "version": 2,
        "project_name": project.project_name,
        "active_floor": project.active_floor,
        "floors": [
            {
                "id": f.id,
                "name": f.name,
                "elevation_ft": f.elevation_ft,
                "drawio_file": f.drawio_file,
                "alignment": dict(f.alignment),
                "anchor_points": [list(p) for p in f.anchor_points],
            }
            for f in project.floors
        ],
        "cable_types": project.cable_types,
        "wire_routes_by_floor": {
            f.id: [out for out in (_serialize_wire(wr) for wr in f.wire_routes)
                   if out is not None]
            for f in project.floors
        },
        "annotations_by_floor": {
            f.id: [a for a in f.annotations if isinstance(a, dict)]
            for f in project.floors
        },
        # Filter risers the same way wires/annotations are filtered —
        # a non-dict entry would survive json.dump but be a tripping
        # hazard on the next load. Skip it at save time too.
        "risers": [r for r in project.risers if isinstance(r, dict)],
        "scale_factor": project.scale_factor,
        "unit": project.unit,
        "next_device_id": project.next_device_id,
    }
    # Atomic write: stage to .tmp, then rename. On any failure during
    # serialization, clean up the staging file so the directory doesn't
    # accumulate '.wiremap.json.tmp' orphans across crashes. Matches the
    # pattern used by cwt.bom.write_csv and cwt.export.
    tmp = project_path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, project_path)
    except Exception:
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except OSError:
            pass
        raise
