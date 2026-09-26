"""Menu photos on disk, addressed by content. Recipes spec A5.

The bytes live in `settings.media_dir` as `<sha256>.<ext>`; only metadata is in SQLite
(`media_asset`). Content-addressed names never change, so Caddy can serve them with an
immutable cache header, and the same photo uploaded twice is one file and one row.

**The type is decided by the bytes, never by the client.** A `Content-Type` header is
a claim the uploader makes; the magic number is what the file is. Anything that is not
a PNG, a JPEG or a WebP by its own first bytes is refused, whatever it says it is --
which is also what keeps an HTML or SVG file (script in a served URL) out of the media
directory. 2 MB is the ceiling (the client downscales to 1200px WebP first).

Files are written atomically (temp file + rename in the same directory), so a reader
never sees half a photo and a crash leaves at most a stray temp file.
"""

from __future__ import annotations

import hashlib
import os
import struct
import tempfile
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from cafeops.config import settings
from cafeops.db.models import MediaAsset

__all__ = [
    "MAX_BYTES",
    "MediaRefusedError",
    "StoredImage",
    "media_path",
    "media_url",
    "sniff_image",
    "store_image",
]

MAX_BYTES = 2 * 1024 * 1024
_EXT = {"image/webp": "webp", "image/jpeg": "jpg", "image/png": "png"}


class MediaRefusedError(ValueError):
    """Not an image we accept. 422 with the reason."""


@dataclass(frozen=True, slots=True)
class StoredImage:
    asset_id: int
    sha256: str
    content_type: str
    bytes: int
    width: int | None
    height: int | None
    filename: str
    url: str
    created: bool


def media_url(filename: str) -> str:
    return f"/media/{filename}"


def media_path(filename: str) -> Path:
    return Path(settings.media_dir) / filename


def sniff_image(data: bytes) -> tuple[str, int | None, int | None]:
    """(content type, width, height) from the bytes themselves. Refuses anything else."""
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24 and data[12:16] == b"IHDR":
        width, height = struct.unpack(">II", data[16:24])
        return "image/png", int(width), int(height)
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", *_jpeg_size(data)
    if len(data) >= 30 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp", *_webp_size(data)
    raise MediaRefusedError(
        "that file is not a PNG, JPEG or WebP image (judged by its contents, not its name "
        "or declared type), so it was not stored"
    )


def _jpeg_size(data: bytes) -> tuple[int | None, int | None]:
    i = 2
    while i + 9 < len(data):
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        length = struct.unpack(">H", data[i + 2 : i + 4])[0]
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            height, width = struct.unpack(">HH", data[i + 5 : i + 9])
            return int(width), int(height)
        i += 2 + length
    return None, None


def _webp_size(data: bytes) -> tuple[int | None, int | None]:
    chunk = data[12:16]
    if chunk == b"VP8X":
        width = 1 + int.from_bytes(data[24:27], "little")
        height = 1 + int.from_bytes(data[27:30], "little")
        return width, height
    if chunk == b"VP8 " and data[23:26] == b"\x9d\x01\x2a":
        width, height = struct.unpack("<HH", data[26:30])
        return int(width) & 0x3FFF, int(height) & 0x3FFF
    if chunk == b"VP8L" and data[20] == 0x2F:
        bits = int.from_bytes(data[21:25], "little")
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    return None, None


def store_image(session: Session, data: bytes, *, uploaded_by: str | None) -> StoredImage:
    """Validate, write to disk atomically, and record (or reuse) the `media_asset` row.

    Does not commit: the caller attaches the asset to its menu items in the same unit
    of work.
    """
    if not data:
        raise MediaRefusedError("the upload was empty")
    if len(data) > MAX_BYTES:
        raise MediaRefusedError(
            f"that photo is {len(data) / 1024 / 1024:.1f} MB; the limit is 2 MB. "
            "Resize it (1200px wide is plenty) and try again"
        )
    content_type, width, height = sniff_image(data)
    sha = hashlib.sha256(data).hexdigest()
    filename = f"{sha}.{_EXT[content_type]}"
    target = media_path(filename)
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".upload-", suffix=".part")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(tmp, 0o644)
            os.replace(tmp, target)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    existing = session.scalar(select(MediaAsset).where(MediaAsset.sha256 == sha))
    created = existing is None
    if existing is None:
        existing = MediaAsset(
            sha256=sha,
            content_type=content_type,
            bytes=len(data),
            width=width,
            height=height,
            uploaded_by=(uploaded_by or "").strip()[:120] or None,
        )
        session.add(existing)
        session.flush()
    return StoredImage(
        asset_id=existing.id,
        sha256=sha,
        content_type=content_type,
        bytes=len(data),
        width=width,
        height=height,
        filename=filename,
        url=media_url(filename),
        created=created,
    )
