-- Provision the application runtime role (dev container only).
--
-- Runs once at first container init, as the superuser/migration-owner. The
-- runtime role is NOSUPERUSER + NOBYPASSRLS so Postgres Row-Level Security is
-- always enforced for the application (CLAUDE.md §4). Default privileges grant
-- access to tables/sequences the migration owner creates afterward, so the
-- migration itself stays role-agnostic.
--
-- The password is a clearly-marked DEV credential, consistent with the dev
-- defaults in docker-compose.yml. Production provisions this role with a real
-- secret out-of-band.

CREATE ROLE onmixai_app WITH LOGIN PASSWORD 'onmixai_app'
    NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;

GRANT CONNECT ON DATABASE onmixai TO onmixai_app;
GRANT USAGE ON SCHEMA public TO onmixai_app;

ALTER DEFAULT PRIVILEGES FOR ROLE onmixai IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO onmixai_app;
ALTER DEFAULT PRIVILEGES FOR ROLE onmixai IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO onmixai_app;
