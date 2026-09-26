"""Uploaded images (menu photos). recipes-menu-ingredients spec A5.

Content-addressed: the file lives on disk at `/media/menu/{sha256}.{ext}` (ext from
`content_type`, normally webp), served by Caddy with an immutable cache header. Only
the metadata is here -- BLOBs would grow the single-writer SQLite file six-fold and
every nightly backup with it. The same bytes uploaded twice are one row.

Distinct from the website's `site_media` table (site/ has its own Alembic history).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from cafeops.db.base import Base
from cafeops.db.models._common import UTCDateTime, utcnow


class MediaAsset(Base):
    __tablename__ = "media_asset"

    id: Mapped[int] = mapped_column(primary_key=True)
    #: Lowercase hex sha256 of the stored bytes; also the filename stem.
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    #: image/webp | image/jpeg | image/png
    content_type: Mapped[str] = mapped_column(String(40), nullable=False)
    bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    uploaded_by: Mapped[str | None] = mapped_column(String(120))

    __table_args__ = (
        CheckConstraint("bytes > 0 AND bytes <= 2097152", name="size_limit"),
        CheckConstraint(
            "content_type IN ('image/webp', 'image/jpeg', 'image/png')", name="image_type"
        ),
    )

    @property
    def filename(self) -> str:
        ext = {"image/webp": "webp", "image/jpeg": "jpg", "image/png": "png"}[self.content_type]
        return f"{self.sha256}.{ext}"


__all__ = ["MediaAsset"]
