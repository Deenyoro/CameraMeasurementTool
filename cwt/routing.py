"""A* auto-routing on a downsampled obstacle grid.

Wall detection: threshold the floor-plan PNG. Dark pixels = obstacle.
The grid is downsampled to `cell_px` pixels per cell, so a 4620×3300
floor-plan with cell_px=20 becomes ~232×165 cells (~38k nodes), which
A* solves in <100ms even in pure Python.

Movement: 8-directional. Diagonals are penalized 1.5× (so paths prefer
orthogonal moves, matching how cable runs in real conduit/ceilings).
Existing wires can be supplied as an "attract" mask to encourage cable
bundling.

Output: a list of (image_x, image_y) waypoints. After A* finds the raw
path, we collapse colinear runs so a 50-cell straight segment becomes
just two endpoints — typical result is 3-8 waypoints between two devices.
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass

import numpy as np
from PIL import Image


def build_obstacle_grid(
    bg_image: Image.Image,
    *,
    cell_px: int = 20,
    threshold: int = 120,
    dilate_iters: int = 1,
) -> np.ndarray:
    """Build a 2D bool grid where True = obstacle.

    Pipeline:
        1. Convert image to grayscale
        2. Threshold dark pixels = candidate walls
        3. Downsample by cell_px (a cell is wall if ANY pixel is wall)
        4. Optional binary dilation to thicken walls (gives clearance)
    """
    gray = np.asarray(bg_image.convert("L"))
    walls = gray < threshold  # True where dark

    h, w = walls.shape
    cells_h = h // cell_px
    cells_w = w // cell_px
    if cells_h == 0 or cells_w == 0:
        return np.zeros((1, 1), dtype=bool)

    # Trim to multiple of cell_px and reshape
    walls = walls[: cells_h * cell_px, : cells_w * cell_px]
    blocks = walls.reshape(cells_h, cell_px, cells_w, cell_px)
    grid = blocks.any(axis=(1, 3))  # cell is wall if any source pixel is wall

    if dilate_iters > 0:
        grid = _dilate(grid, dilate_iters)

    return grid


def _dilate(grid: np.ndarray, iters: int) -> np.ndarray:
    """Simple 8-neighborhood binary dilation."""
    out = grid.copy()
    for _ in range(iters):
        nxt = out.copy()
        nxt[1:, :]  |= out[:-1, :]
        nxt[:-1, :] |= out[1:, :]
        nxt[:, 1:]  |= out[:, :-1]
        nxt[:, :-1] |= out[:, 1:]
        nxt[1:, 1:]   |= out[:-1, :-1]
        nxt[1:, :-1]  |= out[:-1, 1:]
        nxt[:-1, 1:]  |= out[1:, :-1]
        nxt[:-1, :-1] |= out[1:, 1:]
        out = nxt
    return out


@dataclass
class RoutingContext:
    grid: np.ndarray         # bool[cells_h, cells_w] — True = obstacle
    cell_px: int

    @property
    def cells_h(self) -> int: return self.grid.shape[0]
    @property
    def cells_w(self) -> int: return self.grid.shape[1]

    def img_to_cell(self, ix: float, iy: float) -> tuple[int, int]:
        cx = int(ix // self.cell_px)
        cy = int(iy // self.cell_px)
        return max(0, min(self.cells_w - 1, cx)), max(0, min(self.cells_h - 1, cy))

    def cell_to_img(self, cx: int, cy: int) -> tuple[float, float]:
        # center of cell
        return (cx + 0.5) * self.cell_px, (cy + 0.5) * self.cell_px

    def is_blocked(self, cx: int, cy: int) -> bool:
        if not (0 <= cx < self.cells_w and 0 <= cy < self.cells_h):
            return True
        return bool(self.grid[cy, cx])


# Move offsets (dx, dy, cost_multiplier). Diagonals penalized.
_MOVES: list[tuple[int, int, float]] = [
    (1, 0, 1.0), (-1, 0, 1.0), (0, 1, 1.0), (0, -1, 1.0),
    (1, 1, 1.5), (1, -1, 1.5), (-1, 1, 1.5), (-1, -1, 1.5),
]


def astar(
    ctx: RoutingContext,
    start_cell: tuple[int, int],
    goal_cell: tuple[int, int],
    *,
    max_iters: int = 500_000,
    wall_penalty: float = 8.0,
    block_walls: bool = False,
) -> list[tuple[int, int]] | None:
    """Soft-A* on the obstacle grid.

    By default walls are NOT impassable — they cost `wall_penalty` × extra
    (default 8×). This matches reality for cable routing: runs go through
    ceiling cavities, conduit, and wall penetrations all the time, so a
    "wall" is more of a routing preference than a hard barrier. The result
    is A* always finds a path, prefers corridors strongly, but won't fail
    just because the building has lots of architectural clutter in the
    floor-plan PNG.

    If `block_walls=True`, walls become impassable (classic A*) and the
    function may return None when no clear corridor exists.

    Returns a list of cells from start → goal (inclusive), or None.
    """
    if start_cell == goal_cell:
        return [start_cell]

    gx, gy = goal_cell

    open_heap: list[tuple[float, int, tuple[int, int]]] = []
    counter = 0
    heapq.heappush(open_heap, (0.0, counter, start_cell))
    came_from: dict[tuple[int, int], tuple[int, int]] = {}
    g_score: dict[tuple[int, int], float] = {start_cell: 0.0}

    iters = 0
    while open_heap:
        iters += 1
        if iters > max_iters:
            return None
        _, _, current = heapq.heappop(open_heap)
        if current == goal_cell:
            return _reconstruct(came_from, current)

        cx, cy = current
        for dx, dy, cost in _MOVES:
            nx, ny = cx + dx, cy + dy
            if not (0 <= nx < ctx.cells_w and 0 <= ny < ctx.cells_h):
                continue
            is_wall = bool(ctx.grid[ny, nx])
            if is_wall and block_walls and (nx, ny) != goal_cell:
                continue
            penalty = (cost * wall_penalty) if is_wall else cost
            tentative = g_score[current] + penalty
            if tentative < g_score.get((nx, ny), float("inf")):
                came_from[(nx, ny)] = current
                g_score[(nx, ny)] = tentative
                h = _octile(nx, ny, gx, gy)
                counter += 1
                heapq.heappush(open_heap, (tentative + h, counter, (nx, ny)))

    return None


def _reconstruct(came_from, current):
    path = [current]
    while current in came_from:
        current = came_from[current]
        path.append(current)
    path.reverse()
    return path


def _octile(x1, y1, x2, y2) -> float:
    dx = abs(x1 - x2); dy = abs(y1 - y2)
    return (dx + dy) + (math.sqrt(2) - 2) * min(dx, dy)


# ---------------- Path simplification
def simplify_collinear(cells: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Drop intermediate points whose direction equals the prior step's direction."""
    if len(cells) <= 2:
        return list(cells)
    out: list[tuple[int, int]] = [cells[0]]
    for i in range(1, len(cells) - 1):
        px, py = cells[i - 1]
        cx, cy = cells[i]
        nx, ny = cells[i + 1]
        # direction vectors
        d1 = (cx - px, cy - py)
        d2 = (nx - cx, ny - cy)
        if d1 != d2:
            out.append(cells[i])
    out.append(cells[-1])
    return out


# ---------------- High-level: find route between two image-pixel points
def route_between(
    ctx: RoutingContext,
    start_img: tuple[float, float],
    goal_img: tuple[float, float],
    *,
    block_walls: bool = False,
) -> list[tuple[float, float]] | None:
    """Find an A* route between two image-pixel points.

    Returns the list of waypoints in IMAGE-PIXEL coords (not grid cells),
    starting exactly at `start_img` and ending exactly at `goal_img` with
    intermediate orthogonal waypoints from A*.
    """
    s = ctx.img_to_cell(*start_img)
    g = ctx.img_to_cell(*goal_img)
    cells = astar(ctx, s, g, block_walls=block_walls)
    if cells is None:
        return None
    cells = simplify_collinear(cells)
    waypoints = [start_img] + [ctx.cell_to_img(cx, cy) for cx, cy in cells] + [goal_img]
    # Drop duplicate start/end if they coincide with first/last cell center
    return _dedup_close(waypoints, eps=1.0)


def _erode_pixels(walls: np.ndarray) -> np.ndarray:
    """Remove isolated wall pixels (noise from dimension callouts, dots).

    A wall cell survives only if at least one 8-neighbor is also a wall.
    This strips 1×1 dark blobs while preserving contiguous walls/lines.
    """
    out = walls.copy()
    h, w = walls.shape
    neighbor_count = np.zeros_like(walls, dtype=int)
    for dy, dx in [(-1, -1), (-1, 0), (-1, 1),
                   (0, -1),           (0, 1),
                   (1, -1),  (1, 0),  (1, 1)]:
        shifted = np.zeros_like(walls)
        sr = slice(max(0, dy), h + min(0, dy) or None)
        sc = slice(max(0, dx), w + min(0, dx) or None)
        tr = slice(max(0, -dy), h + min(0, -dy) or None)
        tc = slice(max(0, -dx), w + min(0, -dx) or None)
        shifted[tr, tc] = walls[sr, sc]
        neighbor_count += shifted
    # Kill cells with zero wall neighbors
    out[neighbor_count == 0] = False
    return out


def _dedup_close(pts, eps=1.0):
    if not pts:
        return pts
    out = [pts[0]]
    for p in pts[1:]:
        if math.hypot(p[0] - out[-1][0], p[1] - out[-1][1]) > eps:
            out.append(p)
    return out
