-- =============================================================================
-- 340B Compliance Audit & Investigation Platform
-- Script 001: Extensions and Schema Namespaces
-- =============================================================================

-- ---------------------------------------------------------------------------
-- PostgreSQL extensions
-- ---------------------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS "pgcrypto";    -- gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS "btree_gin";   -- GIN indexes on scalar types
CREATE EXTENSION IF NOT EXISTS "pg_trgm";     -- Trigram similarity for name search

-- ---------------------------------------------------------------------------
-- Schema namespaces
--
--   ref   : Reference / master data (covered entities, pharmacies, providers, drugs)
--   ops   : Operational / transaction data (purchases, dispenses, claims)
--   audit : Compliance intelligence (rules, findings, investigation cases, traces)
--   meta  : Ingestion metadata and data lineage
-- ---------------------------------------------------------------------------
CREATE SCHEMA IF NOT EXISTS ref;
CREATE SCHEMA IF NOT EXISTS ops;
CREATE SCHEMA IF NOT EXISTS audit;
CREATE SCHEMA IF NOT EXISTS meta;

COMMENT ON SCHEMA ref   IS 'Reference and master data: covered entities, contract pharmacies, providers, NDC drugs';
COMMENT ON SCHEMA ops   IS 'Operational transaction data: purchases, dispenses, claims, split billing';
COMMENT ON SCHEMA audit IS 'Compliance audit intelligence: rules, findings, investigation cases, reasoning traces';
COMMENT ON SCHEMA meta  IS 'Ingestion metadata, data lineage, and batch tracking';
