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
- **Manual wire routing** — click points to draw a wire; Shift snaps to
  0/45/90° from the previous point; cursor snaps to nearby device centers
  and wire endpoints automatically. Drag any waypoint to reshape; drag
  the source endpoint onto another device to reassign.
- **Multi-floor + anchor-point alignment** — multiple floor plans in one
  project; pick 2 reference points per floor and the system computes the
  alignment transform automatically.
- **Map annotations** — drop styled text labels (room names, zone callouts)
  on the floor plan. Persist per-floor, fully undoable.
- **PDF + CSV export** — multi-page PDF report (cover, BOM, per-device),
  3-CSV bundle (BOM + devices + wires) for procurement & installer hand-off.

Plus everything from v0.2: dark Sun Valley theme, segmented mode bar, 4-tab
side panel (Devices / Wires / Symbols / BOM) with search, universal
undo/redo with keystroke coalescing, breadcrumb status bar, recent files,
dirty-state tracking aware of undo-to-saved, atomic file writes, auto-save
with crash recovery, tooltips everywhere, arrow-key nudge, Ctrl+D
duplicate, Ctrl+U jump-to-unwired-camera, F11 fullscreen.

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
├── camera_wire_tool.py             # main app (Tk UI + command stack)
├── cwt/                            # pure-logic modules (no UI deps)
│   ├── bom.py                      # cable catalog + BOM rollup + CSV writer
│   ├── export.py                   # PDF + CSV bundle writers
│   ├── fov.py                      # IEC 62676-4 DORI cone math
│   ├── lenses.py                   # sensor + lens + install presets
│   ├── multifloor.py               # v2 project file + anchor alignment
│   ├── models.py                   # TypedDicts: Device / WireRoute / SymbolType
│   └── routing.py                  # soft-A* (dormant — auto-route was removed)
├── tests/                          # pytest suite (136 tests, run with `pytest`)
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

## Development

```
pip install -r requirements-dev.txt   # adds pytest, ruff, mypy
pytest tests/
ruff check .
mypy cwt/
```

## License

MIT — see `LICENSE`.
