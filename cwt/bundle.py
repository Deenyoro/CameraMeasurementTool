"""`.zsmt` project bundle — a single self-contained file for the Camera
Measurement Tool that packages everything for a project together:

    * the project wiremap JSON (floors, alignment, wires, drop points, …)
    * every floor's .drawio
    * a **revision backlog** so any past save can be restored

A .zsmt is just a ZIP with this layout::

    manifest.json      { format, format_version, app_version, updated, current }
    revisions.json     [ {id, ts, iso, note, wiremap{…}, floors{fid: sha}} … ]
    blobs/<sha256>     content-addressed .drawio bytes (deduped across revisions)

Each save appends one revision. Drawio contents are stored content-addressed,
so unchanged floors cost nothing on subsequent saves — a 50-deep history of a
two-floor project is a couple of MB, not 100. Old revisions are pruned to a
cap and unreferenced blobs garbage-collected. The newest revision is the
"current" project state; reverting just re-extracts (or re-commits) an older
one, so nothing is ever destructively overwritten.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
import zipfile

BUNDLE_EXT = ".zsmt"
_FORMAT = "zsmt"
_FORMAT_VERSION = 1
_MANIFEST = "manifest.json"
_REVISIONS = "revisions.json"
_BLOB_DIR = "blobs/"
DEFAULT_KEEP = 50


def is_bundle(path: str) -> bool:
    """True if `path` looks like a bundle file (by extension)."""
    return isinstance(path, str) and path.lower().endswith(BUNDLE_EXT)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _new_rev_id() -> str:
    return time.strftime("rev_%Y%m%dT%H%M%S") + "_" + uuid.uuid4().hex[:6]


def _read_revisions(zf: zipfile.ZipFile) -> list[dict]:
    if _REVISIONS in zf.namelist():
        try:
            data = json.loads(zf.read(_REVISIONS))
            if isinstance(data, list):
                return [r for r in data if isinstance(r, dict)]
        except (ValueError, KeyError):
            pass
    return []


def commit_revision(
    bundle_path: str,
    wiremap: dict,
    floor_files: dict[str, str],
    *,
    note: str = "",
    app_version: str = "",
    keep: int = DEFAULT_KEEP,
) -> str:
    """Append a new revision to (or create) the bundle and return its id.

    `wiremap`     — the project state as a JSON-able dict (multifloor save form).
    `floor_files` — {floor_id: path to that floor's current .drawio}.

    Drawio contents are hashed and stored as deduped blobs. Revisions beyond
    `keep` are dropped (oldest first) and orphaned blobs garbage-collected.
    The whole zip is rewritten atomically (.tmp → os.replace)."""
    # Load existing revisions + ALL existing blob bytes into memory, then
    # close the old zip. Reading upfront is what lets us os.replace() over
    # it afterwards on Windows (can't rename onto a still-open file).
    revisions: list[dict] = []
    blobs: dict[str, bytes] = {}
    if os.path.exists(bundle_path):
        with zipfile.ZipFile(bundle_path) as z:
            revisions = _read_revisions(z)
            for n in z.namelist():
                if n.startswith(_BLOB_DIR):
                    blobs[n[len(_BLOB_DIR):]] = z.read(n)

    # Hash each floor's current drawio; add any new content as a blob.
    floor_shas: dict[str, str] = {}
    for fid, src in floor_files.items():
        with open(src, "rb") as fh:
            data = fh.read()
        sha = _sha(data)
        floor_shas[fid] = sha
        blobs.setdefault(sha, data)

    # Skip a no-op revision: if nothing changed since the last one, don't
    # spam the backlog (autosaves fire often). Compared on the drawio
    # content hashes + the wiremap payload.
    if revisions:
        last = revisions[-1]
        if (last.get("floors") == floor_shas
                and last.get("wiremap") == wiremap):
            return last.get("id")

    rev = {
        "id": _new_rev_id(),
        "ts": time.time(),
        "iso": time.strftime("%Y-%m-%d %H:%M:%S"),
        "note": str(note or ""),
        "wiremap": wiremap,
        "floors": floor_shas,
    }
    revisions.append(rev)
    if keep and len(revisions) > keep:
        revisions = revisions[-keep:]

    # Which blobs are still referenced by a surviving revision? (GC the rest.)
    referenced: set[str] = set()
    for r in revisions:
        for sha in (r.get("floors") or {}).values():
            referenced.add(sha)

    manifest = {
        "format": _FORMAT,
        "format_version": _FORMAT_VERSION,
        "app_version": str(app_version or ""),
        "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "current": rev["id"],
    }

    tmp = bundle_path + ".tmp"
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as out:
            out.writestr(_MANIFEST, json.dumps(manifest, indent=2))
            out.writestr(_REVISIONS, json.dumps(revisions, indent=2))
            for sha in sorted(referenced):
                if sha in blobs:
                    out.writestr(_BLOB_DIR + sha, blobs[sha])
                # else: referenced but missing — skip rather than abort.
        os.replace(tmp, bundle_path)
    except Exception:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass
        raise
    return rev["id"]


def list_revisions(bundle_path: str) -> list[dict]:
    """Lightweight revision list (no wiremap payload), newest LAST.
    Each entry: {id, ts, iso, note}."""
    with zipfile.ZipFile(bundle_path) as z:
        revs = _read_revisions(z)
    return [{"id": r.get("id"), "ts": r.get("ts"),
             "iso": r.get("iso", ""), "note": r.get("note", "")}
            for r in revs]


def read_revision(bundle_path: str, rev_id: str | None = None):
    """Return (wiremap_dict, {floor_id: drawio_bytes}, rev_meta) for the
    given revision id, or the newest revision when rev_id is None."""
    with zipfile.ZipFile(bundle_path) as z:
        revs = _read_revisions(z)
        if not revs:
            raise ValueError("bundle has no revisions")
        if rev_id is None:
            rev = revs[-1]
        else:
            rev = next((r for r in revs if r.get("id") == rev_id), None)
            if rev is None:
                raise KeyError(f"revision {rev_id!r} not found")
        wiremap = rev.get("wiremap") or {}
        floors: dict[str, bytes] = {}
        for fid, sha in (rev.get("floors") or {}).items():
            name = _BLOB_DIR + sha
            if name in z.namelist():
                floors[fid] = z.read(name)
    return wiremap, floors, rev


def _atomic_write_bytes(path: str, data: bytes) -> None:
    """Write bytes atomically (tmp + os.replace), cleaning up the staging
    file on failure — same contract as the rest of the codebase."""
    tmp = path + ".tmp"
    try:
        with open(tmp, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except Exception:
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except OSError:
            pass
        raise


def _flat_name(name: object) -> str:
    """Reduce a bundle-supplied name (floor id or drawio_file) to a bare
    filename that cannot escape the extraction folder. Both separators are
    treated as path breaks regardless of host OS, and "."/".." are
    rejected, so a crafted .zsmt can't write outside `dest_dir`.
    Returns "" when nothing usable remains."""
    base = os.path.basename(str(name).replace("\\", "/"))
    return "" if base in (".", "..") else base


def extract_revision(bundle_path: str, dest_dir: str,
                     rev_id: str | None = None):
    """Materialize a revision into `dest_dir` as a normal *flat* folder
    project: every floor's .drawio plus project.wiremap.json, all in the
    same directory (the layout the app's project-owner scan expects — it
    finds the wiremap by reading the drawios' sibling folder). Floor
    drawio_file fields are normalized to the flat basenames; colliding
    basenames (two floors whose drawio_file flattens to the same name)
    are uniquified with a _2/_3/… suffix so no floor overwrites another.
    All writes are atomic (tmp + os.replace). Returns
    (wiremap_path, {floor_id: drawio_path}, rev_meta)."""
    wiremap, floors, rev = read_revision(bundle_path, rev_id)
    os.makedirs(dest_dir, exist_ok=True)
    wm = json.loads(json.dumps(wiremap))  # deep copy
    # Decide each floor's flat filename from its drawio_file basename,
    # uniquifying collisions (case-insensitive — Windows filesystems).
    # The wiremap is written last under a fixed name; reserve it so a floor
    # named "project.wiremap.json" can't be overwritten by it.
    used: set[str] = {"project.wiremap.json"}

    def _unique(name: str) -> str:
        base, ext = os.path.splitext(name)
        candidate = name
        n = 2
        while candidate.lower() in used:
            candidate = f"{base}_{n}{ext}"
            n += 1
        used.add(candidate.lower())
        return candidate

    name_by_id: dict[str, str] = {}
    for f in wm.get("floors", []):
        fid = f.get("id")
        df = f.get("drawio_file") or f"{fid}.drawio"
        # _flat_name(f"{fid}.drawio") always ends in ".drawio", so it is
        # never empty and needs no further fallback.
        name = _unique(_flat_name(df) or _flat_name(f"{fid}.drawio"))
        name_by_id[fid] = name
        f["drawio_file"] = name
    floor_paths: dict[str, str] = {}
    for fid, data in floors.items():
        name = name_by_id.get(fid) or _unique(_flat_name(f"{fid}.drawio"))
        p = os.path.join(dest_dir, name)
        _atomic_write_bytes(p, data)
        floor_paths[fid] = p
    wmp = os.path.join(dest_dir, "project.wiremap.json")
    _atomic_write_bytes(wmp, json.dumps(wm, indent=2).encode("utf-8"))
    return wmp, floor_paths, rev
