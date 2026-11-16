CREATE TABLE IF NOT EXISTS google_connections (
    owner_id text PRIMARY KEY REFERENCES app_users(id) ON DELETE CASCADE,
    encrypted_tokens text NOT NULL,
    generation text NOT NULL,
    scopes jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS meet_import_jobs (
    id uuid PRIMARY KEY,
    owner_id text REFERENCES app_users(id) ON DELETE CASCADE,
    provider text NOT NULL CHECK (provider IN ('google','demo')),
    conference_record text NOT NULL,
    status text NOT NULL CHECK (status IN ('waiting','processing','needs_speakers','complete','failed','expired','cancelled')),
    payload jsonb NOT NULL DEFAULT '{}',
    message text NOT NULL DEFAULT 'Waiting to check the selected meeting',
    attempts integer NOT NULL DEFAULT 0,
    google_requests integer NOT NULL DEFAULT 0,
    next_poll double precision NOT NULL,
    deadline double precision NOT NULL,
    lease_until double precision NOT NULL DEFAULT 0,
    lease_token text,
    notebook_id uuid REFERENCES interview_notebooks(id) ON DELETE SET NULL,
    created_at double precision NOT NULL,
    updated_at double precision NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS meet_import_owner_record
    ON meet_import_jobs(COALESCE(owner_id,''),provider,conference_record);
CREATE INDEX IF NOT EXISTS meet_import_due ON meet_import_jobs(status,next_poll,lease_until);
INSERT INTO schema_migrations(version) VALUES (5) ON CONFLICT DO NOTHING;
