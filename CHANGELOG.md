# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Changed

- **Auto-routing removed.** A* pathfinding produced too many paths through
  walls and around them awkwardly on real construction blueprints; the user
  preferred manual wire drawing with snap-to-orthogonal (Shift) and
  snap-to-points. The `cwt.routing` module is retained for tests and a
  potential future rework but is no longer wired into the UI.
- Drawing wires manually is now the only path (Wire mode + click points,
  or right-click a device → "Draw wire from here…").
- Map annotations: drop styled text labels on the floor plan with full
  undo coverage and per-floor persistence.
- Atomic file writes everywhere: .drawio, project file (v2 .wiremap.json),
  autosave, settings, recent files, log rotation, PDF export, all CSV
  exports, and PNG map snapshots — torn writes are no longer possible.
- Wires follow their associated device when the device is moved (smart-
  follow with tolerance for manually-placed endpoints).
- Cable type per-wire override via right-click menu.
- Universal undo coverage: every catalog/state mutation goes through a
  Command, including FOV (single + bulk + clear + rotate-coalescing),
  cable-type catalog edits, annotations, and wire-cable overrides.
- Arrow-key device nudge (Shift = 10× step) with coalescing so held
  keys don't flood the undo stack.
- Ctrl+D duplicate, Ctrl+U jump-to-next-unwired-camera, F11 fullscreen.
- DPI-aware on Windows (SetProcessDpiAwareness) so the canvas renders
  crisply at >100% display scaling.
- Test suite grew from 37 → 114 pytests covering BOM rollup, FOV/DORI
  math, alignment math, project-file round-trips, every Command class's
  do/undo cycle, malformed-input tolerance, and the pre-export scale
  guard.

### Roadmap

- **v1.1.0** — Auto-detected wall opening (re-attempt routing once the
  wall geometry is reliable); per-room reports; per-camera FOV defaults
  saved with the symbol type.
- **v1.2.0** — Cloud sync; per-installer field app for checkoff & photos.

## [1.0.0] - 2026-05-07

### Added

- **`cwt/` package** for pure-logic modules:
  - `cwt.models` — TypedDicts for Device / WireRoute / SymbolType / Floor / Riser
  - `cwt.bom` — cable catalog defaults, BOM rollup, CSV writer
  - `cwt.fov` — IEC 62676-4 DORI math (Detection / Observation / Recognition / Identification)
  - `cwt.lenses` — sensor + lens + resolution + install-preset databases
  - `cwt.routing` — soft-A* on a downsampled obstacle grid (8-direction, diagonal-penalized, wall-penalty for soft mode)
  - `cwt.multifloor` — project file v2 schema, anchor-point alignment math, v1→v2 migration
  - `cwt.export` — multi-page PDF report (reportlab) + CSV bundle (BOM, devices, wires)
- **`tests/`** — 37 pytest tests covering BOM rollup, FOV/DORI math, A*
  pathfinding, alignment math, project file round-trips
- **Cable types + BOM**:
  - Default catalog (Cat6, Cat5e, RG59, OM4, Speaker, Alarm) with cost/ft
  - 4th side-panel tab with sortable BOM table, waste multiplier, tax,
    currency, grand-total breakdown
  - Cable Types editor dialog (add/edit/delete cables)
  - Per-route `cable_type` override; falls back to symbol-type cable;
    falls back to category default
- **Camera FOV / DORI overlays**:
  - Per-camera lens / sensor / resolution / mount-height / tilt / azimuth
  - Live DORI distances (D/O/R/I) computed from IEC 62676-4 thresholds
  - Semi-transparent cones rendered on the map (View menu toggle)
  - "Show DORI rings" sub-toggle (off → only outermost detection cone)
  - Quick presets: Lobby, Hallway, Reception desk, Parking, Loading dock,
    Long corridor, Server room
  - Right-click camera → Edit FOV…
- **A\* auto-routing** *(removed in unreleased — see top of changelog)*:
  - Wall detection from background-image threshold (lazy-built obstacle grid)
  - Soft-walls A* (walls penalized 8× rather than impassable — matches
    real cable behavior through ceiling/conduit penetrations)
  - 8-direction movement with diagonal penalty so paths prefer orthogonal
  - Auto-route mode (segmented control + `A` shortcut + right-click
    "Auto-route from here…")
  - Path simplification collapses colinear runs to ~5–20 waypoints
- **Multi-floor + anchor-point alignment**:
  - Project file format upgraded to v2 (auto-migrates v1 single-floor)
  - Floors menu: Add Floor, switch active floor, alignment dialog,
    floor manager
  - Anchor-point alignment: click 2 corresponding points on each floor,
    similarity transform (translation + rotation + uniform scale)
    computed and stored
  - Manual nudge dialog for fine-tuning alignment
  - Risers (cross-floor cables) included in BOM rollup
- **Export**:
  - File → Export → BOM (CSV) — single CSV
  - File → Export → All CSVs — bundle of 3 (BOM, devices, wires)
  - File → Export → Project PDF Report — multi-page with cover, BOM
    table, riser list, per-device listing
- **Right-click context menu** on canvas:
  - For cameras: Edit FOV, toggle FOV cone, auto-route from, delete
  - For empty canvas: fit, toggle device IDs, toggle FOV
- **`A` keyboard shortcut** for auto-route mode

### Changed

- Project file format is now v2 by default (single-floor still works
  identically; v1 files auto-upgrade on first save)
- Wire routes can carry an optional `cable_type` field (preserved across
  save/load)
- Symbol types can declare a default `cable_type` (used by all instances)

## [0.2.0] - 2026-05-07

### Added

## [0.2.0] - 2026-05-07

### Added

- **Sun Valley dark theme** via optional `sv-ttk` dependency (graceful fallback
  if missing).
- **`Mode` enum** replaces the four mutually-exclusive booleans
  (`move_mode`, `wire_mode`, `calibration_mode`, `placing_device`) — single
  source of truth for the active mode.
- **Segmented mode bar** in the toolbar (Move / Wire / Calibrate radios) with
  cursor changes, status-bar feedback, and `M` / `W` / `C` keyboard shortcuts.
- **Icon toolbar** with grouped sections: file ops, undo/redo, scale + zoom,
  mode segment, units, scale state, total wire readout. Tooltips on every
  button.
- **3-tab side panel**: Devices / Wires / Symbols, each with a per-tab search
  field (`Ctrl+F` focuses the active one).
- **Universal undo/redo command stack** (`Ctrl+Z` / `Ctrl+Y`) covering device
  moves, add/delete device, add/delete wire, clear-all-wires, edit symbol,
  delete symbol, and calibration. History capped at 100.
- **Real error handling**: file I/O wrapped in try/except with user-friendly
  dialogs; errors written to `~/.camera_wire_tool.log` for diagnostics.
- **Keyboard shortcuts**: `M`/`W`/`C` modes, `Ctrl+Z`/`Y`, `Delete`, `Esc`,
  `Home` (fit), `+`/`-` (zoom), `[`/`]` (cycle devices), Space-hold (temporary
  pan), `Ctrl+F` (search), `Ctrl+O`/`S`/`Shift+S`. Help → Keyboard Shortcuts.
- **Constants dataclass** (`C`): every former magic number named — debounce,
  viewport cap, hit-test margin, zoom factor/limits, legend row height, font
  clamps, history limit, etc.
- **Breadcrumb status bar** at window bottom showing mode + selected device +
  cursor coordinates with live real-world distance from origin (when scale
  set).
- **Dirty-state tracking**: window title shows `*` when unsaved; close prompts
  to save.
- **Recent Files** menu (last 6 files; persisted to
  `~/.camera_wire_tool_recent.json`).
- **Wire reassignment** dialog: change a wire's device association without
  redrawing it.
- **Toggle device IDs** on the map (View menu).
- **About** + **Keyboard Shortcuts** help dialogs.

### Changed

- **Repo organization**: project data moved to `data/` (with original PDF in
  `data/source/`); the simpler `measure_tool.py` moved to `old/` since
  `camera_wire_tool.py` supersedes it. Each new folder has a README explaining
  contents.
- `main()` now discovers `.drawio` files in `data/` first, then the script
  directory.
- `parse_drawio` raises a typed `RuntimeError` with context on bad XML rather
  than crashing the interpreter; corrupt project files no longer fail
  silently.
- Calibration dialog gets a Cancel button and `Esc` binding.
- Symbol-editor dialog has `Apply` + `Cancel` + Enter/Esc bindings.

## [0.1.0] - 2026-05-07

### Added

- Initial git repo from existing `camera_wire_tool.py` + `measure_tool.py`.
- `PLAYBOOK.md` — complete architecture, file format, and design reference.
- `Floor1_Security_Measurements.drawio` and companion `.wiremap.json`.
- Project boilerplate: `.gitignore`, `README.md`, `LICENSE` (MIT),
  `pyproject.toml`, `requirements.txt`, `requirements-dev.txt`.

### Removed

- `measure_tool_original.py` (byte-identical duplicate of `measure_tool.py`;
  use git history for backups instead).
