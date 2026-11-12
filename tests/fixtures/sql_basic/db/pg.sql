-- postgres flavor: schema-qualified names, dollar bodies, separate indexes, a view.

CREATE TABLE app."Events" (
    id      bigserial PRIMARY KEY,
    user_id bigint NOT NULL REFERENCES users (id),
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    tags    text[] DEFAULT NULL,
    at      timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS events_user_at ON app."Events" USING btree (user_id, at);
CREATE INDEX CONCURRENTLY events_tag ON ONLY app."Events" (lower(payload ->> 'tag'));
CREATE INDEX elsewhere_idx ON other_table (x);

-- latest event per user.
CREATE OR REPLACE VIEW latest_events AS
SELECT e.user_id, max(e.at) AS last_at, count(*) n, e.payload
FROM app."Events" e
GROUP BY e.user_id, e.payload;

CREATE VIEW named_cols (a, b) AS SELECT 1, 2;

CREATE OR REPLACE FUNCTION bump(p_id bigint, VARIADIC p_tags text[]) RETURNS TABLE (id bigint, n int)
LANGUAGE plpgsql AS $body$
BEGIN
  -- CREATE TABLE inside a body string is not schema
  RETURN QUERY SELECT p_id, 1;
END;
$body$;
