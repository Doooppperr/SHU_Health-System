# HealthDoc 1.0—6.0 database migrations

HealthDoc 1.0 established the initial account, institution, appointment and
health-record schema. The 2.0 work evolved that model into schema v6 with
self-measurements, institution report lifecycle and authorization boundaries.
HealthDoc 3.0 first introduced schema v7 for health domains, package versions,
group bookings, waitlists and richer report results, then schema v8 for
organization/branch collaboration and cross-branch access auditing.
HealthDoc 4.0 adds schema v9 token versions, hashed password-verification
challenges and moderated institution comment replies.
HealthDoc 5.0 adds schema v10 appointment intake/privacy snapshots, explicit
termination responsibility, in-app notifications, structured report-asset
slots, reference rules and directional indicator results.
HealthDoc 6.0 adds schema v11 encrypted Agent threads/actions, redacted tool
events, idempotent executions, support handoffs and OAuth client/token state.

The current production baseline is schema v11. Production openGauss/GaussDB
deployments use Flask-Migrate/Alembic and must be upgraded during a maintenance
window after backing up the database, permanent uploads and environment file:

```powershell
$env:FLASK_APP = "wsgi:app"
$env:HEALTHDOC_SCHEMA_MIGRATION = "1"
flask db upgrade 20260729_schema_v11
```

The v11 revision depends on the v10 revision, so Alembic applies every missing revision when an
older production database requires them. Never use `db.create_all()` as a
replacement for production migration.

SQLite uses `scripts/upgrade_local_database.py` and `PRAGMA user_version=11`;
do not run the Alembic revision against the local SQLite file. The synthetic
demo reset is also not a migration tool and must never target production.
