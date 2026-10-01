-- search_path is set to the app schema (default "beacon") by the migration runner.

CREATE TABLE items (
    id          bigserial PRIMARY KEY,
    name        text        NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
    description text,
    created_by  text        NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX items_created_at_idx ON items (created_at DESC);

CREATE TABLE audit_events (
    id          bigserial PRIMARY KEY,
    kind        text        NOT NULL,
    detail      jsonb       NOT NULL DEFAULT '{}'::jsonb,
    pod         text        NOT NULL,
    version     text        NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX audit_events_kind_created_idx ON audit_events (kind, created_at DESC);
