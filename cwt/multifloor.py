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


def is_identity_alignment(alignment: dict) -> bool:
    """True when an alignment is (within tolerance) the identity transform.

    tx/ty within half a pixel, scale within 1e-4, rotation within 0.01°.
    A non-dict / corrupt value counts as identity — there's nothing
    meaningful to apply. Used to tell an aligned floor (mapped onto a
    shared frame) apart from an un-aligned one."""
    if not isinstance(alignment, dict):
        return True
    return (abs(_safe_align_field(alignment, "tx", 0.0)) < 0.5
            and abs(_safe_align_field(alignment, "ty", 0.0)) < 0.5
            and abs(_safe_align_field(alignment, "scale", 1.0) - 1.0) < 1e-4
            and abs(_safe_align_field(alignment, "rotation_deg", 0.0)) < 0.01)


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
    # Cross-floor cable drop points (vertical risers placed by the user).
    # Each is a project-level entity positioned in SHARED world/aligned
    # coords so it lands at the same physical spot on every floor it's
    # shown on. See coerce_drop_point() for the dict shape.
    drop_points: list[dict] = field(default_factory=list)
    # Camera model catalog + assignments. `camera_models` is the catalog
    # (each: id, name, model_number, price, fov). `type_default_models`
    # maps a symbol type code -> model id (the default for every camera of
    # that type). `device_models` maps a device id -> model id (a per-device
    # override). Effective model = device override, else type default.
    camera_models: list[dict] = field(default_factory=list)
    type_default_models: dict = field(default_factory=dict)
    device_models: dict = field(default_factory=dict)
    # Per-device FOV aim (azimuth degrees, 0 = up/north). Stored separately
    # from the model so a camera keeps its model's cone SHAPE while pointing
    # where the user aimed it — and the aim persists across reloads.
    device_azimuths: dict = field(default_factory=dict)
    # Project-wide default cable type (id into cable_types). When set, a
    # wire with no explicit cable and whose device-type doesn't specify one
    # uses this instead of the per-category inference. None = infer.
    default_cable: str | None = None
    # Per-symbol-type metadata that must survive reloads. symbol_types
    # themselves are rebuilt from the .drawio legend on every load, so any
    # field the legend can't encode (category, cable_type, is_run_point)
    # was previously LOST on reload. This map is the durable home:
    #   {type_code: {"category": str, "cable_type": str,
    #                "is_run_point": bool}}
    # All keys optional per entry. Overlaid onto symbol_types after parse.
    symbol_meta: dict = field(default_factory=dict)
    scale_factor: float | None = None
    unit: str = "ft-in"
    next_device_id: int = 100

    def get_floor(self, fid: str) -> FloorState | None:
        return next((f for f in self.floors if f.id == fid), None)


# Standard equipment categories. Users can type their own in the Symbol
# Editor — these are just the suggestions every project starts with.
DEFAULT_CATEGORIES = ["IT", "Security", "Cameras"]


def coerce_symbol_meta(raw) -> dict | None:
    """Normalize one symbol_meta entry ({category, cable_type,
    is_run_point}), or None when nothing usable is present. Unknown keys
    are dropped; values are type-checked so a hand-edited file can't
    poison symbol_types at overlay time."""
    if not isinstance(raw, dict):
        return None
    out: dict = {}
    cat = raw.get("category")
    if isinstance(cat, str) and cat.strip():
        out["category"] = cat.strip()
    ct = raw.get("cable_type")
    if isinstance(ct, str) and ct.strip():
        out["cable_type"] = ct.strip()
    if "is_run_point" in raw:
        out["is_run_point"] = bool(raw.get("is_run_point"))
    return out or None


def default_category_for(symbol: dict) -> str:
    """Category to assume when a type has no explicit one: cameras →
    'Cameras', everything else → 'Security' (this is a security-overlay
    tool; IT gear gets tagged explicitly)."""
    if isinstance(symbol, dict) and symbol.get("is_camera"):
        return "Cameras"
    return "Security"


def coerce_camera_model(raw: dict) -> dict | None:
    """Normalize a camera-model dict (catalog entry), or None if unusable.

    Shape: {id, name, model_number, price, fov?}
      - price coerces to a non-negative float (0.0 on junk).
      - fov, when present, is kept as a plain dict of its numeric-ish fields
        (lens_mm, sensor, resolution, mount_height_ft, tilt_deg, azimuth_deg)."""
    if not isinstance(raw, dict):
        return None
    mid = raw.get("id")
    if not mid or not isinstance(mid, str):
        return None
    name = raw.get("name")
    name = name if isinstance(name, str) and name else mid
    model_number = raw.get("model_number")
    model_number = model_number if isinstance(model_number, str) else ""
    try:
        price = max(0.0, float(raw.get("price", 0) or 0))
    except (TypeError, ValueError):
        price = 0.0
    out = {"id": mid, "name": name, "model_number": model_number, "price": price}
    fov = raw.get("fov")
    if isinstance(fov, dict):
        out["fov"] = dict(fov)
    return out


def effective_model_id(project: ProjectState, device_id: str,
                       device_type: str) -> str | None:
    """The model id that applies to a device: its per-device override if set,
    otherwise the default model for its type, otherwise None. Only returns
    an id that still exists in the catalog."""
    valid = {m.get("id") for m in project.camera_models if isinstance(m, dict)}
    dm = project.device_models.get(device_id) if isinstance(project.device_models, dict) else None
    if dm in valid:
        return dm
    td = (project.type_default_models.get(device_type)
          if isinstance(project.type_default_models, dict) else None)
    if td in valid:
        return td
    return None


def get_camera_model(project: ProjectState, model_id: str | None) -> dict | None:
    if not model_id:
        return None
    return next((m for m in project.camera_models
                 if isinstance(m, dict) and m.get("id") == model_id), None)


def elevation_gap_ft(project: ProjectState, floor_a_id: str, floor_b_id: str) -> float:
    """Absolute vertical distance in FEET between two floors, from their
    elevation_ft. Returns 0.0 if either floor is unknown (no gap to add)."""
    a = project.get_floor(floor_a_id)
    b = project.get_floor(floor_b_id)
    if a is None or b is None:
        return 0.0
    try:
        return abs(float(a.elevation_ft) - float(b.elevation_ft))
    except (TypeError, ValueError):
        return 0.0


def vertical_inches_between(project: ProjectState, floor_a_id: str, floor_b_id: str) -> float:
    """Vertical riser distance in INCHES between two floors (feet × 12).
    Wire lengths are tracked in inches, so this is the amount added to a
    cross-floor wire that terminates on a drop point whose home floor
    differs from the floor the wire was drawn on."""
    return elevation_gap_ft(project, floor_a_id, floor_b_id) * 12.0


def coerce_drop_point(raw: dict, valid_floor_ids: set[str] | None = None) -> dict | None:
    """Normalize a drop-point dict (from disk or UI) into a safe, round-
    trippable shape, or None if too malformed to keep.

    Shape: {id, label, x, y, floors: [floor_id, ...], home_floor, cable_type?}
      - x, y    : SHARED world/aligned coords (same physical spot on every
                  floor the drop is shown on — a true vertical riser).
      - floors  : the floors the drop appears on / can be wired to (>= 1).
      - home_floor : where the cable actually terminates; the vertical
                  reference. Forced into `floors` if missing/invalid.
      - cable_type : optional explicit cable override.

    When valid_floor_ids is given, floor refs outside it are dropped (used
    when cascading a floor deletion); on plain load we pass None so a
    transiently-missing floor doesn't silently discard the drop."""
    if not isinstance(raw, dict):
        return None
    did = raw.get("id")
    if not did or not isinstance(did, str):
        return None
    try:
        x = float(raw.get("x"))
        y = float(raw.get("y"))
    except (TypeError, ValueError):
        return None
    raw_floors = raw.get("floors", [])
    floors = ([str(fid) for fid in raw_floors if isinstance(fid, str)]
              if isinstance(raw_floors, list) else [])
    if valid_floor_ids is not None:
        floors = [fid for fid in floors if fid in valid_floor_ids]
    # De-dup while preserving order.
    seen: set[str] = set()
    floors = [f for f in floors if not (f in seen or seen.add(f))]
    if not floors:
        return None
    home = raw.get("home_floor")
    if not isinstance(home, str) or home not in floors:
        home = floors[0]
    label = raw.get("label")
    label = label if isinstance(label, str) and label else did
    out = {"id": did, "label": label, "x": x, "y": y,
           "floors": floors, "home_floor": home}
    ct = raw.get("cable_type")
    if isinstance(ct, str) and ct:
        out["cable_type"] = ct
    return out


def load_project(project_path: str, drawio_path: str) -> ProjectState:
    """Load a project file. Auto-migrates from v1 (single-floor) format."""
    if not os.path.exists(project_path):
        return _make_empty_project(drawio_path)

    try:
        with open(project_path, encoding="utf-8") as f:
            data = json.load(f)
        version = int(data.get("version", 1))
        if version >= 2:
            return _load_v2(data, project_path)
        return _migrate_from_v1(data, drawio_path)
    except (OSError, ValueError):
        # Unreadable / unparseable / zero-floors-after-parse (see
        # _load_v2) — fall back to an empty single-floor project so the
        # app always has at least one floor to show.
        return _make_empty_project(drawio_path)


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
    # A v2 file where EVERY floor entry was malformed (all skipped above)
    # would yield a zero-floor project — downstream code indexes
    # floors[0] and would IndexError. Treat it like any other corrupt
    # file: raise so load_project falls back to a default single-floor
    # empty project.
    if not floors:
        raise ValueError("v2 project file contains no usable floors")
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
    # Drop points: normalize each entry, skipping malformed ones. Floors
    # are NOT filtered against the loaded set here — a floor that failed to
    # parse this session shouldn't silently discard a valid drop.
    raw_drops = data.get("drop_points", [])
    drop_points = ([d for d in (coerce_drop_point(r) for r in raw_drops)
                    if d is not None] if isinstance(raw_drops, list) else [])
    # Camera models + assignments.
    raw_models = data.get("camera_models", [])
    camera_models = ([m for m in (coerce_camera_model(r) for r in raw_models)
                      if m is not None] if isinstance(raw_models, list) else [])
    raw_tdm = data.get("type_default_models", {})
    type_default_models = ({str(k): str(v) for k, v in raw_tdm.items()
                            if isinstance(v, str)}
                           if isinstance(raw_tdm, dict) else {})
    raw_dm = data.get("device_models", {})
    device_models = ({str(k): str(v) for k, v in raw_dm.items()
                      if isinstance(v, str)}
                     if isinstance(raw_dm, dict) else {})
    raw_az = data.get("device_azimuths", {})
    device_azimuths = {}
    if isinstance(raw_az, dict):
        for k, v in raw_az.items():
            if isinstance(v, list):
                # Per-channel aim (multi-sensor camera).
                vals = []
                for x in v:
                    try:
                        vals.append(float(x))
                    except (TypeError, ValueError):
                        vals.append(0.0)
                device_azimuths[str(k)] = vals
            else:
                try:
                    device_azimuths[str(k)] = float(v)
                except (TypeError, ValueError):
                    pass
    raw_sm = data.get("symbol_meta", {})
    symbol_meta = {}
    if isinstance(raw_sm, dict):
        for code, meta in raw_sm.items():
            cleaned = coerce_symbol_meta(meta)
            if cleaned:
                symbol_meta[str(code)] = cleaned
    return ProjectState(
        version=2,
        project_name=project_name,
        active_floor=active_floor,
        floors=floors,
        cable_types=cable_types,
        risers=risers,
        drop_points=drop_points,
        camera_models=camera_models,
        type_default_models=type_default_models,
        device_models=device_models,
        device_azimuths=device_azimuths,
        default_cable=(data.get("default_cable")
                       if isinstance(data.get("default_cable"), str) else None),
        symbol_meta=symbol_meta,
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
    # Drop-point ids the wire terminates on — needed to recompute its
    # cross-floor riser footage on reload.
    raw_drop_ids = wr.get("drop_ids")
    if isinstance(raw_drop_ids, list):
        ids = [str(i) for i in raw_drop_ids if isinstance(i, str)]
        if ids:
            out["drop_ids"] = ids
    return out


def save_project(project: ProjectState, project_path: str) -> None:
    """Write a v2 project file."""
    def _safe_alignment(al):
        """A corrupted floor.alignment (None / string / int) would crash
        dict(al) on save. Coerce to identity to keep the save going —
        the user's wires + devices are more important than preserving
        a junk alignment value."""
        if isinstance(al, dict):
            return dict(al)
        return dict(IDENTITY_ALIGNMENT)
    def _safe_anchors(pts):
        """Same defensive shape for anchor_points — a non-list or
        non-iterable entry shouldn't take the save down."""
        if not isinstance(pts, list):
            return []
        out = []
        for p in pts:
            try:
                out.append([float(p[0]), float(p[1])])
            except (TypeError, ValueError, IndexError):
                continue
        return out
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
                "alignment": _safe_alignment(f.alignment),
                "anchor_points": _safe_anchors(f.anchor_points),
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
        # Normalize drops on the way out too, so a junk in-memory entry
        # can't write an un-loadable record.
        "drop_points": [d for d in (coerce_drop_point(r)
                                    for r in project.drop_points)
                        if d is not None],
        "camera_models": [m for m in (coerce_camera_model(r)
                                      for r in project.camera_models)
                          if m is not None],
        "type_default_models": {str(k): str(v)
                                for k, v in project.type_default_models.items()
                                if isinstance(v, str)},
        "device_models": {str(k): str(v)
                          for k, v in project.device_models.items()
                          if isinstance(v, str)},
        "device_azimuths": {
            str(k): ([float(x) for x in v] if isinstance(v, list)
                     else float(v))
            for k, v in project.device_azimuths.items()
            if isinstance(v, (int, float, list))},
        "default_cable": project.default_cable,
        "symbol_meta": {
            str(code): cleaned
            for code, cleaned in (
                (c, coerce_symbol_meta(m))
                for c, m in (project.symbol_meta.items()
                             if isinstance(project.symbol_meta, dict) else ())
            )
            if cleaned
        },
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
