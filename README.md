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

This installs Pillow, sv-ttk and reportlab (PDF report export). The DXF/SVG
importers are optional extras: `pip install ".[cad]"`.

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

## Downloads / Building

Release builds come from the GitLab pipeline (`.gitlab-ci.yml`) and appear
under **Deploy → Releases** of the GitLab project, with the files stored in
its **Package Registry** (generic package `CameraWireTool`, one version per
release):

| File | What it is |
|------|------------|
| `CameraWireTool-<ver>-windows-x64-setup.exe` | Windows installer (per-user by default, no admin; Start menu + optional desktop shortcut) |
| `CameraWireTool-<ver>-windows-x64-portable.zip` / `CameraWireTool.exe` | Portable Windows exe, no install; put it next to your floor plans and it opens them |
| `CameraWireTool-<ver>-linux-x86_64.AppImage` | Linux, any distro with glibc 2.35+ (Ubuntu 22.04+, Debian 12+, RHEL 9+): `chmod +x` and run |
| `CameraWireTool-<ver>-linux-x86_64.flatpak` | Linux Flatpak bundle: `flatpak install --user <file>` (pulls the freedesktop 25.08 runtime from Flathub) |

**Cutting a release:** first set the version in `cwt/__init__.py`
(`__version__`) and `pyproject.toml` to `X.Y.Z` and commit. The pipeline
fails if the tag and `cwt.__version__` differ, because the app shows that
version in its About screen and reports. Then push a tag `vX.Y.Z` (for
example `git tag v1.3.0 && git push origin v1.3.0`). The pipeline runs the
tests, builds every file above (Windows natively on the Windows runner, Linux
in containers) and publishes the release for that tag. To rebuild or
re-publish an existing tag, run the pipeline from the web UI (**Build →
Pipelines → Run pipeline**) **on the tag `vX.Y.Z` as the ref** with the
variable `RELEASE_VERSION=vX.Y.Z`. The release job refuses to publish if the
tag points at a different commit from the one the pipeline built. A web/API run without `RELEASE_VERSION`
only builds; download the files from the job artifacts (kept 30 days). The
pipeline does not run on ordinary pushes.

The existing GitHub Actions workflow (`.github/workflows/build-exe.yml`) is
unchanged and still builds `CameraWireTool.exe` on github.com.

**Building locally**

```
pip install -r requirements.txt -r packaging/requirements-build.txt
pyinstaller --noconfirm camera_wire_tool.spec     # dist/CameraWireTool(.exe)
python packaging/check_bundle.py dist/CameraWireTool.exe   # or dist/CameraWireTool on Linux
packaging/linux/build_appimage.sh 0.0.0-dev       # Linux: AppImage in packaging/linux/out/
```

The spec stops the build if sv-ttk or reportlab is missing, so a build can no
longer ship without the theme or PDF export. The AppImage script downloads a
pinned appimagetool and checks its SHA-256. The Windows installer is built
with Inno Setup from `installer/windows.iss`. Inno Setup's licence terms
changed with recent releases: check them before using the installer
commercially. The app icon comes from
`packaging/make_icon.py`.

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
│   ├── bundle.py                   # .zsmt revision bundle (zip backlog)
│   ├── export.py                   # PDF + CSV bundle writers
│   ├── fov.py                      # IEC 62676-4 DORI cone math
│   ├── import_cad.py               # DXF / SVG floor-plan import
│   ├── lenses.py                   # sensor + lens + install presets
│   ├── multifloor.py               # v2 project file + anchor alignment
│   ├── models.py                   # TypedDicts: Device / WireRoute / SymbolType
│   └── raster_import.py            # PNG/JPG floor-plan import helpers
├── tests/                          # pytest suite (417 tests, run with `pytest`)
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
