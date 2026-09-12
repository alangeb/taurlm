"""Tests for reconcile.py - normalisation, dedupe, exclusions, cross-entity gaps.

Structure mirrors the module: normaliser units, then per-source behaviour,
then the cross-source report and the CLI contract.
"""
import csv
import json
import subprocess
import sys
from pathlib import Path

import pytest

import reconcile as R

SRC_DIR = Path(__file__).resolve().parents[1]
DATA = SRC_DIR / "data"


def devices_fixture():
    rows, _ = R._read_csv(DATA / "devices.csv")
    return R._read_csv(DATA / "devices.csv")[1]


def telemetry_fixture():
    return R._read_csv(DATA / "telemetry_q1.csv")[1]


def billing_fixture():
    return R._read_csv(DATA / "billing_raw.csv")[1]


# --------------------------------------------------------------------------
# Normalisers
# --------------------------------------------------------------------------
@pytest.mark.parametrize("raw,expected", [
    ("DEV012", "DEV012"), ("dev012", "DEV012"), (" DEV012 ", "DEV012"),
    ("DEV-012", "DEV012"), ("DEV012;", "DEV012"), ("dev-910", "DEV910"),
    ("", ""), ("   ", ""), ("???", "")])
def test_canonical_device_id(raw, expected):
    assert R.canonical_device_id(raw) == expected


def test_canonical_device_id_is_idempotent():
    for raw in ("dev-1", " DEV02 ", "910"):
        once = R.canonical_device_id(raw)
        assert R.canonical_device_id(once) == once


def test_canonical_site():
    assert R.canonical_site("site c") == "SITE-C"
    assert R.canonical_site("SITE_D") == "SITE_D".replace("_", "-")
    assert R.canonical_site("") == ""
    assert R.canonical_site("N/A") == ""


@pytest.mark.parametrize("raw,value", [
    ("500", 500.0), (" 1,200 ", 1200.0), ("3,200 kW", 3200.0), ("1.5 MW", 1500.0),
    ("$1,200.50", 1200.5), ("0", 0.0)])
def test_parse_number_accepts(raw, value):
    parsed = R.parse_number(raw)
    assert parsed["ok"] and abs(parsed["value"] - value) < 1e-9


@pytest.mark.parametrize("raw", ["abc", "1,23", "-170", "(150.25)", "inf", "10 foo"])
def test_parse_number_rejects(raw):
    assert not R.parse_number(raw)["ok"]


@pytest.mark.parametrize("raw", ["", "N/A", "null", "none", "-"])
def test_parse_number_treats_placeholders_as_missing(raw):
    assert R.parse_number(raw)["how"] == "missing"


def test_parse_number_marks_only_clean_input_canonical():
    assert R.parse_number("500")["how"] == "canonical"
    assert R.parse_number("1,200")["how"] == "normalised"


@pytest.mark.parametrize("raw,iso", [
    ("2026-09-12", "2026-09-12"), ("2026-09-12T14:57:03+00:00", "2026-09-12T14:57:03+00:00"),
    ("2026-09-12T14:57:03+00:00", "2026-09-12T14:57:03+00:00"),
    ("15/04/2019", "2026-09-12"), ("1704067200", "2026-09-12"),
    ("1704067200000", "2026-09-12")])
def test_parse_datetime_accepts(raw, iso):
    parsed = R.parse_datetime(raw)
    assert parsed["ok"] and R._fmt(parsed["dt"]) == iso


def test_parse_datetime_ambiguous_is_not_guessed():
    parsed = R.parse_datetime("01/05/2019")
    assert not parsed["ok"] and parsed["ambiguous"]
    assert "not guessed" in parsed["reason"] or "could be" in parsed["reason"]


def test_parse_datetime_invalid_and_missing():
    assert R.parse_datetime("2026-09-12")["how"] == "invalid"
    assert R.parse_datetime("not a date")["how"] == "invalid"
    assert R.parse_datetime("")["how"] == "missing"


def test_parse_datetime_normalises_offset_to_utc():
    parsed = R.parse_datetime("2026-09-12T14:57:03+00:00")
    assert parsed["ok"] and parsed["dt"].utcoffset().total_seconds() == 0


@pytest.mark.parametrize("raw,period", [("2024-01", "2024-01"), ("2024-1", "2024-01"),
                                        ("03/2024", "2024-03"), ("2026-09-12", "2024-01")])
def test_parse_period_accepts(raw, period):
    parsed = R.parse_period(raw)
    assert parsed["ok"] and parsed["period"] == period


@pytest.mark.parametrize("raw", ["2024-13", "Q1-2024", "", "N/A"])
def test_parse_period_rejects(raw):
    assert not R.parse_period(raw)["ok"]


@pytest.mark.parametrize("raw,expected", [("Y", True), ("yes", True), ("TRUE", True),
                                          ("1", True), ("T", True), ("N", False),
                                          ("no", False), ("0", False), ("", False)])
def test_parse_flag(raw, expected):
    parsed = R.parse_flag(raw)
    assert parsed["ok"] and parsed["value"] is expected


def test_parse_flag_unknown_token():
    parsed = R.parse_flag("maybe")
    assert not parsed["ok"] and parsed["how"] == "unknown"


@pytest.mark.parametrize("raw,expected", [("active", "active"), (" ACTIVE ", "active"),
                                          ("IN SERVICE", "active"), ("retired", "inactive"),
                                          ("decommissioned", "inactive"),
                                          ("standby", None), ("", None)])
def test_canonical_status(raw, expected):
    assert R.canonical_status(raw) == expected


def test_resolve_headers_maps_aliases():
    mapping, unresolved = R.resolve_headers(
        ["device", "timestamp", "kwh", "period", "credit", "notes"])
    assert mapping["device"] == "device_id" and mapping["kwh"] == "energy_kwh"
    assert mapping["period"] == "billing_period" and mapping["credit"] == "credit_flag"
    assert unresolved == ["notes"]


def test_read_csv_applies_header_aliases(tmp_path):
    path = tmp_path / "telem.csv"
    path.write_text("device,datetime,kwh\nDEV001,2026-09-12T14:57:03+00:00,7\n")
    headers, rows = R._read_csv(path)
    assert rows[0]["device_id"] == "DEV001" and rows[0]["energy_kwh"] == "7"
    assert any(h.startswith("UNRESOLVED") for h in headers) is False


# --------------------------------------------------------------------------
# Dedupe / exclusion behaviour
# --------------------------------------------------------------------------
def _devices(*rows):
    header = ["device_id", "site_id", "capacity_kw", "commissioned_date", "status"]
    return [dict(zip(header, row)) for row in rows]


def test_identical_device_rows_collapse_without_loss():
    rows = _devices(("DEV001", "SITE-A", "100", "2026-09-12", "active"),
                    ("DEV001", "SITE-A", "100", "2026-09-12", "active"))
    anomalies = R.Anomalies()
    registry, stats = R.normalize_devices(rows, anomalies)
    assert len(registry) == 1 and registry["DEV001"]["excluded"] is False
    assert anomalies.counts_by("rule")[R.R_DEVICE_DUP_IDENTICAL] == 1
    assert stats["active"] == 1


def test_conflicting_device_rows_are_excluded_not_guessed():
    rows = _devices(("DEV001", "SITE-A", "100", "2026-09-12", "active"),
                    ("DEV001", "SITE-B", "200", "2026-09-12", "active"))
    anomalies = R.Anomalies()
    registry, stats = R.normalize_devices(rows, anomalies)
    assert registry["DEV001"]["excluded"] is True and stats["active"] == 0
    row = [r for r in anomalies.rows if r["rule"] == R.R_DEVICE_DUP_CONFLICT][0]
    assert "capacity" in row["reason"] and row["canonical_value"] == ""


def test_dirty_key_duplicate_collapses_with_clean_row():
    rows = _devices(("DEV001", "SITE-A", "100", "2026-09-12", "active"),
                    ("dev-001;", "SITE-A", "100", "2026-09-12", "active"))
    anomalies = R.Anomalies()
    registry, stats = R.normalize_devices(rows, anomalies)
    assert len(registry) == 1 and registry["DEV001"]["excluded"] is False
    assert stats["active"] == 1


def test_unknown_status_excludes_device_but_normalised_status_does_not():
    rows = _devices(("DEV001", "SITE-A", "100", "2026-09-12", "standby"),
                    ("DEV002", "SITE-A", "100", "2026-09-12", " IN SERVICE "),
                    ("DEV003", "SITE-A", "100", "2026-09-12", "retired"))
    anomalies = R.Anomalies()
    registry, stats = R.normalize_devices(rows, anomalies)
    assert registry["DEV001"]["excluded"] and registry["DEV002"]["status"] == "active"
    assert registry["DEV003"]["status"] == "inactive"
    assert stats["active"] == 1


def test_ambiguous_or_invalid_date_excludes_device():
    rows = _devices(("DEV001", "SITE-A", "100", "01/05/2019", "active"),
                    ("DEV002", "SITE-A", "100", "2026-09-12", "active"),
                    ("DEV003", "SITE-A", "100", "15/04/2019", "active"))
    registry, stats = R.normalize_devices(rows, R.Anomalies())
    assert registry["DEV001"]["excluded"] and registry["DEV002"]["excluded"]
    assert not registry["DEV003"]["excluded"] and stats["active"] == 1


def test_commission_date_after_window_excludes_device():
    rows = _devices(("DEV001", "SITE-A", "100", "2026-09-12", "active"))
    registry, stats = R.normalize_devices(rows, R.Anomalies())
    assert registry["DEV001"]["excluded"] and stats["active"] == 0


def test_capacity_problem_is_reported_but_does_not_change_active_count():
    rows = _devices(("DEV001", "SITE-A", "abc", "2026-09-12", "active"),
                    ("DEV002", "SITE-A", "1,500", "2026-09-12", "active"))
    anomalies = R.Anomalies()
    registry, stats = R.normalize_devices(rows, anomalies)
    assert stats["active"] == 2                       # both still counted as devices
    assert stats["active_with_valid_capacity"] == 1   # only one usable capacity
    assert registry["DEV002"]["capacity_kw"] == 1500.0
    assert registry["DEV002"]["capacity_valid"] is True


def test_missing_device_id_is_dropped_and_logged():
    rows = _devices(("", "SITE-A", "100", "2026-09-12", "active"))
    anomalies = R.Anomalies()
    _, stats = R.normalize_devices(rows, anomalies)
    assert stats["dropped_no_key"] == 1
    assert anomalies.rows[0]["rule"] == R.R_DEVICE_KEY_MISSING


def test_telemetry_window_dedupe_and_cadence():
    rows = [{"device_id": "DEV001", "timestamp": ts, "energy_kwh": v} for ts, v in [
        ("2026-09-12T14:57:03+00:00", "10"), ("2026-09-12T14:57:03+00:00", "10"),   # identical dup
        ("2026-09-12T14:57:03+00:00", "11"),
        ("2026-09-12T14:57:03+00:00", "12"),                                   # conflicting dup
        ("2026-09-12T14:57:03+00:00", "13"),                                   # off-grid minute
        ("2026-09-12T14:57:03+00:00", "9"),                                    # before window
        ("2026-09-12T14:57:03+00:00", "9"),                                    # after window
        ("01/05/2024 02:00:00", "9"),                                    # ambiguous
        ("2026-09-12T14:57:03+00:00", "-1"),                                   # negative value
    ]] + [{"device_id": "DEV900", "timestamp": "2026-09-12T14:57:03+00:00", "energy_kwh": "5"}]
    anomalies = R.Anomalies()
    readings, stats = R.normalize_telemetry(rows, anomalies)
    assert stats["readings"] == 4   # DEV001: 00:00,00:10,00:25 + DEV900
    assert stats["dropped_out_of_window"] == 2
    assert stats["dropped_bad_timestamp"] == 1
    assert stats["dropped_bad_value"] == 1
    counts = anomalies.counts_by("rule")
    assert counts[R.R_TELEMETRY_DUP_IDENTICAL] == 1
    assert counts[R.R_TELEMETRY_DUP_CONFLICT] == 1
    assert counts[R.R_TELEMETRY_CADENCE] >= 1


def test_telemetry_accepts_epoch_tzaware_and_separators():
    rows = [{"device_id": "DEV001", "timestamp": "1704067200", "energy_kwh": "1,024"},
            {"device_id": "DEV002", "timestamp": "2026-09-12T14:57:03+00:00", "energy_kwh": "5"}]
    anomalies = R.Anomalies()
    readings, stats = R.normalize_telemetry(rows, anomalies)
    assert stats["readings"] == 2
    from datetime import datetime, timezone
    assert readings[("DEV001", datetime(2024, 1, 1, tzinfo=timezone.utc))] == 1024.0
    assert anomalies.counts_by("rule")[R.R_TELEMETRY_TS_CANON] == 1  # tz-aware form is already canonical


def test_billing_window_dedupe_and_flag_exclusion():
    rows = [
        {"device_id": "DEV001", "billing_period": "2024-01", "billed_kwh": "100", "credit_flag": "Y"},
        {"device_id": "DEV001", "billing_period": "2024-01", "billed_kwh": "100", "credit_flag": "yes"},
        {"device_id": "DEV001", "billing_period": "2024-01", "billed_kwh": "999", "credit_flag": "N"},
        {"device_id": "DEV002", "billing_period": "2023-12", "billed_kwh": "10", "credit_flag": "Y"},
        {"device_id": "DEV003", "billing_period": "2024-02", "billed_kwh": "10", "credit_flag": "maybe"},
        {"device_id": "DEV004", "billing_period": "2024/03", "billed_kwh": "$1,200.50", "credit_flag": "1"},
    ]
    anomalies = R.Anomalies()
    lines, stats = R.normalize_billing(rows, anomalies)
    assert stats["lines"] == 2, anomalies.counts_by("rule")
    assert stats["dropped_out_of_window"] == 1 and stats["dropped_bad_flag"] == 1
    assert lines[("DEV001", "2024-01")]["billed_kwh"] == 100.0
    assert lines[("DEV004", "2024-03")]["billed_kwh"] == 1200.5
    assert lines[("DEV004", "2024-03")]["credit"] is True
    counts = anomalies.counts_by("rule")
    assert counts[R.R_BILLING_DUP_IDENTICAL] == 1 and counts[R.R_BILLING_DUP_CONFLICT] == 1


# --------------------------------------------------------------------------
# Cross-source reconciliation and report shape
# --------------------------------------------------------------------------
def _mini_dataset():
    devices = _devices(("DEV001", "SITE-A", "100", "2026-09-12", "active"),
                       ("DEV002", "SITE-B", "200", "2026-09-12", "active"))
    telemetry = [{"device_id": "DEV001", "timestamp": "2026-09-12T14:57:03+00:00", "energy_kwh": "10"},
                 {"device_id": "DEV001", "timestamp": "2026-09-12T14:57:03+00:00", "energy_kwh": "20"},
                 {"device_id": "DEV900", "timestamp": "2026-09-12T14:57:03+00:00", "energy_kwh": "5"}]
    billing = [{"device_id": "DEV001", "billing_period": "2024-01", "billed_kwh": "30", "credit_flag": "Y"},
               {"device_id": "DEV910", "billing_period": "2024-01", "billed_kwh": "50", "credit_flag": "N"}]
    return devices, telemetry, billing


def test_cross_entity_populations_are_disjoint_and_reported():
    result = R.reconcile(*_mini_dataset())
    coverage = result["coverage"]
    assert coverage["devices_only"] == ["DEV002"]
    assert coverage["telemetry_only"] == ["DEV900"]
    assert coverage["billing_only"] == ["DEV910"]
    assert coverage["matched_all_three"] == ["DEV001"]


def test_totals_only_cover_devices_being_counted():
    result = R.reconcile(*_mini_dataset())
    assert result["summary"]["devices_canonical_active"] == 2
    assert result["summary"]["energy_kwh_total"] == 30.0      # DEV001 only
    assert result["summary"]["billed_kwh_total"] == 30.0
    assert result["summary"]["unbilled_active_devices"] == ["DEV002"]


def test_every_input_row_is_accounted_for():
    devices, telemetry, billing = _mini_dataset()
    result = R.reconcile(devices, telemetry, billing)
    counted = (result["summary"]["device_rows_in"] - result["summary"]["devices_excluded_total"])
    assert counted >= result["summary"]["devices_canonical_active"]
    logged = len(result["anomalies"].rows)
    assert logged >= 1
    for row in result["anomalies"].rows:
        assert row["category"] in {"normalised", "deduped", "excluded",
                                   "excluded_window", "unknown_entity", "reported"}


def test_pipeline_is_deterministic():
    first = R.reconcile(*_mini_dataset())
    second = R.reconcile(*_mini_dataset())
    assert first["summary"] == second["summary"]
    assert first["anomalies"].rows == second["anomalies"].rows


def test_canonical_input_reproduces_itself_without_normalisation_noise():
    """Re-running on already-canonical data must report zero normalisations."""
    result = R.reconcile(*_mini_dataset())
    canonical_devices = _devices(*[(r["device_id"], r["site_id"], "100",
                                    r["commissioned_date"], r["status"])
                                   for r in result["registry"].values()])
    second = R.reconcile(canonical_devices, [], [])
    categories = second["anomalies"].counts_by("category")
    assert "normalised" not in categories and "deduped" not in categories
    assert second["summary"]["devices_canonical_active"] == 2


# --------------------------------------------------------------------------
# Q1 fixtures end to end (these numbers are hand-derived from the generator)
# --------------------------------------------------------------------------
def test_q1_fixture_active_count_matches_hand_derivation():
    result = R.reconcile_csvs(DATA / "devices.csv", DATA / "telemetry_q1.csv",
                              DATA / "billing_raw.csv")
    assert result["summary"]["devices_canonical_active"] == 27


def test_gap_is_fully_explained_and_adds_up():
    result = R.reconcile_csvs(DATA / "devices.csv", DATA / "telemetry_q1.csv",
                              DATA / "billing_raw.csv")
    summary, gap = result["summary"], result["gap"]
    assert gap["raw_rows_active_vs_canonical"] == 7
    parts = (gap["duplicate_rows_collapsed"] + gap["disqualified_active_rows"]
             + gap["keyless_active_rows"])
    assert parts == gap["raw_rows_active_vs_canonical"]          # nothing unexplained
    assert summary["devices_canonical_active"] == 27


def test_report_anomaly_counts_match_pinned_contract():
    """Pins every rule count against the generator's documented defect list."""
    result = R.reconcile_csvs(DATA / "devices.csv", DATA / "telemetry_q1.csv",
                              DATA / "billing_raw.csv")
    assert result["anomalies"].counts_by("rule") == {
        R.R_DEVICE_DUP_IDENTICAL: 1, R.R_DEVICE_DUP_CONFLICT: 1,
        R.R_DEVICE_STATUS_UNKNOWN: 2, R.R_DEVICE_STATUS_CANON: 6,
        R.R_DEVICE_DATE_AMBIGUOUS: 1, R.R_DEVICE_DATE_INVALID: 1,
        R.R_DEVICE_DATE_OUT_OF_RANGE: 1, R.R_DEVICE_DATE_CANON: 3,
        R.R_DEVICE_CAPACITY_CANON: 2, R.R_DEVICE_CAPACITY_INVALID: 4,
        R.R_DEVICE_CAPACITY_MISSING: 1, R.R_DEVICE_KEY_MISSING: 1,
        R.R_DEVICE_KEY_CANON: 2, R.R_DEVICE_SITE_CANON: 2,
        R.R_KEY_MISSING: 1,
        R.R_TELEMETRY_TS_OUT_OF_RANGE: 2, R.R_TELEMETRY_TS_AMBIGUOUS: 1,
        R.R_TELEMETRY_TS_CANON: 2,
        R.R_TELEMETRY_VALUE_INVALID: 4, R.R_TELEMETRY_VALUE_CANON: 1,
        R.R_TELEMETRY_DUP_IDENTICAL: 1, R.R_TELEMETRY_DUP_CONFLICT: 2,
        R.R_TELEMETRY_CADENCE: 2,
        R.R_BILLING_PERIOD_OUT_OF_RANGE: 2, R.R_BILLING_PERIOD_CANON: 2,
        R.R_BILLING_KWH_INVALID: 2, R.R_BILLING_KWH_CANON: 2,
        R.R_BILLING_FLAG_UNKNOWN: 1, R.R_BILLING_FLAG_CANON: 111,
        R.R_BILLING_DUP_IDENTICAL: 1, R.R_BILLING_DUP_CONFLICT: 1,
        R.R_BILLING_KEY_CANON: 1,
        R.R_CROSS_DEVICE_ONLY: 1, R.R_CROSS_TELEMETRY_ONLY: 2,
        R.R_CROSS_BILLING_ONLY: 2,
    }


def test_cli_writes_outputs_and_parses(tmp_path):
    out = tmp_path / "out.csv"
    report = tmp_path / "report.json"
    anomalies_csv = tmp_path / "anomalies.csv"
    rc = R.main([str(DATA / "devices.csv"), str(DATA / "telemetry_q1.csv"),
                 str(DATA / "billing_raw.csv"), "--out", str(out),
                 "--report", str(report), "--anomalies-csv", str(anomalies_csv)])
    assert rc == 0
    assert out.is_file() and report.is_file() and anomalies_csv.is_file()
    payload = json.loads(report.read_text())
    assert payload["summary"]["devices_canonical_active"] == 27
    with open(out, newline="") as fh:
        header = next(csv.reader(fh))
    assert header[:5] == ["device_id", "site_id", "capacity_kw",
                          "commissioned_date", "status"]
    assert "energy_kwh" in header and "billed_kwh" in header
    with open(anomalies_csv, newline="") as fh:
        assert next(csv.reader(fh)) == R.ANOMALY_COLUMNS


def test_cli_rejects_missing_inputs(capsys):
    with pytest.raises(SystemExit):
        R.main(["does_not_exist.csv", str(DATA / "telemetry_q1.csv"),
                str(DATA / "billing_raw.csv")])


def test_cli_defaults_produce_data_devices_telemetry(tmp_path):
    """Default args resolve the data/ files and write <stem>.reconciled.csv."""
    script = SRC_DIR / "reconcile.py"
    proc = subprocess.run([sys.executable, str(script), "--out", str(tmp_path / "d.csv"),
                           "--report", str(tmp_path / "r.json"), "--anomalies-csv", ""],
                          cwd=str(SRC_DIR), stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, timeout=180)
    assert proc.returncode == 0, proc.stderr
    assert (tmp_path / "d.csv").is_file()
