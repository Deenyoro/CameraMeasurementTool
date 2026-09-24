"""Add Floor accepts the same drawings as File > Open (DXF/SVG are
converted first) and refuses a drawing with no floor-plan image."""

from __future__ import annotations

import camera_wire_tool as cwt
from tests.gui_support import pump, write_drawio


def _dialogs(root):
    return [w for w in root.winfo_children()
            if isinstance(w, cwt.tk.Toplevel) and w.winfo_exists()]


def test_add_floor_from_dxf_converts_first(gui_app, tmp_path, monkeypatch):
    app = gui_app
    dxf = tmp_path / "Level2.dxf"
    dxf.write_text("0\nEOF\n")
    converted = write_drawio(tmp_path / "Level2.drawio")
    seen = {}
    monkeypatch.setattr(cwt.filedialog, "askopenfilename",
                        lambda **k: (seen.update(k), str(dxf))[1])
    monkeypatch.setattr(app, "_convert_cad_drawing",
                        lambda p: (seen.update(src=p), converted)[1])
    app._add_floor_dialog()
    pump(app.root)
    assert seen["src"] == str(dxf)
    assert any("*.dxf" in pat for _lbl, pat in seen["filetypes"])
    assert any(d.title() == "New floor" for d in _dialogs(app.root))


def test_add_floor_refuses_drawing_without_image(gui_app, tmp_path,
                                                 monkeypatch):
    app = gui_app
    bad = write_drawio(tmp_path / "NoImage.drawio", with_image=False)
    errors = []
    monkeypatch.setattr(cwt.filedialog, "askopenfilename", lambda **k: bad)
    monkeypatch.setattr(cwt.messagebox, "showerror",
                        lambda *a, **k: errors.append(a))
    app._add_floor_dialog()
    pump(app.root)
    assert errors and errors[0][0] == "Can't add floor"
    assert not any(d.title() == "New floor" for d in _dialogs(app.root))
