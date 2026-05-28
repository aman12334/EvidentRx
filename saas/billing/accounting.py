"""
Tenant usage accounting and cost attribution.

Aggregates raw UsageEvents from the meter into billing periods and
produces cost attribution breakdowns by org, entity, model, and event
type. This layer is read-heavy — the meter is the write path.

Billing periods
───────────────
  Default period is calendar month. Periods are identified by
  (tenant_id, year, month). A period is OPEN until closed at
  month-end, then FINALISED (immutable for invoicing).
"""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass, field
from datetime    import datetime, date, timedelta, timezone
from enum        import Enum
from typing      import Any, Optional

from saas.billing.meter import UsageEvent, UsageEventType

log = logging.getLogger("evidentrx.saas.billing.accounting")


class PeriodStatus(str, Enum):
    OPEN      = "open"
    FINALISED = "finalised"
    INVOICED  = "invoiced"


@dataclass
class UsageSummary:
    """Aggregated usage totals for one (tenant, period, dimension)."""
    tenant_id:   str
    period_year: int
    period_month: int
    event_type:  UsageEventType
    total_quantity: float
    event_count: int
    org_breakdown:    dict[str, float] = field(default_factory=dict)  # org_id → quantity
    entity_breakdown: dict[str, float] = field(default_factory=dict)  # entity_id → quantity
    model_breakdown:  dict[str, float] = field(default_factory=dict)  # model_id → quantity

    @property
    def period_label(self) -> str:
        return f"{self.period_year}-{self.period_month:02d}"


@dataclass
class BillingPeriod:
    """
    A closed billing period for a tenant.

    Carries all aggregated usage summaries for the period and is
    finalised at month-end to produce the invoice input.
    """
    period_id:   str
    tenant_id:   str
    year:        int
    month:       int
    status:      PeriodStatus
    summaries:   list[UsageSummary] = field(default_factory=list)
    opened_at:   datetime           = field(default_factory=lambda: datetime.now(tz=timezone.utc))
    finalised_at: Optional[datetime] = None

    @property
    def label(self) -> str:
        return f"{self.year}-{self.month:02d}"

    @property
    def total_investigations(self) -> float:
        return self._total(UsageEventType.INVESTIGATION_RUN)

    @property
    def total_api_requests(self) -> float:
        return self._total(UsageEventType.API_REQUEST)

    @property
    def total_tokens_in(self) -> float:
        return self._total(UsageEventType.MODEL_TOKENS_IN)

    @property
    def total_tokens_out(self) -> float:
        return self._total(UsageEventType.MODEL_TOKENS_OUT)

    def _total(self, event_type: UsageEventType) -> float:
        return sum(
            s.total_quantity for s in self.summaries
            if s.event_type == event_type
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "period_id":          self.period_id,
            "tenant_id":          self.tenant_id,
            "label":              self.label,
            "status":             self.status.value,
            "total_investigations": self.total_investigations,
            "total_api_requests": self.total_api_requests,
            "total_tokens_in":    self.total_tokens_in,
            "total_tokens_out":   self.total_tokens_out,
            "finalised_at":       self.finalised_at.isoformat() if self.finalised_at else None,
        }


class UsageAccounting:
    """
    Aggregates usage events into billing periods and cost breakdowns.

    The accounting layer does not price events — it produces quantity
    summaries that are passed to an external pricing engine for invoicing.
    """

    def __init__(self) -> None:
        # (tenant_id, year, month) → BillingPeriod
        self._periods: dict[tuple[str, int, int], BillingPeriod] = {}
        # Raw events stored for re-aggregation
        self._events:  list[UsageEvent] = []

    # ── Ingest ─────────────────────────────────────────────────────────────────

    def ingest_events(self, events: list[UsageEvent]) -> None:
        """Add a batch of usage events to the accounting store."""
        self._events.extend(events)

    # ── Aggregate ──────────────────────────────────────────────────────────────

    def aggregate_period(
        self,
        tenant_id: str,
        year:      int,
        month:     int,
    ) -> BillingPeriod:
        """
        Compute or refresh the usage summary for a billing period.

        Existing OPEN periods are re-aggregated on each call.
        FINALISED periods are returned as-is.
        """
        import uuid as _uuid
        key = (tenant_id, year, month)

        period = self._periods.get(key)
        if period and period.status == PeriodStatus.FINALISED:
            return period

        period_events = [
            e for e in self._events
            if e.tenant_id == tenant_id
            and e.occurred_at.year  == year
            and e.occurred_at.month == month
        ]

        summaries = self._build_summaries(tenant_id, year, month, period_events)

        if period is None:
            period = BillingPeriod(
                period_id = str(_uuid.uuid4()),
                tenant_id = tenant_id,
                year      = year,
                month     = month,
                status    = PeriodStatus.OPEN,
            )
            self._periods[key] = period

        period.summaries = summaries
        return period

    def finalise_period(
        self,
        tenant_id: str,
        year:      int,
        month:     int,
    ) -> BillingPeriod:
        """
        Freeze a billing period for invoicing.

        Once finalised the period cannot be re-aggregated — any late-
        arriving events are accrued to the next period.
        """
        period = self.aggregate_period(tenant_id, year, month)
        if period.status != PeriodStatus.FINALISED:
            period.status       = PeriodStatus.FINALISED
            period.finalised_at = datetime.now(tz=timezone.utc)
            log.info(
                "UsageAccounting: finalised period %s for tenant %s",
                period.label, tenant_id[:8],
            )
        return period

    # ── Cost attribution ───────────────────────────────────────────────────────

    def cost_attribution(
        self,
        tenant_id: str,
        year:      int,
        month:     int,
    ) -> dict[str, Any]:
        """
        Return a cost attribution breakdown for a period.

        Breakdowns are by org, entity, and model. Quantities only —
        pricing is applied externally.
        """
        period = self.aggregate_period(tenant_id, year, month)

        org_totals:    dict[str, dict[str, float]] = {}
        entity_totals: dict[str, dict[str, float]] = {}
        model_totals:  dict[str, float]            = {}

        for s in period.summaries:
            et = s.event_type.value
            for org, qty in s.org_breakdown.items():
                org_totals.setdefault(org, {})[et] = (
                    org_totals[org].get(et, 0.0) + qty
                )
            for eid, qty in s.entity_breakdown.items():
                entity_totals.setdefault(eid, {})[et] = (
                    entity_totals[eid].get(et, 0.0) + qty
                )
            for mid, qty in s.model_breakdown.items():
                model_totals[mid] = model_totals.get(mid, 0.0) + qty

        return {
            "period":        period.label,
            "tenant_id":     tenant_id,
            "by_org":        org_totals,
            "by_entity":     entity_totals,
            "by_model":      model_totals,
            "totals":        period.to_dict(),
        }

    # ── Trend ──────────────────────────────────────────────────────────────────

    def month_over_month_trend(
        self,
        tenant_id:   str,
        event_type:  UsageEventType,
        months:      int = 3,
    ) -> list[dict[str, Any]]:
        """Return quantity totals for the last N months."""
        now    = datetime.now(tz=timezone.utc)
        result = []
        for i in range(months - 1, -1, -1):
            d = date(now.year, now.month, 1) - timedelta(days=30 * i)
            period = self.aggregate_period(tenant_id, d.year, d.month)
            qty    = period._total(event_type)
            result.append({
                "period": period.label,
                "quantity": qty,
                "event_type": event_type.value,
            })
        return result

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _build_summaries(
        self,
        tenant_id: str,
        year:      int,
        month:     int,
        events:    list[UsageEvent],
    ) -> list[UsageSummary]:
        from collections import defaultdict

        by_type: dict[UsageEventType, list[UsageEvent]] = defaultdict(list)
        for e in events:
            by_type[e.event_type].append(e)

        summaries: list[UsageSummary] = []
        for event_type, evts in by_type.items():
            total_qty = sum(e.quantity for e in evts)
            org_breakdown:    dict[str, float] = {}
            entity_breakdown: dict[str, float] = {}
            model_breakdown:  dict[str, float] = {}

            for e in evts:
                if e.org_id:
                    org_breakdown[e.org_id] = org_breakdown.get(e.org_id, 0.0) + e.quantity
                if e.entity_id:
                    entity_breakdown[e.entity_id] = entity_breakdown.get(e.entity_id, 0.0) + e.quantity
                if e.model_id:
                    model_breakdown[e.model_id] = model_breakdown.get(e.model_id, 0.0) + e.quantity

            summaries.append(UsageSummary(
                tenant_id        = tenant_id,
                period_year      = year,
                period_month     = month,
                event_type       = event_type,
                total_quantity   = total_qty,
                event_count      = len(evts),
                org_breakdown    = org_breakdown,
                entity_breakdown = entity_breakdown,
                model_breakdown  = model_breakdown,
            ))

        return summaries

    def get_period(
        self,
        tenant_id: str,
        year:      int,
        month:     int,
    ) -> Optional[BillingPeriod]:
        return self._periods.get((tenant_id, year, month))

    def list_periods(self, tenant_id: str) -> list[BillingPeriod]:
        return sorted(
            [p for p in self._periods.values() if p.tenant_id == tenant_id],
            key=lambda p: (p.year, p.month),
            reverse=True,
        )
