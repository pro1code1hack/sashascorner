"""Media library: sniff, process and store uploaded photos.

Every upload is checked by its content (never its name or claimed type), turned
upright from EXIF, flattened to RGB, and written as WebP variants
``{SITE_MEDIA_DIR}/{id}-{w}.webp`` at 480/960/1600 px wide, never upscaled: a
narrower original gets its own width as the largest variant. Metadata (EXIF,
GPS, XMP) is not carried over. A ~24 px blurred WebP is kept in the DB as a data
URI. Identical bytes (sha256) are stored once.

Files are content-addressed by id and never rewritten, so they are served as
immutable; site_media uses AUTOINCREMENT so an id is never reused.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import io
import logging
import os
import tempfile
import warnings
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageCms, ImageFilter, ImageOps
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from sashasite.config import get_settings
from sashasite.db import SiteMedia, SiteSlotItem, session_scope, utcnow
from sashasite.schemas import MediaOut, MediaUsageOut

log = logging.getLogger("sashasite.media")

MAX_UPLOAD_BYTES = 15 * 1024 * 1024
#: Whole request body cap for the upload endpoint: the file plus multipart overhead.
MAX_REQUEST_BYTES = MAX_UPLOAD_BYTES + 512 * 1024
MAX_PIXELS = 60_000_000
TARGET_WIDTHS = (480, 960, 1600)
SRC_WIDTH = 960
WEBP_QUALITY = 80
BLUR_WIDTH = 24
PAPER_RGB = (0xF3, 0xF1, 0xEB)  # --paper: what transparency is flattened onto
VARIANT_NAME = r"^[1-9]\d*-[1-9]\d*\.webp$"

# Ours is checked from the header before decoding, with a useful message. Pillow's
# own guard (warns above MAX_IMAGE_PIXELS, raises above twice that) stays as a
# backstop for anything that decodes to more than the header said.
Image.MAX_IMAGE_PIXELS = MAX_PIXELS * 2

_SNIFF: tuple[tuple[str, str], ...] = (
    ("image/jpeg", "JPEG"),
    ("image/png", "PNG"),
    ("image/webp", "WEBP"),
)


class MediaError(Exception):
    """An upload the library refuses. ``status`` is the HTTP code to answer with."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def sniff(data: bytes) -> str | None:
    """The real content type from magic bytes, or None if it isn't one we take."""
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


@dataclass(frozen=True)
class Processed:
    content_type: str
    width: int
    height: int
    variants: dict[int, bytes]  # width -> WebP bytes
    blur: str


def _to_srgb_rgb(img: Image.Image) -> Image.Image:
    """Flatten alpha onto the page colour and convert to sRGB RGB. An embedded ICC
    profile (iPhone Display P3, Adobe RGB) is converted, not just dropped, so
    colours don't shift once the profile is stripped."""
    has_alpha = img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info)
    icc = img.info.get("icc_profile")
    if has_alpha:
        rgba = img.convert("RGBA")
        ground = Image.new("RGB", rgba.size, PAPER_RGB)
        ground.paste(rgba, mask=rgba.getchannel("A"))
        out = ground
    else:
        out = img.convert("RGB")
    if icc:
        try:
            src = ImageCms.ImageCmsProfile(io.BytesIO(icc))
            dst = ImageCms.createProfile("sRGB")
            converted = ImageCms.profileToProfile(out, src, dst, outputMode="RGB")
            if converted is not None:
                out = converted
        except (ImageCms.PyCMSError, OSError, ValueError) as exc:
            log.info("ICC conversion skipped (%s); treating pixels as sRGB", exc)
    return out


def _webp(img: Image.Image, quality: int = WEBP_QUALITY) -> bytes:
    buf = io.BytesIO()
    # No exif=/icc_profile=/xmp= arguments: the output carries no metadata.
    img.save(buf, format="WEBP", quality=quality, method=5)
    return buf.getvalue()


def variant_widths(width: int) -> list[int]:
    """480/960/1600, each capped at the original width, de-duplicated."""
    return sorted({min(t, width) for t in TARGET_WIDTHS})


def process(data: bytes) -> Processed:
    """Validate and render every variant in memory. Raises MediaError."""
    if len(data) > MAX_UPLOAD_BYTES:
        raise MediaError(413, f"Photos can be up to {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")
    if not data:
        raise MediaError(422, "The file is empty.")
    content_type = sniff(data)
    if content_type is None:
        raise MediaError(415, "Only JPEG, PNG or WebP photos can be uploaded.")
    expected = dict(_SNIFF)[content_type]
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", Image.DecompressionBombWarning)
            img = Image.open(io.BytesIO(data), formats=[expected])
            w0, h0 = img.size  # from the header; nothing decoded yet
            if w0 * h0 > MAX_PIXELS:
                raise MediaError(
                    413,
                    f"That image is {w0}x{h0} ({w0 * h0 / 1e6:.0f} megapixels); "
                    f"the limit is {MAX_PIXELS // 1_000_000}.",
                )
            img.load()
            upright = ImageOps.exif_transpose(img)
            rgb = _to_srgb_rgb(upright)
    except MediaError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise MediaError(413, "That image is too large to process.") from exc
    except Exception as exc:  # Pillow raises a zoo of types for corrupt input
        raise MediaError(415, "That file could not be read as a photo.") from exc

    width, height = rgb.size
    variants: dict[int, bytes] = {}
    for w in variant_widths(width):
        h = max(1, round(height * w / width))
        frame = rgb if w == width else rgb.resize((w, h), Image.Resampling.LANCZOS)
        variants[w] = _webp(frame)

    bh = max(1, round(height * BLUR_WIDTH / width))
    tiny = rgb.resize((BLUR_WIDTH, bh), Image.Resampling.BOX).filter(ImageFilter.GaussianBlur(1.2))
    blur = "data:image/webp;base64," + base64.b64encode(_webp(tiny, quality=50)).decode("ascii")
    return Processed(content_type, width, height, variants, blur)


# --- storage -------------------------------------------------------------------


def media_dir() -> Path:
    d = get_settings().media_dir
    d.mkdir(parents=True, exist_ok=True)
    return d


def variant_path(media_id: int, width: int) -> Path:
    return media_dir() / f"{media_id}-{width}.webp"


def _atomic_write(path: Path, data: bytes) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        raise


def _remove_files(media_id: int, widths: list[int]) -> None:
    for w in widths:
        with contextlib.suppress(FileNotFoundError):
            variant_path(media_id, w).unlink()


def media_urls(m: SiteMedia) -> tuple[str, str]:
    """(src, srcset). src is the 960 variant, or the largest below it."""
    base = get_settings().media_base_url
    widths = m.width_list
    src_w = max((w for w in widths if w <= SRC_WIDTH), default=widths[0])
    src = f"{base}/{m.id}-{src_w}.webp"
    srcset = ", ".join(f"{base}/{m.id}-{w}.webp {w}w" for w in widths)
    return src, srcset


# --- library operations ----------------------------------------------------------


def _clean_name(name: str | None) -> str:
    base = Path(name or "upload").name.strip() or "upload"
    return base[:255]


def add_media(data: bytes, original_name: str | None, alt: str = "") -> tuple[SiteMedia, bool]:
    """Store a photo. Returns (media, created); identical bytes return the
    existing item with created=False (its alt is left alone)."""
    digest = hashlib.sha256(data).hexdigest()
    with session_scope() as session:
        existing = session.scalars(select(SiteMedia).where(SiteMedia.sha256 == digest)).first()
        if existing is not None:
            return existing, False

    p = process(data)  # CPU work outside any transaction
    widths = sorted(p.variants)
    media_id: int | None = None
    try:
        with session_scope(immediate=True) as session:
            m = SiteMedia(
                sha256=digest,
                original_name=_clean_name(original_name),
                content_type=p.content_type,
                width=p.width,
                height=p.height,
                bytes=len(data),
                alt=alt.strip(),
                widths=",".join(str(w) for w in widths),
                blur=p.blur,
                created_at=utcnow(),
            )
            session.add(m)
            session.flush()
            media_id = m.id
            # Files first, then commit: a crash leaves at worst unreferenced
            # files (harmless, overwritten by nothing since ids never repeat),
            # never a row pointing at missing files.
            for w in widths:
                _atomic_write(variant_path(m.id, w), p.variants[w])
    except IntegrityError:
        if media_id is not None:
            _remove_files(media_id, widths)
        # Lost a race with an identical upload: return the winner.
        with session_scope() as session:
            winner = session.scalars(select(SiteMedia).where(SiteMedia.sha256 == digest)).one()
            return winner, False
    except BaseException:
        if media_id is not None:
            _remove_files(media_id, widths)
        raise
    return m, True


def get_media(media_id: int) -> SiteMedia | None:
    with session_scope() as session:
        return session.get(SiteMedia, media_id)


def set_alt(media_id: int, alt: str) -> SiteMedia | None:
    with session_scope() as session:
        m = session.get(SiteMedia, media_id)
        if m is None:
            return None
        m.alt = alt.strip()
        return m


class MediaInUse(Exception):
    def __init__(self, keys: list[str]) -> None:
        super().__init__(f"used by slot(s): {', '.join(keys)}")
        self.keys = keys


def delete_media(media_id: int, *, force: bool) -> list[str] | None:
    """Delete a photo and its files. Returns the slot keys it was removed from,
    or None if there is no such photo. Raises MediaInUse unless ``force``.
    Forcing re-packs the positions of every slot it leaves, in the same
    transaction."""
    with session_scope(immediate=True) as session:
        m = session.get(SiteMedia, media_id)
        if m is None:
            return None
        uses = session.scalars(select(SiteSlotItem).where(SiteSlotItem.media_id == media_id)).all()
        keys = sorted({u.slot_key for u in uses})
        if keys and not force:
            raise MediaInUse(keys)
        widths = m.width_list
        session.execute(delete(SiteSlotItem).where(SiteSlotItem.media_id == media_id))
        session.flush()
        for key in keys:
            rest = session.scalars(
                select(SiteSlotItem)
                .where(SiteSlotItem.slot_key == key)
                .order_by(SiteSlotItem.position)
            ).all()
            # Ascending, so each move goes into a position just vacated.
            for pos, it in enumerate(rest):
                if it.position != pos:
                    it.position = pos
                    session.flush()
        session.delete(m)
    _remove_files(media_id, widths)  # only once the rows are gone for good
    return keys


def list_media() -> list[tuple[SiteMedia, list[SiteSlotItem]]]:
    with session_scope() as session:
        media = session.scalars(select(SiteMedia).order_by(SiteMedia.id.desc())).all()
        items = session.scalars(
            select(SiteSlotItem).order_by(SiteSlotItem.slot_key, SiteSlotItem.position)
        ).all()
        by_media: dict[int, list[SiteSlotItem]] = {}
        for it in items:
            by_media.setdefault(it.media_id, []).append(it)
        return [(m, by_media.get(m.id, [])) for m in media]


def usage_of(media_id: int) -> list[SiteSlotItem]:
    with session_scope() as session:
        return list(
            session.scalars(
                select(SiteSlotItem)
                .where(SiteSlotItem.media_id == media_id)
                .order_by(SiteSlotItem.slot_key, SiteSlotItem.position)
            ).all()
        )


def media_out(m: SiteMedia, uses: list[SiteSlotItem], labels: dict[str, str]) -> MediaOut:
    src, srcset = media_urls(m)
    return MediaOut(
        id=m.id,
        sha256=m.sha256,
        original_name=m.original_name,
        content_type=m.content_type,
        width=m.width,
        height=m.height,
        bytes=m.bytes,
        alt=m.alt,
        created_at=m.created_at,
        widths=m.width_list,
        src=src,
        srcset=srcset,
        blur=m.blur,
        usage=[
            MediaUsageOut(slot_key=u.slot_key, position=u.position, label=labels.get(u.slot_key))
            for u in uses
        ],
    )


def missing_files() -> list[tuple[int, int]]:
    """(media_id, width) for every variant the DB promises that isn't on disk."""
    out: list[tuple[int, int]] = []
    with session_scope() as session:
        for m in session.scalars(select(SiteMedia).order_by(SiteMedia.id)).all():
            out.extend((m.id, w) for w in m.width_list if not variant_path(m.id, w).is_file())
    return out
