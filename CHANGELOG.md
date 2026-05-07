# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Roadmap

- **v0.3.0** — Modular refactor (`cwt/` package) + pytest suite + TypedDicts
  for `Device` / `WireRoute` / `SymbolType`.
- **v1.0.0** — Killer features: cable types + BOM, FOV/DORI overlays, A*
  auto-routing, multi-floor with anchor-point alignment + risers.
- **v1.1.0** — PDF/CSV export, settings dialog, About dialog.

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
