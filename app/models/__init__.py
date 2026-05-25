"""
Model registry — import all models so that SQLAlchemy's mapper and Alembic's
autogenerate can discover them via Base.metadata.
"""

from app.models.base import Base  # noqa: F401

# meta
from app.models.meta.ingestion_batch import IngestionBatch  # noqa: F401

# ref
from app.models.reference.covered_entity import CoveredEntity  # noqa: F401
from app.models.reference.contract_pharmacy import ContractPharmacy  # noqa: F401
from app.models.reference.medicaid_exclusion import MedicaidExclusion  # noqa: F401
from app.models.reference.provider import Provider, ProviderTaxonomy  # noqa: F401
from app.models.reference.ndc_drug import NdcDrug  # noqa: F401

# ops
from app.models.operational.purchase import Purchase  # noqa: F401
from app.models.operational.dispense import Dispense  # noqa: F401
from app.models.operational.claim import Claim  # noqa: F401
from app.models.operational.split_billing import SplitBilling  # noqa: F401

# audit
from app.models.audit.compliance_rule import ComplianceRule  # noqa: F401
from app.models.audit.investigation_case import InvestigationCase  # noqa: F401
from app.models.audit.audit_finding import AuditFinding  # noqa: F401
from app.models.audit.reasoning_trace import ReasoningTrace  # noqa: F401
from app.models.audit.investigation_case_finding import InvestigationCaseFinding  # noqa: F401
from app.models.audit.investigation_timeline import InvestigationTimeline  # noqa: F401
from app.models.audit.agent_run import AgentRun  # noqa: F401
from app.models.audit.case_risk_snapshot import CaseRiskSnapshot  # noqa: F401
from app.models.audit.workflow_checkpoint import WorkflowCheckpoint  # noqa: F401

# Phase 7 — intelligence layer
from app.models.audit.monitoring_run import MonitoringRun  # noqa: F401
from app.models.audit.compliance_trend import ComplianceTrend  # noqa: F401
from app.models.audit.entity_risk_score import EntityRiskScore  # noqa: F401
from app.models.audit.cross_case_correlation import CrossCaseCorrelation  # noqa: F401
from app.models.audit.intelligence_graph_edge import IntelligenceGraphEdge  # noqa: F401
from app.models.audit.copilot_session import CopilotSession  # noqa: F401
from app.models.audit.analyst_override import AnalystOverride  # noqa: F401
from app.models.audit.evaluation_run import EvaluationRun  # noqa: F401
