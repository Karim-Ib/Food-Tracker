#!/usr/bin/env bash
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    -- Create the two application roles
    CREATE ROLE app_user WITH LOGIN PASSWORD '${APP_USER_PASSWORD}';
    CREATE ROLE dbt_user WITH LOGIN PASSWORD '${DBT_USER_PASSWORD}';

    -- app_user: owns tables in 'app' schema, reads from 'analytics'
    GRANT USAGE, CREATE ON SCHEMA app TO app_user;
    GRANT USAGE ON SCHEMA analytics TO app_user;

    -- dbt_user: reads from 'app' schema, owns tables in 'analytics'
    GRANT USAGE ON SCHEMA app TO dbt_user;
    GRANT USAGE, CREATE ON SCHEMA analytics TO dbt_user;

    -- When app_user creates a table in 'app', dbt_user automatically gets SELECT on it
    ALTER DEFAULT PRIVILEGES FOR ROLE app_user IN SCHEMA app
        GRANT SELECT ON TABLES TO dbt_user;

    -- When dbt_user creates a table in 'analytics', app_user automatically gets SELECT on it
    ALTER DEFAULT PRIVILEGES FOR ROLE dbt_user IN SCHEMA analytics
        GRANT SELECT ON TABLES TO app_user;
EOSQL