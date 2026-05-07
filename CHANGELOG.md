# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Roadmap

- **v0.2.0** — Polish pass: dark theme, mode enum + segmented control, icon
  toolbar, 3-tab side panel with search, tooltips, universal undo/redo, real
  error handling, full keyboard shortcuts, named constants, breadcrumb status
  bar, dirty-state tracking, recent files.
- **v0.3.0** — Modular refactor (`cwt/` package) + pytest suite + TypedDicts
  for `Device` / `WireRoute` / `SymbolType`.
- **v1.0.0** — Killer features: cable types + BOM, FOV/DORI overlays, A*
  auto-routing, multi-floor with anchor-point alignment + risers.
- **v1.1.0** — PDF/CSV export, settings dialog, About dialog.

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
