"""
Marketplace registry — search, install, rate, and upgrade templates.

Tenants browse the marketplace, install templates into their playbook
libraries, and receive notifications when newer template versions are
available. All operations are tenant-isolated and RBAC-gated.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from saas.marketplace.templates import (
    MarketplaceStatus,
    PlaybookEntry,
    TemplateType,
    WorkflowTemplate,
)

log = logging.getLogger("evidentrx.saas.marketplace.registry")


@dataclass
class TemplateRating:
    """A tenant's rating and optional review for an installed template."""
    rating_id:   str
    tenant_id:   str
    template_id: str
    score:       int          # 1–5
    review:      str          = ""
    rated_at:    datetime     = field(default_factory=lambda: datetime.now(tz=UTC))
    rated_by:    str          = "system"


@dataclass
class UpgradeNotification:
    """
    Notification issued when a newer template version is published.

    Sent to tenants that have an older PlaybookEntry for the same
    template name. The tenant decides whether to upgrade.
    """
    notification_id: str
    tenant_id:       str
    entry_id:        str          # existing PlaybookEntry
    current_version: str
    new_template_id: str
    new_version:     str
    change_summary:  str
    created_at:      datetime     = field(default_factory=lambda: datetime.now(tz=UTC))
    acknowledged:    bool         = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "notification_id": self.notification_id,
            "tenant_id":       self.tenant_id,
            "entry_id":        self.entry_id,
            "current_version": self.current_version,
            "new_template_id": self.new_template_id,
            "new_version":     self.new_version,
            "change_summary":  self.change_summary,
            "acknowledged":    self.acknowledged,
        }


class MarketplaceRegistry:
    """
    Central registry for marketplace templates, installs, ratings, and
    upgrade notifications.

    Responsibilities
    ────────────────
    - Template CRUD (submitted by TemplatePublisher, stored here)
    - Tenant search with visibility + tier gating
    - Install (creates PlaybookEntry) / uninstall tracking
    - 1–5 star ratings with per-template average maintenance
    - Upgrade notification dispatch when a new version is published
    """

    def __init__(self, db_writer: Callable | None = None) -> None:
        # template_id → WorkflowTemplate
        self._templates: dict[str, WorkflowTemplate] = {}
        # (template_name, version) → template_id  (for version lookup)
        self._by_name_version: dict[tuple[str, str], str] = {}
        # entry_id → PlaybookEntry
        self._entries: dict[str, PlaybookEntry] = {}
        # (tenant_id, template_id) → entry_id  (one active install per template per tenant)
        self._installs: dict[tuple[str, str], str] = {}
        # rating_id → TemplateRating
        self._ratings: dict[str, TemplateRating] = {}
        # notification_id → UpgradeNotification
        self._upgrade_notifications: dict[str, UpgradeNotification] = {}
        self._db_writer = db_writer

    # ── Template registration (called by TemplatePublisher) ────────────────────

    def register_template(self, template: WorkflowTemplate) -> None:
        """Store a template after it has been approved and published."""
        self._templates[template.template_id] = template
        self._by_name_version[(template.name, template.version)] = template.template_id
        log.info(
            "MarketplaceRegistry: registered template '%s' v%s (%s)",
            template.name, template.version, template.template_id[:8],
        )

    def deprecate_template(self, template_id: str, reason: str = "") -> None:
        tmpl = self._templates.get(template_id)
        if tmpl is None:
            raise TemplateNotFoundError(template_id)
        tmpl.status = MarketplaceStatus.DEPRECATED
        tmpl.metadata["deprecation_reason"] = reason
        log.info("MarketplaceRegistry: deprecated template %s", template_id[:8])

    # ── Search / browse ────────────────────────────────────────────────────────

    def search(
        self,
        tenant_id:       str,
        tier:            str,
        template_type:   TemplateType | None = None,
        tags:            list[str] | None    = None,
        query:           str | None          = None,
        include_deprecated: bool               = False,
        limit:           int                   = 50,
        offset:          int                   = 0,
    ) -> list[WorkflowTemplate]:
        """
        Search templates visible to this tenant / tier.

        Results are sorted by install_count DESC then avg_rating DESC.
        """
        results = []
        for tmpl in self._templates.values():
            if not tmpl.is_accessible_to(tenant_id, tier):
                continue
            if not include_deprecated and tmpl.status == MarketplaceStatus.DEPRECATED:
                continue
            if template_type and tmpl.template_type != template_type:
                continue
            if tags:
                if not all(t in tmpl.tags for t in tags):
                    continue
            if query:
                q = query.lower()
                if q not in tmpl.title.lower() and q not in tmpl.description.lower():
                    continue
            results.append(tmpl)

        results.sort(
            key=lambda t: (t.install_count, t.avg_rating or 0.0),
            reverse=True,
        )
        return results[offset : offset + limit]

    def get_template(self, template_id: str) -> WorkflowTemplate | None:
        return self._templates.get(template_id)

    def get_by_name_version(
        self,
        name:    str,
        version: str,
    ) -> WorkflowTemplate | None:
        tid = self._by_name_version.get((name, version))
        return self._templates.get(tid) if tid else None

    def latest_version(self, name: str) -> WorkflowTemplate | None:
        """Return the most-recently published version of a named template."""
        candidates = [
            t for t in self._templates.values()
            if t.name == name and t.status == MarketplaceStatus.PUBLISHED
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda t: t.created_at)

    # ── Install / uninstall ────────────────────────────────────────────────────

    def install(
        self,
        tenant_id:    str,
        template_id:  str,
        installed_by: str,
        tier:         str,
        custom_name:  str | None = None,
        org_id:       str | None = None,
        custom_config: dict[str, Any] | None = None,
    ) -> PlaybookEntry:
        """
        Install a template into a tenant's playbook library.

        Raises TemplateNotFoundError if not found or not accessible.
        Raises AlreadyInstalledError if tenant already has this template
        installed (use upgrade() to switch versions).
        """
        tmpl = self._templates.get(template_id)
        if tmpl is None or not tmpl.is_accessible_to(tenant_id, tier):
            raise TemplateNotFoundError(template_id)

        install_key = (tenant_id, template_id)
        if install_key in self._installs:
            raise AlreadyInstalledError(template_id, tenant_id)

        entry = PlaybookEntry(
            entry_id         = str(uuid.uuid4()),
            tenant_id        = tenant_id,
            template_id      = template_id,
            template_version = tmpl.version,
            name             = custom_name or tmpl.title,
            installed_by     = installed_by,
            custom_config    = custom_config or {},
            org_id           = org_id,
        )
        self._entries[entry.entry_id] = entry
        self._installs[install_key] = entry.entry_id
        tmpl.install_count += 1

        log.info(
            "MarketplaceRegistry: tenant %s installed template '%s' v%s",
            tenant_id[:8], tmpl.name, tmpl.version,
        )
        return entry

    def uninstall(self, tenant_id: str, entry_id: str) -> None:
        entry = self._entries.get(entry_id)
        if entry is None or entry.tenant_id != tenant_id:
            raise EntryNotFoundError(entry_id)
        entry.active = False
        install_key = (tenant_id, entry.template_id)
        self._installs.pop(install_key, None)

    def upgrade(
        self,
        tenant_id:       str,
        entry_id:        str,
        new_template_id: str,
        upgraded_by:     str,
    ) -> PlaybookEntry:
        """
        Replace an existing PlaybookEntry with a newer template version.

        Preserves custom_name and org_id from the old entry.
        """
        old_entry = self._entries.get(entry_id)
        if old_entry is None or old_entry.tenant_id != tenant_id:
            raise EntryNotFoundError(entry_id)

        new_tmpl = self._templates.get(new_template_id)
        if new_tmpl is None:
            raise TemplateNotFoundError(new_template_id)

        # Deactivate old entry
        old_entry.active = False
        old_install_key = (tenant_id, old_entry.template_id)
        self._installs.pop(old_install_key, None)

        # Create upgraded entry
        new_entry = PlaybookEntry(
            entry_id         = str(uuid.uuid4()),
            tenant_id        = tenant_id,
            template_id      = new_template_id,
            template_version = new_tmpl.version,
            name             = old_entry.name,
            installed_by     = upgraded_by,
            custom_config    = old_entry.custom_config,
            org_id           = old_entry.org_id,
        )
        self._entries[new_entry.entry_id] = new_entry
        self._installs[(tenant_id, new_template_id)] = new_entry.entry_id
        new_tmpl.install_count += 1

        # Acknowledge the upgrade notification if any
        for notif in self._upgrade_notifications.values():
            if notif.tenant_id == tenant_id and notif.entry_id == entry_id:
                notif.acknowledged = True

        log.info(
            "MarketplaceRegistry: tenant %s upgraded '%s' → v%s",
            tenant_id[:8], old_entry.name, new_tmpl.version,
        )
        return new_entry

    def get_entry(self, entry_id: str) -> PlaybookEntry | None:
        return self._entries.get(entry_id)

    def list_installed(
        self,
        tenant_id: str,
        org_id:    str | None = None,
        active_only: bool        = True,
    ) -> list[PlaybookEntry]:
        return [
            e for e in self._entries.values()
            if e.tenant_id == tenant_id
            and (not active_only or e.active)
            and (org_id is None or e.org_id == org_id)
        ]

    # ── Ratings ────────────────────────────────────────────────────────────────

    def rate_template(
        self,
        tenant_id:   str,
        template_id: str,
        score:       int,
        rated_by:    str,
        review:      str = "",
    ) -> TemplateRating:
        if score < 1 or score > 5:
            raise ValueError(f"Score must be 1–5, got {score}")
        tmpl = self._templates.get(template_id)
        if tmpl is None:
            raise TemplateNotFoundError(template_id)

        rating = TemplateRating(
            rating_id   = str(uuid.uuid4()),
            tenant_id   = tenant_id,
            template_id = template_id,
            score       = score,
            review      = review,
            rated_by    = rated_by,
        )
        self._ratings[rating.rating_id] = rating
        self._recompute_avg_rating(template_id)
        return rating

    def _recompute_avg_rating(self, template_id: str) -> None:
        scores = [
            r.score for r in self._ratings.values()
            if r.template_id == template_id
        ]
        tmpl = self._templates.get(template_id)
        if tmpl and scores:
            tmpl.avg_rating = round(sum(scores) / len(scores), 2)

    # ── Upgrade notifications ──────────────────────────────────────────────────

    def create_upgrade_notifications(
        self,
        new_template: WorkflowTemplate,
        change_summary: str = "",
    ) -> list[UpgradeNotification]:
        """
        For every tenant that has an older version of this template installed,
        create an UpgradeNotification.
        """
        notifications: list[UpgradeNotification] = []
        for entry in self._entries.values():
            if not entry.active:
                continue
            old_tmpl = self._templates.get(entry.template_id)
            if old_tmpl is None:
                continue
            if old_tmpl.name != new_template.name:
                continue
            if old_tmpl.version == new_template.version:
                continue

            notif = UpgradeNotification(
                notification_id = str(uuid.uuid4()),
                tenant_id       = entry.tenant_id,
                entry_id        = entry.entry_id,
                current_version = old_tmpl.version,
                new_template_id = new_template.template_id,
                new_version     = new_template.version,
                change_summary  = change_summary,
            )
            self._upgrade_notifications[notif.notification_id] = notif
            notifications.append(notif)

        log.info(
            "MarketplaceRegistry: issued %d upgrade notifications for template '%s' v%s",
            len(notifications), new_template.name, new_template.version,
        )
        return notifications

    def list_upgrade_notifications(
        self,
        tenant_id:         str,
        unacknowledged_only: bool = True,
    ) -> list[UpgradeNotification]:
        return [
            n for n in self._upgrade_notifications.values()
            if n.tenant_id == tenant_id
            and (not unacknowledged_only or not n.acknowledged)
        ]

    def acknowledge_notification(
        self,
        tenant_id:       str,
        notification_id: str,
    ) -> None:
        notif = self._upgrade_notifications.get(notification_id)
        if notif is None or notif.tenant_id != tenant_id:
            raise NotificationNotFoundError(notification_id)
        notif.acknowledged = True

    # ── Stats ──────────────────────────────────────────────────────────────────

    def platform_stats(self) -> dict[str, Any]:
        published = [
            t for t in self._templates.values()
            if t.status == MarketplaceStatus.PUBLISHED
        ]
        return {
            "total_templates":    len(self._templates),
            "published_templates":len(published),
            "total_installs":     sum(t.install_count for t in published),
            "total_entries":      sum(1 for e in self._entries.values() if e.active),
            "total_ratings":      len(self._ratings),
        }


# ── Exceptions ─────────────────────────────────────────────────────────────────

class TemplateNotFoundError(Exception):
    pass


class AlreadyInstalledError(Exception):
    def __init__(self, template_id: str, tenant_id: str) -> None:
        super().__init__(
            f"Tenant {tenant_id[:8]} already has template {template_id[:8]} installed"
        )


class EntryNotFoundError(Exception):
    pass


class NotificationNotFoundError(Exception):
    pass


# ── Singleton ──────────────────────────────────────────────────────────────────

_registry: MarketplaceRegistry | None = None


def get_marketplace_registry(
    db_writer: Callable | None = None,
) -> MarketplaceRegistry:
    global _registry
    if _registry is None:
        _registry = MarketplaceRegistry(db_writer=db_writer)
    return _registry
