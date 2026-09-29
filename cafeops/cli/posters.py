"""`cafeops loyalty qr-posters --out DIR`: print-ready QR posters, one per placement.

SPEC "QR codes and attribution": every printed QR carries `?src=<place>` so a member's
`source` (and Swetrix's `src`) says which table tent, cup or flyer brought them in. The
seven placements and their URLs are fixed here, in one table, so a reprint can never
invent a new spelling of a source nobody's report groups with the old one.

Output, per placement, in DIR:

* `<name>.svg` -- vector, A6 (105 x 148 mm). Send this one to a printer; it scales to a
  table tent or an A4 window poster without going soft.
* `<name>.png` -- the same at 300 dpi (1240 x 1748 px), for a quick home print or a post.
* `qr-posters.pdf` -- every PNG as one A6 page each, to print the whole set at once.

The QR codes use error correction level Q (25 % of the code can be lost), because cups
get wet and table tents get scuffed. Colours are the pass's (SPEC "Wallet passes"): blush
background, olive foreground, coffee label -- the QR sits on a paper-white panel, since
scanners want the strongest contrast they can get.

The base URL is `CAFEOPS_LOYALTY_PUBLIC_URL` (docker derives it from SITE_DOMAIN), so the
posters point wherever the live site is. Check it before printing: a poster is the one
link that cannot be fixed after it leaves the building.
"""

from __future__ import annotations

import base64
import io
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated
from urllib.parse import urlsplit
from xml.sax.saxutils import escape

import typer
from PIL import Image, ImageDraw, ImageFont

__all__ = ["PLACEMENTS", "Placement", "qr_posters", "render_all"]

# A6 at 300 dpi.
WIDTH, HEIGHT = 1240, 1748
DPI = 300
BLUSH = "#E9DCD6"
OLIVE = "#474531"
COFFEE = "#9B6038"
PAPER = "#FFFFFF"
FONT_STACK = "Nunito, 'Helvetica Neue', Arial, sans-serif"


@dataclass(frozen=True, slots=True)
class Placement:
    name: str
    #: Path and query appended to the public origin.
    path: str
    headline: str
    caption: str


# SPEC: /rewards?src=table|till|cup|flyer-uni|flyer-centre; /menu?src=delivery; /?src=ig.
PLACEMENTS: tuple[Placement, ...] = (
    Placement(
        "table",
        "/rewards?src=table",
        "Collect stamps at your table",
        "8 stamps and your 9th drink is on us. Scan to join Sasha's Corner Rewards.",
    ),
    Placement(
        "till",
        "/rewards?src=till",
        "Join before you pay",
        "Scan, add the card to your phone, and we will stamp today's drink.",
    ),
    Placement(
        "cup",
        "/rewards?src=cup",
        "This drink could count",
        "Scan for a free digital stamp card: 8 stamps, the 9th drink is on us.",
    ),
    Placement(
        "flyer-uni",
        "/rewards?src=flyer-uni",
        "Sasha's Corner Rewards",
        "8 stamps, the 9th drink is on us. No app needed: it lives in your phone's wallet.",
    ),
    Placement(
        "flyer-centre",
        "/rewards?src=flyer-centre",
        "Sasha's Corner Rewards",
        "8 stamps, the 9th drink is on us. No app needed: it lives in your phone's wallet.",
    ),
    Placement(
        "delivery",
        "/menu?src=delivery",
        "Enjoyed it? See the full menu",
        "Everything we make, and what is only in the café.",
    ),
    Placement(
        "ig",
        "/?src=ig",
        "Sasha's Corner",
        "Menu, opening hours, events and bookings.",
    ),
)

#: The address line printed on every poster (flyers go out of the building).
_ADDRESS_FALLBACK = "23 Commercial Street, Dundee"


def _address() -> str:
    try:
        from cafeops.integrations.wallet.cafe import cafe

        return cafe().address
    except Exception:  # the site tree is absent: the constant is right today
        return _ADDRESS_FALLBACK


def _matrix(url: str) -> list[list[bool]]:
    import segno

    qr = segno.make_qr(url, error="q", boost_error=False)
    return [[bool(cell) for cell in row] for row in qr.matrix]


def _logo() -> Image.Image | None:
    from cafeops.integrations.wallet.config import ASSETS_DIR

    path = ASSETS_DIR / "google-logo.png"  # the line-art mark on the blush, 660 px
    return Image.open(path).convert("RGB") if path.is_file() else None


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    # Pillow's bundled scalable font (Aileron): present in the slim image, no system
    # fonts needed. The SVG names the brand font instead and lets the printer's apply.
    return ImageFont.load_default(size=size)


def _wrap(text: str, font: ImageFont.FreeTypeFont | ImageFont.ImageFont, width: int) -> list[str]:
    lines: list[str] = []
    line = ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if line and font.getlength(trial) > width:
            lines.append(line)
            line = word
        else:
            line = trial
    if line:
        lines.append(line)
    return lines


@dataclass(frozen=True, slots=True)
class _Layout:
    logo_box: tuple[int, int, int]  # x, y, size
    headline_y: int
    panel: tuple[int, int, int]  # x, y, size
    caption_y: int
    address_y: int
    url_y: int


LAYOUT = _Layout(
    logo_box=((WIDTH - 300) // 2, 60, 300),
    headline_y=420,
    panel=((WIDTH - 860) // 2, 520, 860),
    caption_y=1478,
    address_y=1622,
    url_y=1680,
)
HEADLINE_SIZE, CAPTION_SIZE, SMALL_SIZE = 66, 42, 34
TEXT_WIDTH = WIDTH - 2 * 110


def _display_url(url: str) -> str:
    parts = urlsplit(url)
    path = parts.path if parts.path not in {"", "/"} else ""
    return f"{parts.hostname}{path}"


def render_png(p: Placement, url: str, address: str) -> Image.Image:
    img = Image.new("RGB", (WIDTH, HEIGHT), BLUSH)
    draw = ImageDraw.Draw(img)
    L = LAYOUT

    logo = _logo()
    if logo is not None:
        x, y, size = L.logo_box
        img.paste(logo.resize((size, size), Image.Resampling.LANCZOS), (x, y))

    head = _font(HEADLINE_SIZE)
    lines = _wrap(p.headline, head, TEXT_WIDTH)[:2]
    for i, line in enumerate(lines):
        y = L.headline_y - (len(lines) - 1 - i) * 76
        draw.text((WIDTH // 2, y), line, font=head, fill=OLIVE, anchor="ms")

    px, py, psize = L.panel
    draw.rounded_rectangle((px, py, px + psize, py + psize), radius=36, fill=PAPER)
    matrix = _matrix(url)
    quiet = 4
    modules = len(matrix) + 2 * quiet
    cell = (psize - 40) // modules
    qr_size = cell * modules
    ox = px + (psize - qr_size) // 2 + quiet * cell
    oy = py + (psize - qr_size) // 2 + quiet * cell
    for r, row in enumerate(matrix):
        for c, dark in enumerate(row):
            if dark:
                x0, y0 = ox + c * cell, oy + r * cell
                draw.rectangle((x0, y0, x0 + cell - 1, y0 + cell - 1), fill=OLIVE)

    cap = _font(CAPTION_SIZE)
    for i, line in enumerate(_wrap(p.caption, cap, TEXT_WIDTH)[:3]):
        draw.text((WIDTH // 2, L.caption_y + 54 * i), line, font=cap, fill=OLIVE, anchor="ms")
    small = _font(SMALL_SIZE)
    draw.text((WIDTH // 2, L.address_y), address, font=small, fill=OLIVE, anchor="ms")
    draw.text((WIDTH // 2, L.url_y), _display_url(url), font=small, fill=COFFEE, anchor="ms")
    return img


def render_svg(p: Placement, url: str, address: str) -> str:
    L = LAYOUT
    parts: list[str] = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="105mm" height="148mm" '
        f'viewBox="0 0 {WIDTH} {HEIGHT}">',
        f"<title>{escape(p.headline)} ({escape(p.name)})</title>",
        f'<rect width="{WIDTH}" height="{HEIGHT}" fill="{BLUSH}"/>',
    ]
    logo = _logo()
    if logo is not None:
        buf = io.BytesIO()
        logo.save(buf, format="PNG", optimize=True)
        data = base64.b64encode(buf.getvalue()).decode("ascii")
        x, y, size = L.logo_box
        parts.append(
            f'<image x="{x}" y="{y}" width="{size}" height="{size}" '
            f'href="data:image/png;base64,{data}"/>'
        )

    def text(y: int, s: str, size: int, fill: str) -> str:
        return (
            f'<text x="{WIDTH // 2}" y="{y}" text-anchor="middle" font-family="{FONT_STACK}" '
            f'font-size="{size}" fill="{fill}">{escape(s)}</text>'
        )

    head = _font(HEADLINE_SIZE)
    lines = _wrap(p.headline, head, TEXT_WIDTH)[:2]
    for i, line in enumerate(lines):
        parts.append(text(L.headline_y - (len(lines) - 1 - i) * 76, line, HEADLINE_SIZE, OLIVE))

    px, py, psize = L.panel
    parts.append(
        f'<rect x="{px}" y="{py}" width="{psize}" height="{psize}" rx="36" fill="{PAPER}"/>'
    )
    matrix = _matrix(url)
    quiet = 4
    modules = len(matrix) + 2 * quiet
    cell = (psize - 40) / modules
    ox = px + (psize - cell * modules) / 2 + quiet * cell
    oy = py + (psize - cell * modules) / 2 + quiet * cell
    # One path, one horizontal run per rectangle: exact module edges, no hairline seams.
    d: list[str] = []
    for r, row in enumerate(matrix):
        c = 0
        while c < len(row):
            if not row[c]:
                c += 1
                continue
            start = c
            while c < len(row) and row[c]:
                c += 1
            d.append(f"M{ox + start * cell:.2f} {oy + r * cell:.2f}h{(c - start) * cell:.2f}")
            d.append(f"v{cell:.2f}h{-(c - start) * cell:.2f}z")
    parts.append(f'<path fill="{OLIVE}" d="{"".join(d)}"/>')

    cap = _font(CAPTION_SIZE)
    for i, line in enumerate(_wrap(p.caption, cap, TEXT_WIDTH)[:3]):
        parts.append(text(L.caption_y + 54 * i, line, CAPTION_SIZE, OLIVE))
    parts.append(text(L.address_y, address, SMALL_SIZE, OLIVE))
    parts.append(text(L.url_y, _display_url(url), SMALL_SIZE, COFFEE))
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def render_all(out: Path, base_url: str) -> list[tuple[Placement, str, list[Path]]]:
    out.mkdir(parents=True, exist_ok=True)
    base = base_url.rstrip("/")
    address = _address()
    written: list[tuple[Placement, str, list[Path]]] = []
    pages: list[Image.Image] = []
    for p in PLACEMENTS:
        url = f"{base}{p.path}"
        svg_path = out / f"{p.name}.svg"
        svg_path.write_text(render_svg(p, url, address), encoding="utf-8")
        png = render_png(p, url, address)
        png_path = out / f"{p.name}.png"
        png.save(png_path, dpi=(DPI, DPI), optimize=True)
        pages.append(png)
        written.append((p, url, [svg_path, png_path]))
    pdf = out / "qr-posters.pdf"
    pages[0].save(pdf, save_all=True, append_images=pages[1:], resolution=DPI)
    return written


def qr_posters(
    out: Annotated[Path, typer.Option("--out", help="Directory to write the posters into.")],
    base_url: Annotated[
        str | None,
        typer.Option(
            "--base-url",
            help="Public origin the codes point at. Default: CAFEOPS_LOYALTY_PUBLIC_URL.",
        ),
    ] = None,
) -> None:
    """Print-ready QR posters for every SPEC placement: SVG + 300 dpi PNG, one PDF.

    Table, till, cup, both flyers (/rewards?src=...), delivery bags (/menu?src=delivery)
    and Instagram (/?src=ig), each with a short caption and the café's address."""
    from rich.console import Console
    from rich.table import Table

    from cafeops.config import settings

    console = Console()
    base = (base_url or settings.loyalty_public_url).rstrip("/")
    parts = urlsplit(base)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        console.print(f"[red]{base!r} is not a public origin (https://host).[/red]")
        raise typer.Exit(2)
    if parts.scheme != "https":
        console.print(f"[yellow]{base} is not https -- fine for a proof, not for print.[/yellow]")

    written = render_all(out, base)
    table = Table(title=f"{len(written)} poster(s) -> {out}", title_style="bold")
    table.add_column("Placement", no_wrap=True)
    table.add_column("QR opens")
    table.add_column("Files")
    for p, url, files in written:
        table.add_row(p.name, url, ", ".join(f.name for f in files))
    console.print(table)
    console.print(f"All pages, one per A6 sheet: {out / 'qr-posters.pdf'}")
    console.print(
        "[dim]Scan every PNG with a phone before printing: it must open the URL shown.[/dim]"
    )
