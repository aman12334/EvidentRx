# EvidentRx — 340B Compliance Audit & Investigation Platform

Enterprise-grade 340B pharmaceutical compliance platform built for covered entities, contract pharmacies, and compliance teams. Detects violations, investigates patterns, and generates audit-ready documentation.

---

## What is 340B?

The 340B Drug Pricing Program requires pharmaceutical manufacturers to provide outpatient drugs to eligible health care organizations at significantly reduced prices. Violations — duplicate discounts, Medicaid overlap, contract pharmacy eligibility breaches — carry serious regulatory and financial consequences.

---

## Platform Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        EvidentRx                            │
├─────────────────────────────────────────────────────────────┤
│  Phase 1 │ PostgreSQL Schema (ref / ops / audit / meta)     │
│  Phase 2 │ Synthetic Operational Simulation                 │
│  Phase 3 │ Deterministic Rules Engine                       │
│  Phase 4 │ Investigation & Case Orchestration               │
│  Phase 5 │ Agentic Investigation Runtime (LangGraph)        │
└─────────────────────────────────────────────────────────────┘
```

**Core principle:** The deterministic rules engine is the single source of truth for compliance violations. LLMs are used only for analysis, summarization, and audit narrative — never for violation determination.

---

## Phase 1 — PostgreSQL Schema

Four schema namespaces:

| Namespace | Purpose |
|---|---|
| `ref` | Reference data — covered entities, contract pharmacies, NDC drugs, providers (SCD2) |
| `ops` | Operational transactions — purchases, dispenses, claims, split billing (partitioned) |
| `audit` | Compliance findings, investigation cases, reasoning traces |
| `meta` | Ingestion batch tracking |

- SCD Type 2 for all reference tables
- Range-partitioned operational tables (composite PKs)
- Immutable evidence snapshots on `audit.audit_findings`
- Append-only `audit.reasoning_traces` for AI reasoning lineage

---

## Phase 2 — Synthetic Operational Simulation

Event-driven healthcare workflow simulation for generating realistic 340B transaction data.

**Causal chain:**
```
Purchase → InventoryPool (FIFO) → Dispense → Claim → SplitBilling
```

**Violation types injected at generation time:**
- Duplicate discount (340B purchase + Medicaid billing)
- Contract pharmacy eligibility (unregistered pharmacy)
- Split billing mismatch (negative accumulator balance)
- Temporal mismatch (dispense before CE program start)

```bash
python run_simulation.py --ces 50 --weeks 52 --violation-rate 0.07 --seed 42
```

---

## Phase 3 — Deterministic Rules Engine

10 compliance rules evaluated against `ops.split_billing`:

| Rule | Category | Severity |
|---|---|---|
| DD-001 | Duplicate Discount — 340B + Medicaid, same patient | Critical |
| DD-002 | Duplicate Discount — 340B + Medicaid, no carve-out | High |
| MEO-001 | Carve-out elected but 340B dispensed to Medicaid | Critical |
| MEO-002 | Carve-in with Medicaid billing on non-340B purchase | High |
| CPE-001 | Dispensed at unregistered contract pharmacy | Critical |
| CPE-002 | Dispensed after contract pharmacy termination | High |
| SB-001 | Accumulator balance negative (over-dispensed) | High |
| EE-001 | Dispensed after CE terminated from 340B program | Critical |
| DQ-001 | Missing patient identifier on 340B dispense | Medium |
| DQ-002 | NDC not found in FDA drug directory | Low |

- Keyset-paginated batch processing (scales to millions of rows)
- Immutable evidence snapshots at detection time
- Dedup-safe reruns

```bash
python run_rules_engine.py
python run_rules_engine.py --batch-id <uuid>
```

---

## Phase 4 — Investigation & Case Orchestration

Groups related findings into investigation cases and manages their lifecycle.

**Clustering logic:**
- Groups by covered entity + violation type
- NDC-level sub-grouping for drug-specific violations
- 14-day temporal sweep window
- Result: 50 related findings → 1 investigation case

**Lifecycle state machine:**
```
OPEN → TRIAGED → INVESTIGATING → ESCALATED → RESOLVED
                             ↘              ↗
                          FALSE_POSITIVE
```

**Services:**
- `CaseBuilderService` — clusters findings into cases
- `InvestigationLifecycleService` — enforces state transitions
- `EvidenceAggregationService` — risk snapshots and financial exposure
- `TimelineService` — append-only audit event log

```bash
python run_investigation.py build
python run_investigation.py status <case_id>
python run_investigation.py history <case_id>
```

---

## Phase 5 — Agentic Investigation Runtime

LangGraph-based multi-agent workflow for AI-assisted case investigation.

**Workflow:**
```
case_intake → evidence_aggregation → risk_prioritization → pattern_analysis
           → narrative_generation → escalation_decision → case_summary
```

**Agents:**
- `InvestigationOrchestratorAgent` — coordinates workflow, no LLM calls
- `EvidenceAnalysisAgent` — pattern detection, temporal anomaly correlation
- `RiskPrioritizationAgent` — severity ranking, escalation recommendations
- `ComplianceNarrativeAgent` — audit-ready documentation, executive summaries

**LLM provider abstraction:**
- Anthropic Claude (primary, with prompt caching)
- OpenAI GPT-4 (fallback)
- Per-task model routing

**Every agent execution produces:**
- Immutable reasoning trace in `audit.reasoning_traces`
- Agent run record in `audit.agent_runs`
- Workflow checkpoint in `audit.workflow_checkpoints` (resumable)

```bash
python run_agents.py run <case_id>
python run_agents.py run <case_id> --resume
python run_agents.py batch --limit 10
```

---

## Getting Started

### Prerequisites
- Python 3.11+
- PostgreSQL 14+
- Anthropic API key (for Phase 5)

### Setup

```bash
# 1. Clone
git clone https://github.com/aman12334/EvidentRx.git
cd EvidentRx

# 2. Install dependencies
pip install -e .

# 3. Configure environment
cp .env.example .env
# Edit .env — set DATABASE_URL and ANTHROPIC_API_KEY

# 4. Run migrations
alembic upgrade head

# 5. Seed compliance rules
psql $DATABASE_URL -f database/seeds/compliance_rules.sql
```

### Running the Platform

```bash
# Ingest reference data
python run_ingestion.py

# Generate synthetic transaction data
python run_simulation.py --ces 50 --weeks 52

# Run compliance rules engine
python run_rules_engine.py

# Build investigation cases
python run_investigation.py build

# Run AI investigation on a case (requires ANTHROPIC_API_KEY)
python run_agents.py run <case_id>
```

---

## Project Structure

```
EvidentRx/
├── app/                        # SQLAlchemy models + DB config
│   ├── models/
│   │   ├── reference/          # CoveredEntity, ContractPharmacy, NdcDrug, Provider
│   │   ├── operational/        # Purchase, Dispense, Claim, SplitBilling
│   │   └── audit/              # ComplianceRule, AuditFinding, InvestigationCase, ...
├── database/
│   ├── schema/                 # Raw DDL (001-006)
│   ├── migrations/             # Alembic versions
│   └── seeds/                  # Compliance rules seed data
├── ingestion/                  # Data ingestion pipeline
│   ├── loaders/                # CE, Medicaid exclusion, NPPES, NDC loaders
│   └── normalizers.py          # NDC normalization, SCD2 upsert
├── simulation/                 # Synthetic data generator
│   ├── generators/             # purchases, dispenses, claims, split_billing
│   ├── violations/             # Violation injectors
│   └── orchestrator.py
├── rules_engine/               # Deterministic compliance rules
│   └── rules/                  # dd_001, meo_001, cpe_001, sb_001, ee_001, dq_001, ...
├── investigation/              # Case orchestration
│   ├── domain/                 # State machine, clustering algorithm
│   └── services/               # CaseBuilder, Lifecycle, Evidence, Timeline
├── agents/                     # LangGraph agentic runtime
│   ├── agents/                 # EvidenceAnalysis, RiskPrioritization, Narrative
│   ├── nodes/                  # LangGraph graph nodes
│   ├── llm/                    # Anthropic + OpenAI provider abstraction
│   ├── memory/                 # Workflow + case memory
│   ├── persistence/            # Reasoning traces + checkpoint manager
│   └── graph.py                # Compiled LangGraph workflow
├── run_simulation.py
├── run_rules_engine.py
├── run_investigation.py
├── run_agents.py
└── run_ingestion.py
```

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `DATABASE_URL` | ✅ | PostgreSQL connection string |
| `ANTHROPIC_API_KEY` | Phase 5 only | Claude API key |
| `OPENAI_API_KEY` | Optional | Fallback LLM provider |
| `DATABASE_ECHO` | No | Log all SQL (default: false) |
| `LOG_LEVEL` | No | Logging level (default: INFO) |

---

## License

Private — EvidentRx. All rights reserved.
