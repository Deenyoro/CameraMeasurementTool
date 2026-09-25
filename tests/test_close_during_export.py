"""Closing the window while an export runs asks first and stops the
worker cleanly, instead of killing it silently mid-export."""

from __future__ import annotations

import threading
import time

import camera_wire_tool as cwt
from tests.gui_support import pump


def _start_slow_job(app):
    started = threading.Event()
    seen_cancel = {"v": False}

    def work(report, is_cancelled):
        started.set()
        for _ in range(200):                # up to ~10 s
            if is_cancelled():
                seen_cancel["v"] = True
                return {"cancelled": True}
            time.sleep(0.05)
        return {"path": "x"}

    app._run_in_background(work, lambda _r: None)
    assert started.wait(5)
    return seen_cancel


def test_close_during_export_can_be_declined(gui_app, monkeypatch):
    app = gui_app
    quits = []
    monkeypatch.setattr(app.root, "quit", lambda: quits.append(1))
    asked = []
    monkeypatch.setattr(cwt.messagebox, "askyesno",
                        lambda *a, **k: asked.append(a) or False)
    seen = _start_slow_job(app)
    assert app._background_work_running()
    app._on_close()
    assert asked and asked[0][0] == "Still working"
    assert quits == []                       # still open
    assert app._background_thread.is_alive()  # export keeps going
    app._stop_background_work()
    assert seen["v"] is True


def test_close_during_export_stops_the_worker_then_quits(gui_app, monkeypatch):
    app = gui_app
    quits = []
    monkeypatch.setattr(app.root, "quit", lambda: quits.append(1))
    monkeypatch.setattr(cwt.messagebox, "askyesno", lambda *a, **k: True)
    seen = _start_slow_job(app)
    t0 = time.monotonic()
    app._on_close()
    # The Tk thread is not blocked while the worker stops (no "Not
    # Responding"); the window closes once it has.
    assert time.monotonic() - t0 < 0.5
    assert quits == []
    ticks = 0
    while not quits and time.monotonic() - t0 < 5:
        pump(app.root, 1)
        ticks += 1
        time.sleep(0.02)
    assert seen["v"] is True                 # cancelled, not killed
    assert not app._background_thread.is_alive()
    assert quits == [1]
    assert ticks >= 1


def test_second_close_while_stopping_does_not_ask_again(gui_app, monkeypatch):
    app = gui_app
    monkeypatch.setattr(app.root, "quit", lambda: None)
    asked = []
    monkeypatch.setattr(cwt.messagebox, "askyesno",
                        lambda *a, **k: asked.append(a) or True)
    _start_slow_job(app)
    app._on_close()
    app._on_close()
    assert len(asked) == 1
    app._background_thread.join(5)
    pump(app.root)


def test_close_without_export_does_not_ask(gui_app, monkeypatch):
    app = gui_app
    quits = []
    monkeypatch.setattr(app.root, "quit", lambda: quits.append(1))
    asked = []
    monkeypatch.setattr(cwt.messagebox, "askyesno",
                        lambda *a, **k: asked.append(a) or True)
    app._on_close()
    assert asked == []
    assert quits == [1]


def test_window_is_blocked_while_the_export_stops(gui_app, monkeypatch):
    """While closing waits for an export to stop, a modal "Closing"
    window keeps the user from starting new work that would then be cut
    off (another export, File > Open); it goes away when the app
    closes."""
    app = gui_app
    quits = []
    monkeypatch.setattr(app.root, "quit", lambda: quits.append(1))
    monkeypatch.setattr(cwt.messagebox, "askyesno", lambda *a, **k: True)
    _start_slow_job(app)
    app._on_close()
    pump(app.root, 2)
    tops = [w for w in app.root.winfo_children()
            if isinstance(w, cwt.tk.Toplevel) and w.winfo_exists()]
    assert tops and tops[-1].title() == "Closing"
    assert app.root.grab_current() is tops[-1]
    end = time.monotonic() + 5
    while not quits and time.monotonic() < end:
        pump(app.root, 1)
        time.sleep(0.02)
    assert quits == [1]
    assert not tops[-1].winfo_exists()
