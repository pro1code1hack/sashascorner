"""Pass artwork in `assets/pass/`: icon, logo, Google programme logo, stamp SVG, and the
copies of the stamp stickers (`assets/pass/stickers/`, see `stickers.py`).

Generated once from the brand line-art (`cafeops wallet assets`) and committed, so the
runtime image never needs the site tree or a rasteriser beyond Pillow. The strips are
not here: they depend on the stamp count and are rendered on demand (`strips.py`).

Sizes are Apple's (PassKit "Pass Design and Creation"): icon 29 pt square, logo at most
160x50 pt. Google's programme logo is shown circle-cropped at 660 px, hence the wide
padding on that one.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PIL import Image

from cafeops.integrations.wallet import stickers
from cafeops.integrations.wallet.brand import BRAND_DIR, load_svg, render_mark
from cafeops.integrations.wallet.config import ASSETS_DIR
from cafeops.integrations.wallet.strips import BACKGROUND, FOREGROUND, stamp_svg

MARK_SVG = BRAND_DIR / "lineart.svg"

#: Files bundled into every .pkpass (the strip is added per pass).
APPLE_FILES = (
    "icon.png",
    "icon@2x.png",
    "icon@3x.png",
    "logo.png",
    "logo@2x.png",
    "logo@3x.png",
)
#: Files the public route serves (Google fetches images by URL).
PUBLIC_FILES = frozenset({*APPLE_FILES, "google-logo.png", "stamp.svg", "stamp-empty.svg"})


def _mark_aspect() -> float:
    _vb, shapes = load_svg(MARK_SVG)
    pts = [p for s in shapes for sp in s.subpaths for p in sp]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return (max(xs) - min(xs)) / (max(ys) - min(ys))


def _on_background(mark: Image.Image) -> Image.Image:
    # Apple composites icons onto the lock screen; a transparent icon shows whatever
    # is behind it, so the icon gets the card's own blush.
    bg = Image.new("RGBA", mark.size, BACKGROUND)
    bg.alpha_composite(mark)
    return bg.convert("RGB")


def generate(out_dir: Path = ASSETS_DIR) -> list[Path]:
    """(Re)write every static pass asset. Returns the paths written."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    def save(img: Image.Image, name: str) -> None:
        path = out_dir / name
        img.save(path, format="PNG", optimize=True)
        written.append(path)

    aspect = _mark_aspect()
    for scale in (1, 2, 3):
        suffix = "" if scale == 1 else f"@{scale}x"
        side = 29 * scale
        # Thicken grows with scale: the source hairline is ~0.4 pt at icon size.
        icon = render_mark(MARK_SVG, (side, side), FOREGROUND, padding=0.08, thicken=0.3 * scale)
        save(_on_background(icon), f"icon{suffix}.png")

        h = 50 * scale
        w = min(160 * scale, round(h * aspect))
        logo = render_mark(MARK_SVG, (w, h), FOREGROUND, padding=0.02, thicken=0.3 * scale)
        save(logo, f"logo{suffix}.png")

    google = render_mark(MARK_SVG, (660, 660), FOREGROUND, padding=0.2, thicken=2.0)
    save(_on_background(google), "google-logo.png")

    for name, earned in (("stamp.svg", True), ("stamp-empty.svg", False)):
        path = out_dir / name
        path.write_text(stamp_svg(earned), encoding="utf-8")
        written.append(path)
    # The stamp stickers are hand-drawn SVGs, canonical in assets/pass/stickers/. Check
    # they render (palette, supported SVG subset), then refresh the site's and the
    # dashboard's copies -- their Docker build contexts cannot see assets/.
    stickers.check_all()
    written += stickers.sync_copies()
    pass_asset.cache_clear()
    return written


@lru_cache(maxsize=32)
def pass_asset(name: str) -> bytes:
    """Bytes of a committed asset. FileNotFoundError when it was never generated."""
    if name not in PUBLIC_FILES:
        raise FileNotFoundError(name)
    return (ASSETS_DIR / name).read_bytes()


__all__ = ["APPLE_FILES", "PUBLIC_FILES", "generate", "pass_asset"]
