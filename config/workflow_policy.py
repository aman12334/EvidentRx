"""
Workflow execution policy controls.

Governs how LangGraph workflows are permitted to run:
  - Maximum token budgets per node and per case
  - Confidence thresholds before escalation
  - Deterministic override protection (AI cannot override rules engine findings)
  - Human-in-the-loop checkpoint triggers
  - Maximum retries per workflow node

These policies are enforced by workflow_governance/policy.py at execution time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing      import Dict, Optional


@dataclass
class NodePolicy:
    """Per-workflow-node execution policy."""
    max_tokens:          int   = 4096
    timeout_seconds:     int   = 120
    max_retries:         int   = 2
    require_confidence:  float = 0.0    # 0.0 = no minimum
    require_human_review: bool = False


@dataclass
class WorkflowPolicy:
    """
    Global workflow execution policy.

    Invariant: deterministic compliance findings are NEVER modified by AI.
    AI outputs are advisory; the rules engine output is authoritative.
    """

    # ── Global limits ─────────────────────────────────────────────────────
    max_tokens_per_case:        int   = 50_000
    max_agent_runs_per_case:    int   = 20
    max_workflow_duration_sec:  int   = 900    # 15 minutes
    global_timeout_sec:         int   = 3600   # 1 hour hard stop

    # ── Confidence thresholds ─────────────────────────────────────────────
    min_confidence_for_close:   float = 0.80
    min_confidence_for_resolve: float = 0.70
    escalation_confidence_cap:  float = 0.95   # AI cannot claim >95% certainty

    # ── Deterministic protection ──────────────────────────────────────────
    # These rules are ENFORCED, not advisory:
    ai_cannot_reduce_severity:  bool = True    # severity only goes up
    ai_cannot_dismiss_finding:  bool = True    # findings require human close
    ai_cannot_modify_evidence:  bool = True    # evidence chain is immutable

    # ── Human-in-the-loop triggers ────────────────────────────────────────
    require_human_on_escalation:      bool = True
    require_human_on_critical_finding: bool = True
    require_human_on_low_confidence:  bool = True
    low_confidence_threshold:         float = 0.50

    # ── Per-node policies ─────────────────────────────────────────────────
    node_policies: Dict[str, NodePolicy] = field(default_factory=lambda: {
        "case_intake":         NodePolicy(max_tokens=2048, timeout_seconds=60),
        "evidence_aggregation": NodePolicy(max_tokens=4096, timeout_seconds=90),
        "pattern_analysis":    NodePolicy(max_tokens=4096, timeout_seconds=120),
        "risk_prioritization": NodePolicy(max_tokens=2048, timeout_seconds=60,
                                          require_confidence=0.60),
        "escalation_decision": NodePolicy(max_tokens=2048, timeout_seconds=60,
                                          require_confidence=0.70,
                                          require_human_review=True),
        "narrative_generation": NodePolicy(max_tokens=8192, timeout_seconds=180),
        "case_summary":        NodePolicy(max_tokens=4096, timeout_seconds=120),
    })

    def get_node_policy(self, node: str) -> NodePolicy:
        return self.node_policies.get(node, NodePolicy())

    def is_ai_action_permitted(
        self,
        action:     str,
        severity:   Optional[str] = None,
        confidence: Optional[float] = None,
    ) -> tuple[bool, str]:
        """
        Check whether an AI-proposed action is permitted under current policy.
        Returns (permitted, reason).
        """
        if action == "reduce_severity" and self.ai_cannot_reduce_severity:
            return False, "Policy: AI cannot reduce finding severity"
        if action == "dismiss_finding" and self.ai_cannot_dismiss_finding:
            return False, "Policy: AI cannot dismiss compliance findings"
        if action == "modify_evidence" and self.ai_cannot_modify_evidence:
            return False, "Policy: AI cannot modify evidence chain"

        if confidence is not None and confidence > self.escalation_confidence_cap:
            return False, (
                f"Policy: AI confidence {confidence:.2%} exceeds cap "
                f"{self.escalation_confidence_cap:.2%}"
            )

        return True, "permitted"


workflow_policy = WorkflowPolicy()
