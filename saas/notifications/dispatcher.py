"""
Notification dispatcher — tenant-aware delivery across channels.

The dispatcher resolves each recipient's preferences, respects quiet
hours, and routes notifications to the appropriate delivery adapters.
IN_APP notifications are stored locally; EMAIL/WEBHOOK/SLACK are
forwarded to pluggable adapter callables.

Design principles
─────────────────
- Tenant isolation: a notification can never cross tenant boundaries
- Preference-first: user opt-outs and quiet hours are always honoured
  (except CRITICAL priority, which bypasses quiet hours)
- Fail-open for IN_APP storage (always store); fail-gracefully for
  external channels (log and continue — never block the caller)
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any

from saas.notifications.models import (
    Notification,
    NotificationChannel,
    NotificationPreference,
    NotificationPriority,
    NotificationStatus,
    NotificationType,
    new_notification_id,
)

log = logging.getLogger("evidentrx.saas.notifications.dispatcher")

# Adapter signature: async fn(notification: Notification) → None
DeliveryAdapter = Callable[[Notification], Any]


class NotificationDispatcher:
    """
    Routes notifications to recipients based on preferences and channel.

    Adapters for EMAIL, WEBHOOK, and SLACK are injected at construction
    time. If an adapter is not registered, that channel is silently skipped
    (IN_APP always works without an adapter).
    """

    def __init__(
        self,
        email_adapter:   DeliveryAdapter | None = None,
        webhook_adapter: DeliveryAdapter | None = None,
        slack_adapter:   DeliveryAdapter | None = None,
    ) -> None:
        # notification_id → Notification  (in-app store)
        self._store:       dict[str, Notification] = {}
        # (tenant_id, user_id, notification_type) → NotificationPreference
        self._preferences: dict[tuple[str, str, str], NotificationPreference] = {}
        self._adapters: dict[NotificationChannel, DeliveryAdapter | None] = {
            NotificationChannel.EMAIL:   email_adapter,
            NotificationChannel.WEBHOOK: webhook_adapter,
            NotificationChannel.SLACK:   slack_adapter,
        }

    # ── Preferences ────────────────────────────────────────────────────────────

    def set_preference(self, pref: NotificationPreference) -> None:
        key = (pref.tenant_id, pref.user_id, pref.notification_type.value)
        self._preferences[key] = pref

    def get_preference(
        self,
        tenant_id:         str,
        user_id:           str,
        notification_type: NotificationType,
    ) -> NotificationPreference | None:
        return self._preferences.get((tenant_id, user_id, notification_type.value))

    def list_preferences(
        self,
        tenant_id: str,
        user_id:   str,
    ) -> list[NotificationPreference]:
        return [
            p for (tid, uid, _), p in self._preferences.items()
            if tid == tenant_id and uid == user_id
        ]

    # ── Dispatch ───────────────────────────────────────────────────────────────

    async def dispatch(
        self,
        tenant_id:         str,
        recipient_id:      str,
        notification_type: NotificationType,
        title:             str,
        body:              str,
        priority:          NotificationPriority      = NotificationPriority.NORMAL,
        reference_id:      str | None             = None,
        reference_type:    str | None             = None,
        metadata:          dict[str, Any] | None  = None,
        expires_at:        datetime | None        = None,
    ) -> list[Notification]:
        """
        Dispatch a notification to a single recipient.

        Returns the list of Notification records created (one per channel).
        IN_APP is always included unless the user has disabled this type.
        """
        pref = self.get_preference(tenant_id, recipient_id, notification_type)

        # If user has explicitly disabled this type, drop (except CRITICAL)
        if pref and not pref.enabled and priority != NotificationPriority.CRITICAL:
            return []

        channels = pref.effective_channels() if pref else [NotificationChannel.IN_APP]

        # Respect quiet hours
        if pref and pref.is_quiet_now(priority):
            # Suppress external channels during quiet hours; keep IN_APP
            channels = [c for c in channels if c == NotificationChannel.IN_APP]

        # Always ensure IN_APP is present
        if NotificationChannel.IN_APP not in channels:
            channels = [NotificationChannel.IN_APP] + channels

        created: list[Notification] = []
        for channel in channels:
            notif = Notification(
                notification_id   = new_notification_id(),
                tenant_id         = tenant_id,
                recipient_id      = recipient_id,
                notification_type = notification_type,
                title             = title,
                body              = body,
                priority          = priority,
                channel           = channel,
                reference_id      = reference_id,
                reference_type    = reference_type,
                metadata          = metadata or {},
                expires_at        = expires_at,
            )

            if channel == NotificationChannel.IN_APP:
                self._store[notif.notification_id] = notif
                notif.mark_sent()
            else:
                adapter = self._adapters.get(channel)
                if adapter:
                    try:
                        await adapter(notif)
                        notif.mark_sent()
                    except Exception as exc:
                        log.error(
                            "NotificationDispatcher: %s delivery failed for %s: %s",
                            channel.value, notif.notification_id[:8], exc,
                        )
                        notif.status = NotificationStatus.FAILED
                        self._store[notif.notification_id] = notif
                else:
                    log.debug(
                        "NotificationDispatcher: no adapter for channel %s", channel.value
                    )
                    continue

            created.append(notif)

        return created

    async def broadcast(
        self,
        tenant_id:         str,
        recipient_ids:     list[str],
        notification_type: NotificationType,
        title:             str,
        body:              str,
        priority:          NotificationPriority     = NotificationPriority.NORMAL,
        reference_id:      str | None            = None,
        reference_type:    str | None            = None,
    ) -> dict[str, list[Notification]]:
        """Dispatch to multiple recipients in one call."""
        results: dict[str, list[Notification]] = {}
        for rid in recipient_ids:
            results[rid] = await self.dispatch(
                tenant_id         = tenant_id,
                recipient_id      = rid,
                notification_type = notification_type,
                title             = title,
                body              = body,
                priority          = priority,
                reference_id      = reference_id,
                reference_type    = reference_type,
            )
        return results

    # ── Inbox queries ──────────────────────────────────────────────────────────

    def inbox(
        self,
        tenant_id:    str,
        recipient_id: str,
        unread_only:  bool = False,
        limit:        int  = 50,
    ) -> list[Notification]:
        results = [
            n for n in self._store.values()
            if n.tenant_id == tenant_id
            and n.recipient_id == recipient_id
            and n.channel == NotificationChannel.IN_APP
            and not n.is_expired
            and (not unread_only or n.status == NotificationStatus.SENT)
        ]
        results.sort(key=lambda n: n.created_at, reverse=True)
        return results[:limit]

    def mark_read(self, tenant_id: str, notification_id: str) -> None:
        notif = self._store.get(notification_id)
        if notif and notif.tenant_id == tenant_id:
            notif.mark_read()

    def dismiss(self, tenant_id: str, notification_id: str) -> None:
        notif = self._store.get(notification_id)
        if notif and notif.tenant_id == tenant_id:
            notif.dismiss()

    def unread_count(self, tenant_id: str, recipient_id: str) -> int:
        return sum(
            1 for n in self._store.values()
            if n.tenant_id == tenant_id
            and n.recipient_id == recipient_id
            and n.channel == NotificationChannel.IN_APP
            and n.status == NotificationStatus.SENT
            and not n.is_expired
        )

    # ── Convenience factories ──────────────────────────────────────────────────

    async def send_quota_warning(
        self,
        tenant_id:    str,
        recipient_id: str,
        event_type:   str,
        utilization:  float,
    ) -> list[Notification]:
        pct = round(utilization * 100, 1)
        critical = utilization >= 0.95
        ntype    = (
            NotificationType.QUOTA_CRITICAL
            if critical
            else NotificationType.QUOTA_WARNING
        )
        priority = (
            NotificationPriority.CRITICAL
            if critical
            else NotificationPriority.HIGH
        )
        return await self.dispatch(
            tenant_id         = tenant_id,
            recipient_id      = recipient_id,
            notification_type = ntype,
            title             = f"Usage quota {'critical' if critical else 'warning'}: {event_type}",
            body              = f"{pct}% of your {event_type} quota has been consumed this period.",
            priority          = priority,
            reference_type    = "quota",
        )

    async def send_template_upgrade(
        self,
        tenant_id:       str,
        recipient_id:    str,
        template_name:   str,
        new_version:     str,
        notification_id: str,
    ) -> list[Notification]:
        return await self.dispatch(
            tenant_id         = tenant_id,
            recipient_id      = recipient_id,
            notification_type = NotificationType.TEMPLATE_UPGRADE_AVAILABLE,
            title             = f"New version available: {template_name}",
            body              = f"Version {new_version} of '{template_name}' is now available in the marketplace.",
            priority          = NotificationPriority.NORMAL,
            reference_id      = notification_id,
            reference_type    = "template_upgrade",
        )


# ── Singleton ──────────────────────────────────────────────────────────────────

_dispatcher: NotificationDispatcher | None = None


def get_notification_dispatcher(
    email_adapter:   DeliveryAdapter | None = None,
    webhook_adapter: DeliveryAdapter | None = None,
    slack_adapter:   DeliveryAdapter | None = None,
) -> NotificationDispatcher:
    global _dispatcher
    if _dispatcher is None:
        _dispatcher = NotificationDispatcher(
            email_adapter   = email_adapter,
            webhook_adapter = webhook_adapter,
            slack_adapter   = slack_adapter,
        )
    return _dispatcher
