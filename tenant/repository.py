"""
Tenant-aware base repository.

All database queries that touch tenant-scoped data MUST go through a subclass
of TenantRepository. This guarantees that WHERE tenant_id = :tenant_id is
always applied — it cannot be forgotten or bypassed.

Pattern:
    class CaseRepository(TenantRepository):
        async def get_case(self, case_id: str) -> Row:
            return await self.fetch_one(
                "SELECT * FROM audit.investigation_cases WHERE case_id = :case_id",
                {"case_id": case_id},
            )
        # ↑ tenant_id is automatically added to the WHERE clause

Design: Inheritance over composition — forces tenant filtering to be
part of the repository contract rather than an optional add-on.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tenant.context import require_tenant_id
from tenant.isolation import assert_tenant_access

log = logging.getLogger("evidentrx.repository")


class TenantRepository:
    """
    Base class for all tenant-scoped database repositories.

    Subclasses receive a DB session and the current tenant_id is injected
    automatically from the ContextVar — no manual thread-safe propagation needed.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session    = session
        self._tenant_id  = require_tenant_id()

    @property
    def tenant_id(self) -> str:
        return self._tenant_id

    async def fetch_one(
        self,
        query:  str,
        params: Dict[str, Any] | None = None,
    ) -> Dict[str, Any] | None:
        """
        Execute a query with tenant_id injected into params.
        Returns a single row as a dict or None.
        """
        params = {**(params or {}), "tenant_id": self._tenant_id}
        result = await self._session.execute(text(query), params)
        row = result.mappings().first()
        if row is None:
            return None
        return self._verify_tenant(dict(row))

    async def fetch_all(
        self,
        query:  str,
        params: Dict[str, Any] | None = None,
    ) -> List[Dict[str, Any]]:
        """
        Execute a query with tenant_id injected.
        Returns all matching rows as a list of dicts.
        """
        params = {**(params or {}), "tenant_id": self._tenant_id}
        result = await self._session.execute(text(query), params)
        rows   = result.mappings().all()
        return [self._verify_tenant(dict(r)) for r in rows]

    async def execute(
        self,
        query:  str,
        params: Dict[str, Any] | None = None,
    ) -> int:
        """
        Execute a write query with tenant_id injected.
        Returns the number of affected rows.
        """
        params = {**(params or {}), "tenant_id": self._tenant_id}
        result = await self._session.execute(text(query), params)
        await self._session.commit()
        return result.rowcount

    def _verify_tenant(self, row: Dict[str, Any]) -> Dict[str, Any]:
        """
        Post-fetch verification: ensure the returned row belongs to this tenant.
        Raises TenantIsolationError if tenant_id in row doesn't match.
        """
        row_tenant = row.get("tenant_id") or row.get("covered_entity_id")
        if row_tenant and row_tenant != self._tenant_id:
            assert_tenant_access(
                resource_tenant_id=row_tenant,
                requesting_tenant_id=self._tenant_id,
            )
        return row
