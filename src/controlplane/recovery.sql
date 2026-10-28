-- Additive migration 3. Recovery metadata is operational authority, not customer truth.
CREATE TABLE cp.recovery_sessions (
 id uuid PRIMARY KEY, binding jsonb NOT NULL, binding_hash text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 status text NOT NULL CHECK(status IN ('closed','approved','resumed','blocked'))
);
CREATE TABLE cp.recovery_proofs (
 id uuid PRIMARY KEY, session_id uuid NOT NULL REFERENCES cp.recovery_sessions,
 report jsonb NOT NULL, report_hash text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TRIGGER recovery_proofs_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON cp.recovery_proofs
 FOR EACH STATEMENT EXECUTE FUNCTION cp.immutable();
CREATE TABLE cp.recovery_gate (
 singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
 mode text NOT NULL CHECK(mode IN ('open','closed')), epoch bigint NOT NULL DEFAULT 0,
 session_id uuid REFERENCES cp.recovery_sessions,
 approved_proof uuid REFERENCES cp.recovery_proofs, approved_at timestamptz
);
INSERT INTO cp.recovery_gate(singleton,mode) VALUES(true,'open');
REVOKE ALL ON cp.recovery_sessions,cp.recovery_proofs,cp.recovery_gate FROM PUBLIC,control_app;

-- A shared gate lock lasts until the caller's transaction ends. Closing/reopening
-- takes the exclusive row lock before reading business state.
CREATE FUNCTION cp.execution_open() RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,cp AS $$
DECLARE current_mode text;
BEGIN
 SELECT mode INTO STRICT current_mode FROM cp.recovery_gate WHERE singleton FOR SHARE;
 RETURN current_mode='open';
END; $$;
REVOKE ALL ON FUNCTION cp.execution_open() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION cp.execution_open() TO control_app;

CREATE FUNCTION cp.require_open_admission() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,cp AS $$
BEGIN
 IF NOT cp.execution_open() THEN
  RAISE EXCEPTION 'recovery_closed' USING ERRCODE='55000';
 END IF;
 RETURN NEW;
END; $$;
CREATE TRIGGER recovery_proposal_admission BEFORE INSERT ON cp.proposals
 FOR EACH ROW EXECUTE FUNCTION cp.require_open_admission();
CREATE TRIGGER recovery_job_admission BEFORE INSERT ON cp.jobs
 FOR EACH ROW EXECUTE FUNCTION cp.require_open_admission();

-- Source observations remain admissible during closure. They acquire the same
-- shared lock so an authoritative update cannot straddle the resume decision.
CREATE FUNCTION cp.recovery_business_lock() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,cp AS $$
BEGIN
 PERFORM cp.execution_open();
 RETURN NULL;
END; $$;
DO $$ DECLARE item record;
BEGIN
 FOR item IN SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname='cp'
  AND tablename NOT IN ('recovery_gate','recovery_sessions','recovery_proofs')
 LOOP
  EXECUTE format('CREATE TRIGGER recovery_state_lock BEFORE INSERT OR UPDATE OR DELETE ON cp.%I '
                 'FOR EACH STATEMENT EXECUTE FUNCTION cp.recovery_business_lock()',item.tablename);
 END LOOP;
END; $$;

CREATE FUNCTION cp.recovery_binding_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.id IS DISTINCT FROM OLD.id OR NEW.binding IS DISTINCT FROM OLD.binding
  OR NEW.binding_hash IS DISTINCT FROM OLD.binding_hash OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
  RAISE EXCEPTION 'immutable recovery binding';
 END IF;
 RETURN NEW;
END; $$;
CREATE TRIGGER recovery_binding_immutable BEFORE UPDATE ON cp.recovery_sessions
 FOR EACH ROW EXECUTE FUNCTION cp.recovery_binding_immutable();
CREATE TRIGGER recovery_session_delete BEFORE DELETE OR TRUNCATE ON cp.recovery_sessions
 FOR EACH STATEMENT EXECUTE FUNCTION cp.immutable();
