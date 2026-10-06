-- claude_ro: the only role safe-data connects as.
-- It can SELECT from the `claude` schema and nothing else. Read-only is enforced
-- by *not granting* anything else; the transaction-level READ ONLY in safe-data
-- is a second layer, not the first.
--
-- Run as a superuser / owner:  psql -d <db> -f db/01_roles.sql

CREATE ROLE claude_ro LOGIN NOINHERIT;                 -- set a password or use peer/cert auth
ALTER ROLE claude_ro SET default_transaction_read_only = on;
ALTER ROLE claude_ro SET statement_timeout = '30s';
ALTER ROLE claude_ro SET search_path = claude, pg_catalog;

REVOKE ALL ON SCHEMA public FROM claude_ro;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM claude_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM claude_ro;
-- Repeat the three REVOKEs for every schema that holds raw data.

CREATE SCHEMA IF NOT EXISTS claude;
GRANT USAGE ON SCHEMA claude TO claude_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA claude GRANT SELECT ON TABLES TO claude_ro;
