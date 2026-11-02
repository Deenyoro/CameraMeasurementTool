"""Readable per-floor layout PDF.

One page per floor — the floor-plan image (already rendered + cropped by the
caller, symbols enlarged to a fixed printed size) beside a large KEY panel:
every symbol on that floor with its count, building-wide card totals, which
door numbers are on the page, and the building's door list. A summary page
up front tabulates every device type by floor, lists each building's doors
with where their tagged device lives, and prints an optional note.

Door lists are plain text: a title line (e.g. "Expansion Building Door
List") followed by "N. Description" rows. A row's location is the page
whose devices carry tag "N" within the same building.

Pure layout — no Tk. Requires reportlab.
"""

from __future__ import annotations

import collections
import math
import re
import time

try:
    from reportlab.lib import colors
    from reportlab.lib.units import inch
    from reportlab.lib.utils import simpleSplit
    from reportlab.pdfgen import canvas as rl_canvas
    _HAS_REPORTLAB = True
except ImportError:  # pragma: no cover - optional dependency
    _HAS_REPORTLAB = False

PAGE_SIZES = {               # landscape (width, height) in inches
    "Tabloid 11x17": (17.0, 11.0),
    "Letter 8.5x11": (11.0, 8.5),
    "A3": (16.54, 11.69),
}

def _geometry(page_size: str) -> dict:
    """Page geometry in inches for a page size name."""
    pw, ph = PAGE_SIZES.get(page_size, PAGE_SIZES["Tabloid 11x17"])
    small = pw < 14
    g = {"pw": pw, "ph": ph, "small": small,
         "margin": 0.3 if small else 0.4, "title_h": 0.6 if small else 0.75,
         "panel_w": 2.9 if small else 3.7, "gap": 0.2}
    g["map_w"] = pw - 2 * g["margin"] - g["panel_w"] - g["gap"]
    g["map_h"] = ph - 2 * g["margin"] - g["title_h"]
    return g


def map_area_inches(page_size: str) -> tuple[float, float]:
    """(width, height) in inches of the floor-plan area on a floor page —
    lets the caller size symbols/notes for a fixed printed size."""
    g = _geometry(page_size)
    return g["map_w"], g["map_h"]


_DOOR_ROW = re.compile(r"\s*(\d+)\s*[.)]\s*(.+)")


def parse_door_list(text: str) -> tuple[str, list[tuple[str, str]]] | None:
    """'Title\\n1. A\\n2. B' -> ('Title', [('1', '1. A'), ('2', '2. B')]).
    None unless the first line ends with 'door list' (case-insensitive)."""
    if not isinstance(text, str):
        return None
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines or not lines[0].lower().endswith("door list"):
        return None
    rows = []
    for ln in lines[1:]:
        m = _DOOR_ROW.match(ln)
        if m:
            rows.append((m.group(1), ln))
    return lines[0], rows


def number_ranges(nums: list[str]) -> str:
    """['1','2','3','5','7','8'] -> '1-3, 5, 7-8' (non-numeric tags listed as-is)."""
    ints = sorted({int(n) for n in nums if n.isdigit()})
    out, i = [], 0
    while i < len(ints):
        j = i
        while j + 1 < len(ints) and ints[j + 1] == ints[j] + 1:
            j += 1
        out.append(str(ints[i]) if i == j else f"{ints[i]}-{ints[j]}")
        i = j + 1
    out += sorted(n for n in set(nums) if not n.isdigit())
    return ", ".join(out) if out else "none"


def _icon(c, info: dict, code: str, x: float, y: float, s: float) -> None:
    """Symbol icon centered at (x, y), size s pt, matching the map style."""
    c.setFillColor(colors.HexColor(info.get("color", "#FFFF00")))
    c.setStrokeColor(colors.HexColor("#0000FF"))
    c.setLineWidth(1.2)
    shape = info.get("shape", "ellipse")
    r = s / 2
    if shape == "rect":
        c.rect(x - r, y - r, s, s, stroke=1, fill=1)
    elif shape == "roundrect":
        c.roundRect(x - r, y - r, s, s, s * 0.2, stroke=1, fill=1)
    elif shape in ("hexagon", "step", "halfrect"):
        p = c.beginPath()
        if shape == "hexagon":
            pts = [(r * math.cos(math.radians(a * 60 - 30)),
                    r * math.sin(math.radians(a * 60 - 30))) for a in range(6)]
        elif shape == "step":
            pts = [(-r, -r), (0.4 * r, -r), (r, 0), (0.4 * r, r), (-r, r)]
        else:
            pts = [(r * math.cos(math.radians(a)), r * math.sin(math.radians(a)))
                   for a in range(0, 181, 15)]
        p.moveTo(x + pts[0][0], y + pts[0][1])
        for px, py in pts[1:]:
            p.lineTo(x + px, y + py)
        p.close()
        c.drawPath(p, stroke=1, fill=1)
    else:
        c.circle(x, y, r, stroke=1, fill=1)
    c.setFillColor(colors.black)
    fs = s * (0.36 if len(code) >= 3 else 0.42)
    c.setFont("Helvetica-Bold", fs)
    c.drawCentredString(x, y - fs * 0.35, code)


def write_layout_pdf(
    path: str,
    *,
    title: str,
    pages: list[dict],
    symbols: dict[str, dict],
    categories: dict[str, str],
    door_lists: dict[str, dict] | None = None,
    summary_note: str = "",
    include_summary: bool = True,
    page_size: str = "Tabloid 11x17",
    tag_label: str = "Door number",
) -> None:
    """Write the layout PDF.

    pages: [{"id", "name", "building", "short", "image": png path,
             "size": (w, h) px, "counts": {type: n}, "tags": {tag: type}}]
    door_lists: {building: {"title": str, "rows": [(tag, line), ...]}}
    """
    if not _HAS_REPORTLAB:
        raise RuntimeError("reportlab is not installed. `pip install reportlab`.")
    door_lists = door_lists or {}
    g = _geometry(page_size)
    PW, PH = g["pw"] * inch, g["ph"] * inch
    small = g["small"]
    M = g["margin"] * inch
    TITLE_H = g["title_h"] * inch
    PANEL_W = g["panel_w"] * inch
    map_w, map_h = g["map_w"] * inch, g["map_h"] * inch

    # Where each tagged device lives: building -> {tag: page id}
    tag_page: dict[str, dict[str, str]] = collections.defaultdict(dict)
    page_short = {}
    for pg in pages:
        page_short[pg["id"]] = pg.get("short") or pg["name"]
        for t in pg.get("tags", {}):
            tag_page[pg.get("building", "")].setdefault(t, pg["id"])
    stamp = time.strftime("%B %d, %Y")
    total_pages = len(pages) + (1 if include_summary else 0)

    c = rl_canvas.Canvas(path, pagesize=(PW, PH))
    c.setTitle(f"{title} - Device Locations")

    def header(head, sub):
        c.setFillColor(colors.HexColor("#1F3B63"))
        bottom = PH - M - TITLE_H + 0.05 * inch
        c.rect(0, bottom, PW, PH - bottom, stroke=0, fill=1)
        c.setFillColor(colors.white)
        c.setFont("Helvetica-Bold", 16 if small else 20)
        c.drawString(M, PH - M - (0.22 if small else 0.3) * inch, head)
        sub_y = bottom + (0.1 if small else 0.14) * inch
        c.setFont("Helvetica", 9 if small else 11)
        c.drawString(M, sub_y, sub)
        c.setFont("Helvetica", 8 if small else 9)
        c.drawRightString(PW - M, sub_y, f"{title}  |  {stamp}")

    def footer(n):
        c.setFillColor(colors.HexColor("#555555"))
        c.setFont("Helvetica", 7 if small else 8)
        c.drawString(M, M * 0.45, "Symbols are enlarged for legibility and not to scale.")
        c.drawRightString(PW - M, M * 0.45, f"Page {n} of {total_pages}")
        c.setFillColor(colors.black)

    def row_location(building, tag):
        pid = tag_page.get(building, {}).get(tag)
        return page_short.get(pid) if pid else None

    page_no = 1
    # ------------------------------------------------------------ summary
    if include_summary:
        header("Device Locations - Summary", "Devices by floor")
        types = sorted({t for pg in pages for t in pg["counts"]},
                       key=lambda k: (categories.get(k, ""),
                                      symbols.get(k, {}).get("label", k)))
        tx, ty = M, PH - M - TITLE_H - 0.45 * inch
        c.setFillColor(colors.black)
        name_w = (3.3 if small else 4.2) * inch
        col_w = min((1.45 if not small else 1.0) * inch,
                    (PW - 2 * M - name_w - (3.2 if not small else 0) * inch)
                    / max(1, len(pages) + 1))
        fs = 9 if small else 11
        c.setFont("Helvetica-Bold", fs)
        c.drawString(tx + 34, ty, "Device")
        for i, pg in enumerate(pages):
            lab = pg["name"]
            for ln_i, part in enumerate(simpleSplit(lab, "Helvetica-Bold", fs, col_w - 6)[:2]):
                c.drawCentredString(tx + name_w + col_w * (i + 0.5), ty + 12 * (1 - ln_i) - 12, part)
        c.drawCentredString(tx + name_w + col_w * (len(pages) + 0.5), ty, "Total")
        ty -= 22
        right = tx + name_w + col_w * (len(pages) + 1)
        c.setStrokeColor(colors.HexColor("#333333"))
        c.line(tx, ty, right, ty)
        ty -= 22
        rh = 22 if small else 28
        for code in types:
            _icon(c, symbols.get(code, {}), code, tx + 11, ty + 4, 18 if small else 20)
            c.setFont("Helvetica", fs)
            lab = f"{symbols.get(code, {}).get('label', code)}  ({categories.get(code, '')})"
            c.drawString(tx + 34, ty, lab if len(lab) < 60 else lab[:59] + "...")
            tot = 0
            for i, pg in enumerate(pages):
                n = pg["counts"].get(code, 0)
                tot += n
                c.drawCentredString(tx + name_w + col_w * (i + 0.5), ty, str(n) if n else "-")
            c.setFont("Helvetica-Bold", fs)
            c.drawCentredString(tx + name_w + col_w * (len(pages) + 0.5), ty, str(tot))
            ty -= rh
        c.line(tx, ty + rh / 2, right, ty + rh / 2)
        c.setFont("Helvetica-Bold", fs)
        c.drawString(tx + 34, ty - 4, "Total")
        for i, pg in enumerate(pages):
            c.drawCentredString(tx + name_w + col_w * (i + 0.5), ty - 4,
                                str(sum(pg["counts"].values())))
        c.drawCentredString(tx + name_w + col_w * (len(pages) + 0.5), ty - 4,
                            str(sum(sum(pg["counts"].values()) for pg in pages)))

        # Note box (top-right, beside the table).
        if summary_note.strip():
            nx = right + 0.3 * inch
            nw = PW - M - nx
            if nw < 2.2 * inch:          # no room beside the table: put it below
                nx, nw = M, min(6 * inch, PW - 2 * M)
                ntop = ty - 0.35 * inch
                ty = ntop
            else:
                ntop = PH - M - TITLE_H - 0.3 * inch
            body = []
            for para in summary_note.strip().splitlines():
                body += simpleSplit(para, "Helvetica", 10.5, nw - 24) or [""]
            nh = 32 + 14 * len(body)
            c.setFillColor(colors.HexColor("#FFF6D6"))
            c.setStrokeColor(colors.HexColor("#E0A800"))
            c.setLineWidth(1.5)
            c.rect(nx, ntop - nh, nw, nh, stroke=1, fill=1)
            c.setFillColor(colors.HexColor("#7A5200"))
            c.setFont("Helvetica-Bold", 12)
            c.drawString(nx + 12, ntop - 19, "NOTE")
            c.setFillColor(colors.black)
            c.setFont("Helvetica", 10.5)
            for k, t in enumerate(body):
                c.drawString(nx + 12, ntop - 36 - 14 * k, t)
            if nx == M:
                ty = ntop - nh

        # Door lists as tables: Door | Location.
        dy = ty - 0.5 * inch
        blocks = [(b, dl) for b, dl in door_lists.items() if dl.get("rows")]
        bw = (PW - 2 * M) / max(1, len(blocks)) if blocks else 0
        for j, (b, dl) in enumerate(blocks):
            x = M + j * bw
            loc_x = x + min(4.1 * inch, bw * 0.66)
            yy = dy
            c.setFillColor(colors.black)
            c.setFont("Helvetica-Bold", 11 if small else 12)
            c.drawString(x, yy, dl.get("title", "Door List"))
            yy -= 17
            c.setFont("Helvetica-Bold", 9)
            c.drawString(x, yy, "Door")
            c.drawString(loc_x, yy, "Location")
            yy -= 4
            c.setStrokeColor(colors.HexColor("#999999"))
            c.line(x, yy, x + bw - 0.25 * inch, yy)
            yy -= 12
            rows = dl["rows"]
            step = max(8.0, min(12.5, (yy - M - 10) / max(1, len(rows))))
            for tag, line in rows:
                loc = row_location(b, tag)
                c.setFillColor(colors.black if loc else colors.HexColor("#8A8A8A"))
                c.setFont("Helvetica", min(9.5, step * 0.78))
                c.drawString(x, yy, line if len(line) < 60 else line[:59] + "...")
                c.drawString(loc_x, yy, loc or "Not on plan")
                yy -= step
            c.setFillColor(colors.black)
        footer(page_no)
        c.showPage()
        page_no += 1

    # ------------------------------------------------------------ floors
    for pg in pages:
        bld = pg.get("building", "")
        head = f"{bld} - {pg.get('short') or pg['name']}" if bld else pg["name"]
        header(head, "Device locations")
        iw, ih = pg["size"]
        k = min(map_w / iw, map_h / ih)
        dw, dh = iw * k, ih * k
        mx = M + (map_w - dw) / 2
        my = M + (map_h - dh) / 2 + 0.1 * inch
        c.drawImage(pg["image"], mx, my, width=dw, height=dh)
        c.setStrokeColor(colors.HexColor("#BBBBBB"))
        c.rect(mx, my, dw, dh, stroke=1, fill=0)

        px = PW - M - PANEL_W
        c.setFillColor(colors.HexColor("#F3F5F8"))
        c.rect(px - 12, M, PANEL_W + 12, PH - 2 * M - TITLE_H, stroke=0, fill=1)
        x, y = px, PH - M - TITLE_H - 0.2 * inch
        c.setFillColor(colors.black)
        s = 18 if small else 22
        fs = 9.5 if small else 11
        c.setFillColor(colors.black)
        c.setFont("Helvetica-Bold", 13 if small else 15)
        c.drawString(x, y - 15, "KEY")
        y -= 38
        by_cat = collections.defaultdict(list)
        for code in pg["counts"]:
            by_cat[categories.get(code, "Other")].append(code)
        for cat in sorted(by_cat):
            c.setFont("Helvetica-Bold", fs - 0.5)
            c.setFillColor(colors.HexColor("#1F3B63"))
            c.drawString(x, y, cat.upper())
            y -= s - 2
            for code in sorted(by_cat[cat], key=lambda kk: symbols.get(kk, {}).get("label", kk)):
                _icon(c, symbols.get(code, {}), code, x + s / 2, y + 3, s)
                c.setFillColor(colors.black)
                c.setFont("Helvetica", fs)
                lab = symbols.get(code, {}).get("label", code)
                maxw = PANEL_W - s - 40
                while c.stringWidth(lab, "Helvetica", fs) > maxw and len(lab) > 4:
                    lab = lab[:-4] + "..."
                c.drawString(x + s + 10, y - 1, lab)
                c.setFont("Helvetica-Bold", fs)
                c.drawRightString(x + PANEL_W - 6, y - 1, str(pg["counts"][code]))
                y -= s + 6
            y -= 2
        c.setStrokeColor(colors.HexColor("#999999"))
        c.line(x, y + 8, x + PANEL_W - 6, y + 8)
        c.setFont("Helvetica-Bold", fs)
        c.drawString(x, y - 8, "Total devices on this floor")
        c.drawRightString(x + PANEL_W - 6, y - 8, str(sum(pg["counts"].values())))
        y -= 30

        # Building-wide counts of the tagged device types, so badge numbers
        # that continue onto another floor don't look like missing devices.
        bpages = [p for p in pages if p.get("building", "") == bld]
        tagged_types = sorted({t for p in bpages for t in p.get("tags", {}).values()})
        if len(bpages) > 1:
            for tt in tagged_types:
                tot = sum(p["counts"].get(tt, 0) for p in bpages)
                lab = symbols.get(tt, {}).get("label", tt)
                c.setFont("Helvetica-Bold", fs)
                c.drawString(x, y, f"{lab}s - whole building"[:40])
                c.drawRightString(x + PANEL_W - 6, y, str(tot))
                y -= 13
                c.setFont("Helvetica", fs - 1.5)
                c.setFillColor(colors.HexColor("#444444"))
                parts = [f"{p['counts'].get(tt, 0)} on {p.get('short') or p['name']}"
                         + (" (this page)" if p is pg else "") for p in bpages]
                for t in simpleSplit(", ".join(parts), "Helvetica", fs - 1.5, PANEL_W - 10):
                    c.drawString(x + 4, y, t)
                    y -= 12
                c.setFillColor(colors.black)
                y -= 12

        dl = door_lists.get(bld)
        here = sorted(pg.get("tags", {}), key=lambda t: (not t.isdigit(), int(t) if t.isdigit() else 0, t))
        if here or dl:
            # Which numbers are on this page, and where the rest are.
            msg1 = f"{len(here)} numbered device(s) on this page: {number_ranges(here)}."
            missing = []
            if dl:
                for tag, _line in dl["rows"]:
                    if tag not in pg.get("tags", {}):
                        loc = row_location(bld, tag)
                        missing.append(f"#{tag} ({loc or 'not on plan'})")
            msg2 = ("Not on this page: " + ", ".join(missing) + ".") if missing else ""
            l1 = simpleSplit(msg1, "Helvetica-Bold", fs - 1, PANEL_W - 14)
            l2 = simpleSplit(msg2, "Helvetica", fs - 1, PANEL_W - 14) if msg2 else []
            boxh = 13 * (len(l1) + len(l2)) + 10
            c.setFillColor(colors.HexColor("#FFF6D6"))
            c.rect(x - 2, y - boxh + 12, PANEL_W - 2, boxh, stroke=0, fill=1)
            c.setFillColor(colors.black)
            c.setFont("Helvetica-Bold", fs - 1)
            for t in l1:
                c.drawString(x + 4, y, t)
                y -= 13
            c.setFont("Helvetica", fs - 1)
            for t in l2:
                c.drawString(x + 4, y, t)
                y -= 13
            y -= 16
            c.setFillColor(colors.HexColor("#111111"))
            c.circle(x + s / 2, y + 3, s * 0.45, stroke=0, fill=1)
            c.setFillColor(colors.white)
            c.setFont("Helvetica-Bold", 10)
            c.drawCentredString(x + s / 2, y - 0.5, "#")
            c.setFillColor(colors.black)
            c.setFont("Helvetica", fs)
            c.drawString(x + s + 10, y - 1, f"{tag_label} (see list)" if dl else tag_label)
            y -= 28
        if dl:
            rows = [line for tag, line in dl["rows"] if tag in pg.get("tags", {})]
            ttl = dl.get("title", "Door List")
            ttl = ttl[: -len("door list")].strip() + " Doors on this page" if ttl.lower().endswith("door list") else ttl
            c.setFont("Helvetica-Bold", fs + 1)
            c.drawString(x, y, ttl[:44])
            y -= 17
            if rows:
                lh = min(14, max(8.0, (y - M - 6) / len(rows)))
                c.setFont("Helvetica", min(fs, lh * 0.8))
                for line in rows:
                    c.drawString(x + 4, y, line if len(line) < 52 else line[:51] + "...")
                    y -= lh
            else:
                c.setFont("Helvetica-Oblique", fs - 1)
                c.drawString(x + 4, y, "(none)")
        footer(page_no)
        c.showPage()
        page_no += 1
    c.save()
