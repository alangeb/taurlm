# CLAUDE.md

## Reconciliation pipeline (`src/reconcile.py`)

Device-register / telemetry / billing reconciliation: `python3 src/reconcile.py` (defaults read `src/data/devices.csv`, `src/data/telemetry_q1.csv`, `src/data/billing_raw.csv`; writes `<stem>.reconciled.csv`, `reconciliation_report.json`, `reconciliation_anomalies.csv`). Tests: `cd src && python3 -m pytest tests/test_reconcile.py`. **Full design notes, normalisation rules, exclusion policy and the count-gap decomposition: `docs/reconciliation.md`** — read that before changing normalisation or counting behaviour. Decision rationale lives in `docs/designs/DECISIONS.md` §10.
