-- Additive v2; the original schema.sql / version-1 checksum is unchanged.
CREATE TABLE cp.destinations (
 id text PRIMARY KEY CHECK(id ~ '^[a-z][a-z0-9_]{0,31}$'),
 url text NOT NULL, credential_env text NOT NULL,
 requests_per_second integer NOT NULL CHECK(requests_per_second BETWEEN 2 AND 1000),
 concurrency integer NOT NULL CHECK(concurrency BETWEEN 1 AND 32),
 backlog_limit integer NOT NULL CHECK(backlog_limit BETWEEN 1 AND 100000)
);
CREATE TABLE cp.destination_routes(owner text PRIMARY KEY, destination text NOT NULL REFERENCES cp.destinations);
CREATE TABLE cp.job_routes(id uuid PRIMARY KEY REFERENCES cp.proposals, destination text NOT NULL REFERENCES cp.destinations);
CREATE TABLE cp.capacity_state (
 destination text PRIMARY KEY REFERENCES cp.destinations,
 window_id bigint NOT NULL DEFAULT 0, used integer NOT NULL DEFAULT 0,
 next_probe timestamptz NOT NULL DEFAULT '-infinity', last_served timestamptz NOT NULL DEFAULT '-infinity'
);
CREATE TABLE cp.request_permits (
 token uuid PRIMARY KEY, destination text NOT NULL REFERENCES cp.destinations,
 expires timestamptz NOT NULL, kind text NOT NULL CHECK(kind IN ('dispatch','receipt'))
);
CREATE TABLE cp.capacity_requests (
 token uuid PRIMARY KEY, destination text NOT NULL REFERENCES cp.destinations,
 window_id bigint NOT NULL, kind text NOT NULL, issued_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TRIGGER capacity_log_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON cp.capacity_requests
 FOR EACH STATEMENT EXECUTE FUNCTION cp.immutable();
CREATE TABLE cp.capacity_completions (
 token uuid PRIMARY KEY REFERENCES cp.capacity_requests,
 finished_at timestamptz NOT NULL DEFAULT clock_timestamp(), failed boolean NOT NULL
);
CREATE TRIGGER completion_log_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON cp.capacity_completions
 FOR EACH STATEMENT EXECUTE FUNCTION cp.immutable();
CREATE TRIGGER destination_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON cp.destinations
 FOR EACH STATEMENT EXECUTE FUNCTION cp.immutable();
CREATE TRIGGER route_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON cp.job_routes
 FOR EACH STATEMENT EXECUTE FUNCTION cp.immutable();
GRANT SELECT ON cp.destinations,cp.destination_routes,cp.job_routes TO control_app;
GRANT INSERT ON cp.job_routes TO control_app;
GRANT SELECT,INSERT,UPDATE ON cp.capacity_state TO control_app;
GRANT SELECT,INSERT,DELETE ON cp.request_permits TO control_app;
GRANT SELECT,INSERT ON cp.capacity_requests TO control_app;
GRANT SELECT,INSERT ON cp.capacity_completions TO control_app;
