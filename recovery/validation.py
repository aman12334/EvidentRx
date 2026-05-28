"""
Replay validation — verifies that replayed investigations produce identical
deterministic outputs to the original run.

Used for:
  - Model drift detection: re-run with same inputs, compare AI outputs
  - Determinism verification: rules engine always produces same findings
  - Regression testing: verify system changes don't alter compliance logic
  - Audit defense: prove findings were correctly derived from evidence

Validation workflow:
  1. Load original investigation's finding set from DB
  2. Replay the event stream to reconstruct inputs
  3. Re-run the rules engine with those inputs
  4. Compare finding sets (count, severity, rule codes)
  5. Generate validation report with diff

Deterministic expectation:
  - Rules engine findings MUST be identical on replay (same inputs)
  - AI narrative and confidence may differ (model nondeterminism is expected)
  - Any difference in deterministic findings is a CRITICAL violation
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing      import Any, Dict, List, Optional, Set, Tuple

log = logging.getLogger("evidentrx.recovery.validation")


@dataclass
class FindingFingerprint:
    """Minimal, hashable representation of a finding for comparison."""
    rule_code:  str
    severity:   str
    entity_id:  str
    finding_code: Optional[str] = None

    def __hash__(self) -> int:
        return hash((self.rule_code, self.severity, self.entity_id))

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, FindingFingerprint):
            return NotImplemented
        return (
            self.rule_code  == other.rule_code
            and self.severity   == other.severity
            and self.entity_id  == other.entity_id
        )


@dataclass
class ValidationResult:
    """Outcome of a replay validation run."""
    case_id:             str
    is_deterministic:    bool
    original_count:      int
    replayed_count:      int
    matched_count:       int
    missing_findings:    List[Dict[str, Any]] = field(default_factory=list)
    extra_findings:      List[Dict[str, Any]] = field(default_factory=list)
    severity_drift:      Dict[str, int] = field(default_factory=dict)
    validation_notes:    List[str] = field(default_factory=list)

    @property
    def delta(self) -> int:
        return abs(self.original_count - self.replayed_count)

    @property
    def match_rate(self) -> float:
        if self.original_count == 0:
            return 1.0
        return self.matched_count / self.original_count

    def as_dict(self) -> Dict[str, Any]:
        return {
            "case_id":          self.case_id,
            "is_deterministic": self.is_deterministic,
            "original_count":   self.original_count,
            "replayed_count":   self.replayed_count,
            "matched_count":    self.matched_count,
            "match_rate":       round(self.match_rate, 4),
            "delta":            self.delta,
            "missing_count":    len(self.missing_findings),
            "extra_count":      len(self.extra_findings),
            "severity_drift":   self.severity_drift,
            "validation_notes": self.validation_notes,
        }


class ReplayValidator:
    """
    Validates replay determinism by comparing original vs. replayed findings.
    """

    def validate(
        self,
        case_id:           str,
        original_findings: List[Dict[str, Any]],
        replayed_findings: List[Dict[str, Any]],
    ) -> ValidationResult:
        """
        Compare two finding sets and return a ValidationResult.
        """
        original_fps = {self._fingerprint(f) for f in original_findings}
        replayed_fps = {self._fingerprint(f) for f in replayed_findings}

        matched  = original_fps & replayed_fps
        missing  = original_fps - replayed_fps  # in original, not in replay
        extra    = replayed_fps - original_fps  # in replay, not in original

        # Severity distribution comparison
        orig_sev  = self._severity_dist(original_findings)
        rep_sev   = self._severity_dist(replayed_findings)
        sev_drift = {
            k: rep_sev.get(k, 0) - orig_sev.get(k, 0)
            for k in set(orig_sev) | set(rep_sev)
        }

        notes: List[str] = []
        if missing:
            notes.append(f"{len(missing)} finding(s) present in original but missing in replay")
        if extra:
            notes.append(f"{len(extra)} finding(s) in replay not found in original")
        if not missing and not extra:
            notes.append("Determinism verified — all findings reproduced exactly")

        is_deterministic = len(missing) == 0 and len(extra) == 0

        if not is_deterministic:
            log.error(
                "DETERMINISM VIOLATION: case=%s missing=%d extra=%d",
                case_id, len(missing), len(extra),
            )

        return ValidationResult(
            case_id=case_id,
            is_deterministic=is_deterministic,
            original_count=len(original_findings),
            replayed_count=len(replayed_findings),
            matched_count=len(matched),
            missing_findings=[self._fp_to_dict(fp) for fp in missing],
            extra_findings=[self._fp_to_dict(fp) for fp in extra],
            severity_drift={k: v for k, v in sev_drift.items() if v != 0},
            validation_notes=notes,
        )

    @staticmethod
    def _fingerprint(finding: Dict[str, Any]) -> FindingFingerprint:
        return FindingFingerprint(
            rule_code=finding.get("rule_code", ""),
            severity=finding.get("severity", ""),
            entity_id=finding.get("covered_entity_id", "") or finding.get("entity_id", ""),
            finding_code=finding.get("finding_code"),
        )

    @staticmethod
    def _severity_dist(findings: List[Dict[str, Any]]) -> Dict[str, int]:
        dist: Dict[str, int] = {}
        for f in findings:
            s = f.get("severity", "unknown")
            dist[s] = dist.get(s, 0) + 1
        return dist

    @staticmethod
    def _fp_to_dict(fp: FindingFingerprint) -> Dict[str, Any]:
        return {
            "rule_code":    fp.rule_code,
            "severity":     fp.severity,
            "entity_id":    fp.entity_id,
            "finding_code": fp.finding_code,
        }


replay_validator = ReplayValidator()
