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
The sixth acceptance round adds schema v12 public catalog access, one-account
branches, one-time identity completion, bidirectional linked accounts, secure
proxy-booking participant tokens, report review, complaint workflows, comment
sanctions/appeals and institution audience-insight caches.
The seventh round adds schema v13 payment orders, per-appointment finance
items, refund cases, immutable transactions/ledger entries and independent
institution operation suspension. Fulfilled v12 appointments are backfilled
as historical paid-and-settled items at migration time.

The current production baseline is schema v13. Production openGauss/GaussDB
deployments use Flask-Migrate/Alembic and must be upgraded during a maintenance
window after stopping writers and cold-backing up the database, permanent
uploads, current release, environment file and Apache configuration:

```powershell
$env:FLASK_APP = "wsgi:app"
$env:HEALTHDOC_SCHEMA_MIGRATION = "1"
flask db upgrade 20260804_schema_v13
```

The v12 revision depends on v11, so Alembic applies every missing revision when
an older production database requires them. It transactionally promotes either
legacy friend-authorization flag into one bidirectional relationship, remaps
historical booking/waitlist relation references before removing reverse
duplicates, and preserves inactive duplicate institution-account history.
Never use `db.create_all()` as a replacement for production migration.

SQLite uses `scripts/upgrade_local_database.py` and
`PRAGMA user_version=13`; do not run the Alembic revision directly against the
local SQLite file. `scripts/reset_v13_demo_data.py` rebuilds only synthetic
acceptance data; it is not a migration tool and must never target production.
The older `reset_v10_demo_data.py` filename remains only as a compatibility
wrapper.
