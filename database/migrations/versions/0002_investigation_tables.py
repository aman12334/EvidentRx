"""Investigation & Case Orchestration Infrastructure — Phase 4

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-23

Adds:
  audit.investigation_case_findings  — case↔finding junction with provenance
  audit.investigation_timelines      — append-only event log
  audit.agent_runs                   — agent execution ledger
  audit.case_risk_snapshots          — immutable aggregation snapshots
  audit.workflow_checkpoints         — LangGraph pause/resume state
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. investigation_case_findings
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE audit.investigation_case_findings (
            id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            case_id     UUID        NOT NULL
                                    REFERENCES audit.investigation_cases(case_id)
                                    ON DELETE CASCADE,
            finding_id  UUID        NOT NULL
                                    REFERENCES audit.audit_findings(finding_id),
            is_primary  BOOLEAN     NOT NULL DEFAULT FALSE,
            added_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            added_by    VARCHAR(100) NOT NULL DEFAULT 'case_builder',
            UNIQUE (case_id, finding_id)
        )
    """)
    op.execute("CREATE INDEX ix_icf_case_id    ON audit.investigation_case_findings (case_id)")
    op.execute("CREATE INDEX ix_icf_finding_id ON audit.investigation_case_findings (finding_id)")

    # ------------------------------------------------------------------
    # 2. investigation_timelines
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE audit.investigation_timelines (
            event_id        UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            case_id         UUID        NOT NULL
                                        REFERENCES audit.investigation_cases(case_id)
                                        ON DELETE CASCADE,
            event_type      VARCHAR(50) NOT NULL,
            event_data      JSONB       NOT NULL DEFAULT '{}',
            actor_id        VARCHAR(255),
            actor_type      VARCHAR(50) NOT NULL DEFAULT 'system',
            occurred_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            sequence_number BIGSERIAL   NOT NULL
        )
    """)
    op.execute("CREATE INDEX ix_it_case_id    ON audit.investigation_timelines (case_id, occurred_at)")
    op.execute("CREATE INDEX ix_it_event_type ON audit.investigation_timelines (event_type)")
    op.execute("""
        COMMENT ON TABLE audit.investigation_timelines IS
            'Append-only event log — rows must never be updated or deleted.'
    """)

    # ------------------------------------------------------------------
    # 3. agent_runs
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE audit.agent_runs (
            agent_run_id    UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            case_id         UUID        NOT NULL
                                        REFERENCES audit.investigation_cases(case_id),
            agent_type      VARCHAR(100) NOT NULL,
            agent_name      VARCHAR(255),
            status          VARCHAR(30) NOT NULL DEFAULT 'pending',
            input_payload   JSONB       NOT NULL DEFAULT '{}',
            output_payload  JSONB,
            started_at      TIMESTAMPTZ,
            completed_at    TIMESTAMPTZ,
            error_message   TEXT,
            model_id        VARCHAR(100),
            token_usage     JSONB,
            workflow_run_id VARCHAR(255),
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_agent_run_status
                CHECK (status IN ('pending','running','completed','failed','cancelled'))
        )
    """)
    op.execute("CREATE INDEX ix_ar_case_id      ON audit.agent_runs (case_id)")
    op.execute("CREATE INDEX ix_ar_workflow_run ON audit.agent_runs (workflow_run_id)")
    op.execute("""
        CREATE INDEX ix_ar_status ON audit.agent_runs (status)
        WHERE status IN ('pending','running')
    """)

    # ------------------------------------------------------------------
    # 4. case_risk_snapshots
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE audit.case_risk_snapshots (
            snapshot_id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            case_id                  UUID        NOT NULL
                                                 REFERENCES audit.investigation_cases(case_id),
            snapshot_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            snapshot_trigger         VARCHAR(50) NOT NULL DEFAULT 'manual',
            total_findings           INTEGER     NOT NULL DEFAULT 0,
            critical_findings        INTEGER     NOT NULL DEFAULT 0,
            high_findings            INTEGER     NOT NULL DEFAULT 0,
            medium_findings          INTEGER     NOT NULL DEFAULT 0,
            low_findings             INTEGER     NOT NULL DEFAULT 0,
            total_financial_exposure NUMERIC(15,2),
            composite_risk_score     NUMERIC(5,4),
            findings_by_rule         JSONB       NOT NULL DEFAULT '{}',
            ndc_list                 JSONB       NOT NULL DEFAULT '[]',
            temporal_window_start    DATE,
            temporal_window_end      DATE,
            unique_patients          INTEGER,
            unique_pharmacies        INTEGER,
            CONSTRAINT ck_snapshot_trigger
                CHECK (snapshot_trigger IN
                    ('case_created','finding_added','manual','scheduled','status_changed'))
        )
    """)
    op.execute("CREATE INDEX ix_crs_case_id ON audit.case_risk_snapshots (case_id, snapshot_at DESC)")
    op.execute("""
        COMMENT ON TABLE audit.case_risk_snapshots IS
            'Immutable snapshots — never update after insert. Each trigger creates a new row.'
    """)

    # ------------------------------------------------------------------
    # 5. workflow_checkpoints
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE audit.workflow_checkpoints (
            checkpoint_id   UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            case_id         UUID        NOT NULL
                                        REFERENCES audit.investigation_cases(case_id),
            agent_run_id    UUID        REFERENCES audit.agent_runs(agent_run_id),
            workflow_name   VARCHAR(255) NOT NULL,
            checkpoint_name VARCHAR(255) NOT NULL,
            state_data      JSONB       NOT NULL DEFAULT '{}',
            is_resumable    BOOLEAN     NOT NULL DEFAULT TRUE,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX ix_wc_case_id   ON audit.workflow_checkpoints (case_id)")
    op.execute("""
        CREATE INDEX ix_wc_resumable ON audit.workflow_checkpoints (case_id, is_resumable)
        WHERE is_resumable = TRUE
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS audit.workflow_checkpoints CASCADE")
    op.execute("DROP TABLE IF EXISTS audit.case_risk_snapshots CASCADE")
    op.execute("DROP TABLE IF EXISTS audit.agent_runs CASCADE")
    op.execute("DROP TABLE IF EXISTS audit.investigation_timelines CASCADE")
    op.execute("DROP TABLE IF EXISTS audit.investigation_case_findings CASCADE")
