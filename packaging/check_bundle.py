"""Fail if a frozen CameraWireTool build is missing a required package.

    python packaging/check_bundle.py dist/CameraWireTool.exe   # Windows
    python packaging/check_bundle.py dist/CameraWireTool       # Linux

Reads the PyInstaller single-file archive with PyInstaller's own reader
(run it with the same Python/PyInstaller that built the binary) and checks
that the Sun Valley theme, reportlab (PDF export) and the Tk image bridge
are really inside. A build that silently drops one of them still starts,
but loses its theme or PDF export, which no smoke test of "does the exe
exist" would catch.

The optional CAD importers (ezdxf + matplotlib for DXF, PyMuPDF for SVG)
are only reported, never required: the spec deliberately excludes
PyMuPDF and the build does not install the others.
"""

from __future__ import annotations

import sys

# Python modules that must be in the embedded PYZ.
REQUIRED_MODULES = [
    "sv_ttk",
    "reportlab.pdfgen.canvas",
    "reportlab.platypus",
    "PIL.ImageTk",
    "PIL._tkinter_finder",
]
# Data files that must be in the outer archive.
REQUIRED_DATA = [
    "sv_ttk/sv.tcl",
    "sv_ttk/theme/dark.tcl",
    "reportlab/fonts/Vera.ttf",
]
OPTIONAL_MODULES = {
    "ezdxf": "DXF import",
    "matplotlib": "DXF import (render backend)",
    "fitz": "SVG import (PyMuPDF)",
}


def check(names: set[str] | list[str], modules: set[str] | list[str]) -> list[str]:
    """Return the required entries missing from an archive listing.

    `names` are the outer CArchive entry names, `modules` the dotted module
    names in the embedded PYZ. PyInstaller stores entry names with the host
    OS separator (backslashes in a Windows build, as the bootloader there
    needs them), so separators are normalised to "/" before comparing.
    """
    norm = {n.replace("\\", "/") for n in names}
    mods = set(modules)
    missing = [m for m in REQUIRED_MODULES if m not in mods]
    missing += [d for d in REQUIRED_DATA if d not in norm]
    return missing


def read_archive(path: str) -> tuple[set[str], set[str]]:
    """(outer entry names, PYZ module names) of a single-file PyInstaller build."""
    # Imported here so check() stays usable (and testable) without PyInstaller.
    from PyInstaller.archive.readers import CArchiveReader

    outer = CArchiveReader(path)
    names = set(outer.toc)
    pyz_name = next((n for n in names if n.endswith(".pyz")), None)
    if pyz_name is None:
        raise SystemExit(f"FAIL: no PYZ archive inside {path}")
    return names, set(outer.open_embedded_archive(pyz_name).toc)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    path = argv[1]
    names, modules = read_archive(path)
    missing = check(names, modules)
    for m, what in OPTIONAL_MODULES.items():
        state = "bundled" if m in modules else "not bundled"
        print(f"optional  {m:<12} {state:<12} ({what})")
    for m in REQUIRED_MODULES + REQUIRED_DATA:
        print(f"required  {m:<28} {'MISSING' if m in missing else 'ok'}")
    if missing:
        print(f"FAIL: {path} is missing: {', '.join(missing)}")
        return 1
    print(f"OK: {path} contains every required package")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
