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


def test_astar_fully_blocked_with_strict_walls():
    """In `block_walls=True` mode, a complete wall blocks the path."""
    grid = np.zeros((5, 5), dtype=bool)
    grid[:, 2] = True   # vertical wall through column 2
    ctx = make_ctx(grid)
    assert grid[:, 2].all()
    path = routing.astar(ctx, (0, 0), (4, 4), block_walls=True)
    assert path is None


def test_astar_soft_walls_finds_path_through_clutter():
    """Default soft-walls mode penalizes walls but always finds a path."""
    grid = np.zeros((10, 10), dtype=bool)
    grid[:, 5] = True
    ctx = make_ctx(grid)
    path = routing.astar(ctx, (0, 0), (9, 9))
    assert path is not None
    assert path[0] == (0, 0)
    assert path[-1] == (9, 9)


def test_astar_routes_around_wall_strict():
    """In strict mode (block_walls=True), A* must detour around walls."""
    grid = np.zeros((10, 10), dtype=bool)
    grid[:9, 5] = True   # wall with a gap at y=9
    ctx = make_ctx(grid)
    path = routing.astar(ctx, (0, 0), (9, 0), block_walls=True)
    assert path is not None
    ys = [c[1] for c in path]
    assert max(ys) >= 9


def test_astar_soft_penetrates_thin_wall():
    """A 1-cell wall is cheap to cross — soft A* takes the direct path."""
    grid = np.zeros((20, 20), dtype=bool)
    grid[:, 10] = True   # 1-cell wall, no corridor
    ctx = make_ctx(grid)
    path = routing.astar(ctx, (5, 5), (15, 5))
    assert path is not None
    assert path[-1] == (15, 5)


def test_astar_soft_detours_around_thick_wall():
    """A thick wall + a clear corridor — detour through the corridor is cheaper."""
    grid = np.zeros((30, 30), dtype=bool)
    # Wall 5 cells thick, with a clear gap at the bottom
    grid[:25, 10:15] = True
    ctx = make_ctx(grid)
    path = routing.astar(ctx, (5, 5), (20, 5))
    assert path is not None
    ys = [c[1] for c in path]
    # Crossing 5 wall cells = 5*8 = 40 extra penalty; detour ~30 cells
    # at 1x is cheaper, so we expect ys to dip down to find the corridor.
    assert max(ys) >= 24


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
