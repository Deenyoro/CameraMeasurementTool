"""PDF + CSV exporters."""

from __future__ import annotations

import csv
import os
from datetime import datetime

from cwt.bom import BomReport, write_csv as write_bom_csv

try:
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib import colors
    from reportlab.lib.units import inch
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak,
    )
    _HAS_REPORTLAB = True
except ImportError:
    _HAS_REPORTLAB = False


def _atomic_csv_write(path: str, rows: list[list]) -> None:
    """Write rows to path atomically via tmp+rename so a crash during
    serialization doesn't leave a half-written CSV at the destination."""
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerows(rows)
        os.replace(tmp, path)
    except Exception:
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except OSError:
            pass
        raise


def write_devices_csv(devices: list[dict], symbol_types: dict[str, dict],
                      wire_routes: list[dict], format_length, path: str) -> None:
    """One row per device: id, type, label, position, wire totals.

    Defensive against hand-edited project files: skips non-dict entries
    and coerces non-numeric length_in / x / y to safe defaults rather
    than crashing the export."""
    wire_by_device: dict[str, dict] = {}
    for wr in wire_routes:
        if not isinstance(wr, dict):
            continue
        did = wr.get("device_id")
        if not did:
            continue
        wire_by_device.setdefault(did, {"runs": 0, "total_in": 0.0})
        wire_by_device[did]["runs"] += 1
        try:
            wire_by_device[did]["total_in"] += float(wr.get("length_in") or 0)
        except (TypeError, ValueError):
            pass

    def _coerce_pos(v):
        try:
            return f"{float(v):.1f}"
        except (TypeError, ValueError):
            return "—"

    rows = [["Device ID", "Type", "Label", "x", "y", "Wire Runs",
             "Total Wire (in)", "Total Wire"]]
    for d in devices:
        if not isinstance(d, dict) or "id" not in d:
            continue
        sym = symbol_types.get(d.get("type"), {})
        if not isinstance(sym, dict):
            sym = {}
        wi = wire_by_device.get(d["id"], {"runs": 0, "total_in": 0.0})
        rows.append([
            d["id"], d.get("type", ""), sym.get("label", ""),
            _coerce_pos(d.get("x")), _coerce_pos(d.get("y")),
            wi["runs"], f"{wi['total_in']:.2f}",
            format_length(wi["total_in"]) if wi["total_in"] else "--",
        ])
    _atomic_csv_write(path, rows)


def write_wires_csv(wire_routes: list[dict], format_length, path: str,
                     *,
                     devices: list[dict] | None = None,
                     symbol_types: dict[str, dict] | None = None,
                     cable_types: dict[str, dict] | None = None) -> None:
    """One row per wire: device, cable, segments, length.

    Cable column shows the EFFECTIVE cable (the one the BOM rolls up
    against) when devices/symbol_types/cable_types are supplied — same
    resolution rule as `bom.cable_for_route`. An '(auto)' suffix marks
    routes that fell back to a device/symbol default instead of an
    explicit override. When the dependencies are omitted (older callers),
    falls back to the explicit override only — preserves the prior
    contract.
    """
    rows = [["#", "Device", "Cable", "Segments", "Length (in)", "Length"]]
    # Build the index once instead of O(N) per route. Skip malformed
    # entries (non-dict or missing id) — same filter as bom.rollup.
    devices_by_id = ({d["id"]: d for d in devices
                      if isinstance(d, dict) and "id" in d}
                     if devices is not None else None)
    can_resolve = (devices_by_id is not None
                   and symbol_types is not None
                   and cable_types is not None)
    for i, wr in enumerate(wire_routes):
        if not isinstance(wr, dict):
            continue
        try:
            length_in = float(wr.get("length_in") or 0)
        except (TypeError, ValueError):
            length_in = 0.0
        if can_resolve:
            # Lazy import to avoid a top-level cycle (cwt.bom imports
            # are fine, but the symmetric expectation is bom needs nothing
            # from export — keep the export module light).
            from cwt.bom import cable_for_route
            explicit = wr.get("cable_type")
            effective = cable_for_route(
                wr, devices_by_id, symbol_types, cable_types)
            cable_cell = effective + ("" if explicit else " (auto)")
        else:
            cable_cell = wr.get("cable_type") or ""
        # Segment count = points-1, but tolerate missing/short/non-list
        # points (corrupted entry) — emit 0 rather than crashing the row.
        points = wr.get("points")
        n_segs = max(0, len(points) - 1) if isinstance(points, list) else 0
        rows.append([
            i + 1,
            wr.get("device_id") or "(unassigned)",
            cable_cell,
            n_segs,
            f"{length_in:.2f}",
            format_length(length_in) if length_in else "--",
        ])
    _atomic_csv_write(path, rows)


def write_bundle_csv(directory: str, *, devices, symbol_types, wire_routes,
                     bom_report, format_length, currency: str = "$",
                     cable_types: dict[str, dict] | None = None) -> list[str]:
    """Write all three CSVs into `directory`. Returns the list of paths."""
    os.makedirs(directory, exist_ok=True)
    paths = [
        os.path.join(directory, "bom.csv"),
        os.path.join(directory, "devices.csv"),
        os.path.join(directory, "wires.csv"),
    ]
    write_bom_csv(bom_report, paths[0], currency=currency)
    write_devices_csv(devices, symbol_types, wire_routes, format_length, paths[1])
    write_wires_csv(wire_routes, format_length, paths[2],
                     devices=devices, symbol_types=symbol_types,
                     cable_types=cable_types)
    return paths


# ---------------- PDF
def write_pdf_report(
    path: str,
    *,
    project_name: str,
    devices: list[dict],
    symbol_types: dict[str, dict],
    wire_routes: list[dict],
    bom_report: BomReport,
    risers: list[dict] | None = None,
    floor_label: str | None = None,
    scale_factor: float | None = None,
    format_length=lambda x: f"{x:.2f}\"" if x is not None else "--",
    currency: str = "$",
) -> None:
    """Write a multi-page PDF report. Requires `reportlab`."""
    if not _HAS_REPORTLAB:
        raise RuntimeError("reportlab is not installed. `pip install reportlab`.")

    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("H1", parent=styles["Title"], fontSize=22, leading=26,
                        textColor=colors.HexColor("#1a1a1a"))
    h2 = ParagraphStyle("H2", parent=styles["Heading2"], fontSize=14,
                        textColor=colors.HexColor("#202020"))
    body = styles["BodyText"]

    doc = SimpleDocTemplate(path, pagesize=letter,
                            leftMargin=0.6 * inch, rightMargin=0.6 * inch,
                            topMargin=0.6 * inch, bottomMargin=0.6 * inch)
    story = []

    # Cover
    story.append(Paragraph(project_name or "Camera Wire Project", h1))
    story.append(Spacer(1, 8))
    story.append(Paragraph(
        f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M')}", body))
    if floor_label:
        story.append(Paragraph(f"Floor: {floor_label}", body))
    if scale_factor:
        story.append(Paragraph(f"Calibration: 1 px = {scale_factor:.4f} inches", body))
    story.append(Spacer(1, 12))

    # Headline numbers — defensive against hand-edited project files
    # (matching the malformed-input tolerance in the CSV exporters and
    # bom.rollup).
    def _safe_len_in(wr):
        if not isinstance(wr, dict):
            return 0.0
        try:
            return float(wr.get("length_in") or 0)
        except (TypeError, ValueError):
            return 0.0
    def _is_camera_dev(d):
        if not isinstance(d, dict) or "type" not in d:
            return False
        sym = symbol_types.get(d["type"], {})
        return isinstance(sym, dict) and sym.get("is_camera")
    grand_in = sum(_safe_len_in(wr) for wr in wire_routes)
    head_data = [
        ["Total devices", str(len(devices))],
        ["Cameras", str(sum(1 for d in devices if _is_camera_dev(d)))],
        ["Wire runs", str(len(wire_routes))],
        ["Total wire", format_length(grand_in)],
        ["Material cost (subtotal)", f"{currency}{bom_report.subtotal:,.2f}"],
        ["Grand total (w/ waste & tax)", f"{currency}{bom_report.grand_total:,.2f}"],
    ]
    head_table = Table(head_data, colWidths=[2.5 * inch, 2.5 * inch])
    head_table.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#888888")),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cccccc")),
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f0f0f0")),
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 11),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(head_table)
    story.append(Spacer(1, 16))

    # BOM table
    story.append(Paragraph("Bill of Materials", h2))
    story.append(Spacer(1, 6))
    bom_data = [["Cable", "Description", "Runs", "Length (ft)",
                 f"Cost/ft ({currency})", f"Subtotal ({currency})"]]
    for line in bom_report.lines:
        bom_data.append([
            line.cable_id, line.label, str(line.runs),
            f"{line.total_ft:,.2f}",
            f"{line.cost_per_ft:.4f}",
            f"{line.subtotal:,.2f}",
        ])
    bom_data.extend([
        ["", "", "", "", "Subtotal", f"{bom_report.subtotal:,.2f}"],
        ["", "", "", "",
         f"Waste (×{bom_report.waste_factor:.2f})",
         f"{bom_report.waste_added:,.2f}"],
        ["", "", "", "",
         f"Tax ({bom_report.tax_rate*100:.2f}%)",
         f"{bom_report.tax_amount:,.2f}"],
        ["", "", "", "", "GRAND TOTAL", f"{bom_report.grand_total:,.2f}"],
    ])
    bom_table = Table(bom_data, colWidths=[
        0.8 * inch, 2.2 * inch, 0.6 * inch, 1.0 * inch, 1.0 * inch, 1.2 * inch,
    ])
    bom_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#202060")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#202060")),
        ("TEXTCOLOR", (0, -1), (-1, -1), colors.white),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#888888")),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cccccc")),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (2, 1), (-1, -1), "RIGHT"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(bom_table)

    # Risers
    if risers:
        story.append(PageBreak())
        story.append(Paragraph("Risers (cross-floor cabling)", h2))
        story.append(Spacer(1, 6))
        riser_data = [["From floor", "From device", "To floor", "To device",
                       "Vertical (ft)", "Cable"]]
        for r in risers:
            if not isinstance(r, dict):
                continue
            frm = r.get("from", {})
            to = r.get("to", {})
            if not isinstance(frm, dict): frm = {}
            if not isinstance(to, dict):  to = {}
            try:
                vert = f"{float(r.get('vertical_ft', 0) or 0):.1f}"
            except (TypeError, ValueError):
                vert = "—"
            riser_data.append([
                frm.get("floor", ""), frm.get("device_id", ""),
                to.get("floor", ""), to.get("device_id", ""),
                vert,
                r.get("cable_type", ""),
            ])
        riser_table = Table(riser_data)
        riser_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#202060")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#888888")),
            ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cccccc")),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
        ]))
        story.append(riser_table)

    # Per-device listing
    story.append(PageBreak())
    story.append(Paragraph("Devices", h2))
    story.append(Spacer(1, 6))
    dev_data = [["ID", "Type", "Label", "Wire runs", "Total length"]]
    wire_by_device: dict[str, dict] = {}
    for wr in wire_routes:
        if not isinstance(wr, dict):
            continue
        did = wr.get("device_id")
        if not did: continue
        wire_by_device.setdefault(did, {"runs": 0, "total_in": 0.0})
        wire_by_device[did]["runs"] += 1
        try:
            wire_by_device[did]["total_in"] += float(wr.get("length_in") or 0)
        except (TypeError, ValueError):
            pass
    for d in devices:
        if not isinstance(d, dict) or "id" not in d:
            continue
        info = symbol_types.get(d.get("type"), {})
        if not isinstance(info, dict):
            info = {}
        wi = wire_by_device.get(d["id"], {"runs": 0, "total_in": 0.0})
        dev_data.append([
            d["id"], d.get("type", ""), info.get("label", ""),
            str(wi["runs"]),
            format_length(wi["total_in"]) if wi["total_in"] else "—",
        ])
    dev_table = Table(dev_data, colWidths=[
        0.7 * inch, 0.6 * inch, 2.8 * inch, 0.8 * inch, 1.4 * inch,
    ])
    dev_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#202060")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#888888")),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cccccc")),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
    ]))
    story.append(dev_table)

    doc.build(story)


def write_map_pdf(
    path: str,
    *,
    project_name: str,
    map_pages: list[dict] | None = None,
    devices: list[dict],
    symbol_types: dict[str, dict],
    wire_routes: list[dict],
    bom_report: BomReport,
    model_lines: list | None = None,
    include_summary: bool = True,
    summary_options: dict | None = None,
    floor_label: str | None = None,
    scale_factor: float | None = None,
    format_length=lambda x: f"{x:.2f}\"" if x is not None else "--",
    currency: str = "$",
) -> None:
    """Multi-page PDF: one full-view floor-plan page per entry in `map_pages`
    (each {"image": path, "caption": str}) followed by an optional one-page
    summary (project stats, the cable BOM, and the per-camera-model hardware
    rollup). Each map page is sized to its image so the plan fills the page
    instead of being shrunk onto a letter sheet; the summary is force-fit onto
    ONE page via KeepInFrame(shrink). Requires `reportlab`."""
    if not _HAS_REPORTLAB:
        raise RuntimeError("reportlab is not installed. `pip install reportlab`.")

    from reportlab.lib.pagesizes import landscape
    from reportlab.pdfgen import canvas as rl_canvas
    from reportlab.platypus import Frame, KeepInFrame
    try:
        from PIL import Image as PILImage
    except ImportError:
        PILImage = None

    styles = getSampleStyleSheet()
    h2 = ParagraphStyle("MH2", parent=styles["Heading2"], fontSize=13,
                        textColor=colors.HexColor("#202020"))
    small = ParagraphStyle("MSmall", parent=styles["BodyText"], fontSize=8,
                           leading=10, textColor=colors.HexColor("#404040"))

    c = rl_canvas.Canvas(path)

    # ---- One full-view page per map view. Each page is sized to its image so
    # the plan fills the page (PDF viewers fit-page → whole map, large). A
    # small caption in the top-left corner names the view (wires / no wires /
    # FOV …) without shrinking the image. Long side clamped to a sane range
    # so tiny/huge captures still produce a usable page.
    for pg in (map_pages or []):
        img_path = pg.get("image") if isinstance(pg, dict) else None
        if not img_path or not os.path.exists(img_path):
            continue
        caption = pg.get("caption", "") if isinstance(pg, dict) else ""
        iw, ih = (PILImage.open(img_path).size
                  if PILImage is not None else (1600, 900))
        iw = iw or 1600
        ih = ih or 900
        # Page sized to the image aspect; the full-resolution image is
        # embedded regardless, so viewers can zoom in to read every symbol.
        # Clamp the long side to a sane point range (the page is just the
        # default display size — detail is preserved at any page size).
        mw, mh = float(iw), float(ih)
        longside = max(mw, mh)
        target = min(max(longside, 1000.0), 1700.0)
        k = target / longside if longside else 1.0
        mw, mh = mw * k, mh * k
        c.setPageSize((mw, mh))
        c.drawImage(img_path, 0, 0, width=mw, height=mh,
                    preserveAspectRatio=True, anchor="c")
        if caption:
            # Caption is drawn verbatim — the caller decides the exact text
            # (and whether to include one at all), so the export dialog's
            # editable caption field is what shows up here.
            label = caption
            c.setFont("Helvetica-Bold", 11)
            tw = c.stringWidth(label, "Helvetica-Bold", 11)
            c.setFillColorRGB(1, 1, 1)
            c.rect(8, mh - 26, tw + 12, 20, fill=1, stroke=0)
            c.setFillColorRGB(0.1, 0.1, 0.1)
            c.drawString(14, mh - 21, label)
        c.showPage()

    # ---- Summary page (landscape letter, forced onto ONE page).
    if include_summary:
        # Caller-controlled sections (the export dialog's "Advanced" box).
        opts = summary_options or {}
        show_breakdown = opts.get("device_breakdown", True)
        show_cable = opts.get("cable_bom", True)
        show_models = opts.get("camera_models", True)

        page = landscape(letter)
        LM = RM = TM = BM = 0.5 * inch
        avail_w = page[0] - LM - RM
        avail_h = page[1] - TM - BM
        c.setPageSize(page)

        def _is_cam(d):
            if not isinstance(d, dict) or "type" not in d:
                return False
            sym = symbol_types.get(d["type"], {})
            return isinstance(sym, dict) and sym.get("is_camera")

        n_cam = sum(1 for d in devices if _is_cam(d))
        total_ft = sum(getattr(l, "total_ft", 0.0) for l in bom_report.lines)
        hw_total = sum(getattr(m, "subtotal", 0.0) for m in (model_lines or []))
        full_total = bom_report.grand_total + hw_total
        # No wire on the sheet → every cable/footage figure is just zeros, so
        # omit the wire stats AND the cable BOM rather than printing them.
        has_wire = (len(wire_routes) > 0 or bool(bom_report.lines)
                    or total_ft > 0.0)
        show_cable = show_cable and has_wire

        content = []
        content.append(Paragraph(f"{project_name} — Summary", h2))
        content.append(Paragraph(
            f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M')}"
            + (f"  ·  Floor {floor_label}" if floor_label else "")
            + (f"  ·  1 px = {scale_factor:.4f}\"" if scale_factor else ""),
            small))
        content.append(Spacer(1, 8))

        # Top stats — wire cells only appear when there's wire; hardware only
        # when there's hardware cost. Pairs laid out 3-per-row (6 columns).
        pairs = [("Devices", str(len(devices)))]
        if n_cam > 0:
            pairs.append(("Cameras", str(n_cam)))
        if has_wire:
            pairs += [("Wire runs", str(len(wire_routes))),
                      ("Total wire", f"{total_ft:,.1f} ft"),
                      ("Cable cost", f"{currency}{bom_report.grand_total:,.2f}")]
        if hw_total > 0:
            pairs.append(("Hardware", f"{currency}{hw_total:,.2f}"))
        stat_rows = []
        for i in range(0, len(pairs), 3):
            row = []
            for lbl, val in pairs[i:i + 3]:
                row += [lbl, val]
            while len(row) < 6:
                row.append("")
            stat_rows.append(row)
        stats_tbl = Table(stat_rows, colWidths=[1.1*inch, 1.4*inch, 1.1*inch,
                                                1.4*inch, 1.1*inch, 1.5*inch])
        st_style = [
            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#888888")),
            ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#dddddd")),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
        ]
        for col in (0, 2, 4):
            st_style.append(("BACKGROUND", (col, 0), (col, -1),
                             colors.HexColor("#f0f0f0")))
            st_style.append(("FONTNAME", (col, 0), (col, -1), "Helvetica-Bold"))
        stats_tbl.setStyle(TableStyle(st_style))
        content.append(stats_tbl)
        content.append(Spacer(1, 10))

        # Detail tables shown side-by-side (only the enabled/relevant ones).
        detail_tables = []

        # Device breakdown by type (code, description, qty).
        if show_breakdown:
            counts: dict = {}
            for d in devices:
                if isinstance(d, dict) and "type" in d:
                    counts[d["type"]] = counts.get(d["type"], 0) + 1
            bd = [["Type", "Description", "Qty"]]
            for code in sorted(counts):
                sym = symbol_types.get(code, {})
                lab = sym.get("label", code) if isinstance(sym, dict) else code
                bd.append([str(code), str(lab)[:26], str(counts[code])])
            bd.append(["TOTAL", "", str(sum(counts.values()))])
            bd_tbl = Table(bd, colWidths=[0.7*inch, 1.8*inch, 0.5*inch])
            bd_tbl.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#204020")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#e8f4e8")),
                ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#888888")),
                ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cccccc")),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("ALIGN", (2, 0), (-1, -1), "RIGHT"),
            ]))
            detail_tables.append(bd_tbl)

        # Cable BOM — only when there's wire.
        if show_cable:
            bom_data = [["Cable", "Runs", "Ft", f"Sub ({currency})"]]
            for line in bom_report.lines:
                bom_data.append([line.cable_id, str(line.runs),
                                 f"{line.total_ft:,.1f}", f"{line.subtotal:,.2f}"])
            bom_data.append(["TOTAL", "", f"{total_ft:,.1f}",
                             f"{bom_report.grand_total:,.2f}"])
            bom_tbl = Table(bom_data,
                            colWidths=[1.2*inch, 0.6*inch, 0.8*inch, 1.0*inch])
            bom_tbl.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#202060")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#e8e8f4")),
                ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#888888")),
                ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cccccc")),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
            ]))
            detail_tables.append(bom_tbl)

        # Camera-model hardware — only when models are assigned.
        if show_models and (model_lines or []):
            model_data = [["Model", "Model #", "Qty", f"Sub ({currency})"]]
            for m in model_lines:
                model_data.append([str(getattr(m, "name", ""))[:24],
                                   str(getattr(m, "model_number", "")),
                                   str(getattr(m, "count", 0)),
                                   f"{getattr(m, 'subtotal', 0.0):,.2f}"])
            model_data.append(["HARDWARE TOTAL", "",
                               str(sum(getattr(m, "count", 0) for m in model_lines)),
                               f"{hw_total:,.2f}"])
            model_tbl = Table(model_data,
                              colWidths=[1.8*inch, 1.1*inch, 0.5*inch, 1.0*inch])
            model_tbl.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#604020")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#f4ece8")),
                ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#888888")),
                ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cccccc")),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("ALIGN", (2, 0), (-1, -1), "RIGHT"),
            ]))
            detail_tables.append(model_tbl)

        # Stack the detail tables vertically. Their natural widths are all
        # well under the page width, so this can NEVER run off the right edge
        # — the old side-by-side layout did when model names were long, and
        # KeepInFrame couldn't catch it (the overflow was inside a cell). There
        # is ample vertical room on the landscape page, and KeepInFrame still
        # shrinks the whole thing if a huge type list would overflow downward.
        for dt in detail_tables:
            content.append(dt)
            content.append(Spacer(1, 10))

        # Cost total — only when there's actually a cost to report.
        if full_total > 0:
            if has_wire and hw_total > 0:
                tlabel = "FULL TOTAL (wiring + hardware)"
            elif hw_total > 0:
                tlabel = "HARDWARE TOTAL"
            else:
                tlabel = "WIRING TOTAL"
            content.append(Paragraph(
                f"<b>{tlabel}: {currency}{full_total:,.2f}</b>", h2))

        # KeepInFrame(mode='shrink') guarantees the whole summary fits on one
        # page — it scales the content down rather than spilling over.
        kif = KeepInFrame(avail_w, avail_h, content, mode="shrink")
        frame = Frame(LM, BM, avail_w, avail_h, leftPadding=0, rightPadding=0,
                      topPadding=0, bottomPadding=0)
        frame.addFromList([kif], c)
        c.showPage()

    c.save()
