# Camera Wire Tool

A Tkinter desktop tool for security-camera installers: load a draw.io floor plan,
place devices, draw multi-point wire routes, calibrate real-world scale, and
estimate total cable per device + project-wide BOM.

Built for the **a commercial building** project (Sheet A111B,
First Floor) but works on any draw.io plan.

## What it does today

- Parses a `.drawio` XML floor plan (devices, legend, embedded background image)
- Lets you drag devices, draw multi-point wire routes between them
- Calibrates pixel-to-real-world scale from a known dimension
- Saves a companion `.wiremap.json` file alongside the `.drawio`
- Auto-regenerates the legend from your symbol-type set
- Estimates total wire per device, with running total in the side panel

## What it will do (v1.0 roadmap)

See `PLAYBOOK.md` for the architecture and `CHANGELOG.md` for the path forward:

- Cable-type assignment (Cat6 / Coax / Fiber) and BOM with per-foot cost
- Camera FOV / DORI overlays (industry-standard coverage zones)
- A* auto-routing between two devices (avoids walls, snaps orthogonal)
- Multi-floor projects with cross-floor anchor-point alignment + risers
- PDF / CSV export for installers and procurement
- Universal undo/redo, dark theme, dirty-state tracking, tooltips everywhere

## Install

```
pip install -r requirements.txt
```

Python 3.10+ recommended.

## Run

```
python camera_wire_tool.py
```

The tool auto-finds the `.drawio` file in `data/` (then the repo root if
nothing's there). If multiple files are present, it shows a picker. If a
companion `.wiremap.json` exists alongside the `.drawio`, it auto-loads wire
routes and scale.

For the simpler PNG-only measurement tool (kept for quick one-off measurements):

```
python old/measure_tool.py
```

## Controls (cheat sheet)

| Action | Control |
|--------|---------|
| Zoom | Mouse wheel (toward cursor) |
| Pan | Middle-click drag, or Ctrl + left-drag |
| Select / drag device | Left-click (Move Mode) |
| Place wire point | Left-click (Wire Mode) |
| Finish wire route | Right-click |
| Cancel current action | Escape, or right-click with <2 points |
| Delete selected wire | Delete key |
| Fit to view | Home key |
| Save | Ctrl+S |

Full reference in `PLAYBOOK.md`.

## Repo layout

```
CameraMeasurementTool/
├── camera_wire_tool.py             # main app
├── PLAYBOOK.md                     # complete architecture + format docs
├── README.md / CHANGELOG.md / LICENSE
├── pyproject.toml / requirements*.txt
├── data/                           # active project data
│   ├── Floor1_Security_Measurements.drawio
│   ├── Floor1_Security_Measurements.wiremap.json
│   ├── schematic.png
│   └── source/                     # original assets (PDF, etc.)
│       └── A111B-…Rev.6.pdf
└── old/                            # superseded files kept for reference
    └── measure_tool.py             # simpler PNG-only measurement tool
```

Future work (per `CHANGELOG.md` roadmap): a `cwt/` Python package for the
modular refactor, plus `tests/` for the pytest suite.

## Development

```
pip install -r requirements-dev.txt   # adds pytest, ruff, mypy
pytest tests/
ruff check .
mypy cwt/
```

## License

MIT — see `LICENSE`.
