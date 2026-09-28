"""Stamp-strip images: the stamp grid on the pass (SPEC "Wallet passes").

One template, parameterised by (stamps, required, scale): Apple's storeCard strip at
@1x/@2x/@3x (375x123 pt -> 375x123 / 750x246 / 1125x369 px), and the @3x one doubles as
Google's heroImage via the public route. Rendered with Pillow on demand and cached --
there are only (required+1) x 2 (reward or not) x 3 distinct images, and they never
change without a deploy.

**Why one row along the bottom.** On a storeCard, Wallet draws the *primary field*
("Free drink after 3 more") over the top-left of the strip. A 2x4 grid would sit under
that text; a single row in the lower half leaves the text a clear field and still reads
as a stamp card. The row is the slots plus one reward position at the end, like the
paper card's free ninth coffee.

**The stamps are stickers** (`stickers.py`, canonical SVGs in `assets/pass/stickers/`):
a different one per slot, in a fixed order, so a strip is a pure function of
(stamps, required, reward) and can be cached forever. Because Google re-fetches a
heroImage only when its URL changes, every strip URL carries `strip_version()`, a hash
of the sticker files -- redraw a sticker and every pass gets a new URL.

The cup line mark below (`CUP`, `stamp_svg()` -> `assets/pass/stamp.svg`) is the
ghosted "free drink" in the reward position and the web card's voucher icon.
"""

from __future__ import annotations

from functools import lru_cache
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

from cafeops.integrations.wallet import stickers

BACKGROUND = "#E9DCD6"
LABEL = "#9B6038"
FOREGROUND = "#474531"

STRIP_PT = (375, 123)
SCALES = (1, 2, 3)
#: Supersampling factor: Pillow's line drawing is not anti-aliased, so draw big and
#: downsample. 4x is where the cup's curves stop showing steps at @1x.
_SS = 4

Pt = tuple[float, float]
Seg = tuple[Pt, Pt, Pt, Pt]

# Geometry in a 100x100 box. Each stroke is a chain of cubic segments.
CUP: tuple[tuple[Seg, ...], ...] = (
    # rim, a touch of sag so it reads as drawn rather than ruled
    (((18, 40), (38, 42.5), (62, 42.5), (82, 40)),),
    # bowl
    (
        ((22, 40), (22, 62), (34, 76), (50, 76)),
        ((50, 76), (66, 76), (78, 62), (78, 40)),
    ),
    # handle
    (((77, 47), (93, 43), (94, 65), (72, 64)),),
    # saucer
    (((12, 85), (36, 89.5), (64, 89.5), (88, 85)),),
)
#: Steam only on an earned stamp -- a hot drink that has been had.
STEAM: tuple[tuple[Seg, ...], ...] = (
    (((43, 33), (36, 27), (50, 22), (43, 13)),),
    (((57, 33), (50, 27), (64, 22), (57, 13)),),
)
STROKE = 6.0  # in the 100-unit box


def _flatten(seg: Seg, steps: int = 24) -> list[Pt]:
    p0, p1, p2, p3 = seg
    out: list[Pt] = []
    for i in range(steps + 1):
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


def _draw_strokes(
    draw: ImageDraw.ImageDraw,
    strokes: tuple[tuple[Seg, ...], ...],
    *,
    origin: Pt,
    unit: float,
    colour: str,
    width: float,
) -> None:
    ox, oy = origin
    w = max(1, round(width))
    r = width / 2
    for chain in strokes:
        pts: list[Pt] = []
        for seg in chain:
            pts.extend(_flatten(seg))
        scaled = [(ox + x * unit, oy + y * unit) for x, y in pts]
        draw.line(scaled, fill=colour, width=w, joint="curve")
        # Round caps: Pillow draws butt ends, which look clipped on a line drawing.
        for x, y in (scaled[0], scaled[-1]):
            draw.ellipse((x - r, y - r, x + r, y + r), fill=colour)


def _mix(hex_colour: str, alpha: float, over: str = BACKGROUND) -> str:
    """`hex_colour` at `alpha` over the background, as an opaque colour.

    Pre-mixed rather than drawn translucent: ImageDraw *replaces* pixels instead of
    compositing, so a translucent fill would punch a hole, not tint.
    """
    fg = [int(hex_colour.lstrip("#")[i : i + 2], 16) for i in (0, 2, 4)]
    bg = [int(over.lstrip("#")[i : i + 2], 16) for i in (0, 2, 4)]
    return "#" + "".join(f"{round(b + (f - b) * alpha):02x}" for f, b in zip(fg, bg, strict=True))


def _dashed_ring(
    draw: ImageDraw.ImageDraw, cx: float, cy: float, r: float, colour: str, width: float
) -> None:
    """The empty slot: a dashed ring, like the web card's. 16 dashes, half on."""
    box = (cx - r, cy - r, cx + r, cy + r)
    for k in range(16):
        a0 = k * 22.5 - 90
        draw.arc(box, a0, a0 + 12.5, fill=colour, width=max(1, round(width)))


def _layout(required: int, w: float, h: float) -> list[tuple[float, float, float]]:
    """Centres and diameter of the `required` slots plus the reward slot, in px.

    One row along the bottom, the reward slot last -- the "9th coffee" of the paper
    card. Over 9 positions (not the stamp programme, but the schema allows others) the
    row wraps into two.
    """
    n = required + 1
    margin_x = w * 0.045
    if n <= 11:
        rows, per_row = 1, n
    else:
        rows, per_row = 2, -(-n // 2)
    gap_ratio = 0.2  # gap as a fraction of the diameter
    d = (w - 2 * margin_x) / (per_row + (per_row - 1) * gap_ratio)
    # One row sits in the lower part, clear of the primary field's text.
    band_top, band_bottom = (h * 0.44, h * 0.94) if rows == 1 else (h * 0.08, h * 0.94)
    d = min(d, (band_bottom - band_top) / (rows + (rows - 1) * 0.15), w * 0.14)
    gap = d * gap_ratio
    out: list[tuple[float, float, float]] = []
    for i in range(n):
        row, col = divmod(i, per_row)
        in_row = min(per_row, n - row * per_row)
        row_w = in_row * d + (in_row - 1) * gap
        x0 = (w - row_w) / 2
        cx = x0 + col * (d + gap) + d / 2
        band_h = rows * d + (rows - 1) * d * 0.15
        y0 = band_top + (band_bottom - band_top - band_h) / 2
        cy = y0 + row * d * 1.15 + d / 2
        out.append((cx, cy, d))
    return out


#: A sticker is drawn a little larger than its slot: the 64-unit box includes the
#: white die-cut margin, so at 1.18x the drawing itself is about the slot's size.
_STICKER_SCALE = 1.18
_REWARD_SCALE = 1.3
_EMPTY_RING = 0.8  # empty ring diameter as a fraction of the slot


@lru_cache(maxsize=256)
def strip_png(
    stamps: int,
    required: int,
    scale: int,
    reward: bool = False,
    keys: tuple[str, ...] | None = None,
) -> bytes:
    """PNG bytes for a strip showing `stamps` of `required` earned. Cached.

    Earned slots carry their sticker: the card's own (`keys`, one sticker key per slot,
    BACKOFFICE-V2 §2), else the fixed art per slot (`stickers.slot_sticker`); empty
    ones a faint dashed ring and the slot number, so the customer can count what is
    left. The last position is the reward: a ghosted cup until `reward` (a stamp-card
    reward is ready), then the reward sticker.

    `stamps` is clamped into [0, required]: after a reward the card carries over and
    `stamps_current < required` always, but a programme whose `stamps_required` was
    lowered could briefly show more stamps than slots, and a full strip is right then.
    """
    if required < 1 or required > 20:
        raise ValueError(f"required must be 1..20, got {required}")
    if scale not in SCALES:
        raise ValueError(f"scale must be one of {SCALES}, got {scale}")
    stamps = max(0, min(stamps, required))
    own = keys
    w, h = STRIP_PT[0] * scale, STRIP_PT[1] * scale
    slots = _layout(required, w, h)

    # Pass 1, supersampled: the rings, numbers and ghost cup (Pillow lines are not
    # anti-aliased, so they are drawn big and downsampled).
    big = Image.new("RGBA", (w * _SS, h * _SS), BACKGROUND)
    draw = ImageDraw.Draw(big)
    faint = _mix(FOREGROUND, 0.45)
    font = ImageFont.load_default(size=max(8, round(slots[0][2] * _SS * 0.34)))
    for i, (cx, cy, d) in enumerate(slots):
        cx, cy, d = cx * _SS, cy * _SS, d * _SS
        is_reward_slot = i == required
        if (is_reward_slot and reward) or (not is_reward_slot and i < stamps):
            continue  # a sticker goes here, pass 2
        _dashed_ring(draw, cx, cy, d * _EMPTY_RING / 2, faint, d * 0.035)
        if is_reward_slot:
            unit = d * 0.5 / 82
            origin = (cx - 53 * unit, cy - 57 * unit)
            _draw_strokes(
                draw, CUP + STEAM, origin=origin, unit=unit, colour=faint, width=STROKE * unit
            )
        else:
            draw.text((cx, cy), str(i + 1), fill=faint, font=font, anchor="mm")
    img = big.resize((w, h), Image.Resampling.LANCZOS)

    # Pass 2, at final size: the stickers (they carry their own supersampling).
    for i, (cx, cy, d) in enumerate(slots):
        if i == required:
            if not reward:
                continue
            name, size = stickers.REWARD, round(d * _REWARD_SCALE * _STICKER_SCALE)
            cy -= d * 0.08  # the rosette's ribbons hang low; lift it back into line
        elif i < stamps:
            key = own[i] if own is not None and i < len(own) else None
            name, size = stickers.key_sticker(key, i), round(d * _STICKER_SCALE)
        else:
            continue
        art = stickers.sticker_image(name, size)
        img.alpha_composite(art, (round(cx - size / 2), round(cy - size / 2)))

    buf = BytesIO()
    img.convert("RGB").save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def strip_version() -> str:
    """Goes into every strip URL (`?v=`): new art or layout => a URL nobody cached."""
    return stickers.art_version()


def strip_filename(scale: int) -> str:
    """Apple's names for the strip in a .pkpass bundle."""
    return "strip.png" if scale == 1 else f"strip@{scale}x.png"


def stamp_svg(earned: bool = True) -> str:
    """The stamp mark as a standalone SVG (100x100, stroke = currentColor).

    Same geometry as the strips, so the web card and the pass cannot drift apart.
    """

    def path(chain: tuple[Seg, ...]) -> str:
        first = chain[0][0]
        parts = [f"M{first[0]:g} {first[1]:g}"]
        for _p0, p1, p2, p3 in chain:
            parts.append(f"C{p1[0]:g} {p1[1]:g} {p2[0]:g} {p2[1]:g} {p3[0]:g} {p3[1]:g}")
        return " ".join(parts)

    strokes = CUP + STEAM if earned else CUP
    body = "\n  ".join(f'<path d="{path(c)}"/>' for c in strokes)
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" fill="none" '
        f'stroke="currentColor" stroke-width="{STROKE:g}" stroke-linecap="round" '
        'stroke-linejoin="round">\n'
        "  <!-- Sasha's Corner Rewards stamp: a cup in the logo's single-line style.\n"
        "       Generated by `cafeops wallet assets` from cafeops/integrations/wallet/"
        "strips.py;\n       edit the geometry there, not here. -->\n"
        f"  {body}\n</svg>\n"
    )


__all__ = [
    "BACKGROUND",
    "FOREGROUND",
    "LABEL",
    "SCALES",
    "STRIP_PT",
    "stamp_svg",
    "strip_filename",
    "strip_png",
    "strip_version",
]
