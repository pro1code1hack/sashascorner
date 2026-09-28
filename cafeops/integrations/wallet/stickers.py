"""The stamp stickers: one per slot, plus the free-drink one, drawn by us.

**Canonical source: `assets/pass/stickers/*.svg`** (slot-1.svg ... slot-8.svg,
reward.svg). Hand-written SVG, 64x64 viewBox. Everything that draws a stamp reads
those files or a copy of them:

* the Apple strip / Google heroImage PNGs (`strips.py`, rendered here with Pillow);
* the public site (`site/web/public/stickers/`) -- the web card, the /rewards sample
  card and the staff scanner. The site's Docker build context is `site/`, so it
  cannot reach `assets/`; it gets a copy;
* the dashboard (`web/public/stickers/`) for the Members card, same reason.

The copies are written by `cafeops wallet assets` (`sync_copies`), and
`cafeops wallet doctor` says when one has drifted. Edit the files in
`assets/pass/stickers/`, never a copy.

Slot order is fixed: slot i (1-based) always shows `slot-<((i-1) % 8) + 1>.svg`, so
every card shows the same sticker in the same place and a strip image is a pure
function of (stamps, required, reward) -- which is what lets it be cached forever.

**The renderer** is deliberately small (no cairo/rsvg in the slim runtime image): it
understands exactly the subset the stickers use -- `<g>`, `<path>` (M L H V C S Q Z,
absolute or relative, no arcs), `<circle>`, `<ellipse>`, `<rect>` and `<line>`;
`transform` (matrix/translate/scale/rotate); `fill`, `stroke`, `stroke-width`,
`opacity`, `fill-rule`. Round caps and joins always (the art uses nothing else).
Anything else raises rather than drawing something subtly different from the browser.
Colours must come from `PALETTE` -- the site's brand tokens, the pass colours and the
white of the die-cut -- so a stray hex fails loudly here instead of shipping.
"""

from __future__ import annotations

import hashlib
import math
import re
import shutil
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

from cafeops.config import REPO_ROOT
from cafeops.integrations.wallet.config import ASSETS_DIR

STICKER_DIR = ASSETS_DIR / "stickers"
#: Where the copies go (see the module docstring for why copies exist at all).
COPY_DIRS = (
    REPO_ROOT / "site" / "web" / "public" / "stickers",
    REPO_ROOT / "web" / "public" / "stickers",
)
SLOT_STICKERS = 8
REWARD = "reward.svg"
NAMES = (*(f"slot-{i}.svg" for i in range(1, SLOT_STICKERS + 1)), REWARD)

#: site/BRIEF.md tokens + the pass colours (SPEC "Wallet passes") + die-cut white.
PALETTE = frozenset(
    {
        "#0d0d0b",  # ink
        "#474531",  # olive-900 / pass foreground
        "#666749",  # olive-700
        "#899c6a",  # sage
        "#9b6038",  # coffee / pass label
        "#d4884e",  # caramel
        "#f3f1eb",  # paper
        "#ebe6dd",  # oat
        "#e9dcd6",  # blush / pass background
        "#ffffff",  # the die-cut
    }
)

SVG_NS = "{http://www.w3.org/2000/svg}"
Point = tuple[float, float]
Matrix = tuple[float, float, float, float, float, float]  # a b c d e f, as SVG's matrix()
IDENTITY: Matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def slot_sticker(slot: int) -> str:
    """File name of the sticker for 0-based slot index `slot`."""
    return NAMES[slot % SLOT_STICKERS]


# ---- parsing -------------------------------------------------------------------------

_NUM = r"-?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"
_TOKEN = re.compile(rf"[A-Za-z]|{_NUM}")
_TRANSFORM = re.compile(r"(matrix|translate|scale|rotate)\s*\(([^)]*)\)")


def _mul(m: Matrix, n: Matrix) -> Matrix:
    """m * n (apply n first, then m)."""
    a, b, c, d, e, f = m
    a2, b2, c2, d2, e2, f2 = n
    return (
        a * a2 + c * b2,
        b * a2 + d * b2,
        a * c2 + c * d2,
        b * c2 + d * d2,
        a * e2 + c * f2 + e,
        b * e2 + d * f2 + f,
    )


def _apply(m: Matrix, p: Point) -> Point:
    a, b, c, d, e, f = m
    return (a * p[0] + c * p[1] + e, b * p[0] + d * p[1] + f)


def parse_transform(text: str | None) -> Matrix:
    m = IDENTITY
    if not text:
        return m
    consumed = _TRANSFORM.sub("", text).replace(",", " ").strip()
    if consumed:
        raise ValueError(f"unsupported transform {text!r}")
    for name, raw in _TRANSFORM.findall(text):
        v = [float(x) for x in re.findall(_NUM, raw)]
        if name == "matrix" and len(v) == 6:
            t: Matrix = (v[0], v[1], v[2], v[3], v[4], v[5])
        elif name == "translate" and len(v) in (1, 2):
            t = (1, 0, 0, 1, v[0], v[1] if len(v) == 2 else 0.0)
        elif name == "scale" and len(v) in (1, 2):
            t = (v[0], 0, 0, v[1] if len(v) == 2 else v[0], 0, 0)
        elif name == "rotate" and len(v) in (1, 3):
            r = math.radians(v[0])
            cos, sin = math.cos(r), math.sin(r)
            t = (cos, sin, -sin, cos, 0, 0)
            if len(v) == 3:
                cx, cy = v[1], v[2]
                t = _mul((1, 0, 0, 1, cx, cy), _mul(t, (1, 0, 0, 1, -cx, -cy)))
        else:
            raise ValueError(f"bad transform {name}({raw})")
        m = _mul(m, t)
    return m


def _cubic(p0: Point, p1: Point, p2: Point, p3: Point, steps: int = 16) -> list[Point]:
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


@dataclass
class Subpath:
    points: list[Point]
    closed: bool


def parse_path(d: str) -> list[Subpath]:
    """Absolute and relative M L H V C S Q Z. Arcs and T raise."""
    tokens = _TOKEN.findall(d)
    out: list[Subpath] = []
    cur: Subpath | None = None
    pos: Point = (0.0, 0.0)
    start: Point = (0.0, 0.0)
    last_ctrl: Point | None = None  # for S
    cmd = ""
    i = 0

    def num() -> float:
        nonlocal i
        value = float(tokens[i])
        i += 1
        return value

    def pt(rel: bool) -> Point:
        x, y = num(), num()
        return (pos[0] + x, pos[1] + y) if rel else (x, y)

    while i < len(tokens):
        tok = tokens[i]
        if tok.isalpha():
            cmd = tok
            i += 1
            if cmd in "Zz":
                if cur is not None:
                    cur.closed = True
                    out.append(cur)
                    cur = None
                pos = start
                last_ctrl = None
                continue
        rel = cmd.islower()
        c = cmd.upper()
        if c == "M":
            if cur is not None:
                out.append(cur)
            pos = pt(rel)
            start = pos
            cur = Subpath([pos], closed=False)
            cmd = "l" if rel else "L"  # implicit repeats are line-tos
            last_ctrl = None
            continue
        if cur is None:  # a drawing command straight after Z continues from `start`
            cur = Subpath([pos], closed=False)
        if c == "L":
            pos = pt(rel)
            cur.points.append(pos)
            last_ctrl = None
        elif c == "H":
            x = num()
            pos = (pos[0] + x if rel else x, pos[1])
            cur.points.append(pos)
            last_ctrl = None
        elif c == "V":
            y = num()
            pos = (pos[0], pos[1] + y if rel else y)
            cur.points.append(pos)
            last_ctrl = None
        elif c == "C":
            p1, p2, p3 = pt(rel), pt(rel), pt(rel)
            cur.points.extend(_cubic(pos, p1, p2, p3))
            pos, last_ctrl = p3, p2
        elif c == "S":
            p1 = (2 * pos[0] - last_ctrl[0], 2 * pos[1] - last_ctrl[1]) if last_ctrl else pos
            p2, p3 = pt(rel), pt(rel)
            cur.points.extend(_cubic(pos, p1, p2, p3))
            pos, last_ctrl = p3, p2
        elif c == "Q":
            q, p3 = pt(rel), pt(rel)
            p1 = (pos[0] + 2 / 3 * (q[0] - pos[0]), pos[1] + 2 / 3 * (q[1] - pos[1]))
            p2 = (p3[0] + 2 / 3 * (q[0] - p3[0]), p3[1] + 2 / 3 * (q[1] - p3[1]))
            cur.points.extend(_cubic(pos, p1, p2, p3))
            pos, last_ctrl = p3, None
        else:
            raise ValueError(f"path command {cmd!r} not supported by the sticker renderer")
    if cur is not None:
        out.append(cur)
    return out


def _ellipse(cx: float, cy: float, rx: float, ry: float) -> list[Subpath]:
    n = 72
    pts = [
        (cx + rx * math.cos(2 * math.pi * k / n), cy + ry * math.sin(2 * math.pi * k / n))
        for k in range(n)
    ]
    return [Subpath(pts, closed=True)]


def _rect(x: float, y: float, w: float, h: float, rx: float) -> list[Subpath]:
    if rx <= 0:
        return [Subpath([(x, y), (x + w, y), (x + w, y + h), (x, y + h)], closed=True)]
    rx = min(rx, w / 2, h / 2)
    pts: list[Point] = []
    for cx, cy, a0 in (
        (x + w - rx, y + rx, -90),
        (x + w - rx, y + h - rx, 0),
        (x + rx, y + h - rx, 90),
        (x + rx, y + rx, 180),
    ):
        for k in range(10):
            a = math.radians(a0 + 9 * k)
            pts.append((cx + rx * math.cos(a), cy + rx * math.sin(a)))
    return [Subpath(pts, closed=True)]


# ---- drawing -------------------------------------------------------------------------

_INHERITED = ("fill", "stroke", "stroke-width", "fill-rule")
_IGNORED = {"title", "desc", "metadata"}
_SKIPPED_ATTRS = {
    "viewBox",
    "stroke-linecap",
    "stroke-linejoin",
    "xmlns",
    "id",
    "class",
    "aria-hidden",
    "d",
    "cx",
    "cy",
    "r",
    "rx",
    "ry",
    "x",
    "y",
    "x1",
    "y1",
    "x2",
    "y2",
    "width",
    "height",
    "transform",
    "opacity",
    *_INHERITED,
}


def _colour(value: str | None, where: str) -> str | None:
    if value is None or value == "none":
        return None
    v = value.strip().lower()
    if re.fullmatch(r"#[0-9a-f]{3}", v):
        v = "#" + "".join(ch * 2 for ch in v[1:])
    if v not in PALETTE:
        raise ValueError(f"{where}: colour {value!r} is not in the sticker palette")
    return v


def _geometry(el: ET.Element) -> list[Subpath]:
    tag = el.tag.removeprefix(SVG_NS)

    def f(key: str, default: str = "0") -> float:
        return float(el.get(key) or default)

    if tag == "path":
        return parse_path(el.get("d", ""))
    if tag == "circle":
        return _ellipse(f("cx"), f("cy"), f("r"), f("r"))
    if tag == "ellipse":
        return _ellipse(f("cx"), f("cy"), f("rx"), f("ry"))
    if tag == "rect":
        rx = f("rx", el.get("ry") or "0")
        return _rect(f("x"), f("y"), f("width"), f("height"), rx)
    if tag == "line":
        return [Subpath([(f("x1"), f("y1")), (f("x2"), f("y2"))], closed=False)]
    raise ValueError(f"<{tag}> is not supported by the sticker renderer")


class _Renderer:
    def __init__(self, size: int, supersample: int, view: tuple[float, float, float, float]):
        self.px = size * supersample
        vx, vy, vw, vh = view
        k = self.px / max(vw, vh)
        self.base: Matrix = (k, 0, 0, k, -vx * k, -vy * k)
        self.name = ""

    def blank(self) -> Image.Image:
        return Image.new("RGBA", (self.px, self.px), (0, 0, 0, 0))

    def _fill_mask(self, subs: list[Subpath], m: Matrix, evenodd: bool) -> Image.Image:
        mask = Image.new("L", (self.px, self.px), 0)
        for sp in subs:
            if len(sp.points) < 3:
                continue
            layer = Image.new("L", (self.px, self.px), 0)
            ImageDraw.Draw(layer).polygon([_apply(m, p) for p in sp.points], fill=255)
            # even-odd = XOR of the subpaths; nonzero is approximated as their union,
            # which is what it is for the shapes the stickers use (no reversed holes).
            mask = (
                ImageChops.difference(mask, layer) if evenodd else ImageChops.lighter(mask, layer)
            )
        return mask

    def _stroke_mask(self, subs: list[Subpath], m: Matrix, width: float) -> Image.Image:
        mask = Image.new("L", (self.px, self.px), 0)
        draw = ImageDraw.Draw(mask)
        scale = math.sqrt(abs(m[0] * m[3] - m[1] * m[2]))
        w = width * scale
        r = w / 2
        for sp in subs:
            pts = [_apply(m, p) for p in sp.points]
            if sp.closed:
                pts.append(pts[0])
            if len(pts) >= 2:
                draw.line(pts, fill=255, width=max(1, round(w)), joint="curve")
            # round caps and joins: a disc at every vertex (the flattened curves are
            # short segments, so this is also what keeps thick curves smooth)
            for x, y in pts:
                draw.ellipse((x - r, y - r, x + r, y + r), fill=255)
        return mask

    def draw(self, el: ET.Element, target: Image.Image, m: Matrix, style: dict[str, str]) -> None:
        tag = el.tag.removeprefix(SVG_NS)
        if tag in _IGNORED:
            return
        unknown = set(el.attrib) - _SKIPPED_ATTRS
        if unknown:
            raise ValueError(f"{self.name}: unsupported attribute(s) {sorted(unknown)} on <{tag}>")
        style = {**style, **{k: el.attrib[k] for k in _INHERITED if k in el.attrib}}
        m = _mul(m, parse_transform(el.get("transform")))
        opacity = float(el.get("opacity", 1.0))
        layer = self.blank() if opacity < 1 else target
        if tag in ("svg", "g"):
            for child in el:
                self.draw(child, layer, m, style)
        else:
            subs = _geometry(el)
            fill = _colour(style.get("fill", "#000000"), self.name)  # SVG default: black
            stroke = _colour(style.get("stroke"), self.name)
            if fill is not None:
                evenodd = style.get("fill-rule", "nonzero") == "evenodd"
                self._paint(layer, self._fill_mask(subs, m, evenodd), fill)
            if stroke is not None:
                width = float(style.get("stroke-width", 1.0))
                self._paint(layer, self._stroke_mask(subs, m, width), stroke)
        if layer is not target:
            alpha = layer.getchannel("A").point(lambda a: round(a * opacity))
            layer.putalpha(alpha)
            target.alpha_composite(layer)

    @staticmethod
    def _paint(target: Image.Image, mask: Image.Image, colour: str) -> None:
        solid = Image.new("RGBA", target.size, colour)
        solid.putalpha(mask)
        target.alpha_composite(solid)


def render_svg(text: str, size: int, *, name: str = "sticker", supersample: int = 4) -> Image.Image:
    """An SVG (the sticker subset) as a `size` x `size` RGBA image."""
    root = ET.fromstring(text)
    vb = [float(v) for v in root.get("viewBox", "0 0 64 64").replace(",", " ").split()]
    r = _Renderer(size, supersample, (vb[0], vb[1], vb[2], vb[3]))
    r.name = name
    canvas = r.blank()
    r.draw(root, canvas, r.base, {})
    # Downsample in premultiplied alpha, or the transparent edge bleeds dark fringes.
    return canvas.convert("RGBa").resize((size, size), Image.Resampling.LANCZOS).convert("RGBA")


@lru_cache(maxsize=64)
def sticker_image(name: str, size: int) -> Image.Image:
    """The committed sticker `name` at `size` px. Cached; callers must not mutate it."""
    if name not in NAMES:
        raise FileNotFoundError(name)
    return render_svg((STICKER_DIR / name).read_text(encoding="utf-8"), size, name=name)


@lru_cache(maxsize=1)
def art_version() -> str:
    """Short hash of the sticker files: part of every strip URL, so new art = new URL.

    Strips are served `immutable` and Google re-fetches a heroImage only when its URL
    changes, so without this a redrawn sticker would never reach an existing pass.
    Bump `_RENDER_REV` when the strip *layout* changes without the SVGs changing.
    """
    h = hashlib.sha256(_RENDER_REV.encode())
    for name in NAMES:
        path = STICKER_DIR / name
        h.update(name.encode())
        h.update(path.read_bytes() if path.is_file() else b"missing")
    return h.hexdigest()[:10]


_RENDER_REV = "strip-2"


# ---- copies --------------------------------------------------------------------------


def _copy_targets() -> Iterator[tuple[Path, Path]]:
    for folder in COPY_DIRS:
        for name in NAMES:
            yield STICKER_DIR / name, folder / name


def sync_copies() -> list[Path]:
    """Copy the canonical stickers to every consumer that cannot read `assets/`.

    Skips a consumer whose tree is absent (e.g. inside the API image, which has
    neither `site/web/public` nor `web/public` to serve from).
    """
    written: list[Path] = []
    for src, dst in _copy_targets():
        if not dst.parent.parent.is_dir():
            continue
        dst.parent.mkdir(exist_ok=True)
        if not dst.is_file() or dst.read_bytes() != src.read_bytes():
            shutil.copyfile(src, dst)
            written.append(dst)
    return written


def stale_copies() -> list[Path]:
    """Copies that are missing or differ from `assets/pass/stickers/`."""
    return [
        dst
        for src, dst in _copy_targets()
        if dst.parent.parent.is_dir()
        and (not dst.is_file() or dst.read_bytes() != src.read_bytes())
    ]


def check_all() -> None:
    """Render every sticker once: raises on any construct or colour the set may not use."""
    for name in NAMES:
        render_svg((STICKER_DIR / name).read_text(encoding="utf-8"), 32, name=name, supersample=1)


__all__ = [
    "COPY_DIRS",
    "NAMES",
    "PALETTE",
    "REWARD",
    "SLOT_STICKERS",
    "STICKER_DIR",
    "art_version",
    "check_all",
    "render_svg",
    "slot_sticker",
    "stale_copies",
    "sticker_image",
    "sync_copies",
]
