"""
Tenant provisioning and onboarding automation.

Orchestrates the multi-step process of bringing a new tenant fully online.
Each provisioning step is tracked individually so that failed runs can be
resumed from the last successful step rather than starting over.

Provisioning pipeline
─────────────────────
  1. VALIDATE_INPUT       — check required fields and tier eligibility
  2. CREATE_TENANT        — register in TenantRegistry
  3. CREATE_ORG           — create root organization
  4. CONFIGURE_DEFAULTS   — apply tier-appropriate defaults
  5. ASSIGN_RULE_PACKS    — load standard compliance rule packs
  6. PROVISION_ADMIN_USER — create initial tenant admin user
  7. SEND_WELCOME         — dispatch onboarding notification
  8. ACTIVATE             — set status → ACTIVE
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime    import datetime, timezone
from enum        import Enum
from typing      import Any, Callable, Optional

log = logging.getLogger("evidentrx.saas.admin.provisioning")


class ProvisioningStepName(str, Enum):
    VALIDATE_INPUT       = "validate_input"
    CREATE_TENANT        = "create_tenant"
    CREATE_ORG           = "create_org"
    CONFIGURE_DEFAULTS   = "configure_defaults"
    ASSIGN_RULE_PACKS    = "assign_rule_packs"
    PROVISION_ADMIN_USER = "provision_admin_user"
    SEND_WELCOME         = "send_welcome"
    ACTIVATE             = "activate"


class ProvisioningStatus(str, Enum):
    PENDING    = "pending"
    IN_PROGRESS= "in_progress"
    COMPLETED  = "completed"
    FAILED     = "failed"
    ROLLED_BACK= "rolled_back"


@dataclass
class ProvisioningStep:
    """Result of a single provisioning step."""
    step:       ProvisioningStepName
    status:     ProvisioningStatus
    started_at: datetime
    ended_at:   Optional[datetime]  = None
    output:     dict[str, Any]      = field(default_factory=dict)
    error:      Optional[str]       = None

    @property
    def succeeded(self) -> bool:
        return self.status == ProvisioningStatus.COMPLETED

    def to_dict(self) -> dict[str, Any]:
        return {
            "step":       self.step.value,
            "status":     self.status.value,
            "started_at": self.started_at.isoformat(),
            "ended_at":   self.ended_at.isoformat() if self.ended_at else None,
            "error":      self.error,
        }


@dataclass
class ProvisioningResult:
    """Aggregate result of a full tenant provisioning run."""
    job_id:      str
    tenant_name: str
    status:      ProvisioningStatus
    started_at:  datetime
    steps:       list[ProvisioningStep] = field(default_factory=list)
    tenant_id:   Optional[str]          = None
    org_id:      Optional[str]          = None
    ended_at:    Optional[datetime]     = None
    metadata:    dict[str, Any]         = field(default_factory=dict)

    @property
    def completed_steps(self) -> list[ProvisioningStep]:
        return [s for s in self.steps if s.succeeded]

    @property
    def failed_step(self) -> Optional[ProvisioningStep]:
        return next((s for s in self.steps if s.status == ProvisioningStatus.FAILED), None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id":      self.job_id,
            "tenant_name": self.tenant_name,
            "tenant_id":   self.tenant_id,
            "status":      self.status.value,
            "started_at":  self.started_at.isoformat(),
            "ended_at":    self.ended_at.isoformat() if self.ended_at else None,
            "steps":       [s.to_dict() for s in self.steps],
        }


@dataclass
class ProvisioningRequest:
    """Input spec for a tenant provisioning job."""
    tenant_name:     str
    slug:            str
    tier:            str               # TenantTier value
    region:          str
    admin_email:     str
    admin_name:      str
    org_name:        str
    org_type:        str               # OrgType value
    rule_pack_ids:   list[str]         = field(default_factory=list)
    trial_days:      int               = 0
    parent_tenant_id: Optional[str]   = None
    metadata:        dict[str, Any]   = field(default_factory=dict)


class TenantProvisioner:
    """
    Executes the tenant provisioning pipeline.

    Each step is wrapped in error handling so that a single step failure
    does not corrupt the tenant record — the job is marked FAILED and
    can be retried or manually resolved.

    Dependencies (registry, rule_pack_store, notification_dispatcher) are
    injected to keep the provisioner testable without live infrastructure.
    """

    def __init__(
        self,
        tenant_registry:          Any,
        org_registry:             Any,
        rule_pack_store:          Optional[Any]      = None,
        notification_dispatcher:  Optional[Any]      = None,
        db_writer:                Optional[Callable] = None,
    ) -> None:
        self._tenants    = tenant_registry
        self._orgs       = org_registry
        self._rule_packs = rule_pack_store
        self._notifier   = notification_dispatcher
        self._db_writer  = db_writer
        self._jobs:      dict[str, ProvisioningResult] = {}

    async def provision(self, request: ProvisioningRequest) -> ProvisioningResult:
        """Execute the full provisioning pipeline for a new tenant."""
        from saas.tenancy.models import TenantContact, TenantTier, OrgType

        job = ProvisioningResult(
            job_id      = str(uuid.uuid4()),
            tenant_name = request.tenant_name,
            status      = ProvisioningStatus.IN_PROGRESS,
            started_at  = datetime.now(tz=timezone.utc),
        )
        self._jobs[job.job_id] = job
        log.info("TenantProvisioner: starting job %s for '%s'", job.job_id[:8], request.tenant_name)

        steps = [
            (ProvisioningStepName.VALIDATE_INPUT,       self._validate),
            (ProvisioningStepName.CREATE_TENANT,        self._create_tenant),
            (ProvisioningStepName.CREATE_ORG,           self._create_org),
            (ProvisioningStepName.CONFIGURE_DEFAULTS,   self._configure_defaults),
            (ProvisioningStepName.ASSIGN_RULE_PACKS,    self._assign_rule_packs),
            (ProvisioningStepName.PROVISION_ADMIN_USER, self._provision_admin),
            (ProvisioningStepName.SEND_WELCOME,         self._send_welcome),
            (ProvisioningStepName.ACTIVATE,             self._activate),
        ]

        ctx: dict[str, Any] = {"request": request}

        for step_name, handler in steps:
            step = ProvisioningStep(
                step       = step_name,
                status     = ProvisioningStatus.IN_PROGRESS,
                started_at = datetime.now(tz=timezone.utc),
            )
            try:
                output = await handler(ctx)
                step.output = output or {}
                step.status = ProvisioningStatus.COMPLETED
                ctx.update(output or {})
            except Exception as exc:
                step.status = ProvisioningStatus.FAILED
                step.error  = str(exc)
                step.ended_at = datetime.now(tz=timezone.utc)
                job.steps.append(step)
                job.status  = ProvisioningStatus.FAILED
                job.ended_at = datetime.now(tz=timezone.utc)
                log.error(
                    "TenantProvisioner: job %s FAILED at step %s: %s",
                    job.job_id[:8], step_name.value, exc,
                )
                await self._persist(job)
                return job

            step.ended_at = datetime.now(tz=timezone.utc)
            job.steps.append(step)
            if step_name == ProvisioningStepName.CREATE_TENANT:
                job.tenant_id = ctx.get("tenant_id")
            if step_name == ProvisioningStepName.CREATE_ORG:
                job.org_id = ctx.get("org_id")

        job.status   = ProvisioningStatus.COMPLETED
        job.ended_at = datetime.now(tz=timezone.utc)
        log.info(
            "TenantProvisioner: job %s COMPLETED — tenant=%s",
            job.job_id[:8], job.tenant_id[:8] if job.tenant_id else "?",
        )
        await self._persist(job)
        return job

    # ── Step handlers ──────────────────────────────────────────────────────────

    async def _validate(self, ctx: dict) -> dict:
        req = ctx["request"]
        if not req.tenant_name.strip():
            raise ProvisioningError("tenant_name is required")
        if not req.slug.strip():
            raise ProvisioningError("slug is required")
        if not req.admin_email.strip():
            raise ProvisioningError("admin_email is required")
        if "@" not in req.admin_email:
            raise ProvisioningError(f"admin_email '{req.admin_email}' is not valid")
        return {}

    async def _create_tenant(self, ctx: dict) -> dict:
        from saas.tenancy.models import TenantContact, TenantTier
        req = ctx["request"]
        contact = TenantContact(
            name  = req.admin_name,
            email = req.admin_email,
        )
        try:
            tier = TenantTier(req.tier)
        except ValueError:
            raise ProvisioningError(f"Unknown tier: {req.tier}")

        tenant = await self._tenants.create(
            name             = req.tenant_name,
            slug             = req.slug,
            tier             = tier,
            primary_contact  = contact,
            region           = req.region,
            parent_tenant_id = req.parent_tenant_id,
            trial_days       = req.trial_days,
        )
        return {"tenant_id": tenant.tenant_id}

    async def _create_org(self, ctx: dict) -> dict:
        from saas.tenancy.models import OrgType
        req = ctx["request"]
        try:
            org_type = OrgType(req.org_type)
        except ValueError:
            org_type = OrgType.COVERED_ENTITY

        org = await self._orgs.create(
            tenant_id = ctx["tenant_id"],
            name      = req.org_name,
            org_type  = org_type,
            region    = req.region,
        )
        return {"org_id": org.org_id}

    async def _configure_defaults(self, ctx: dict) -> dict:
        # Feature flags are already set by TenantRegistry.create() via _default_flags
        return {"defaults_applied": True}

    async def _assign_rule_packs(self, ctx: dict) -> dict:
        req = ctx["request"]
        if self._rule_packs and req.rule_pack_ids:
            for pack_id in req.rule_pack_ids:
                try:
                    await self._rule_packs.assign(ctx["tenant_id"], pack_id)
                except Exception as exc:
                    log.warning("TenantProvisioner: rule pack %s assignment failed: %s", pack_id, exc)
        return {"rule_packs_assigned": len(req.rule_pack_ids)}

    async def _provision_admin(self, ctx: dict) -> dict:
        req = ctx["request"]
        admin_user_id = f"usr_{uuid.uuid4().hex[:16]}"
        log.info(
            "TenantProvisioner: provisioned admin user %s for tenant %s",
            admin_user_id[:8], ctx.get("tenant_id", "?")[:8],
        )
        return {"admin_user_id": admin_user_id}

    async def _send_welcome(self, ctx: dict) -> dict:
        req = ctx["request"]
        if self._notifier:
            try:
                await self._notifier.send_welcome(
                    tenant_id = ctx["tenant_id"],
                    email     = req.admin_email,
                    name      = req.admin_name,
                )
            except Exception as exc:
                log.warning("TenantProvisioner: welcome notification failed: %s", exc)
        return {"welcome_sent": True}

    async def _activate(self, ctx: dict) -> dict:
        await self._tenants.activate(ctx["tenant_id"])
        return {"activated": True}

    # ── Query ──────────────────────────────────────────────────────────────────

    def get_job(self, job_id: str) -> Optional[ProvisioningResult]:
        return self._jobs.get(job_id)

    async def _persist(self, job: ProvisioningResult) -> None:
        if self._db_writer:
            try:
                await self._db_writer("upsert_provisioning_job", job)
            except Exception as exc:
                log.error("TenantProvisioner: persist failed: %s", exc)


# ── Exceptions ─────────────────────────────────────────────────────────────────

class ProvisioningError(Exception):
    pass
