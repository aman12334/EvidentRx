-- =============================================================================
-- Seed: Initial 340B Compliance Rules
-- Run AFTER 0001_initial_schema migration.
-- =============================================================================

INSERT INTO audit.compliance_rules (
    rule_code, rule_name, rule_category, rule_version, severity,
    description, regulatory_reference, logic_definition, effective_date
) VALUES

-- Duplicate Discount Detection
(
    'DD-001',
    'Duplicate Discount — 340B Purchase + Medicaid Claim Same Drug/Patient',
    'duplicate_discount',
    '1.0.0',
    'critical',
    'A 340B-priced purchase was identified for the same drug, patient, and date of service '
    'as a Medicaid fee-for-service claim, creating a prohibited duplicate discount.',
    '42 U.S.C. § 256b(a)(5)(A); HRSA 340B Program Integrity',
    '{
        "match_fields": ["patient_id_hash", "ndc_11", "service_date"],
        "purchase_flag": "is_340b_purchase",
        "claim_flag": "is_medicaid",
        "tolerance_days": 0
    }',
    '2024-01-01'
),
(
    'DD-002',
    'Duplicate Discount — 340B Purchase + Medicaid Managed Care Same Encounter',
    'duplicate_discount',
    '1.0.0',
    'high',
    'A 340B-priced drug was dispensed and billed to Medicaid managed care for the same '
    'patient encounter without a carve-out election in effect.',
    '42 U.S.C. § 256b(a)(5)(A); CMS Medicaid Managed Care Final Rule',
    '{
        "match_fields": ["patient_id_hash", "ndc_11", "service_date"],
        "purchase_flag": "is_340b_purchase",
        "payer_type": "medicaid",
        "requires_carve_out": true,
        "tolerance_days": 1
    }',
    '2024-01-01'
),

-- Medicaid Exclusion / Overlap
(
    'MEO-001',
    'Medicaid Carve-Out Violation — 340B Drug Dispensed to Medicaid Patient Under Carve-Out',
    'carve_in_out',
    '1.0.0',
    'critical',
    'Covered entity has a carve-out election in effect but dispensed a 340B drug '
    'to a Medicaid patient, creating a prohibited overlap.',
    'HRSA 340B Medicaid Exclusion Program; 42 CFR § 447.518',
    '{
        "exclusion_type": "carve_out",
        "payer_type": "medicaid",
        "is_340b_dispense": true,
        "match_period": true
    }',
    '2024-01-01'
),
(
    'MEO-002',
    'Medicaid Carve-In Inconsistency — Medicaid Billed Without 340B Purchase Under Carve-In',
    'carve_in_out',
    '1.0.0',
    'high',
    'Covered entity with carve-in election billed Medicaid for a drug not purchased at '
    '340B pricing, creating potential fraud exposure.',
    'HRSA 340B Medicaid Exclusion Program',
    '{
        "exclusion_type": "carve_in",
        "is_medicaid": true,
        "is_340b_purchase": false
    }',
    '2024-01-01'
),

-- Contract Pharmacy Eligibility
(
    'CPE-001',
    'Dispense at Unregistered Contract Pharmacy',
    'contract_pharmacy_eligibility',
    '1.0.0',
    'critical',
    'A 340B drug was dispensed at a pharmacy not registered as a contract pharmacy '
    'for the covered entity at the time of service.',
    'HRSA 340B Contract Pharmacy Program; Notice Regarding 340B Drug Pricing Program',
    '{
        "check": "contract_pharmacy_active_at_service_date",
        "is_340b_dispense": true
    }',
    '2024-01-01'
),
(
    'CPE-002',
    'Contract Pharmacy Dispensing After Termination Date',
    'contract_pharmacy_eligibility',
    '1.0.0',
    'high',
    'A 340B drug was dispensed at a contract pharmacy after its termination date '
    'in the HRSA database.',
    'HRSA 340B Contract Pharmacy Program',
    '{
        "check": "dispense_date_after_cp_termination",
        "is_340b_dispense": true
    }',
    '2024-01-01'
),

-- Split Billing
(
    'SB-001',
    'Accumulator Imbalance — Dispenses Exceed 340B Purchases in Period',
    'split_billing',
    '1.0.0',
    'high',
    'The number of 340B dispenses for an NDC exceeds the quantity purchased at '
    '340B pricing in the same accumulation period, indicating potential over-utilization.',
    'HRSA 340B Program Requirements; OIG Advisory Opinion',
    '{
        "check": "dispense_quantity_gt_purchase_quantity",
        "match_fields": ["ndc_11", "covered_entity_id"],
        "period_type": "monthly"
    }',
    '2024-01-01'
),

-- Entity Eligibility
(
    'EE-001',
    'Dispense After Entity Termination from 340B Program',
    'entity_eligibility',
    '1.0.0',
    'critical',
    'A 340B drug was dispensed after the covered entity was terminated from '
    'the 340B program.',
    '42 U.S.C. § 256b; HRSA Termination Procedures',
    '{
        "check": "dispense_date_after_ce_termination",
        "is_340b_dispense": true
    }',
    '2024-01-01'
),

-- Data Quality
(
    'DQ-001',
    'Missing Patient Identifier on 340B Dispense',
    'data_quality',
    '1.0.0',
    'medium',
    'A 340B dispense record is missing a patient identifier hash, '
    'preventing duplicate discount analysis.',
    'HRSA 340B Program Integrity; OIG Audit Guidance',
    '{
        "check": "patient_id_hash_null",
        "is_340b_dispense": true
    }',
    '2024-01-01'
),
(
    'DQ-002',
    'NDC Not Found in FDA Drug Directory',
    'data_quality',
    '1.0.0',
    'low',
    'An NDC in purchase or dispense records does not match any active entry '
    'in the FDA NDC directory, indicating a potential data quality issue.',
    'FDA NDC Directory; HRSA 340B Program Integrity',
    '{
        "check": "ndc_11_not_in_ref_ndc_drugs"
    }',
    '2024-01-01'
);
