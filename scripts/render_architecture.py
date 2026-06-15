#!/usr/bin/env python3
"""Render architecture.drawio → architecture.png.

The .drawio file is the source of truth for the system architecture diagram.
This script parses the mxGraphModel XML and emits a PNG via an in-memory SVG
intermediate (cairosvg).

Run from the repo root:

    python3 scripts/render_architecture.py

Requires: cairosvg (`pip install cairosvg`).
"""

import base64
import html
import re
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote

import cairosvg

ROOT = Path(__file__).resolve().parent.parent
DRAWIO = ROOT / "architecture.drawio"
PNG = ROOT / "architecture.png"


# -------- style parsing --------------------------------------------------- #

def parse_style(s: str) -> dict:
    """Parse a drawio style string. The first bare token (no '=') is the shape
    name. Special-cases 'image=data:...;base64,...' values whose value contains
    a literal ';'."""
    out: dict = {}
    if not s:
        return out
    i = 0
    first_token = True
    while i < len(s):
        # find next ';' that isn't inside a data: URI
        j = i
        while j < len(s) and s[j] != ";":
            j += 1
        part = s[i:j].strip()
        if part:
            if "=" in part:
                k, v = part.split("=", 1)
                k = k.strip()
                v = v.strip()
                if k == "image" and v.startswith("data:") and ";base64," not in v:
                    # If the image value is data:image/svg+xml followed by ;base64,..,
                    # the previous loop ended before 'base64'. Re-extend.
                    if j < len(s) and s[j:j+8] == ";base64,":
                        # consume up to the next ';' or end
                        k2 = j + 1
                        while k2 < len(s) and s[k2] != ";":
                            k2 += 1
                        v = v + s[j:k2]
                        j = k2
                out[k] = v
            else:
                if first_token:
                    out["shape"] = part
                out[f"_shape_{part}"] = "1"
        first_token = False
        i = j + 1
    return out


def is_text_shape(style: dict) -> bool:
    return style.get("shape") == "text" or "_shape_text" in style


def is_image_shape(style: dict) -> bool:
    return style.get("shape") == "image"


def decode_data_uri_svg(uri: str) -> str | None:
    """Return raw SVG text from a data:image/svg+xml URI (base64 or url-encoded)."""
    if not uri:
        return None
    base64_prefix = "data:image/svg+xml;base64,"
    plain_prefix = "data:image/svg+xml,"
    if uri.startswith(base64_prefix):
        try:
            return base64.b64decode(uri[len(base64_prefix):]).decode("utf-8")
        except Exception:
            return None
    if uri.startswith(plain_prefix):
        return unquote(uri[len(plain_prefix):])
    return None


def inline_svg(svg_text: str, x: float, y: float, w: float, h: float) -> str:
    """Inline an SVG (extracted from a data URI) at (x,y) with size (w,h)."""
    m = re.search(r"<svg([^>]*)>(.*)</svg>", svg_text, re.DOTALL)
    if not m:
        return ""
    head = m.group(1)
    body = m.group(2)
    vb_m = re.search(r"viewBox\s*=\s*['\"]([^'\"]+)['\"]", head)
    if vb_m:
        vb = vb_m.group(1).split()
        vw, vh = float(vb[2]), float(vb[3])
    else:
        vw, vh = 64.0, 64.0
    sx = w / vw
    sy = h / vh
    return f'<g transform="translate({x:.1f},{y:.1f}) scale({sx:.3f},{sy:.3f})">{body}</g>'


# -------- HTML label parsing ---------------------------------------------- #

class HTMLLines(HTMLParser):
    """Convert drawio's HTML labels into a list of (text, style-dict) lines."""

    def __init__(self):
        super().__init__()
        self.lines: list[tuple[str, dict]] = []
        self.current = ""
        self.style_stack: list[dict] = [
            {"fontSize": 12.0, "color": None, "bold": False, "italic": False, "marginTop": 0.0}
        ]

    def _cur(self) -> dict:
        return dict(self.style_stack[-1])

    def _flush(self):
        if self.current.strip():
            self.lines.append((self.current.strip(), self._cur()))
        elif self.current:  # preserve empty line for spacing
            self.lines.append(("", self._cur()))
        self.current = ""

    def handle_starttag(self, tag, attrs):
        new = self._cur()
        new["marginTop"] = 0.0
        attrs_d = dict(attrs)

        if tag in ("b", "strong"):
            new["bold"] = True
        if tag in ("i", "em"):
            new["italic"] = True
        if tag == "br":
            self._flush()
            return
        if tag == "div":
            self._flush()

        style_str = attrs_d.get("style", "")
        for prop in style_str.split(";"):
            if ":" not in prop:
                continue
            k, v = prop.split(":", 1)
            k = k.strip()
            v = v.strip()
            if k == "font-size":
                m = re.match(r"(\d+(?:\.\d+)?)", v)
                if m:
                    new["fontSize"] = float(m.group(1))
            elif k == "color":
                new["color"] = v
            elif k == "font-weight":
                if v in ("700", "800", "bold"):
                    new["bold"] = True
            elif k == "font-style" and v == "italic":
                new["italic"] = True
            elif k == "margin-top":
                m = re.match(r"(\d+(?:\.\d+)?)", v)
                if m:
                    new["marginTop"] = float(m.group(1))
            elif k == "letter-spacing":
                m = re.match(r"(\d+(?:\.\d+)?)", v)
                if m:
                    new["letterSpacing"] = float(m.group(1))
        self.style_stack.append(new)

    def handle_endtag(self, tag):
        if tag == "br":
            return
        if tag == "div":
            self._flush()
        if len(self.style_stack) > 1:
            self.style_stack.pop()

    def handle_data(self, data):
        self.current += data

    def finish(self):
        self._flush()
        return self.lines


def parse_label(label: str) -> list[tuple[str, dict]]:
    if not label:
        return []
    # Unescape drawio's stored HTML entities (already part of XML text)
    if "<" in label or "&lt;" in label:
        p = HTMLLines()
        p.feed(label)
        return p.finish()
    # Plain text, possibly with explicit newlines
    return [(l, {"fontSize": 12.0, "color": None, "bold": False, "italic": False, "marginTop": 0.0})
            for l in label.split("\n")]


# -------- vertex rendering ------------------------------------------------- #

def render_vertex(cell, geo, style: dict,
                  parent_origin: tuple[float, float] = (0.0, 0.0)) -> tuple[str, tuple[float, float, float, float]]:
    x = float(geo.get("x", 0)) + parent_origin[0]
    y = float(geo.get("y", 0)) + parent_origin[1]
    w = float(geo.get("width", 100))
    h = float(geo.get("height", 50))

    # Image shape: inline the embedded SVG (used for our brand icons)
    if is_image_shape(style):
        img = style.get("image", "")
        svg_text = decode_data_uri_svg(img)
        if svg_text:
            return inline_svg(svg_text, x, y, w, h), (x, y, w, h)
        return "", (x, y, w, h)

    parts: list[str] = []

    if not is_text_shape(style):
        fill = style.get("fillColor", "#FFFFFF")
        stroke = style.get("strokeColor", "#000000")
        sw = style.get("strokeWidth", "1")
        rx = "14" if style.get("rounded") == "1" else "0"
        dash = ""
        if style.get("dashed") == "1":
            pat = style.get("dashPattern", "4 4").replace(" ", ",")
            dash = f' stroke-dasharray="{pat}"'
        shadow = ' filter="url(#cardShadow)"' if style.get("shadow") == "1" else ""
        parts.append(
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="{sw}"{dash}{shadow}/>'
        )

    label = cell.get("value", "") or ""
    if label:
        lines = parse_label(label)
        parts.append(_render_text_block(lines, x, y, w, h, style))

    return "\n".join(parts), (x, y, w, h)


def _render_text_block(lines, x, y, w, h, style):
    sl = float(style.get("spacingLeft", 0))
    st = float(style.get("spacingTop", 0))
    sr = float(style.get("spacingRight", sl))
    sb = float(style.get("spacingBottom", st))
    align = style.get("align", "center")
    valign = style.get("verticalAlign", "middle")
    base_fs = float(style.get("fontSize", 12))
    base_color = style.get("fontColor", "#0f172a")
    fs_style = style.get("fontStyle", "0")
    base_bold = fs_style in ("1", "3", "5", "7")
    base_italic = fs_style in ("2", "3", "6", "7")

    inner_x = x + sl
    inner_y = y + st
    inner_w = w - sl - sr
    inner_h = h - st - sb

    # Pre-compute line heights
    heights = []
    for _, ls in lines:
        fs = ls.get("fontSize") or base_fs
        mt = ls.get("marginTop", 0)
        heights.append(fs * 1.25 + mt)
    total = sum(heights)

    if valign == "top":
        start_y = inner_y
    elif valign == "bottom":
        start_y = inner_y + inner_h - total
    else:
        start_y = inner_y + (inner_h - total) / 2

    out = []
    cur_y = start_y
    for (text, ls), lh in zip(lines, heights):
        fs = ls.get("fontSize") or base_fs
        mt = ls.get("marginTop", 0)
        color = ls.get("color") or base_color
        bold = ls.get("bold", base_bold)
        italic = ls.get("italic", base_italic)
        ls_attr = ""
        if ls.get("letterSpacing"):
            ls_attr = f' letter-spacing="{ls["letterSpacing"]}"'
        weight = "700" if bold else "400"
        style_attr = ' font-style="italic"' if italic else ""

        cur_y += mt
        baseline = cur_y + fs * 0.95

        if align == "right":
            tx = inner_x + inner_w
            anchor = "end"
        elif align == "left":
            tx = inner_x
            anchor = "start"
        else:
            tx = inner_x + inner_w / 2
            anchor = "middle"

        if text:
            out.append(
                f'<text x="{tx:.1f}" y="{baseline:.1f}" font-size="{fs}" '
                f'fill="{color}" font-weight="{weight}" text-anchor="{anchor}"'
                f'{style_attr}{ls_attr}>{html.escape(text)}</text>'
            )
        cur_y += lh - mt

    return "\n".join(out)


# -------- edge rendering --------------------------------------------------- #

def _infer_dir(ex, ey):
    """Determine if the line leaves/enters horizontally or vertically."""
    if ex in (0, 1) and ey not in (0, 1):
        return "h"
    if ey in (0, 1) and ex not in (0, 1):
        return "v"
    return "h" if ex in (0, 1) else "v"


def _ortho_path(p1, p2, ed, nd):
    x1, y1 = p1
    x2, y2 = p2
    if ed == "h" and nd == "h":
        mx = (x1 + x2) / 2
        return [(x1, y1), (mx, y1), (mx, y2), (x2, y2)]
    if ed == "v" and nd == "v":
        my = (y1 + y2) / 2
        return [(x1, y1), (x1, my), (x2, my), (x2, y2)]
    if ed == "h" and nd == "v":
        return [(x1, y1), (x2, y1), (x2, y2)]
    return [(x1, y1), (x1, y2), (x2, y2)]


def _polyline_midpoint(points):
    seg_lens = []
    total = 0.0
    for i in range(len(points) - 1):
        dx = points[i + 1][0] - points[i][0]
        dy = points[i + 1][1] - points[i][1]
        l = (dx * dx + dy * dy) ** 0.5
        seg_lens.append(l)
        total += l
    half = total / 2
    acc = 0.0
    for i, l in enumerate(seg_lens):
        if acc + l >= half and l > 0:
            t = (half - acc) / l
            x = points[i][0] + (points[i + 1][0] - points[i][0]) * t
            y = points[i][1] + (points[i + 1][1] - points[i][1]) * t
            return (x, y)
        acc += l
    return points[-1]


def render_edge(cell, style: dict, geo_lookup: dict) -> str:
    source = cell.get("source")
    target = cell.get("target")
    stroke = style.get("strokeColor", "#000000")
    sw = style.get("strokeWidth", "2")

    if source and target and source in geo_lookup and target in geo_lookup:
        ex = float(style.get("exitX", 0.5))
        ey = float(style.get("exitY", 0.5))
        nx = float(style.get("entryX", 0.5))
        ny = float(style.get("entryY", 0.5))
        sx, sy, sw_, sh_ = geo_lookup[source]
        tx, ty, tw_, th_ = geo_lookup[target]
        p1 = (sx + sw_ * ex, sy + sh_ * ey)
        p2 = (tx + tw_ * nx, ty + th_ * ny)
        points = _ortho_path(p1, p2, _infer_dir(ex, ey), _infer_dir(nx, ny))
    else:
        geo_el = cell.find("mxGeometry")
        sp = geo_el.find("mxPoint[@as='sourcePoint']") if geo_el is not None else None
        tp = geo_el.find("mxPoint[@as='targetPoint']") if geo_el is not None else None
        if sp is None or tp is None:
            return ""
        points = [(float(sp.get("x")), float(sp.get("y"))),
                  (float(tp.get("x")), float(tp.get("y")))]

    d = "M " + " L ".join(f"{p[0]:.1f},{p[1]:.1f}" for p in points)
    marker_id = f"arrow-{stroke.lstrip('#')}"
    parts = [
        f'<path d="{d}" stroke="{stroke}" stroke-width="{sw}" fill="none" '
        f'marker-end="url(#{marker_id})" stroke-linejoin="round"/>'
    ]

    label = cell.get("value") or ""
    if label:
        mid = _polyline_midpoint(points)
        font_size = float(style.get("fontSize", 11))
        font_color = style.get("fontColor", stroke)
        bold = style.get("fontStyle") in ("1", "3", "5", "7")
        weight = "700" if bold else "400"
        bg = style.get("labelBackgroundColor", "")

        lines = label.split("\n")
        lh = font_size * 1.25
        total_h = lh * len(lines)
        max_chars = max(len(l) for l in lines)
        max_w = max_chars * font_size * 0.55

        if bg:
            parts.append(
                f'<rect x="{mid[0] - max_w / 2 - 4:.1f}" '
                f'y="{mid[1] - total_h / 2 - 2:.1f}" '
                f'width="{max_w + 8:.1f}" height="{total_h + 4:.1f}" '
                f'fill="{bg}" rx="3" ry="3"/>'
            )

        baseline_y = mid[1] - total_h / 2 + font_size * 0.95
        for i, line in enumerate(lines):
            parts.append(
                f'<text x="{mid[0]:.1f}" y="{baseline_y + i * lh:.1f}" '
                f'font-size="{font_size}" fill="{font_color}" '
                f'font-weight="{weight}" text-anchor="middle">{html.escape(line)}</text>'
            )

    return "\n".join(parts)


# -------- main ------------------------------------------------------------- #

def main():
    tree = ET.parse(DRAWIO)
    model = tree.getroot().find(".//mxGraphModel")
    page_w = int(model.get("pageWidth", 1700))
    page_h = int(model.get("pageHeight", 1050))

    vertex_svg: list[str] = []
    edge_jobs: list[tuple] = []
    geo_lookup: dict = {}
    edge_colors: set = set()

    # Collect all cells, ordered so parents render before children
    all_cells = list(model.findall(".//mxCell"))
    cell_by_id = {c.get("id"): c for c in all_cells if c.get("id")}

    def absolute_origin(cell):
        """Walk up the parent chain to get the (x,y) origin of a cell."""
        ox, oy = 0.0, 0.0
        node = cell
        while True:
            parent_id = node.get("parent")
            if not parent_id or parent_id in ("0", "1"):
                break
            parent = cell_by_id.get(parent_id)
            if parent is None or parent.get("vertex") != "1":
                break
            pgeo = parent.find("mxGeometry")
            if pgeo is not None:
                ox += float(pgeo.get("x", 0))
                oy += float(pgeo.get("y", 0))
            node = parent
        return (ox, oy)

    # First pass: render parents (containers), then children
    ordered = sorted(all_cells, key=lambda c: 0 if c.get("parent") in (None, "0", "1") else 1)

    for cell in ordered:
        cid = cell.get("id")
        if cid in (None, "0", "1"):
            continue
        style = parse_style(cell.get("style", ""))

        if cell.get("vertex") == "1":
            geo = cell.find("mxGeometry")
            if geo is None:
                continue
            origin = absolute_origin(cell)
            svg, g = render_vertex(cell, geo, style, parent_origin=origin)
            vertex_svg.append(svg)
            geo_lookup[cid] = g
        elif cell.get("edge") == "1":
            edge_jobs.append((cell, style))
            edge_colors.add(style.get("strokeColor", "#000000"))

    edge_svg = [render_edge(c, s, geo_lookup) for c, s in edge_jobs]

    markers = "\n".join(
        f'<marker id="arrow-{c.lstrip("#")}" viewBox="0 0 10 10" '
        f'refX="9" refY="5" markerWidth="6" markerHeight="6" '
        f'orient="auto-start-reverse">'
        f'<path d="M0,0 L10,5 L0,10 z" fill="{c}"/></marker>'
        for c in edge_colors
    )

    svg_out = f"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {page_w} {page_h}" font-family="Inter, system-ui, -apple-system, Helvetica, Arial, sans-serif">
<defs>
<filter id="cardShadow" x="-20%" y="-20%" width="140%" height="140%">
<feDropShadow dx="0" dy="2" stdDeviation="3" flood-color="#0f172a" flood-opacity="0.08"/>
</filter>
{markers}
</defs>
<rect width="{page_w}" height="{page_h}" fill="#FFFFFF"/>
{chr(10).join(vertex_svg)}
{chr(10).join(edge_svg)}
</svg>"""

    cairosvg.svg2png(
        bytestring=svg_out.encode("utf-8"),
        write_to=str(PNG),
        output_width=2400,
    )
    print(f"Wrote {PNG}")


if __name__ == "__main__":
    main()
