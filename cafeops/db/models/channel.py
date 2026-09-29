"""Marketplace channels as ad platforms, and the agent action log.

Spec 4.6: Deliveroo and Just Eat are not only a revenue channel, they are an ad
platform with impressions, ranking and spend. The numbers worth showing are ROAS,
true contribution after commission AND ad spend, and items that rank well but
convert badly -- which is a photo or description problem, not a product problem.

Access reality, also 4.6: partner APIs are gated to certified POS integrators, so
assume we never get in. Every row records how it arrived (`source`), because a
figure typed from a PDF and a figure pulled by a browser agent deserve different
trust, and a silent mix of the two is unauditable.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    Date,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from cafeops.db.base import Base
from cafeops.db.models._common import UTCDateTime, enum_col, utcnow
from cafeops.domain.enums import (
    AgentToolOutcome,
    ChannelSourceKind,
    SalesChannelName,
)


class ChannelMetric(Base):
    """One channel's performance on one day."""

    __tablename__ = "channel_metric"

    id: Mapped[int] = mapped_column(primary_key=True)
    channel: Mapped[SalesChannelName] = mapped_column(enum_col(SalesChannelName), nullable=False)
    metric_date: Mapped[date] = mapped_column(Date, nullable=False)

    impressions: Mapped[int | None] = mapped_column(Integer)
    menu_views: Mapped[int | None] = mapped_column(Integer)
    conversions: Mapped[int | None] = mapped_column(Integer)
    orders: Mapped[int | None] = mapped_column(Integer)
    ad_spend_pence: Mapped[int | None] = mapped_column(Integer)
    attributed_revenue_pence: Mapped[int | None] = mapped_column(Integer)
    commission_pence: Mapped[int | None] = mapped_column(Integer)
    gross_pence: Mapped[int | None] = mapped_column(Integer)
    # Stored as basis points so an average position of 3.5 needs no float.
    avg_position_bp: Mapped[int | None] = mapped_column(Integer)
    rating_bp: Mapped[int | None] = mapped_column(Integer)

    source: Mapped[ChannelSourceKind] = mapped_column(enum_col(ChannelSourceKind), nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    # Which file or run produced this, so a bad import can be found and undone.
    source_ref: Mapped[str | None] = mapped_column(String(300))

    __table_args__ = (
        UniqueConstraint("channel", "metric_date", name="uq_channel_metric_day"),
        Index("ix_channel_metric_date", "metric_date"),
    )

    @property
    def net_pence(self) -> int | None:
        """Gross less commission less ad spend. None if any part is unknown.

        Deliberately None rather than a partial subtraction: "we do not know what
        this channel actually contributed" is a different statement from a number,
        and the same rule as invariant 8 applies.

        **ALL THREE** must be present. An earlier version returned None only when
        commission *and* ad spend were both missing, so a day that reported commission
        but no ad spend came back as `gross - commission - 0` -- a contribution figure
        with a whole cost silently left out of it, and flattering by exactly the amount
        nobody knew. That is the partial subtraction this property exists to refuse,
        and it mattered more than the absent caller suggests: every docstring in
        `integrations/channels/` points at this property as the statement of the rule
        (`base.py`, `csv_source.py`, `analytics.py`, and the CLI's own footnote), so
        the canonical example of the discipline was the one place breaking it.
        `analytics.channel_performance` had it right independently -- it drops an
        incomplete day whole -- which is why nothing downstream was wrong.
        """
        if self.gross_pence is None or self.commission_pence is None or self.ad_spend_pence is None:
            return None
        return self.gross_pence - self.commission_pence - self.ad_spend_pence

    def __repr__(self) -> str:
        return f"<ChannelMetric {self.channel.value} {self.metric_date}>"


class ChannelItemMetric(Base):
    """One item's performance on one channel on one day.

    This is where "ranks well, converts badly" becomes visible.
    """

    __tablename__ = "channel_item_metric"

    id: Mapped[int] = mapped_column(primary_key=True)
    channel: Mapped[SalesChannelName] = mapped_column(enum_col(SalesChannelName), nullable=False)
    metric_date: Mapped[date] = mapped_column(Date, nullable=False)
    menu_item_id: Mapped[int] = mapped_column(ForeignKey("menu_item.id"), nullable=False)

    views: Mapped[int | None] = mapped_column(Integer)
    orders: Mapped[int | None] = mapped_column(Integer)
    revenue_pence: Mapped[int | None] = mapped_column(Integer)
    rank_in_category: Mapped[int | None] = mapped_column(Integer)

    source: Mapped[ChannelSourceKind] = mapped_column(enum_col(ChannelSourceKind), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "channel", "metric_date", "menu_item_id", name="uq_channel_item_metric_day"
        ),
        Index("ix_channel_item_metric_item", "menu_item_id", "metric_date"),
    )


class AgentActionLog(Base):
    """Every agent action, with its inputs, its output and the tool it called.

    Spec 9's hard rule, made durable. The agent never writes to stock, orders or
    composition directly -- it calls a service or emits a proposal -- and this table
    is how that claim stays checkable after the fact rather than being a promise in
    a docstring.

    `outcome = REFUSED` is the interesting row: it means the whitelist did its job.
    """

    __tablename__ = "agent_action_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    # A single agent run may call several tools; this groups them.
    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(120), nullable=False)
    purpose: Mapped[str | None] = mapped_column(String(300))
    #: Which agent ran: drift_explainer | import_assistant | channel_reporter |
    #: basket_stager (shell-agents spec 6.1). NULL on rows predating the column.
    agent: Mapped[str | None] = mapped_column(String(60))

    inputs: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    output: Mapped[str | None] = mapped_column(Text)
    outcome: Mapped[AgentToolOutcome] = mapped_column(enum_col(AgentToolOutcome), nullable=False)
    # Populated when the tool was refused, naming which rule refused it.
    refusal_reason: Mapped[str | None] = mapped_column(String(400))
    # Set when the action produced something a human must approve before it counts.
    proposal_ref: Mapped[str | None] = mapped_column(String(200))
    model: Mapped[str | None] = mapped_column(String(80))

    __table_args__ = (
        Index("ix_agent_log_run", "run_id"),
        Index("ix_agent_log_at", "occurred_at"),
        Index("ix_agent_log_outcome", "outcome"),
    )

    def __repr__(self) -> str:
        return f"<AgentActionLog {self.tool_name} {self.outcome.value}>"


class TescoRouting(Base):
    """Every time ordering routes to retail instead of a scheduled supplier.

    Spec 4.4: "how often, and how much extra, panic-buying at retail costs. That
    report is the argument for fixing the ordering pattern."

    A separate table rather than a note on the PO, because the point is the trend:
    one emergency is a bad week, a pattern is a broken ordering cadence, and the
    retail premium is the number that makes the case.
    """

    __tablename__ = "tesco_routing"

    id: Mapped[int] = mapped_column(primary_key=True)
    ingredient_id: Mapped[int] = mapped_column(ForeignKey("ingredient.id"), nullable=False)
    po_line_id: Mapped[int | None] = mapped_column(ForeignKey("po_line.id"))
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    reason: Mapped[str] = mapped_column(String(400), nullable=False)
    # What it cost versus the preferred supplier's unit price for the same quantity.
    retail_unit_price_pence: Mapped[int | None] = mapped_column(Integer)
    preferred_unit_price_pence: Mapped[int | None] = mapped_column(Integer)
    premium_pence: Mapped[int | None] = mapped_column(Integer)
    #: Which supplier would have supplied it, had there been time.
    would_be_supplier_id: Mapped[int | None] = mapped_column(ForeignKey("supplier.id"))
    #: "Log as bought" (spec 4.2 / ShopRunIn): where, who, and what was actually paid
    #: for this line. NULL on rows written by the draft builder before anything was bought.
    retailer: Mapped[str | None] = mapped_column(String(80))
    bought_by: Mapped[str | None] = mapped_column(String(120))
    paid_pence: Mapped[int | None] = mapped_column(Integer)

    ingredient: Mapped[Any] = relationship("Ingredient")

    __table_args__ = (Index("ix_tesco_routing_at", "occurred_at"),)
