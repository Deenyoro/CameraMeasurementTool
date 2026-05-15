"""Tests for cwt.routing — A* on synthetic obstacle grids."""

from __future__ import annotations

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cwt import routing


def make_ctx(grid: np.ndarray, cell_px: int = 10) -> routing.RoutingContext:
    return routing.RoutingContext(grid=grid, cell_px=cell_px)


def test_astar_open_grid():
    grid = np.zeros((10, 10), dtype=bool)
    ctx = make_ctx(grid)
    path = routing.astar(ctx, (0, 0), (9, 9))
    assert path is not None
    assert path[0] == (0, 0)
    assert path[-1] == (9, 9)


def test_astar_strict_blocks_full_wall():
    """Default mode (block_walls=True): a complete wall far from start/goal blocks A*."""
    grid = np.zeros((20, 20), dtype=bool)
    grid[:, 10] = True   # vertical wall, far from both endpoints
    ctx = make_ctx(grid)
    # Start at (0,0), goal at (19,0). Tunnel radius=2 around each is too
    # small to reach the wall (10 cells away), so A* must fail.
    path = routing.astar(ctx, (0, 0), (19, 0))
    assert path is None


def test_astar_strict_with_corridor():
    """Strict mode finds a path through a corridor."""
    grid = np.zeros((20, 20), dtype=bool)
    grid[:18, 10] = True   # wall with a corridor at rows 18-19
    ctx = make_ctx(grid)
    path = routing.astar(ctx, (0, 0), (19, 0))
    assert path is not None
    ys = [c[1] for c in path]
    assert max(ys) >= 17    # forced through the corridor


def test_astar_strict_tunnels_around_endpoints():
    """If the start cell IS a wall (camera mounted on wall), tunneling lets A* leave."""
    grid = np.zeros((20, 20), dtype=bool)
    grid[5, 5] = True   # start cell is a wall
    grid[15, 15] = True  # goal cell is a wall
    ctx = make_ctx(grid)
    path = routing.astar(ctx, (5, 5), (15, 15))
    assert path is not None


def test_astar_soft_finds_path_through_thin_wall():
    """Soft-walls mode crosses a 1-cell wall when the cost beats detour."""
    grid = np.zeros((20, 20), dtype=bool)
    grid[:, 10] = True
    ctx = make_ctx(grid)
    path = routing.astar(ctx, (5, 5), (15, 5), block_walls=False)
    assert path is not None
    assert path[-1] == (15, 5)


def test_route_between_default_strict_no_path():
    """route_between defaults to strict — fully blocked = None."""
    grid = np.zeros((30, 30), dtype=bool)
    grid[:, 15] = True
    ctx = make_ctx(grid)
    path = routing.route_between(ctx, (50, 50), (250, 50))
    assert path is None


def test_route_between_soft_mode_finds_path():
    """In soft mode the same scenario produces a path."""
    grid = np.zeros((30, 30), dtype=bool)
    grid[:, 15] = True
    ctx = make_ctx(grid)
    path = routing.route_between(ctx, (50, 50), (250, 50), block_walls=False)
    assert path is not None


def test_simplify_collinear_collapses_straight_runs():
    cells = [(0, 0), (1, 0), (2, 0), (3, 0), (3, 1), (3, 2)]
    out = routing.simplify_collinear(cells)
    # Should keep: (0,0), (3,0) [direction change], (3,2)
    assert out == [(0, 0), (3, 0), (3, 2)]


def test_simplify_short_path_unchanged():
    assert routing.simplify_collinear([(0, 0), (1, 0)]) == [(0, 0), (1, 0)]
    assert routing.simplify_collinear([(0, 0)]) == [(0, 0)]


def test_route_between_returns_image_coords():
    grid = np.zeros((20, 20), dtype=bool)
    ctx = make_ctx(grid, cell_px=10)
    # Start at image-coord (15, 15) → cell (1, 1)
    # Goal at image-coord (185, 185) → cell (18, 18)
    waypoints = routing.route_between(ctx, (15, 15), (185, 185))
    assert waypoints is not None
    assert waypoints[0] == (15, 15)
    assert waypoints[-1] == (185, 185)
    # On open grid, simplification gives a 3-point path: start, corner-or-straight, end
    assert 2 <= len(waypoints) <= 5


def test_dilate_expands_walls():
    g = np.zeros((5, 5), dtype=bool)
    g[2, 2] = True
    out = routing._dilate(g, iters=1)
    # 8-neighborhood: all 9 cells of the 3x3 around (2,2) should be True
    assert out[1:4, 1:4].all()


def test_erode_pixels_removes_isolated_dots():
    """A 1×1 dark blob — like a dimension callout dot — gets erased."""
    walls = np.zeros((10, 10), dtype=bool)
    walls[5, 5] = True
    out = routing._erode_pixels(walls)
    assert not out.any()


def test_erode_pixels_keeps_thick_lines():
    """A 5-cell-thick wall survives erosion."""
    walls = np.zeros((20, 20), dtype=bool)
    walls[5:10, 1:19] = True   # horizontal block 5 cells thick
    out = routing._erode_pixels(walls)
    # Inner cells (with 8 wall neighbors) survive
    assert out[6:9, 2:18].any()
