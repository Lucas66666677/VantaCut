"""Append-only usage metering and deterministic monthly invoice aggregation."""
from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import PlatformAPIKey, PlatformInvoice, PlatformJob, PlatformUsageEvent


def record_usage(
    db: Session,
    *,
    api_key_id: UUID,
    metric: str,
    quantity: float,
    job_id: UUID | None = None,
    dimensions: dict | None = None,
) -> PlatformUsageEvent:
    event = PlatformUsageEvent(
        api_key_id=api_key_id,
        platform_job_id=job_id,
        metric=metric,
        quantity=Decimal(str(max(0.0, quantity))),
        dimensions_json=dimensions or {},
    )
    db.add(event)
    return event


def build_invoice(db: Session, *, api_key: PlatformAPIKey, period_start: datetime, period_end: datetime) -> PlatformInvoice:
    events = db.scalars(
        select(PlatformUsageEvent).where(
            PlatformUsageEvent.api_key_id == api_key.id,
            PlatformUsageEvent.created_at >= period_start,
            PlatformUsageEvent.created_at < period_end,
        )
    ).all()
    totals: defaultdict[str, Decimal] = defaultdict(Decimal)
    for event in events:
        totals[event.metric] += Decimal(str(event.quantity))
    summary = {metric: float(value.quantize(Decimal("0.0001"))) for metric, value in sorted(totals.items())}
    invoice = db.scalar(select(PlatformInvoice).where(PlatformInvoice.api_key_id == api_key.id, PlatformInvoice.period_start == period_start))
    if invoice is None:
        invoice = PlatformInvoice(api_key_id=api_key.id, period_start=period_start, period_end=period_end)
        db.add(invoice)
    invoice.period_end, invoice.status, invoice.totals_json = period_end, "draft", summary
    return invoice


def month_bounds(now: datetime | None = None) -> tuple[datetime, datetime]:
    current = (now or datetime.now(UTC)).astimezone(UTC)
    this_month = current.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    previous_month = (this_month - timedelta(days=1)).replace(day=1)
    return previous_month, this_month
