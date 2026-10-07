-- =============================================================
-- AI Ops Platform - Database Initialization
-- Runs ONLY on first volume creation (postgres container init)
-- Three databases, each with its own owner user
-- =============================================================

-- ---- agent-service database ----
CREATE USER agentuser WITH PASSWORD 'agentpass';
CREATE DATABASE agentdb OWNER agentuser;

-- ---- usage-service database ----
CREATE USER usageuser WITH PASSWORD 'usagepass';
CREATE DATABASE usagedb OWNER usageuser;

-- ---- cost-service database ----
CREATE USER costuser WITH PASSWORD 'costpass';
CREATE DATABASE costdb OWNER costuser;

-- Grant minimal privileges (each service only connects to its own DB)
GRANT ALL PRIVILEGES ON DATABASE agentdb TO agentuser;
GRANT ALL PRIVILEGES ON DATABASE usagedb TO usageuser;
GRANT ALL PRIVILEGES ON DATABASE costdb TO costuser;
