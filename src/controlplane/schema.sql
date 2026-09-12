CREATE SCHEMA cp;
CREATE SCHEMA remote;
CREATE TABLE cp.policies(hash text PRIMARY KEY, document jsonb NOT NULL);
CREATE TABLE cp.customers (
 id uuid PRIMARY KEY, revision bigint NOT NULL DEFAULT 0 CHECK(revision>=0),
 state jsonb NOT NULL DEFAULT '{"lifecycle":"Lead"}', provenance jsonb NOT NULL DEFAULT '{}'
);
CREATE INDEX customer_email ON cp.customers ((state->>'email'));
CREATE TABLE cp.identities (
 source text NOT NULL, subject text NOT NULL, customer_id uuid NOT NULL REFERENCES cp.customers,
 PRIMARY KEY(source,subject), UNIQUE(source,customer_id)
);
CREATE TABLE cp.events (
 source text NOT NULL, event_id text NOT NULL, hash text NOT NULL, body jsonb NOT NULL,
 result jsonb NOT NULL, received_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(source,event_id)
);
CREATE TABLE cp.proposals (
 id uuid PRIMARY KEY, customer_id uuid NOT NULL REFERENCES cp.customers,
 actor text NOT NULL, hash text NOT NULL, body jsonb NOT NULL, policy_hash text NOT NULL REFERENCES cp.policies,
 revision bigint NOT NULL, decision text NOT NULL CHECK(decision IN ('allow','approval','deny')),
 reason text NOT NULL, expires_at timestamptz NOT NULL,
 approved_by text, approved_until timestamptz,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE cp.jobs (
 id uuid PRIMARY KEY REFERENCES cp.proposals,
 status text NOT NULL CHECK(status IN ('ready','leased','retry','succeeded','blocked','dead')),
 attempts integer NOT NULL DEFAULT 0 CHECK(attempts>=0),
 available_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 lease_until timestamptz, lease_token uuid,
 uncertain boolean NOT NULL DEFAULT false,
 mutation jsonb, reason text, remote_result jsonb,
 CHECK ((status='leased') = (lease_until IS NOT NULL AND lease_token IS NOT NULL))
);
CREATE INDEX claimable_jobs ON cp.jobs(available_at) WHERE status IN ('ready','retry','leased');
CREATE TABLE cp.exceptions (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 category text NOT NULL, subject text NOT NULL, detail jsonb NOT NULL,
 status text NOT NULL DEFAULT 'open' CHECK(status IN ('open','acknowledged','requeued')),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE cp.audit (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 at timestamptz NOT NULL DEFAULT clock_timestamp(), actor text NOT NULL,
 kind text NOT NULL, subject text NOT NULL, policy_hash text NOT NULL REFERENCES cp.policies,
 detail jsonb NOT NULL
);
CREATE FUNCTION cp.immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'immutable history'; END; $$;
CREATE TRIGGER audit_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON cp.audit
 FOR EACH STATEMENT EXECUTE FUNCTION cp.immutable();
CREATE TRIGGER policy_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON cp.policies
 FOR EACH STATEMENT EXECUTE FUNCTION cp.immutable();
CREATE TABLE remote.effects (
 key uuid PRIMARY KEY, hash text NOT NULL, body jsonb NOT NULL,
 receipt uuid NOT NULL UNIQUE, committed_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE remote.plans (key uuid PRIMARY KEY, modes jsonb NOT NULL);
REVOKE ALL ON SCHEMA cp,remote FROM PUBLIC;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA cp TO control_app;
GRANT SELECT,INSERT,UPDATE ON ALL TABLES IN SCHEMA cp TO control_app;
REVOKE UPDATE ON cp.audit,cp.policies,cp.events FROM control_app;
REVOKE INSERT ON cp.policies FROM control_app;
GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA cp TO control_app;
GRANT USAGE ON SCHEMA remote TO synthetic_remote;
GRANT SELECT,INSERT,UPDATE ON remote.plans TO synthetic_remote;
GRANT SELECT,INSERT ON remote.effects TO synthetic_remote;
