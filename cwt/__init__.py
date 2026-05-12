"""cwt — Camera Wire Tool support package.

Pure-logic modules with no UI dependencies. Imported by the orchestrator
script `camera_wire_tool.py`.

Modules:
    models      — TypedDicts and small dataclasses used everywhere
    bom         — Cable types + BOM/cost rollup + atomic CSV writer
    fov         — Camera FOV / DORI cone math (IEC 62676-4)
    lenses      — Static database of common lenses + sensor sizes
    multifloor  — Multi-floor project model + anchor-point alignment math
    export      — PDF + CSV-bundle emitters (all atomic)
    routing     — A* obstacle-grid pathfinding (dormant — auto-route was
                  removed from the UI but the module is kept for tests
                  and a future rework)
"""

__version__ = "1.0.0"
