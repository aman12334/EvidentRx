"""
Billing and usage reports for tenant administrators.

Produces structured usage reports suitable for:
  - Tenant admin billing dashboards
  - Cost attribution to business units
  - Usage trend analysis
  - Quota/limit monitoring
"""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass, field
from datetime    import datetime, timezone
from typing      import Any, Optional

from saas.billing.accounting import UsageAccounting, BillingPeriod
from saas.billing.meter      import UsageEventType

log = logging.getLogger("evidentrx.saas.billing.reports")


@dataclass
class TenantUsageSummary:
    """High-level usage summary for a tenant dashboard."""
    tenant_id:            str
    period_label:         str
    computed_at:          datetime
    investigations_total: float
    api_requests_total:   float
    tokens_in_total:      float
    tokens_out_total:     float
    ingestion_records:    float
    storage_gb_days:      float
    export_records:       float
    workflow_executions:  float
    analyst_seat_days:    float
    top_orgs_by_volume:   list[dict[str, Any]]   # [{org_id, quantity}]
    quota_utilization:    dict[str, float]        # event_type → fraction of quota used

    def to_dict(self) -> dict[str, Any]:
        return {
            "tenant_id":             self.tenant_id,
            "period_label":          self.period_label,
            "computed_at":           self.computed_at.isoformat(),
            "investigations_total":  self.investigations_total,
            "api_requests_total":    self.api_requests_total,
            "tokens_in_total":       self.tokens_in_total,
            "tokens_out_total":      self.tokens_out_total,
            "ingestion_records":     self.ingestion_records,
            "workflow_executions":   self.workflow_executions,
            "analyst_seat_days":     self.analyst_seat_days,
            "top_orgs_by_volume":    self.top_orgs_by_volume,
            "quota_utilization":     self.quota_utilization,
        }


@dataclass
class UsageTrendReport:
    """Month-over-month trend for a specific resource type."""
    tenant_id:   str
    event_type:  str
    periods:     list[dict[str, Any]]    # [{period, quantity}]
    avg_monthly: float
    peak_period: Optional[str]
    trend_pct:   Optional[float]         # % change current vs prior month

    def to_dict(self) -> dict[str, Any]:
        return {
            "tenant_id":   self.tenant_id,
            "event_type":  self.event_type,
            "periods":     self.periods,
            "avg_monthly": self.avg_monthly,
            "peak_period": self.peak_period,
            "trend_pct":   self.trend_pct,
        }


class UsageReportEngine:
    """Generates billing and usage reports from the accounting layer."""

    def __init__(self, accounting: Optional[UsageAccounting] = None) -> None:
        self._accounting = accounting or UsageAccounting()

    def tenant_usage_summary(
        self,
        tenant_id:  str,
        year:       int,
        month:      int,
        quotas:     Optional[dict[str, float]] = None,  # event_type → quota quantity
    ) -> TenantUsageSummary:
        period = self._accounting.aggregate_period(tenant_id, year, month)

        def _total(et: UsageEventType) -> float:
            return period._total(et)

        # Top orgs by investigation volume
        inv_summaries = [
            s for s in period.summaries
            if s.event_type == UsageEventType.INVESTIGATION_RUN
        ]
        org_volumes: dict[str, float] = {}
        for s in inv_summaries:
            for org, qty in s.org_breakdown.items():
                org_volumes[org] = org_volumes.get(org, 0.0) + qty

        top_orgs = sorted(
            [{"org_id": o, "quantity": q} for o, q in org_volumes.items()],
            key=lambda x: x["quantity"],
            reverse=True,
        )[:10]

        # Quota utilization
        quota_util: dict[str, float] = {}
        if quotas:
            for et_str, limit in quotas.items():
                try:
                    et  = UsageEventType(et_str)
                    qty = _total(et)
                    quota_util[et_str] = round(qty / limit, 4) if limit > 0 else 0.0
                except ValueError:
                    pass

        return TenantUsageSummary(
            tenant_id            = tenant_id,
            period_label         = period.label,
            computed_at          = datetime.now(tz=timezone.utc),
            investigations_total = _total(UsageEventType.INVESTIGATION_RUN),
            api_requests_total   = _total(UsageEventType.API_REQUEST),
            tokens_in_total      = _total(UsageEventType.MODEL_TOKENS_IN),
            tokens_out_total     = _total(UsageEventType.MODEL_TOKENS_OUT),
            ingestion_records    = _total(UsageEventType.INGESTION_RECORD),
            storage_gb_days      = _total(UsageEventType.STORAGE_GB_DAY),
            export_records       = _total(UsageEventType.EXPORT_RECORD),
            workflow_executions  = _total(UsageEventType.WORKFLOW_EXECUTION),
            analyst_seat_days    = _total(UsageEventType.ANALYST_SEAT_DAY),
            top_orgs_by_volume   = top_orgs,
            quota_utilization    = quota_util,
        )

    def usage_trend(
        self,
        tenant_id:  str,
        event_type: UsageEventType,
        months:     int = 6,
    ) -> UsageTrendReport:
        periods = self._accounting.month_over_month_trend(tenant_id, event_type, months)
        quantities = [p["quantity"] for p in periods]
        avg    = statistics.mean(quantities) if quantities else 0.0
        peak   = max(periods, key=lambda p: p["quantity"]) if periods else None
        trend  = None
        if len(quantities) >= 2 and quantities[-2] > 0:
            trend = round((quantities[-1] - quantities[-2]) / quantities[-2] * 100, 2)

        return UsageTrendReport(
            tenant_id   = tenant_id,
            event_type  = event_type.value,
            periods     = periods,
            avg_monthly = round(avg, 2),
            peak_period = peak["period"] if peak else None,
            trend_pct   = trend,
        )

    def multi_tenant_summary(
        self,
        tenant_ids: list[str],
        year:       int,
        month:      int,
    ) -> dict[str, Any]:
        """
        Platform-level cross-tenant aggregate (platform_admin only).

        Returns platform-wide totals. Never exposes tenant-level details
        to any tenant — platform_admin consumption only.
        """
        platform_totals: dict[str, float] = {}
        for tid in tenant_ids:
            period = self._accounting.aggregate_period(tid, year, month)
            for s in period.summaries:
                et = s.event_type.value
                platform_totals[et] = platform_totals.get(et, 0.0) + s.total_quantity

        return {
            "period":         f"{year}-{month:02d}",
            "tenant_count":   len(tenant_ids),
            "platform_totals":platform_totals,
            "computed_at":    datetime.now(tz=timezone.utc).isoformat(),
        }
