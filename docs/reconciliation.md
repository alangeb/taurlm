# Reconciliation pipeline (`src/reconcile.py`)

Reconciles the renewable-energy asset register (`devices.csv`) against Q1
telemetry (`telemetry_q1.csv`) and raw billing (`billing_raw.csv`), answers the
question *"why doesn't the active-device count match?"*, and emits a
device+billing CSV.

## Run it

```bash
cd src
python3 reconcile.py data/devices.csv data/telemetry_q1.csv data/billing_raw.csv
# -> data/devices.telemetry.csv            (canonical, per task spec)
# -> reconciliation_report.json            (summary + gap + all anomalies)
# -> reconciliation_anomalies.csv          (same anomalies, flat/awk-friendly)
python3 reconcile.py --help                # all flags (--out/--report/--anomalies-csv/--quiet)
python3 data/generate_fixtures.py          # regenerate inputs deterministically
python3 -m pytest tests/test_reconcile.py  # 87 tests
```

Defaults resolve to the three `data/` files and write `<devices stem>.reconciled.csv`.
Library use: `reconcile(devices, telemetry, billing)` is pure (lists of dicts,
no I/O) and returns the full result dict.

## Normalisation rules (applied before any counting)

| Field | Accepted spellings | Canonical form |
|---|---|---|
| `device_id` | `dev012`, `' DEV012 '`, `DEV-012`, `DEV012;`, `dev-910` | `DEV012` (upper-case, non-alphanumerics removed) |
| `site_id` | `site c`, `SITE_C`, `Site-C` | `SITE-C` (upper-case, separators collapsed) |
| `capacity_kw` | `2,200`, `3,200 kW`, `1.5 MW`, `$1,200.50` | numeric kW (MW x1000) |
| dates / `timestamp` | `2026-09-12`, `...T02:40:00`, `+00:00`, `15/04/2019`, `YYYYMMDD`, epoch seconds (10) and ms (13) | ISO-8601 UTC (`2026-09-12T14:57:03+00:00`) |
| `billing_period` | `2024-1`, `03/2024`, `2024/03`, `2026-09-12` | `YYYY-MM` |
| `credit_flag` | `Y/yes/TRUE/T/1`, `N/no/FALSE/F/0/blank` | boolean |
| `status` | `active`, `in service`, `online` → active; `retired`, `decommissioned`, `offline` → inactive | `active` / `inactive` |

Headers are aliased too (`device`, `kwh`, `period`, `credit`, `ts`, … — see
`HEADER_ALIASES`); unrecognised columns are reported, never guessed.

### Values that are never guessed

* `01/05/2019` — **ambiguous** (both 1 May and 5 Jan are valid calendar dates).
  Excluded and reported. `15/04/2019` is *not* ambiguous (15 cannot be a month)
  and normalises to `2026-09-12`.
* `2026-09-12`, `abc`, `inf`, `-170`, `(200)` — invalid, reported. Accounting
  parenthesised negatives are **not** silently negated.
* `standby`, blank status — outside the active/inactive vocabulary.

## Counting, exclusions and dedupe

* **Active count** = canonical devices whose canonical status is `active` and
  which own no disqualifying rule. On the sample data: **27**.
* **Dedupe**: device rows are grouped by canonical id; telemetry by
  (device, UTC timestamp); billing by (device, `YYYY-MM`).
* **Conflicts**: identical duplicates collapse silently into one row (and are
  logged as `*.DUP_IDENTICAL`). Rows that *disagree* never get an arbitrary
  winner at device level — the device is **excluded** (`DEVICE.DUP_CONFLICT`)
  and the disagreeing fields are named in the report. At reading/line level the
  first occurrence is kept and the disagreement is logged as `*.DUP_CONFLICT`.
* **Device-level exclusion** is limited to identity/status/date integrity
  (`DEVICE_DISQUALIFYING_RULES`): conflicting duplicates, unknown status,
  ambiguous/invalid/unparseable date, and commission dates after the window.
  **Capacity problems do not exclude** — they are reported and gate capacity
  sums via `capacity_valid`, because a broken capacity does not change whether
  a device is active.
* **Windows**: telemetry `2026-09-12..2026-09-12` (UTC), billing `2024-01..2024-03`.
  Out-of-window rows are counted as `*_OUT_OF_RANGE`, excluded from totals.
* **Cadence**: after dedupe, each reading must sit on the 10-minute grid and the
  gap to the previous reading must be a positive multiple of 10 minutes.

## "Why does the count differ from the raw data?"

`--out` prints the answer; the JSON `gap` block decomposes it so it always adds
up. On the sample data:

```
34 raw rows that looked active
 -1  duplicate active rows collapsed onto one device   (duplicate_rows_collapsed)
 -5  rows belonging to disqualified devices           (disqualified_active_rows)
 -1  rows with no usable device id                    (keyless_active_rows)
= 27 canonical active devices                         (devices_canonical_active)
```

The device register itself is not the only source of divergence: `DEV900/901`
report telemetry but are not registered, `DEV910/911` are billed but not
registered, and some registered devices produce nothing at all — all listed
under `gap.devices_only` / `telemetry_only` / `billing_only`.

## Report format

`reconciliation_report.json`: `summary` (counts, totals, window),
`gap` (decomposition + unknown entities + unbilled devices), `anomaly_counts`,
`anomalies`. Flat log columns:

```
rule,category,source,record_key,field,raw_value,canonical_value,reason
```

Sample rows (real output):

```
rule=DEVICE.DATE_AMBIGUOUS key=DEV009 field=commissioned_date raw=05/06/2024 canonical= reason=...could be...not guessed
rule=TELEMETRY.DUP_CONFLICT key=DEV001@2026-09-12T14:57:03+00:00 raw=4/504 canonical=4 reason=...values DISAGREE - kept first occurrence
rule=CROSS.BILLING_ONLY key=DEV910 field=device_id raw=DEV910 reason=billed but absent from the register
```
