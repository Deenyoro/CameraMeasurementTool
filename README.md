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

The tool auto-finds the `.drawio` file in the current directory. If multiple
exist, it shows a picker. If a companion `.wiremap.json` exists, it auto-loads
wire routes and scale.

For the simpler PNG-only measurement tool:

```
python measure_tool.py
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

## Files

| File | Purpose |
|------|---------|
| `camera_wire_tool.py` | Main app |
| `measure_tool.py` | Standalone PNG measurement tool |
| `PLAYBOOK.md` | Complete architecture + format docs |
| `Floor1_Security_Measurements.drawio` | Sample floor plan |
| `Floor1_Security_Measurements.wiremap.json` | Sample project state |
| `schematic.png` | High-res raster of the source PDF |
| `A111B-...Rev.6.pdf` | Original architect drawing |

## Development

```
pip install -r requirements-dev.txt   # adds pytest, ruff, mypy
pytest tests/
ruff check .
mypy cwt/
```

## License

MIT — see `LICENSE`.
