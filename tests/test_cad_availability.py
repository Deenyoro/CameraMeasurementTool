"""The Windows .exe ships without the DXF/SVG importers. A copy that
cannot open those files does not offer them (start screen hint, Open and
Add Floor filters, File menu), and one opened anyway (Explorer, command
line) gets a plain-language explanation instead of a pip command.

Where the importers exist, a DXF/SVG opened from the start screen or the
command line renders off the Tk thread with a progress window and
Cancel, like File > Open in the main window."""

from __future__ import annotations

import sys
import threading
import time

import pytest
from PIL import Image

import camera_wire_tool as cwt
from cwt import import_cad
from tests.gui_support import pump


def _xml() -> str:
    return cwt.cad_mod.image_to_drawio_xml(Image.new("RGB", (40, 30), "white"))


def _button(top, text):
    stack = [top]
    while stack:
        w = stack.pop()
        if isinstance(w, cwt.ttk.Button) and w.cget("text") == text:
            return w
        stack.extend(w.winfo_children())
    raise LookupError(text)


def _toplevels(root):
    return [w for w in root.winfo_children()
            if isinstance(w, cwt.tk.Toplevel) and w.winfo_exists()]


# ------------------------------------------------ without the importers
@pytest.mark.usefixtures("no_cad_backends")
def test_nothing_offers_dxf_or_svg_without_importers():
    assert import_cad.available_exts() == ()
    assert cwt.cad_open_exts() == ()
    for filters in (cwt.open_filetypes(), cwt.floor_filetypes()):
        pats = " ".join(p for _lbl, p in filters)
        assert "dxf" not in pats and "svg" not in pats
        assert "*.drawio" in pats
    assert cwt.open_formats_text() == ".zsmt project or .drawio"


@pytest.mark.usefixtures("no_cad_backends")
def test_start_screen_hint_lists_only_what_opens(tk_root, tmp_path):
    start = cwt.StartScreen(tk_root, initial_dir=str(tmp_path))
    hints = [w.cget("text") for w in start.open_btn.master.winfo_children()
             if w is not start.open_btn]
    assert hints and ".drawio" in hints[0]
    assert ".dxf" not in hints[0] and ".svg" not in hints[0]


@pytest.mark.usefixtures("no_cad_backends")
def test_open_dialog_filters_without_importers(tk_root, tmp_path,
                                               monkeypatch):
    seen = {}
    monkeypatch.setattr(cwt.filedialog, "askopenfilename",
                        lambda **k: (seen.update(k), "")[1])
    start = cwt.StartScreen(tk_root, initial_dir=str(tmp_path))
    start.open_dialog()
    assert not any("dxf" in p for _lbl, p in seen["filetypes"])


@pytest.mark.usefixtures("no_cad_backends")
def test_file_menu_and_add_floor_without_importers(gui_app, monkeypatch):
    app = gui_app
    menubar = app.root.nametowidget(app.root.cget("menu"))
    file_menu = next(
        app.root.nametowidget(menubar.entrycget(i, "menu"))
        for i in range(menubar.index("end") + 1)
        if menubar.type(i) == "cascade"
        and menubar.entrycget(i, "label").replace("&", "") == "File")
    labels = [file_menu.entrycget(i, "label")
              for i in range(file_menu.index("end") + 1)
              if file_menu.type(i) == "command"]
    open_label = next(lbl for lbl in labels if lbl.startswith("Open…"))
    assert ".dxf" not in open_label and ".drawio" in open_label
    seen = {}
    monkeypatch.setattr(cwt.filedialog, "askopenfilename",
                        lambda **k: (seen.update(k), "")[1])
    app._add_floor_dialog()
    assert not any("dxf" in p for _lbl, p in seen["filetypes"])


def test_missing_importer_message_is_plain_language(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    msg = import_cad.missing_backend_message(".dxf")
    assert "can't open DXF drawings" in msg
    assert ".drawio" in msg and "PNG" in msg
    assert "pip" not in msg
    monkeypatch.setattr(sys, "frozen", False)
    assert "pip install ezdxf matplotlib" in (
        import_cad.missing_backend_message(".dxf"))
    assert "pip install pymupdf" in import_cad.missing_backend_message(".svg")


@pytest.mark.usefixtures("no_cad_backends")
@pytest.mark.parametrize("fn,ext", [("render_dxf", ".dxf"),
                                    ("render_svg", ".svg")])
def test_renderer_raises_the_plain_message(tmp_path, monkeypatch, fn, ext):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    with pytest.raises(import_cad.CadImportError) as ei:
        getattr(import_cad, fn)(str(tmp_path / f"x{ext}"))
    assert "can't open" in str(ei.value) and "pip" not in str(ei.value)


@pytest.mark.usefixtures("no_cad_backends")
def test_opening_a_dxf_anyway_explains_what_to_do(tk_root, tmp_path,
                                                  monkeypatch):
    """Double-clicking a .dxf in Explorer (or picking one with "All
    files") in the .exe: a clear message, no progress window, no file
    written, and the start screen stays."""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    errors = []
    monkeypatch.setattr(cwt.messagebox, "showerror",
                        lambda *a, **k: errors.append(a))
    rendered = []
    monkeypatch.setattr(cwt.cad_mod, "render_to_drawio_xml",
                        lambda src: rendered.append(src) or _xml())
    dxf = tmp_path / "Plan.dxf"
    dxf.write_text("0\nEOF\n")
    start = cwt.StartScreen(tk_root, initial_dir=str(tmp_path))
    assert start.open_path(str(dxf)) is False
    assert rendered == []                       # no worker, no progress
    assert _toplevels(tk_root) == []
    assert errors and "can't open DXF drawings" in errors[0][1]
    assert "pip" not in errors[0][1]
    assert start.frame.winfo_exists()
    assert list(tmp_path.glob("*.drawio")) == []


@pytest.mark.usefixtures("no_cad_backends")
def test_main_window_open_dxf_without_importers(gui_app, tmp_path,
                                                monkeypatch):
    app = gui_app
    errors = []
    monkeypatch.setattr(cwt.messagebox, "showerror",
                        lambda *a, **k: errors.append(a))
    assert app._convert_cad_drawing(str(tmp_path / "Plan.svg")) is None
    assert errors and "can't open SVG drawings" in errors[0][1]
    assert _toplevels(app.root) == []


# ------------------------------------------------ with the importers
@pytest.mark.usefixtures("cad_backends")
def test_filters_offer_cad_when_importers_exist():
    pats = " ".join(p for _lbl, p in cwt.open_filetypes())
    assert "*.dxf" in pats and "*.svg" in pats
    assert cwt.open_formats_text() == ".zsmt project, .drawio, .dxf or .svg"


@pytest.mark.usefixtures("cad_backends")
def test_start_screen_import_runs_off_the_tk_thread(tk_root, tmp_path,
                                                    monkeypatch):
    """Opening a DXF from the start screen (or the command line) used to
    render on the Tk thread with only a busy cursor: 11 s of "Not
    Responding" on the sample DXF."""
    seen = {}

    def slow_render(src):
        seen["thread"] = threading.current_thread()
        time.sleep(0.6)
        return _xml()
    monkeypatch.setattr(cwt.cad_mod, "render_to_drawio_xml", slow_render)
    dxf = tmp_path / "Level2.dxf"
    dxf.write_text("0\nEOF\n")
    ticks, titles = [], []

    def tick():
        ticks.append(time.monotonic())
        titles.extend(t.title() for t in _toplevels(tk_root))
        if len(ticks) < 50:
            tk_root.after(40, tick)
    tk_root.after(40, tick)
    start = cwt.StartScreen(tk_root, initial_dir=str(tmp_path))
    assert start.open_path(str(dxf)) is True
    assert seen["thread"] is not threading.main_thread()
    assert len(ticks) >= 5
    assert "Import DXF drawing" in titles
    assert (tmp_path / "Level2.drawio").exists()
    assert not start.frame.winfo_exists()      # the project opened


@pytest.mark.usefixtures("cad_backends")
def test_start_screen_import_cancel_writes_nothing(tk_root, tmp_path,
                                                   monkeypatch):
    release = threading.Event()

    def stuck(src):
        release.wait(10)
        return _xml()
    monkeypatch.setattr(cwt.cad_mod, "render_to_drawio_xml", stuck)
    svg = tmp_path / "Plan.svg"
    svg.write_text("<svg/>")
    start = cwt.StartScreen(tk_root, initial_dir=str(tmp_path))

    def press_cancel():
        # (The Cancel button: a synthetic Escape needs keyboard focus,
        # which Xvfb without a window manager never gives the dialog.)
        _button(_toplevels(tk_root)[-1], "Cancel").invoke()
    tk_root.after(300, press_cancel)
    t0 = time.monotonic()
    assert start.open_path(str(svg)) is False
    assert time.monotonic() - t0 < 5
    release.set()
    cwt._CAD_RENDER["thread"].join(5)
    pump(tk_root)
    assert start.frame.winfo_exists()
    assert list(tmp_path.glob("Plan*.drawio")) == []


@pytest.mark.usefixtures("cad_backends")
def test_command_line_dxf_opens_via_the_start_screen(tk_root, tmp_path,
                                                     monkeypatch):
    """main() with a .dxf argument shows the start screen and imports
    from there, so the progress window has a window behind it."""
    dxf = tmp_path / "Level2.dxf"
    dxf.write_text("0\nEOF\n")
    monkeypatch.setattr(sys, "argv", ["cwt", str(dxf)])
    monkeypatch.setattr(cwt, "make_root", lambda: tk_root)
    monkeypatch.setattr(cwt, "_enable_windows_dpi_awareness", lambda: None)
    monkeypatch.setattr(cwt, "_discover_files", lambda exts: [])
    monkeypatch.setattr(cwt, "_discover_drawio_files", lambda: [])
    monkeypatch.setattr(cwt.cad_mod, "render_to_drawio_xml",
                        lambda src: _xml())
    opened = []
    monkeypatch.setattr(cwt.StartScreen, "open_path",
                        lambda self, p: opened.append((self, p)))
    tk_root.after(600, tk_root.quit)
    cwt.main()
    assert opened and opened[0][1] == str(dxf)
    assert isinstance(opened[0][0], cwt.StartScreen)
