# Camera Wire Tool

A Tkinter desktop tool for security-camera installers: load a draw.io floor plan,
place devices, draw multi-point wire routes, calibrate real-world scale, and
estimate total cable per device + project-wide BOM.

Built for the **a commercial building** project (Sheet A111B,
First Floor) but works on any draw.io plan.

## v1.0 features

- Parses a `.drawio` XML floor plan (devices, legend, embedded background)
- Drag devices, draw multi-point wire routes, calibrate real-world scale
- Auto-regenerates the legend from your symbol-type set
- **Cable types + BOM** — per-symbol cable assignment (Cat6 / Cat5e / RG59 /
  fiber / speaker / alarm — editable catalog), running cost with waste & tax,
  CSV export
- **Camera FOV / DORI overlays** — per-camera lens / sensor / resolution /
  mount-height → IEC EN 62676-4 Detection/Observation/Recognition/Identification
  zones drawn on the map. Quick presets for Lobby, Hallway, Parking…
- **A\* auto-routing** — click two devices, system finds the optimal cable
  path. Soft-walls model (cable can cross walls through conduit/ceiling),
  diagonal-penalized so paths prefer orthogonal — matches real conduit runs.
- **Multi-floor + anchor-point alignment** — multiple floor plans in one
  project; pick 2 reference points per floor and the system computes the
  alignment transform automatically. Risers between floors roll into the BOM.
- **PDF + CSV export** — multi-page PDF report (cover, BOM, per-device, risers),
  3-CSV bundle (BOM + devices + wires) for procurement & installer hand-off.

Plus everything from v0.2: dark Sun Valley theme, segmented mode bar, 3-tab
side panel with search, universal undo/redo, breadcrumb status bar, recent
files, dirty-state tracking, tooltips everywhere, full keyboard shortcuts.

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
