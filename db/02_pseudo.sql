-- Keyed pseudonymous IDs, computed inside the database so claude_ro never sees
-- the real ID. The key lives in a schema claude_ro cannot read, and the function
-- is SECURITY DEFINER with a fixed search_path.
--
-- The key text MUST equal the contents of ~/.config/safe-data/pseudo.key
-- (hex string, one line), so the ETL and support-intake produce the same IDs:
--   python: pseudo_id(key, "12345")  ==  sql: claude.pseudo_id(12345)
--
-- pgcrypto is usually installed in `public` or `extensions`; adjust the two
-- places marked ### if yours differs.

CREATE EXTENSION IF NOT EXISTS pgcrypto;          -- ### schema of hmac(): public
CREATE SCHEMA IF NOT EXISTS keys;
REVOKE ALL ON SCHEMA keys FROM PUBLIC, claude_ro;

CREATE TABLE IF NOT EXISTS keys.pseudo (k text NOT NULL);
REVOKE ALL ON keys.pseudo FROM PUBLIC, claude_ro;
-- INSERT INTO keys.pseudo VALUES ('<contents of pseudo.key>');

CREATE OR REPLACE FUNCTION claude.pseudo_id(id anyelement) RETURNS text
  LANGUAGE sql STABLE SECURITY DEFINER
  SET search_path = pg_catalog, keys
  AS $$
    SELECT left(pg_catalog.encode(public.hmac(id::text, (SELECT k FROM keys.pseudo LIMIT 1), 'sha256'), 'hex'), 16)  -- ### public.hmac
  $$;
REVOKE ALL ON FUNCTION claude.pseudo_id(anyelement) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION claude.pseudo_id(anyelement) TO claude_ro;

-- Alternative with no SECURITY DEFINER: precompute users.pseudo_id as a physical
-- column in your ETL and reference that column from the views instead.
