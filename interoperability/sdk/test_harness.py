"""
Integration test harness for the interoperability layer.

Provides fixtures and helper classes for testing the full ingestion pipeline
end-to-end without requiring live EHR / pharmacy connections.

Usage in pytest tests
─────────────────────
  @pytest.mark.asyncio
  async def test_fhir_dispense_pipeline():
      async with InteropTestHarness(tenant_id="test_tenant") as harness:
          # Simulate 10 FHIR dispense records
          harness.simulate_fhir("MedicationDispense", count=10)

          # Run the ingestion pipeline
          result = await harness.run_pipeline(source="fhir")

          assert result.total_records == 10
          assert result.failed == 0
          assert harness.canonical_count("dispense") == 10

Design goals
────────────
  - Zero external dependencies (no Kafka, no DB, no EHR server)
  - Realistic data (uses Simulator classes for valid-format records)
  - Inspectable: canonical records and lineage accessible after run
  - Configurable: override any pipeline component for targeted testing
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime    import datetime, timezone
from typing      import Any, AsyncIterator, Optional

from interoperability.mapping.engine     import MappingEngine, get_mapping_engine
from interoperability.mapping.lineage    import LineageBuilder, LineageRecord, LineageStep, StepStatus
from interoperability.reconciliation.deduplication import DeduplicationEngine
from interoperability.reconciliation.quality       import DataQualityScorer
from interoperability.governance.policy            import get_policy_engine
from interoperability.streaming.event_bus          import InMemoryEventBus
from interoperability.streaming.producer           import InteropProducer
from interoperability.sdk.simulator                import FHIRSimulator, HL7Simulator, EDISimulator


@dataclass
class PipelineRunResult:
    """Summary of a test pipeline run."""
    total_records:    int
    canonical_count:  int
    failed:           int
    rejected:         int
    duplicates:       int
    duration_seconds: float
    errors:           list[str] = field(default_factory=list)


class InteropTestHarness:
    """
    Full-pipeline test harness for the interoperability layer.

    Creates isolated in-memory versions of all pipeline components and
    wires them together for end-to-end testing.
    """

    def __init__(
        self,
        tenant_id:    str = "test_tenant",
        seed:         int = 42,
        strict_mode:  bool = False,
    ) -> None:
        self.tenant_id   = tenant_id
        self._seed       = seed
        self._strict     = strict_mode

        # Components
        self._event_bus      = InMemoryEventBus()
        self._producer       = InteropProducer(event_bus=self._event_bus)
        self._mapping_engine = MappingEngine(strict=strict_mode)
        self._dedup_engine   = DeduplicationEngine()
        self._quality_scorer = DataQualityScorer()
        self._policy_engine  = get_policy_engine()
        self._lineage:       list[LineageRecord] = []

        # Simulator
        self._fhir_sim = FHIRSimulator(seed=seed)
        self._hl7_sim  = HL7Simulator(seed=seed)
        self._edi_sim  = EDISimulator(seed=seed)

        # Raw records queued for processing
        self._raw_queue: list[tuple[dict[str, Any], str, str]] = []
        # (record, source_system, resource_type)

    # ── Context manager ────────────────────────────────────────────────────────

    async def __aenter__(self) -> "InteropTestHarness":
        await self._event_bus.start()
        await self._producer.start()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self._producer.stop()
        await self._event_bus.close()

    # ── Record loading ─────────────────────────────────────────────────────────

    def simulate_fhir(self, resource_type: str, count: int = 5) -> "InteropTestHarness":
        """Queue synthetic FHIR records for processing."""
        records = self._fhir_sim.batch(resource_type, count, self.tenant_id)
        for r in records:
            self._raw_queue.append((r, "fhir", resource_type))
        return self

    def simulate_hl7(self, msg_type: str, count: int = 5) -> "InteropTestHarness":
        """Queue synthetic HL7 v2 message strings for processing."""
        from interoperability.hl7.parser import HL7Parser
        parser = HL7Parser()
        raw_msgs = self._hl7_sim.batch(msg_type, count, self.tenant_id)
        for raw in raw_msgs:
            msg = parser.parse(raw)
            if msg.is_valid:
                from interoperability.hl7.normalizer import normalise_hl7
                canonical = normalise_hl7(msg, self.tenant_id)
                if canonical:
                    self._raw_queue.append((canonical, "hl7v2", msg_type))
        return self

    def simulate_edi(self, count: int = 5) -> "InteropTestHarness":
        """Queue synthetic X12 837P EDI claims for processing."""
        from interoperability.edi.x12_parser      import X12Parser
        from interoperability.edi.pharmacy_claims import normalise_837p
        parser = X12Parser()
        raw_strs = self._edi_sim.batch(count, self.tenant_id)
        for raw in raw_strs:
            interchange = parser.parse(raw)
            canonicals  = normalise_837p(interchange, self.tenant_id)
            for c in canonicals:
                self._raw_queue.append((c, "x12_837p", "837P"))
        return self

    def load_raw(
        self,
        record:        dict[str, Any],
        source_system: str,
        resource_type: str,
    ) -> "InteropTestHarness":
        """Manually queue a raw record for processing."""
        self._raw_queue.append((record, source_system, resource_type))
        return self

    # ── Pipeline execution ─────────────────────────────────────────────────────

    async def run_pipeline(self) -> PipelineRunResult:
        """
        Process all queued records through the full pipeline.

        Steps:
          1. Map to canonical
          2. Deduplication check
          3. Policy evaluation
          4. Data quality scoring
          5. Publish to event bus
        """
        started = datetime.now(tz=timezone.utc)
        failed = rejected = duplicates = canonical_count = 0
        errors: list[str] = []

        for raw_record, source, rtype in self._raw_queue:
            builder = LineageBuilder.start(source, rtype, self.tenant_id)
            builder.step(LineageStep.INGEST, StepStatus.SUCCESS)

            # ── Map ────────────────────────────────────────────────────────────
            # For FHIR, pass through MappingEngine; others arrive pre-normalised
            if source == "fhir":
                result = self._mapping_engine.map(raw_record, source, self.tenant_id, rtype)
                if not result.success:
                    builder.step(LineageStep.NORMALISE, StepStatus.FAILED, str(result.errors))
                    failed += 1
                    errors.extend(result.errors)
                    self._lineage.append(builder.build())
                    continue
                canonical = result.canonical
            else:
                canonical = raw_record   # already canonical

            builder.step(LineageStep.NORMALISE, StepStatus.SUCCESS)

            # ── Dedup ──────────────────────────────────────────────────────────
            dedup_result = self._dedup_engine.check(canonical)
            if dedup_result.is_duplicate:
                builder.step(LineageStep.CHECKSUM, StepStatus.WARNING, "Duplicate suppressed")
                duplicates += 1
                self._lineage.append(builder.build(canonical_type=canonical.get("canonical_type")))
                continue

            builder.step(LineageStep.CHECKSUM, StepStatus.SUCCESS)

            # ── Policy ─────────────────────────────────────────────────────────
            ctx = {"tenant_id": self.tenant_id, "source_system": source}
            passed, _ = self._policy_engine.evaluate(canonical, ctx)
            if not passed:
                builder.step(LineageStep.VALIDATE_CANON, StepStatus.FAILED, "Policy violation")
                rejected += 1
                self._lineage.append(builder.build(canonical_type=canonical.get("canonical_type")))
                continue

            builder.step(LineageStep.VALIDATE_CANON, StepStatus.SUCCESS)

            # ── Quality ────────────────────────────────────────────────────────
            quality = self._quality_scorer.score(canonical)
            # Log quality but do not block (quality is informational in test mode)

            # ── Publish ────────────────────────────────────────────────────────
            await self._producer.publish_canonical(canonical, self.tenant_id)
            builder.step(LineageStep.PERSIST, StepStatus.SUCCESS)

            self._lineage.append(
                builder.build(
                    canonical_type = canonical.get("canonical_type"),
                    checksum       = None,
                )
            )
            canonical_count += 1

        await self._producer.flush()

        finished = datetime.now(tz=timezone.utc)
        return PipelineRunResult(
            total_records    = len(self._raw_queue),
            canonical_count  = canonical_count,
            failed           = failed,
            rejected         = rejected,
            duplicates       = duplicates,
            duration_seconds = (finished - started).total_seconds(),
            errors           = errors,
        )

    # ── Inspection API ─────────────────────────────────────────────────────────

    def canonical_count(self, canonical_type: str) -> int:
        """Return how many canonical records of a given type were published."""
        from interoperability.streaming.event_bus import canonical_topic
        topic    = canonical_topic(self.tenant_id, canonical_type)
        messages = self._event_bus.get_messages(topic)
        return len(messages)

    def get_canonicals(self, canonical_type: str) -> list[dict[str, Any]]:
        """Return all published canonical records of a given type."""
        from interoperability.streaming.event_bus import canonical_topic
        topic = canonical_topic(self.tenant_id, canonical_type)
        return [m.payload for m in self._event_bus.get_messages(topic)]

    def get_lineage(self) -> list[LineageRecord]:
        """Return all lineage records produced during the run."""
        return list(self._lineage)

    def reset(self) -> None:
        """Clear the raw queue and canonical store for re-use."""
        self._raw_queue.clear()
        self._lineage.clear()
        self._event_bus.clear_history()
        self._dedup_engine = DeduplicationEngine()
