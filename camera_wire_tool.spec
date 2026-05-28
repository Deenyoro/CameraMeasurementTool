# PyInstaller build spec for the Camera Wire Tool.
#
#   Build:   pyinstaller --noconfirm camera_wire_tool.spec
#   Output:  dist/CameraWireTool.exe   (single-file, windowed)
#
# The app is a Tkinter GUI; `console=False` keeps a command window from
# popping up behind it. sv_ttk's theme files are collected explicitly so
# the Sun Valley look travels with the exe instead of silently falling
# back to default ttk.

from PyInstaller.utils.hooks import collect_all

datas = []
binaries = []
hiddenimports = []

# Bundle the Sun Valley theme's .tcl data files and reportlab (PDF export
# needs its bundled fonts/data). Best-effort per package — the app still
# launches if one is absent (sv_ttk falls back to ttk; PDF export just
# reports "reportlab unavailable").
for pkg in ("sv_ttk", "reportlab"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        pass

a = Analysis(
    ["camera_wire_tool.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Not imported by the app — excluding trims a lot off the exe size.
    # (reportlab is intentionally NOT excluded — PDF export needs it.)
    excludes=["numpy", "pymupdf", "fitz", "pytest", "mypy", "ruff"],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="CameraWireTool",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
