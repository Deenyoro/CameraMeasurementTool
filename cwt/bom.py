"""BOM / Bill of Materials rollup.

Pure logic — given a list of devices, wire routes, symbol types, cable
types and a scale factor, compute totals per cable type. No UI here.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass


# ---------------- Default cable catalog
# Project file may override or extend this.
DEFAULT_CABLE_TYPES: dict[str, dict] = {
    "Cat6":     {"label": "Cat6 PoE",          "color": "#0080FF", "cost_per_ft": 0.18},
    "Cat5e":    {"label": "Cat5e",             "color": "#3060A0", "cost_per_ft": 0.12},
    "RG59":     {"label": "RG59 Coax",         "color": "#101010", "cost_per_ft": 0.45},
    "OM4":      {"label": "Multimode Fiber OM4","color": "#FF8000", "cost_per_ft": 1.20},
    "Speaker":  {"label": "18/2 Speaker",      "color": "#A0A0A0", "cost_per_ft": 0.08},
    "Alarm":    {"label": "22/4 Stranded",     "color": "#80C040", "cost_per_ft": 0.10},
}


# ---------------- Mapping from device-type categories to default cable
def default_cable_for(symbol: dict) -> str:
    """Return a sensible default cable_type for a SymbolType dict."""
    if symbol.get("is_camera"):
        return "Cat6"
    color = (symbol.get("color") or "").upper()
    if color == "#00E080":   # green (access control)
        return "Cat6"
    if color == "#FF80FF":   # pink (intrusion)
        return "Alarm"
    if color == "#FFB580":   # orange (intrusion / DPS)
        return "Alarm"
    if color == "#FFFF80":   # pale yellow (video door)
        return "Cat6"
    return "Cat5e"


# ---------------- Rollup
@dataclass
class BomLine:
    cable_id: str
    label: str
    color: str
    runs: int
    total_in: float
    total_ft: float
    cost_per_ft: float
    subtotal: float


@dataclass
class BomReport:
    lines: list[BomLine]
    subtotal: float           # sum of cable subtotals (no waste / tax)
    waste_factor: float
    waste_added: float
    tax_rate: float
    tax_amount: float
    grand_total: float
    unassigned_runs: int      # wires with no device_id
    unassigned_inches: float


def cable_for_route(
    route: dict,
    devices_by_id: dict[str, dict],
    symbol_types: dict[str, dict],
    cable_types: dict[str, dict],
) -> str:
    """Resolve which cable a given wire route uses.

    Priority: explicit `cable_type` on the route → device's symbol_type
    `cable_type` → default-for-category.  Falls back to "Cat6" if even
    the inferred default isn't in cable_types.
    """
    explicit = route.get("cable_type")
    if explicit and explicit in cable_types:
        return explicit
    did = route.get("device_id")
    if did and did in devices_by_id:
        dev_type = devices_by_id[did].get("type")
        sym = symbol_types.get(dev_type, {}) if dev_type else {}
        if sym.get("cable_type") and sym["cable_type"] in cable_types:
            return sym["cable_type"]
        inferred = default_cable_for(sym)
        if inferred in cable_types:
            return inferred
    # last-ditch fallback
    if "Cat6" in cable_types:
        return "Cat6"
    return next(iter(cable_types)) if cable_types else "Cat6"


def rollup(
    wire_routes: list[dict],
    devices: list[dict],
    symbol_types: dict[str, dict],
    cable_types: dict[str, dict],
    *,
    waste_factor: float = 1.10,
    tax_rate: float = 0.0,
    risers: list[dict] | None = None,
    scale_factor: float | None = None,
) -> BomReport:
    """Compute a BOM from wire routes (+ optional risers).

    `wire_routes[i]["length_in"]` is the canonical length once scale is
    set. If a route has no length_in (scale not set), it contributes
    zero — the caller should warn the user.
    """
    devices_by_id = {d["id"]: d for d in devices}
    totals: dict[str, dict] = {}
    unassigned_runs = 0
    unassigned_in = 0.0

    for route in wire_routes:
        length_in = route.get("length_in") or 0.0
        cid = cable_for_route(route, devices_by_id, symbol_types, cable_types)
        if cid not in totals:
            totals[cid] = {"runs": 0, "total_in": 0.0}
        totals[cid]["runs"] += 1
        totals[cid]["total_in"] += length_in
        if route.get("device_id") is None:
            unassigned_runs += 1
            unassigned_in += length_in

    # Risers contribute their (vertical + horizontal) length in feet,
    # converted to inches.
    for r in (risers or []):
        cid = r.get("cable_type") or "Cat6"
        if cid not in cable_types:
            continue
        ft = float(r.get("vertical_ft", 0)) + float(r.get("horizontal_ft", 0))
        if ft <= 0:
            continue
        totals.setdefault(cid, {"runs": 0, "total_in": 0.0})
        totals[cid]["runs"] += 1
        totals[cid]["total_in"] += ft * 12

    lines: list[BomLine] = []
    subtotal = 0.0
    for cid, data in sorted(totals.items()):
        ct = cable_types.get(cid, {})
        cost_per_ft = float(ct.get("cost_per_ft", 0.0))
        ft = data["total_in"] / 12.0
        line_subtotal = ft * cost_per_ft
        subtotal += line_subtotal
        lines.append(BomLine(
            cable_id=cid,
            label=ct.get("label", cid),
            color=ct.get("color", "#888888"),
            runs=data["runs"],
            total_in=data["total_in"],
            total_ft=ft,
            cost_per_ft=cost_per_ft,
            subtotal=line_subtotal,
        ))

    waste_added = subtotal * (waste_factor - 1.0)
    after_waste = subtotal + waste_added
    tax_amount = after_waste * tax_rate
    grand_total = after_waste + tax_amount

    return BomReport(
        lines=lines,
        subtotal=subtotal,
        waste_factor=waste_factor,
        waste_added=waste_added,
        tax_rate=tax_rate,
        tax_amount=tax_amount,
        grand_total=grand_total,
        unassigned_runs=unassigned_runs,
        unassigned_inches=unassigned_in,
    )


# ---------------- CSV export
def write_csv(report: BomReport, path: str, currency: str = "$") -> None:
    """Write the BOM report to a CSV. Stable column order suitable for Excel."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "Cable", "Label", "Runs", "Total (ft)", "Total (inches)",
            f"Cost/ft ({currency})", f"Subtotal ({currency})",
        ])
        for line in report.lines:
            w.writerow([
                line.cable_id, line.label, line.runs,
                f"{line.total_ft:.2f}", f"{line.total_in:.2f}",
                f"{line.cost_per_ft:.4f}", f"{line.subtotal:.2f}",
            ])
        w.writerow([])
        w.writerow(["", "Subtotal", "", "", "", "", f"{report.subtotal:.2f}"])
        w.writerow(["", f"Waste (x{report.waste_factor:.2f})", "", "", "", "",
                    f"{report.waste_added:.2f}"])
        w.writerow(["", f"Tax ({report.tax_rate*100:.2f}%)", "", "", "", "",
                    f"{report.tax_amount:.2f}"])
        w.writerow(["", "GRAND TOTAL", "", "", "", "", f"{report.grand_total:.2f}"])
