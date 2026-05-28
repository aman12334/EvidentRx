"""
Tenant workload partitioning.

Distributes investigation and workflow execution workloads across
multiple processing queues to prevent a single large tenant from
starving smaller tenants. Each tenant is assigned a partition slot
based on their tier; slots map to physical queue workers.

Partitioning strategy
─────────────────────
  HASH      — consistent hash of tenant_id mod num_partitions
  TIER      — fixed partition pools per tier (ENTERPRISE gets dedicated)
  ROUND_ROBIN — distribute submissions evenly (stateful)
  DEDICATED — one partition exclusively for this tenant (ENTERPRISE+)
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

log = logging.getLogger("evidentrx.saas.scaling.partitioning")


class PartitionStrategy(str, Enum):
    HASH        = "hash"
    TIER        = "tier"
    ROUND_ROBIN = "round_robin"
    DEDICATED   = "dedicated"


@dataclass
class PartitionAssignment:
    """Maps a tenant to a partition slot."""
    tenant_id:      str
    partition_id:   str       # e.g. "partition_04"
    strategy:       PartitionStrategy
    queue_name:     str       # physical queue identifier
    assigned_at:    datetime  = field(default_factory=lambda: datetime.now(tz=UTC))
    dedicated:      bool      = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "tenant_id":    self.tenant_id,
            "partition_id": self.partition_id,
            "strategy":     self.strategy.value,
            "queue_name":   self.queue_name,
            "dedicated":    self.dedicated,
        }


# Tier → pool of partition slots (ENTERPRISE gets dedicated, others share)
_TIER_POOLS: dict[str, list[str]] = {
    "starter":      ["partition_00", "partition_01"],
    "professional": ["partition_02", "partition_03", "partition_04"],
    "enterprise":   [],   # dedicated per tenant (generated dynamically)
    "government":   [],   # dedicated per tenant
}


class TenantQueuePartitioner:
    """
    Assigns tenants to processing queue partitions.

    Enterprise/Government tenants receive dedicated partitions ensuring
    complete workload isolation. Starter/Professional tenants share
    partition pools, with consistent hashing so a tenant always lands
    on the same partition (avoiding queue ordering issues).
    """

    def __init__(
        self,
        strategy:       PartitionStrategy = PartitionStrategy.TIER,
        num_partitions: int               = 8,
    ) -> None:
        self._strategy       = strategy
        self._num_partitions = num_partitions
        self._assignments:   dict[str, PartitionAssignment] = {}
        self._rr_counter:    int = 0   # round-robin state

    def assign(
        self,
        tenant_id: str,
        tier:      str,
    ) -> PartitionAssignment:
        """Return (or create) the partition assignment for a tenant."""
        existing = self._assignments.get(tenant_id)
        if existing:
            return existing

        if self._strategy == PartitionStrategy.DEDICATED or tier in ("enterprise", "government"):
            assignment = self._dedicated(tenant_id, tier)
        elif self._strategy == PartitionStrategy.ROUND_ROBIN:
            assignment = self._round_robin(tenant_id)
        elif self._strategy == PartitionStrategy.TIER:
            assignment = self._tier_based(tenant_id, tier)
        else:  # HASH
            assignment = self._hash_based(tenant_id)

        self._assignments[tenant_id] = assignment
        log.info(
            "TenantQueuePartitioner: assigned tenant %s → %s (%s)",
            tenant_id[:8], assignment.partition_id, assignment.strategy.value,
        )
        return assignment

    def get_queue(self, tenant_id: str) -> str | None:
        a = self._assignments.get(tenant_id)
        return a.queue_name if a else None

    def list_assignments(self) -> list[PartitionAssignment]:
        return list(self._assignments.values())

    def rebalance(self, tenant_id: str, tier: str) -> PartitionAssignment:
        """Force re-assignment (e.g. after tier upgrade)."""
        self._assignments.pop(tenant_id, None)
        return self.assign(tenant_id, tier)

    # ── Private strategies ─────────────────────────────────────────────────────

    def _dedicated(self, tenant_id: str, tier: str) -> PartitionAssignment:
        slug  = tenant_id.replace("-", "")[:8]
        pid   = f"partition_dedicated_{slug}"
        queue = f"queue.{tier}.{slug}"
        return PartitionAssignment(
            tenant_id    = tenant_id,
            partition_id = pid,
            strategy     = PartitionStrategy.DEDICATED,
            queue_name   = queue,
            dedicated    = True,
        )

    def _tier_based(self, tenant_id: str, tier: str) -> PartitionAssignment:
        pool = _TIER_POOLS.get(tier.lower(), _TIER_POOLS["starter"])
        if not pool:
            return self._dedicated(tenant_id, tier)
        # Consistent hash within pool
        idx = int(hashlib.sha256(tenant_id.encode()).hexdigest(), 16) % len(pool)
        pid = pool[idx]
        return PartitionAssignment(
            tenant_id    = tenant_id,
            partition_id = pid,
            strategy     = PartitionStrategy.TIER,
            queue_name   = f"queue.{pid}",
        )

    def _hash_based(self, tenant_id: str) -> PartitionAssignment:
        idx = int(hashlib.sha256(tenant_id.encode()).hexdigest(), 16) % self._num_partitions
        pid = f"partition_{idx:02d}"
        return PartitionAssignment(
            tenant_id    = tenant_id,
            partition_id = pid,
            strategy     = PartitionStrategy.HASH,
            queue_name   = f"queue.{pid}",
        )

    def _round_robin(self, tenant_id: str) -> PartitionAssignment:
        idx = self._rr_counter % self._num_partitions
        self._rr_counter += 1
        pid = f"partition_{idx:02d}"
        return PartitionAssignment(
            tenant_id    = tenant_id,
            partition_id = pid,
            strategy     = PartitionStrategy.ROUND_ROBIN,
            queue_name   = f"queue.{pid}",
        )


# ── Singleton ──────────────────────────────────────────────────────────────────

_partitioner: TenantQueuePartitioner | None = None


def get_partitioner(
    strategy:       PartitionStrategy = PartitionStrategy.TIER,
    num_partitions: int               = 8,
) -> TenantQueuePartitioner:
    global _partitioner
    if _partitioner is None:
        _partitioner = TenantQueuePartitioner(
            strategy       = strategy,
            num_partitions = num_partitions,
        )
    return _partitioner
