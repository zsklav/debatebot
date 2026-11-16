CREATE TABLE IF NOT EXISTS interview_notebooks (
    id uuid PRIMARY KEY,
    owner_id text REFERENCES app_users(id) ON DELETE CASCADE,
    document jsonb NOT NULL,
    revision integer NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS interview_notebooks_owner_updated
    ON interview_notebooks(owner_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS notebook_revisions (
    notebook_id uuid NOT NULL REFERENCES interview_notebooks(id) ON DELETE CASCADE,
    revision integer NOT NULL,
    changes jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (notebook_id, revision)
);
INSERT INTO schema_migrations(version) VALUES (4) ON CONFLICT DO NOTHING;
