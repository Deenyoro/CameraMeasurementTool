"""cwt — Camera Wire Tool support package.

Pure-logic modules with no UI dependencies. Imported by the orchestrator
script `camera_wire_tool.py`.

Modules:
    models      — TypedDicts and small dataclasses used everywhere
    bom         — Cable types + BOM/cost rollup
    fov         — Camera FOV / DORI cone math
    lenses      — Static database of common lenses + sensor sizes
    routing     — A* obstacle-grid pathfinding
    multifloor  — Multi-floor project model + anchor-point alignment math
    export      — PDF and CSV emitters
"""

__version__ = "1.0.0"
