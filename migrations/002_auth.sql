CREATE TABLE IF NOT EXISTS app_users (
    id TEXT PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS auth_sessions (
    token_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    expires_at BIGINT NOT NULL
);
CREATE INDEX IF NOT EXISTS auth_sessions_user_idx ON auth_sessions(user_id);
ALTER TABLE debate_sessions ADD COLUMN IF NOT EXISTS owner_id TEXT;
ALTER TABLE analysis_runs ADD COLUMN IF NOT EXISTS owner_id TEXT;
CREATE INDEX IF NOT EXISTS debate_sessions_owner_idx ON debate_sessions(owner_id);
CREATE INDEX IF NOT EXISTS analysis_runs_owner_idx ON analysis_runs(owner_id);
INSERT INTO schema_migrations(version) VALUES (2) ON CONFLICT DO NOTHING;
