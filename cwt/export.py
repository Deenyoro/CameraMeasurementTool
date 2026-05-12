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
    # Build the index once instead of O(N) per route.
    devices_by_id = ({d["id"]: d for d in devices}
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

    # Headline numbers
    grand_in = sum((wr.get("length_in") or 0) for wr in wire_routes)
    head_data = [
        ["Total devices", str(len(devices))],
        ["Cameras", str(sum(1 for d in devices
                            if symbol_types.get(d["type"], {}).get("is_camera")))],
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
            frm = r.get("from", {})
            to = r.get("to", {})
            riser_data.append([
                frm.get("floor", ""), frm.get("device_id", ""),
                to.get("floor", ""), to.get("device_id", ""),
                f"{r.get('vertical_ft', 0):.1f}",
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
        did = wr.get("device_id")
        if not did: continue
        wire_by_device.setdefault(did, {"runs": 0, "total_in": 0.0})
        wire_by_device[did]["runs"] += 1
        wire_by_device[did]["total_in"] += (wr.get("length_in") or 0)
    for d in devices:
        info = symbol_types.get(d["type"], {})
        wi = wire_by_device.get(d["id"], {"runs": 0, "total_in": 0.0})
        dev_data.append([
            d["id"], d["type"], info.get("label", ""),
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
