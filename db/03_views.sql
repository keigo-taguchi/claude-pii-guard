-- PII-free views. These are EXAMPLES against a typical schema; rename columns to
-- match yours. Rules:
--   * no name / kana / email / phone / address / exact birth date / free text
--   * real IDs appear only through claude.pseudo_id(...)
--   * quasi-identifiers are coarsened: region block, birth year, day precision
--   * health events get aggregate views only (no per-person rows)
--   * name count columns n_*, count_*, so safe-data suppresses small cells

CREATE OR REPLACE VIEW claude.users_safe AS
  SELECT claude.pseudo_id(u.id)                     AS user_pseudo_id,
         u.app_version,
         u.plan,
         left(u.prefecture_code::text, 1)           AS region_block,   -- e.g. first digit of JIS code
         extract(year FROM u.birth_date)::int       AS birth_year,
         u.created_at::date                         AS created_day,
         u.last_login_at::date                      AS last_login_day,
         u.status
  FROM public.users u;

CREATE OR REPLACE VIEW claude.tickets_safe AS
  SELECT t.id                                       AS ticket_id,
         claude.pseudo_id(t.user_id)                AS user_pseudo_id,
         t.category,
         t.product,
         t.status,
         t.created_at::date                         AS created_day,
         t.resolved_at::date                        AS resolved_day
  FROM public.support_tickets t;
  -- body / subject columns are intentionally absent: support-intake handles text.

-- Aggregate-only view for sensitive events (no per-person rows).
CREATE OR REPLACE VIEW claude.health_events_weekly AS
  SELECT date_trunc('week', e.occurred_at)::date    AS week,
         e.event_type,
         count(*)                                   AS n_events,
         count(DISTINCT e.user_id)                  AS n_users
  FROM public.health_events e
  GROUP BY 1, 2;

GRANT SELECT ON ALL TABLES IN SCHEMA claude TO claude_ro;
