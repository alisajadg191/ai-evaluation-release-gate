CREATE TABLE IF NOT EXISTS datasets (
    version text PRIMARY KEY,
    cases jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS runs (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    dataset_version text NOT NULL REFERENCES datasets(version),
    target text NOT NULL,
    status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'completed', 'failed')),
    progress integer NOT NULL DEFAULT 0,
    total integer NOT NULL,
    attempts integer NOT NULL DEFAULT 0,
    lease_token uuid,
    lease_until timestamptz,
    report jsonb,
    error text,
    created_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz
);

CREATE INDEX IF NOT EXISTS runs_ready_idx ON runs (id) WHERE status IN ('queued', 'running');
