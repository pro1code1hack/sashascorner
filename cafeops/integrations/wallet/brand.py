"""Rasterise the brand line-art (`site/web/public/brand/*.svg`) with Pillow alone.

Wallet artwork must be PNG, and the usual SVG rasterisers (cairosvg, rsvg) need system
libraries the slim runtime image does not carry. The brand files are simple enough not
to need them: every shape is one `<path>` of absolute M/L/H/V/C/Z commands under a
single `matrix(1,0,0,-1,1,870.3232)` flip, filled flat. So this flattens the Béziers to
polygons, fills each subpath, and XORs them (even-odd -- which is how the counters of
the letters and the cup's inner line come out hollow), supersampled for anti-aliasing.

It is used at *asset-generation* time (`cafeops wallet assets`); the output PNGs are
committed under `assets/pass/`, so production never needs the site tree. If the brand
files ever start using arcs, relative commands or strokes, `parse_path` raises rather
than drawing something subtly wrong.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from cafeops.config import REPO_ROOT

BRAND_DIR = REPO_ROOT / "site" / "web" / "public" / "brand"

_TOKEN = re.compile(r"[MLHVCZmlhvcz]|-?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
_MATRIX = re.compile(r"matrix\(([^)]*)\)")
_VIEWBOX = re.compile(r'viewBox="([^"]+)"')
_PATH = re.compile(r"<path\b([^>]*)>", re.S)
_ATTR = re.compile(r'([a-zA-Z:-]+)="([^"]*)"')

Point = tuple[float, float]


@dataclass(frozen=True)
class Shape:
    subpaths: tuple[tuple[Point, ...], ...]
    fill: str | None


def _cubic(p0: Point, p1: Point, p2: Point, p3: Point, steps: int = 12) -> list[Point]:
    out: list[Point] = []
    for i in range(1, steps + 1):
        t = i / steps
        u = 1 - t
        a, b, c, d = u * u * u, 3 * u * u * t, 3 * u * t * t, t * t * t
        out.append(
            (
                a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0],
                a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1],
            )
        )
    return out


def parse_path(d: str) -> list[list[Point]]:
    tokens = _TOKEN.findall(d)
    subpaths: list[list[Point]] = []
    current: list[Point] = []
    pos: Point = (0.0, 0.0)
    cmd = ""
    i = 0

    def num() -> float:
        nonlocal i
        value = float(tokens[i])
        i += 1
        return value

    while i < len(tokens):
        tok = tokens[i]
        if tok.isalpha():
            if tok.islower() and tok != "z":
                raise ValueError(f"relative path command {tok!r} not supported")
            cmd = tok.upper()
            i += 1
            if cmd == "Z":
                if current:
                    subpaths.append(current)
                current = []
                continue
        # Implicit repetition: numbers after a command repeat it (M repeats as L).
        if cmd == "M":
            if current:
                subpaths.append(current)
            pos = (num(), num())
            current = [pos]
            cmd = "L"
        elif cmd == "L":
            pos = (num(), num())
            current.append(pos)
        elif cmd == "H":
            pos = (num(), pos[1])
            current.append(pos)
        elif cmd == "V":
            pos = (pos[0], num())
            current.append(pos)
        elif cmd == "C":
            p1 = (num(), num())
            p2 = (num(), num())
            p3 = (num(), num())
            current.extend(_cubic(pos, p1, p2, p3))
            pos = p3
        else:
            raise ValueError(f"path command {cmd!r} not supported")
    if current:
        subpaths.append(current)
    return subpaths


def load_svg(path: Path) -> tuple[tuple[float, float, float, float], list[Shape]]:
    text = path.read_text(encoding="utf-8")
    vb_match = _VIEWBOX.search(text)
    if vb_match is None:
        raise ValueError(f"{path}: no viewBox")
    vx, vy, vw, vh = (float(v) for v in vb_match.group(1).split())
    shapes: list[Shape] = []
    for attrs_raw in _PATH.findall(text):
        attrs = dict(_ATTR.findall(attrs_raw))
        a, b, c, dd, e, f = 1.0, 0.0, 0.0, 1.0, 0.0, 0.0
        if "transform" in attrs:
            m = _MATRIX.search(attrs["transform"])
            if m is None:
                raise ValueError(f"{path}: unsupported transform {attrs['transform']!r}")
            a, b, c, dd, e, f = (float(v) for v in re.split(r"[ ,]+", m.group(1).strip()))
        subs = tuple(
            tuple((a * x + c * y + e, b * x + dd * y + f) for x, y in sp)
            for sp in parse_path(attrs.get("d", ""))
        )
        shapes.append(Shape(subpaths=subs, fill=attrs.get("fill")))
    return (vx, vy, vw, vh), shapes


def render_mark(
    svg: Path,
    size: tuple[int, int],
    colour: str,
    *,
    padding: float = 0.0,
    thicken: float = 0.0,
    supersample: int = 4,
) -> Image.Image:
    """The SVG's shapes in one flat colour, fitted (aspect kept) and centred in `size`.

    Fits the *drawn* bounds, not the viewBox: the brand files carry generous margins
    that would shrink a 29-pt icon to a smudge.

    `thicken` (output pixels) dilates the line: the art is drawn for a 400-unit canvas,
    and at icon size its hairline would drop below one pixel and break up.
    """
    _vb, shapes = load_svg(svg)
    pts = [p for s in shapes for sp in s.subpaths for p in sp]
    min_x, max_x = min(p[0] for p in pts), max(p[0] for p in pts)
    min_y, max_y = min(p[1] for p in pts), max(p[1] for p in pts)
    w, h = size
    big_w, big_h = w * supersample, h * supersample
    inner_w, inner_h = big_w * (1 - 2 * padding), big_h * (1 - 2 * padding)
    scale = min(inner_w / (max_x - min_x), inner_h / (max_y - min_y))
    off_x = (big_w - (max_x - min_x) * scale) / 2 - min_x * scale
    off_y = (big_h - (max_y - min_y) * scale) / 2 - min_y * scale

    mask = Image.new("L", (big_w, big_h), 0)
    for shape in shapes:
        shape_mask = Image.new("L", (big_w, big_h), 0)
        for sp in shape.subpaths:
            if len(sp) < 3:
                continue
            layer = Image.new("L", (big_w, big_h), 0)
            ImageDraw.Draw(layer).polygon(
                [(x * scale + off_x, y * scale + off_y) for x, y in sp], fill=255
            )
            shape_mask = ImageChops.difference(shape_mask, layer)  # XOR on 0/255
        mask = ImageChops.lighter(mask, shape_mask)
    grow = round(thicken * supersample)
    if grow > 0:
        mask = mask.filter(ImageFilter.MaxFilter(2 * grow + 1))
    mask = mask.resize((w, h), Image.Resampling.LANCZOS)
    out = Image.new("RGBA", (w, h), colour)
    out.putalpha(mask)
    return out


__all__ = ["BRAND_DIR", "load_svg", "parse_path", "render_mark"]
