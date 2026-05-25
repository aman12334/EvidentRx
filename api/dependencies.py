"""Shared FastAPI dependencies — database session, pagination, auth stubs."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Generator

from fastapi import Depends, Header, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db


@dataclass
class PaginationParams:
    page:  int
    limit: int

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.limit


def pagination(
    page:  int = Query(1, ge=1, description="Page number"),
    limit: int = Query(25, ge=1, le=200, description="Items per page"),
) -> PaginationParams:
    return PaginationParams(page=page, limit=limit)


def require_analyst(x_analyst_id: str = Header(..., alias="X-Analyst-Id")) -> str:
    """
    RBAC stub — validates analyst identity header.
    In production this would verify a JWT or session token.
    """
    if not x_analyst_id or len(x_analyst_id) < 3:
        raise HTTPException(status_code=401, detail="Valid X-Analyst-Id header required")
    return x_analyst_id
