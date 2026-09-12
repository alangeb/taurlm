#!/usr/bin/env python3
"""Renewable-energy asset reconciliation pipeline: devices + telemetry + billing.

Purpose
-------
Reconcile three upstream CSV extracts and answer one question: how many
devices are *counted* as active, and why does that number differ from the
number the raw device table appears to contain?

Canonicalization is always "keep the canonical / most-complete record" (see
docs/designs/DECISIONS.md, Reconciliation section) and every record that
changes shape, or is dropped from canonical totals, is written to one
reconciliation report that is machine- and human-readable.

Library entry points:
    reconcile(devices, telemetry, billing) -> dict   (pure, in-memory)
    main() -> int                                    (CLI, reads/writes CSVs)

Pure stdlib (csv, re, datetime, json, argparse). No third-party dependencies.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Report columns (fixed order, stable header).
# ---------------------------------------------------------------------------
ANOMALY_COLUMNS = [
    "rule",            # machine-readable rule id, e.g. DEVICE.DUP_CONFLICT
    "category",        # normalised | deduped | excluded | excluded_window | unknown_entity
    "source",          # devices | telemetry | billing | cross
    "record_key",      # canonical key of the row (device id, or dev@ts)
    "field",           # offending field ('' for record-level findings)
    "raw_value",       # original raw value
    "canonical_value", # value actually used ('' when the record was dropped)
    "reason",          # human-readable explanation
]

# ---------------------------------------------------------------------------
# Rule identifiers. Kept as constants so tests and the report can never drift.
# ---------------------------------------------------------------------------
R_DEVICE_KEY_CANON = "DEVICE.KEY_CANONICALISED"
R_DEVICE_DUP_IDENTICAL = "DEVICE.DUP_IDENTICAL"
R_DEVICE_DUP_CONFLICT = "DEVICE.DUP_CONFLICT"
R_DEVICE_STATUS_CANON = "DEVICE.STATUS_CANONICALISED"
R_DEVICE_STATUS_UNKNOWN = "DEVICE.STATUS_UNKNOWN"
R_DEVICE_CAPACITY_CANON = "DEVICE.CAPACITY_CANONICALISED"
R_DEVICE_CAPACITY_INVALID = "DEVICE.CAPACITY_INVALID"
R_DEVICE_CAPACITY_MISSING = "DEVICE.CAPACITY_MISSING"
R_DEVICE_DATE_CANON = "DEVICE.DATE_CANONICALISED"
R_DEVICE_DATE_AMBIGUOUS = "DEVICE.DATE_AMBIGUOUS"
R_DEVICE_DATE_INVALID = "DEVICE.DATE_INVALID"
R_DEVICE_DATE_OUT_OF_RANGE = "DEVICE.DATE_OUT_OF_RANGE"  # already declared
R_DEVICE_KEY_MISSING = "DEVICE.KEY_MISSING"
R_DEVICE_SITE_CANON = "DEVICE.SITE_CANONICALISED"
R_TELEMETRY_KEY_CANON = "TELEMETRY.KEY_CANONICALISED"
R_TELEMETRY_DUP_IDENTICAL = "TELEMETRY.DUP_IDENTICAL"
R_TELEMETRY_DUP_CONFLICT = "TELEMETRY.DUP_CONFLICT"
R_TELEMETRY_TS_CANON = "TELEMETRY.TIMESTAMP_CANONICALISED"
R_TELEMETRY_TS_AMBIGUOUS = "TELEMETRY.TIMESTAMP_AMBIGUOUS"
R_TELEMETRY_TS_INVALID = "TELEMETRY.TIMESTAMP_INVALID"
R_TELEMETRY_TS_OUT_OF_RANGE = "TELEMETRY.TIMESTAMP_OUT_OF_RANGE"
R_TELEMETRY_CADENCE = "TELEMETRY.CADENCE_VIOLATION"
R_TELEMETRY_VALUE_CANON = "TELEMETRY.VALUE_CANONICALISED"
R_TELEMETRY_VALUE_INVALID = "TELEMETRY.VALUE_INVALID"
R_TELEMETRY_DEVICE_UNKNOWN = "TELEMETRY.DEVICE_UNKNOWN"
R_BILLING_KEY_CANON = "BILLING.KEY_CANONICALISED"
R_BILLING_PERIOD_CANON = "BILLING.PERIOD_CANONICALISED"
R_BILLING_PERIOD_AMBIGUOUS = "BILLING.PERIOD_AMBIGUOUS"
R_BILLING_PERIOD_INVALID = "BILLING.PERIOD_INVALID"
R_BILLING_PERIOD_OUT_OF_RANGE = "BILLING.PERIOD_OUT_OF_RANGE"
R_BILLING_DUP_IDENTICAL = "BILLING.DUP_IDENTICAL"
R_BILLING_DUP_CONFLICT = "BILLING.DUP_CONFLICT"
R_BILLING_KWH_CANON = "BILLING.KWH_CANONICALISED"
R_BILLING_KWH_INVALID = "BILLING.KWH_INVALID"
R_BILLING_FLAG_CANON = "BILLING.FLAG_CANONICALISED"
R_BILLING_FLAG_UNKNOWN = "BILLING.FLAG_UNKNOWN"
R_BILLING_DEVICE_UNKNOWN = "BILLING.DEVICE_UNKNOWN"
R_CROSS_DEVICE_ONLY = "CROSS.DEVICE_ONLY"
R_CROSS_TELEMETRY_ONLY = "CROSS.TELEMETRY_ONLY"
R_CROSS_BILLING_ONLY = "CROSS.BILLING_ONLY"
R_KEY_MISSING = "KEY_MISSING"  # source-agnostic: no usable device id

# Device-level rules whose presence removes a device from the canonical counts.
# Rationale (DECISIONS 10.2): a device row we cannot trust must not silently
# contribute to a headline count; it is excluded wholesale and reported.
# Device-level rules that remove a device from the canonical ACTIVE count.
# Policy (DECISIONS 10.2): only IDENTITY, STATUS and DATE integrity failures
# disqualify a device, because those are what make "is this one active device?"
# unanswerable. Capacity problems are different in kind: they do not change
# whether a device is active, so the device is still counted, but
# capacity_valid=False keeps it out of any capacity sum. Both are reported.
DEVICE_DISQUALIFYING_RULES = frozenset({
    R_DEVICE_DUP_CONFLICT,
    R_DEVICE_STATUS_UNKNOWN,
    R_DEVICE_DATE_AMBIGUOUS,
    R_DEVICE_DATE_INVALID,
    R_DEVICE_DATE_OUT_OF_RANGE,    # commissioned after the window: cannot have window history
})

# Canonical window (Q1 2024, inclusive, UTC): 2026-09-12 .. 2026-09-12.
WINDOW_FIRST_DAY = "2026-09-12"
WINDOW_LAST_DAY = "2026-09-12"
WINDOW_FIRST_PERIOD = "2024-01"
WINDOW_LAST_PERIOD = "2024-03"
TELEMETRY_CADENCE = timedelta(minutes=10)
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)

_ACTIVE = "active"
_INACTIVE = "inactive"
STATUS_MAP = {
    "active": _ACTIVE, "in service": _ACTIVE, "in-service": _ACTIVE,
    "inservice": _ACTIVE, "online": _ACTIVE, "running": _ACTIVE,
    "inactive": _INACTIVE, "retired": _INACTIVE, "decommissioned": _INACTIVE,
    "decomissioned": _INACTIVE, "off": _INACTIVE, "offline": _INACTIVE,
    "out of service": _INACTIVE, "out_of_service": _INACTIVE,
    "out-of-service": _INACTIVE, "mothballed": _INACTIVE, "retired.": _INACTIVE,
}
_TRUE_TOKENS = frozenset({"y", "yes", "true", "t", "1"})
_FALSE_TOKENS = frozenset({"n", "no", "false", "f", "0", ""})
_MISSING_TOKENS = frozenset({"", "n/a", "na", "null", "none", "nan",
                             "unknown", "unknown_value", "-", "--", "?"})
_EPOCH_RE = re.compile(r"^\d{9,14}$")          # bare epoch seconds or ms
_QUANTITY_RE = re.compile(r"^(?P<num>[-+]?\d[\d,]*(?:\.\d+)?)\s*(?P<unit>[A-Za-z]*)$")
_GROUPED_RE = re.compile(r"^\d{1,3}(?:,\d{3})+(?:\.\d+)?$")
_NON_ALNUM_RE = re.compile(r"[^A-Z0-9]+")
_DATE_ISO_RE = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})")
_DATE_SLASH_RE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})(?:[ T](?P<time>.+))?$")
_PERIOD_RE = re.compile(r"^(\d{4})-(\d{1,2})(?:-(\d{1,2}))?$")


def _is_missing(raw) -> bool:
    return str(raw or "").strip().lower() in _MISSING_TOKENS


def canonical_device_id(raw):
    """DEV012 / dev012 / ' dev012 ' / 'DEV012;' / 'DEV-012' -> 'DEV012'.

    Strips every non-alphanumeric character and uppercases. Deterministic and
    idempotent: canonical_device_id(canonical_device_id(x)) == canonical_device_id(x).
    Returns '' for nothing-but-noise input (a missing key, not a device).
    """
    if raw is None:
        return ""
    # Upper-case FIRST: the character class is ASCII-upper only, so applying it
    # before upper-casing would silently delete every lower-case letter.
    return _NON_ALNUM_RE.sub("", str(raw).upper())


def canonical_site(raw):
    """'site c' / 'SITE_C' / 'SITE-C' -> 'SITE-C'; '' for missing.

    Upper-cases first for the same reason as canonical_device_id: the
    character class is ASCII-upper only.
    """
    if _is_missing(raw):
        return ""
    return _NON_ALNUM_RE.sub("-", str(raw).upper()).strip("-")

def parse_number(raw, field="value"):
    """Parse an energy/capacity quantity into non-negative kWh / kW.

    Returns dict(ok, value, how, reason). Accepted shapes:
      '500'  ' 1,200 '  '$1,200.50'  '3,200 kW'  '1.5 MW'
    Unitless means kW/kWh; MW scales by 1000. Rejected (never guessed):
    negatives and '(150)' accounting negatives, inf/nan, unknown units,
    malformed thousand-separators. `how` is 'canonical' when the raw text was
    already a bare non-negative decimal, else 'normalised'.
    """
    text = str(raw or "").strip()
    if text.lower() in _MISSING_TOKENS:
        return {"ok": False, "value": None, "how": "missing",
                "reason": "%s is empty or a placeholder" % field}
    original = text
    negative = False
    if text.startswith("(") and text.endswith(")"):     # accounting negative
        negative, text = True, text[1:-1].strip()
    text = text.lstrip("$\u00a3\u20ac").strip()        # leading currency symbol
    match = _QUANTITY_RE.match(text)
    if not match:
        return {"ok": False, "value": None, "how": "invalid",
                "reason": "%s %r is not a recognisable quantity" % (field, original)}
    digits, unit = match.group("num"), match.group("unit").lower()
    if "," in digits and not _GROUPED_RE.match(digits.lstrip("-+")):
        return {"ok": False, "value": None, "how": "invalid",
                "reason": "%s %r has malformed thousands separators" % (field, original)}
    digits = digits.replace(",", "")
    if unit in ("", "kw", "kwh"):
        scale = 1.0
    elif unit == "mw":
        scale = 1000.0
    else:
        return {"ok": False, "value": None, "how": "invalid",
                "reason": "%s %r has unknown unit %r" % (field, original, unit)}
    try:
        value = float(digits)
    except ValueError:
        return {"ok": False, "value": None, "how": "invalid",
                "reason": "%s %r is not numeric" % (field, original)}
    if not math.isfinite(value):
        return {"ok": False, "value": None, "how": "invalid",
                "reason": "%s %r is not finite" % (field, original)}
    if negative or value < 0:
        return {"ok": False, "value": None, "how": "invalid",
                "reason": "%s %r is negative; kWh/kW quantities are unsigned"
                         % (field, original)}
    canonical = bool(re.match(r"^\d+(\.\d+)?$", text)) and not negative
    return {"ok": True, "value": value * scale,
            "how": "canonical" if canonical else "normalised", "reason": ""}


def parse_datetime(raw):
    """Parse a timestamp/date token -> dict(ok, dt, how, ambiguous, reason).

    Accepted formats (canonical form is ISO-8601 UTC):
      YYYY-MM-DD[THH:MM[:SS]] (+ optional Z / +HH:MM offset)
      DD/MM/YYYY or MM/DD/YYYY        (only when unambiguously one or the other)
      DD-MM-YYYY / YYYYMMDD
      epoch seconds (10 digits) or epoch milliseconds (13 digits)
    Naive timestamps are interpreted as UTC (upstream extracts are UTC-naive).
    Ambiguity rule: a slash/dash day-month pair where BOTH readings are legal
    calendar dates is genuinely ambiguous and is never guessed.
    """
    text = str(raw or "").strip()
    if text.lower() in _MISSING_TOKENS:
        return {"ok": False, "dt": None, "how": "missing", "ambiguous": False,
                "reason": "timestamp is empty or a placeholder"}
    if _EPOCH_RE.match(text):
        try:
            if len(text) == 10:
                dt = EPOCH + timedelta(seconds=int(text))
            elif len(text) == 13:
                dt = EPOCH + timedelta(milliseconds=int(text))
            elif len(text) == 14:
                dt = datetime.strptime(text, "%Y%m%d").replace(tzinfo=timezone.utc)
            else:
                raise ValueError
        except ValueError:
            return {"ok": False, "dt": None, "how": "invalid", "ambiguous": False,
                    "reason": "epoch value %r is out of parseable range" % text}
        return {"ok": True, "dt": dt, "how": "epoch", "ambiguous": False, "reason": ""}
    m = _DATE_SLASH_RE.match(text)
    if m:
        a, b, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        time_part = ""
        head, _, tail = text.partition(" ")
        if tail:
            time_part = tail.strip().replace("T", " ")
        day_first = (a > 12 and b <= 12)
        month_first = (b > 12 and a <= 12)
        if day_first == month_first:  # both plausible, or both impossible
            if _valid_date(year, b, a) and _valid_date(year, a, b):
                return {"ok": False, "dt": None, "how": "ambiguous", "ambiguous": True,
                        "reason": "%r could be %02d/%02d or %02d/%02d %d; not guessed"
                                  % (text, a, b, b, a, year)}
            return {"ok": False, "dt": None, "how": "invalid", "ambiguous": False,
                    "reason": "%r is not a valid calendar date either way" % text}
        day, month = (a, b) if day_first else (b, a)
        if not _valid_date(year, month, day):
            return {"ok": False, "dt": None, "how": "invalid", "ambiguous": False,
                    "reason": "%r is not a valid calendar date" % text}
        dt = _combine(year, month, day, time_part)
        if dt is None:
            return {"ok": False, "dt": None, "how": "invalid", "ambiguous": False,
                    "reason": "%r has an unparseable time part" % text}
        return {"ok": True, "dt": dt, "how": "slash", "ambiguous": False, "reason": ""}
    m = _DATE_ISO_RE.match(text)
    if m:
        year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if not _valid_date(year, month, day):
            return {"ok": False, "dt": None, "how": "invalid", "ambiguous": False,
                    "reason": "%r is not a valid calendar date" % text}
        rest = text[10:].lstrip("T ")
        dt = _combine(year, month, day, rest)
        if dt is None:
            return {"ok": False, "dt": None, "how": "invalid", "ambiguous": False,
                    "reason": "%r has an unparseable time part" % text}
        return {"ok": True, "dt": dt, "how": "iso", "ambiguous": False, "reason": ""}
    m = re.match(r"^(\d{1,2})-(\d{1,2})-(\d{4})$", text)
    if m:
        day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if _valid_date(year, month, day):
            return {"ok": True, "dt": datetime(year, month, day, tzinfo=timezone.utc),
                    "how": "dash", "ambiguous": False, "reason": ""}
        return {"ok": False, "dt": None, "how": "invalid", "ambiguous": False,
                "reason": "%r is not a valid calendar date" % text}
    return {"ok": False, "dt": None, "how": "invalid", "ambiguous": False,
            "reason": "%r is not a recognisable date/timestamp" % text}


def _valid_date(year, month, day):
    try:
        datetime(year, month, day)
        return True
    except ValueError:
        return False


def _combine(year, month, day, time_part):
    time_part = (time_part or "").strip().replace("Z", "+00:00")
    if not time_part:
        return datetime(year, month, day, tzinfo=timezone.utc)
    for fmt in ("%H:%M:%S%z", "%H:%M%z", "%H:%M:%S", "%H:%M"):
        try:
            parsed = datetime.strptime(time_part, fmt)
        except ValueError:
            continue
        dt = datetime(year, month, day, parsed.hour, parsed.minute, parsed.second,
                      tzinfo=parsed.tzinfo or timezone.utc)
        return dt.astimezone(timezone.utc)
    return None


def parse_period(raw):
    """Normalise a billing period token to YYYY-MM. Returns dict(ok, period, how, reason)."""
    text = str(raw or "").strip()
    if text.lower() in _MISSING_TOKENS:
        return {"ok": False, "period": None, "how": "missing",
                "reason": "billing_period is empty or a placeholder"}
    m = _PERIOD_RE.match(text)
    if m:
        year, month = int(m.group(1)), int(m.group(2))
        if not 1 <= month <= 12:
            return {"ok": False, "period": None, "how": "invalid",
                    "reason": "billing_period %r has an out-of-range month" % text}
        if m.group(3) and not _valid_date(year, month, int(m.group(3))):
            return {"ok": False, "period": None, "how": "invalid",
                    "reason": "billing_period %r is not a valid date" % text}
        period = "%04d-%02d" % (year, month)
        how = "canonical" if period == text else "normalised"
        return {"ok": True, "period": period, "how": how, "reason": ""}
    m = re.match(r"^(\d{1,2})/(\d{4})$", text)          # MM/YYYY
    if m and 1 <= int(m.group(1)) <= 12:
        return {"ok": True, "period": "%04s-%02d" % (m.group(2), int(m.group(1))),
                "how": "normalised", "reason": ""}
    m = re.match(r"^(\d{4})/(\d{1,2})$", text)          # YYYY/MM
    if m and 1 <= int(m.group(2)) <= 12:
        return {"ok": True, "period": "%s-%02d" % (m.group(1), int(m.group(2))),
                "how": "normalised", "reason": ""}
    return {"ok": False, "period": None, "how": "invalid",
            "reason": "billing_period %r is not YYYY-MM" % text}


def parse_flag(raw):
    """Normalise a credit flag to True/False. Returns dict(ok, value, how, reason)."""
    token = str(raw or "").strip().lower()
    if token in _TRUE_TOKENS:
        return {"ok": True, "value": True,
                "how": "canonical" if token == "true" else "normalised", "reason": ""}
    if token in _FALSE_TOKENS:
        return {"ok": True, "value": False,
                "how": "canonical" if token == "false" else "normalised", "reason": ""}
    return {"ok": False, "value": None, "how": "unknown",
            "reason": "credit_flag %r is neither a truthy nor a falsy token" % (raw,)}


def canonical_status(raw):
    """Normalise device status -> 'active'/'inactive', or None when unmappable."""
    token = str(raw or "").strip().lower().rstrip(".")
    if token in _MISSING_TOKENS:
        return None
    return STATUS_MAP.get(token)


def _fmt(dt):
    if dt is None:
        return ""
    if dt.hour or dt.minute or dt.second:
        return dt.strftime("%Y-%m-%dT%H:%M:%S+00:00")
    return dt.strftime("%Y-%m-%d")


def _fmt_num(value):
    if value is None:
        return ""
    return ("%f" % value).rstrip("0").rstrip(".")


# ---------------------------------------------------------------------------
# Anomaly collection
# ---------------------------------------------------------------------------
class Anomalies:
    """Append-only collector for reconciliation findings.

    Every normalisation choice, drop and cross-entity gap goes through add(),
    so the report is a complete audit trail: nothing is silently fixed and
    nothing is silently dropped.
    """

    def __init__(self):
        self.rows = []

    def add(self, rule, category, source, record_key, field, raw, canonical, reason):
        self.rows.append({
            "rule": rule, "category": category, "source": source,
            "record_key": record_key, "field": field,
            "raw_value": "" if raw is None else str(raw),
            "canonical_value": "" if canonical is None else str(canonical),
            "reason": reason,
        })

    def counts_by(self, key):
        return dict(Counter(row[key] for row in self.rows))

    def rules_for(self, record_key):
        return {row["rule"] for row in self.rows if row["record_key"] == record_key}


# Accept the header spellings upstream systems actually emit. Keys are the
# canonical column name; values are accepted aliases (compared stripped/lowered).
HEADER_ALIASES = {
    "device_id": ["device", "id", "device_id", "deviceid", "asset_id", "panel_id"],
    "site_id": ["site", "site", "site_name", "site_id", "location"],
    "capacity_kw": ["capacity", "capacity_kw", "capacitykw", "rated_capacity_kw"],
    "commissioned_date": ["commissioned_date", "commission_date", "installed_date",
                          "date_commissioned", "in_service_date"],
    "status": ["status", "device_status", "state"],
    "timestamp": ["timestamp", "datetime", "reading_time", "ts", "time"],
    "energy_kwh": ["energy_kwh", "energy", "kwh", "kwh_total", "value"],
    "billing_period": ["billing_period", "period", "billing_month", "month"],
    "billed_kwh": ["billed_kwh", "billed", "kwh_billed", "billed_energy_kwh"],
    "credit_flag": ["credit_flag", "credit", "has_credit", "credit_applied"],
}


def resolve_headers(fieldnames):
    """Map messy header names onto canonical column names.

    Returns (mapping, unresolved). Unknown headers are left alone rather than
    guessed at, and are reported by the caller.
    """
    mapping, unresolved = {}, []
    lookup = {alias: canon for canon, aliases in HEADER_ALIASES.items()
              for alias in aliases}
    for raw in fieldnames or []:
        if raw is None:
            continue
        key = str(raw).strip().lower().lstrip("\ufeff")
        if key in HEADER_ALIASES:
            mapping[raw] = raw
        elif key in lookup:
            mapping[raw] = lookup[key]
        else:
            mapping[raw] = raw
            unresolved.append(raw)
    return mapping, unresolved


def _read_csv(path):
    path = Path(path)
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None:
            return [], []
        headers = [k.strip() for k in reader.fieldnames]
        mapping, unresolved = resolve_headers(headers)
        rows = []
        for raw_row in reader:
            row = {}
            for key, value in raw_row.items():
                if key is None:
                    continue
                row[mapping.get(str(key).strip(), str(key).strip())] = value
            if raw_row.get(None):     # extra columns beyond the header
                row["_extra"] = ";".join(str(v) for v in raw_row[None] if v)
            rows.append(row)
        return headers + (["UNRESOLVED:" + ",".join(unresolved)] if unresolved else []), rows


def _raw_is_canonical_iso(raw):
    return bool(re.match(r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}:\d{2}(\+00:00)?)?$",
                         str(raw or "").strip()))


def normalize_devices(rows, anomalies, last_day=WINDOW_LAST_DAY):
    """Canonicalise the device register. Returns (registry, stats).

    registry maps canonical device_id -> record dict with status, capacity,
    commissioned date, `excluded` flag and the rule ids that disqualified it.
    Exclusion policy (see DECISIONS 10.2): a device leaves the canonical
    registry only when its IDENTITY, STATUS or DATE cannot be trusted.
    Quantity problems (capacity) are reported but do not remove the device,
    because they do not affect whether it is an active device.
    """
    grouped, raw_key_counts = {}, Counter()
    stats = Counter()
    for pos, row in enumerate(rows):
        stats["rows"] += 1
        raw_id = (row or {}).get("device_id", "")
        key = canonical_device_id(raw_id)
        if not key:
            anomalies.add(R_DEVICE_KEY_MISSING, "excluded", "devices",
                          "(missing)", "device_id", raw_id, "",
                          "row has no usable device id; cannot be attributed")
            stats["dropped_no_key"] += 1
            continue
        raw_key_counts[raw_id] += 1
        if str(raw_id or "") != key:
            anomalies.add(R_DEVICE_KEY_CANON, "normalised", "devices", key,
                          "device_id", raw_id, key,
                          "device id canonicalised: upper-cased, non-alphanumerics removed")
        rec = {"device_id": key, "source_row": pos, "raw": dict(row),
               "rules": set(), "raw_status": str((row or {}).get("status") or "")}
        grouped.setdefault(key, []).append(rec)

    registry = {}
    for key, records in grouped.items():
        if len(records) > 1:
            same = all(_device_fingerprint(r) == _device_fingerprint(records[0])
                       for r in records)
            if same:
                anomalies.add(R_DEVICE_DUP_IDENTICAL, "deduped", "devices", key,
                              "device_id", str(records[0]["raw"].get("device_id", "")),
                              key, "%d identical device rows collapsed to 1"
                              % len(records))
                survivor = records[0]
            else:
                registry[key] = _excluded_record(key, records, R_DEVICE_DUP_CONFLICT)
                diffs = _conflict_diff(records)
                fingerprints = " | ".join(
                    ",".join("" if part is None else str(part)
                             for part in _device_fingerprint(r))
                    for r in records)
                anomalies.add(R_DEVICE_DUP_CONFLICT, "excluded", "devices", key,
                              "(record)", fingerprints, "",
                              "%d rows disagree (%s); device excluded from canonical "
                              "counts rather than silently picking a winner"
                              % (len(records), "; ".join(diffs)))
                stats["excluded_conflict"] += 1
                continue
        else:
            survivor = records[0]
        registry[key] = _finalize_device(survivor, anomalies, stats, last_day)

    for key, rec in sorted(registry.items()):
        if rec["excluded"] or rec["status"] != _ACTIVE:
            continue
        stats["active"] += 1
        if rec["capacity_valid"]:
            stats["active_with_valid_capacity"] += 1
    return registry, stats


def _device_fingerprint(rec):
    row = rec["raw"]
    cap = parse_number(row.get("capacity_kw"), "capacity_kw")
    dt = parse_datetime(row.get("commissioned_date"))
    return (canonical_site(row.get("site_id")),
            None if not cap["ok"] else _fmt_num(cap["value"]),
            None if not dt["ok"] else _fmt(dt["dt"]),
            canonical_status(row.get("status")))


def _conflict_diff(records):
    """Describe which fields disagree across duplicate rows (for the report)."""
    out = []
    for name in ("site_id", "capacity_kw", "commissioned_date", "status"):
        values = {str((rec["raw"] or {}).get(name, "")) for rec in records}
        if len(values) > 1:
            out.append("%s=%s" % (name, "/".join(sorted(values))))
    return out


def _excluded_record(key, records, rule):
    return {"device_id": key, "source_row": records[0]["source_row"],
            "raw": dict(records[0]["raw"]), "site_id": "", "capacity_kw": None,
            "capacity_valid": False,
            "commissioned_date": "", "status": "", "rules": {rule},
            "excluded": True, "raw_status": records[0]["raw"].get("status", "")}


def _finalize_device(rec, anomalies, stats, last_day=WINDOW_LAST_DAY):
    row = rec["raw"]
    key = rec["device_id"]
    rules = set()
    site = canonical_site(row.get("site_id"))
    raw_site = str(row.get("site_id") or "")
    if raw_site != site and not _is_missing(raw_site):
        anomalies.add(R_DEVICE_SITE_CANON, "normalised", "devices", key, "site_id",
                      raw_site, site, "site ids are upper-cased, separators normalised")

    raw_status = str(row.get("status") or "")
    status = canonical_status(raw_status)
    if status is None:
        rules.add(R_DEVICE_STATUS_UNKNOWN)
        anomalies.add(R_DEVICE_STATUS_UNKNOWN, "excluded", "devices", key, "status",
                      raw_status, "",
                      "status is missing or not in the active/inactive vocabulary; "
                      "device excluded from canonical counts")
    elif raw_status.strip().lower() != status:
        rules.add(R_DEVICE_STATUS_CANON)
        anomalies.add(R_DEVICE_STATUS_CANON, "normalised", "devices", key, "status",
                      raw_status, status, "status vocabulary normalised")

    cap = parse_number(row.get("capacity_kw"), "capacity_kw")
    if not cap["ok"]:
        rule = R_DEVICE_CAPACITY_MISSING if cap["how"] == "missing" else R_DEVICE_CAPACITY_INVALID
        anomalies.add(rule, "reported", "devices", key, "capacity_kw",
                      row.get("capacity_kw", ""), "", cap["reason"])
        capacity = None
    else:
        capacity = cap["value"]
        raw_cap = str(row.get("capacity_kw") or "").strip()
        if cap["how"] != "canonical":
            rules.add(R_DEVICE_CAPACITY_CANON)
            anomalies.add(R_DEVICE_CAPACITY_CANON, "normalised", "devices", key,
                          "capacity_kw", raw_cap, _fmt_num(capacity),
                          "capacity normalised to numeric kW")

    dt = parse_datetime(row.get("commissioned_date"))
    if not dt["ok"]:
        rule = (R_DEVICE_DATE_AMBIGUOUS if dt.get("ambiguous")
                else R_DEVICE_DATE_INVALID if dt["how"] != "missing"
                else R_DEVICE_DATE_INVALID)
        rules.add(rule)
        anomalies.add(rule, "excluded", "devices", key, "commissioned_date",
                      row.get("commissioned_date", ""), "", dt["reason"])
        date_out = ""
    else:
        date_out = _fmt(dt["dt"])
        limit = parse_datetime(last_day)["dt"] + timedelta(days=1)
        if dt["dt"] >= limit:
            rules.add(R_DEVICE_DATE_OUT_OF_RANGE)
            anomalies.add(R_DEVICE_DATE_OUT_OF_RANGE, "excluded", "devices", key,
                          "commissioned_date", row.get("commissioned_date", ""), date_out,
                          "commissioned %s, after the reporting window ends %s; it "
                          "cannot legitimately appear in window telemetry or billing"
                          % (date_out, last_day))
        if str(row.get("commissioned_date") or "").strip() != date_out:
            rules.add(R_DEVICE_DATE_CANON)
            anomalies.add(R_DEVICE_DATE_CANON, "normalised", "devices", key,
                          "commissioned_date", row.get("commissioned_date", ""),
                          date_out, "date normalised to ISO-8601 UTC")

    excluded = bool(rules & DEVICE_DISQUALIFYING_RULES)
    rec.update({"site_id": site, "capacity_kw": capacity, "capacity_valid": cap["ok"],
                "commissioned_date": date_out, "status": status or "",
                "rules": rules, "excluded": excluded})
    if excluded:
        stats["excluded_quality"] += 1
    return rec


def normalize_telemetry(rows, anomalies, window=(WINDOW_FIRST_DAY, WINDOW_LAST_DAY)):
    """Canonicalise telemetry. Returns (readings, stats).

    Readings are keyed by (device_id, UTC timestamp) and deduplicated. Rows
    outside the reporting window or with unusable timestamps/values are
    excluded from totals; unknown devices are reported as cross-source gaps.
    `window` is an inclusive (first_day, last_day) pair of YYYY-MM-DD strings.
    """
    first = parse_datetime(window[0])["dt"]
    last_exclusive = parse_datetime(window[1])["dt"] + timedelta(days=1)
    grouped, anomalies_by_key = {}, {}
    stats = Counter()
    for row in rows:
        stats["rows"] += 1
        raw_id = (row or {}).get("device_id", "")
        key = canonical_device_id(raw_id)
        if not key:
            anomalies.add(R_KEY_MISSING, "excluded", "telemetry", "(missing)",
                          "device_id", raw_id, "", "reading has no usable device id")
            stats["dropped_no_key"] += 1
            continue
        if str(raw_id or "") != key:
            anomalies.add(R_TELEMETRY_KEY_CANON, "normalised", "telemetry", key,
                          "device_id", raw_id, key, "device id normalised")
        ts_raw = (row or {}).get("timestamp", "")
        parsed = parse_datetime(ts_raw)
        if not parsed["ok"]:
            rule = (R_TELEMETRY_TS_AMBIGUOUS if parsed.get("ambiguous")
                    else R_TELEMETRY_TS_INVALID)
            anomalies.add(rule, "excluded", "telemetry", key, "timestamp", ts_raw, "",
                          parsed["reason"])
            stats["dropped_bad_timestamp"] += 1
            continue
        dt = parsed["dt"]
        if not (first <= dt < last_exclusive):
            anomalies.add(R_TELEMETRY_TS_OUT_OF_RANGE, "excluded_window", "telemetry",
                          key, "timestamp", ts_raw, _fmt(dt),
                          "timestamp falls outside the reporting window %s..%s"
                          % (window[0], window[1]))
            stats["dropped_out_of_window"] += 1
            continue
        if str(ts_raw or "").strip() != _fmt(dt) and not _raw_is_canonical_iso(ts_raw):
            anomalies.add(R_TELEMETRY_TS_CANON, "normalised", "telemetry", key,
                          "timestamp", ts_raw, _fmt(dt),
                          "timestamp %s (normalised to ISO-8601 UTC)" % parsed["how"])
        val = parse_number((row or {}).get("energy_kwh"), "energy_kwh")
        if not val["ok"]:
            anomalies.add(R_TELEMETRY_VALUE_INVALID, "excluded", "telemetry", key,
                          "energy_kwh", (row or {}).get("energy_kwh", ""), "",
                          val["reason"])
            stats["dropped_bad_value"] += 1
            continue
        raw_val = str((row or {}).get("energy_kwh") or "").strip()
        if val["how"] != "canonical":
            anomalies.add(R_TELEMETRY_VALUE_CANON, "normalised", "telemetry", key,
                          "energy_kwh", raw_val, _fmt_num(val["value"]),
                          "reading normalised to numeric kWh")
        reading_key = (key, dt)
        readings = grouped.setdefault(reading_key, [])
        if readings and readings[0] != val["value"]:
            anomalies_by_key.setdefault(reading_key, set()).add("conflict")
            anomalies_by_key.setdefault(reading_key, set()).add("conflict")
        anomalies_by_key.setdefault(reading_key, set()).add("seen")
        readings.append(val["value"])

    readings_out = {}
    for (key, dt), values in grouped.items():
        if len(values) > 1:
            conflict = "conflict" in anomalies_by_key.get((key, dt), set())
            rule = R_TELEMETRY_DUP_CONFLICT if conflict else R_TELEMETRY_DUP_IDENTICAL
            anomalies.add(rule, "deduped", "telemetry", "%s@%s" % (key, _fmt(dt)),
                          "energy_kwh", "/".join(_fmt_num(v) for v in values),
                          _fmt_num(values[0]),
                          "%d readings for the same (device, timestamp); kept %s (%s)"
                          % (len(values), _fmt_num(values[0]),
                             "values agree" if not conflict else
                             "values DISAGREE - kept first occurrence, see DECISIONS 10.3"))
            stats["deduped"] += 1
        readings_out[(key, dt)] = values[0]

    # 10-minute cadence check on what survived.
    per_device = {}
    for (key, dt), value in readings_out.items():
        per_device.setdefault(key, []).append(dt)
    for key, stamps in sorted(per_device.items()):
        stamps.sort()
        for idx, dt in enumerate(stamps):
            violations = []
            if dt.minute % 10 or dt.second:
                violations.append("off the 10-minute grid (minute=%02d, second=%02d)"
                                  % (dt.minute, dt.second))
            if idx:
                delta = dt - stamps[idx - 1]
                if delta <= timedelta(0):
                    violations.append("not after previous reading %s" % _fmt(stamps[idx - 1]))
                elif delta % TELEMETRY_CADENCE:
                    violations.append("gap of %s from previous reading is not a "
                                      "multiple of 10 minutes" % delta)
            if violations:
                anomalies.add(R_TELEMETRY_CADENCE, "reported", "telemetry", key,
                              "timestamp", _fmt(dt), _fmt(dt), "; ".join(violations))
                stats["cadence_violations"] += 1
    stats["readings"] = len(readings_out)
    return readings_out, stats


def normalize_billing(rows, anomalies, window=(WINDOW_FIRST_PERIOD, WINDOW_LAST_PERIOD)):
    """Canonicalise billing lines. Returns (lines, stats).

    lines maps (device_id, YYYY-MM) -> {billed_kwh, credit} after dedupe.
    """
    lines, dup_values = {}, {}
    stats = Counter()
    for row in rows:
        stats["rows"] += 1
        raw_id = (row or {}).get("device_id", "")
        key = canonical_device_id(raw_id)
        if not key:
            anomalies.add(R_KEY_MISSING, "excluded", "billing", "(missing)",
                          "device_id", raw_id, "", "line has no usable device id")
            stats["dropped_no_key"] += 1
            continue
        if str(raw_id or "") != key:
            anomalies.add(R_BILLING_KEY_CANON, "normalised", "billing", key,
                          "device_id", raw_id, key, "device id normalised")
        period_raw = (row or {}).get("billing_period", "")
        period = parse_period(period_raw)
        if not period["ok"]:
            anomalies.add(R_BILLING_PERIOD_INVALID, "excluded", "billing", key,
                          "billing_period", period_raw, "", period["reason"])
            stats["dropped_bad_period"] += 1
            continue
        if period["how"] == "normalised":
            anomalies.add(R_BILLING_PERIOD_CANON, "normalised", "billing", key,
                          "billing_period", period_raw, period["period"],
                          "period normalised to YYYY-MM")
        if not (window[0] <= period["period"] <= window[1]):
            anomalies.add(R_BILLING_PERIOD_OUT_OF_RANGE, "excluded_window", "billing",
                          key, "billing_period", period_raw, period["period"],
                          "billing period outside the reporting window %s..%s"
                          % (window[0], window[1]))
            stats["dropped_out_of_window"] += 1
            continue
        kwh = parse_number((row or {}).get("billed_kwh"), "billed_kwh")
        if not kwh["ok"]:
            anomalies.add(R_BILLING_KWH_INVALID, "excluded", "billing", key,
                          "billed_kwh", (row or {}).get("billed_kwh", ""), "",
                          kwh["reason"])
            stats["dropped_bad_kwh"] += 1
            continue
        raw_kwh = str((row or {}).get("billed_kwh") or "").strip()
        if kwh["how"] != "canonical":
            anomalies.add(R_BILLING_KWH_CANON, "normalised", "billing", key,
                          "billed_kwh", raw_kwh, _fmt_num(kwh["value"]),
                          "billed_kwh normalised to numeric kWh")
        flag = parse_flag((row or {}).get("credit_flag", ""))
        if not flag["ok"]:
            anomalies.add(R_BILLING_FLAG_UNKNOWN, "excluded", "billing", key,
                          "credit_flag", (row or {}).get("credit_flag", ""), "",
                          flag["reason"] + "; line excluded rather than assumed False")
            stats["dropped_bad_flag"] += 1
            continue
        raw_flag = str((row or {}).get("credit_flag") or "").strip()
        if flag["how"] == "normalised":
            anomalies.add(R_BILLING_FLAG_CANON, "normalised", "billing", key,
                          "credit_flag", raw_flag, str(flag["value"]).lower(),
                          "credit flag truthiness normalised")
        line_key = (key, period["period"])
        payload = (_fmt_num(kwh["value"]), flag["value"])
        if line_key in lines:
            conflict = lines[line_key]["payload"] != payload
            rule = R_BILLING_DUP_CONFLICT if conflict else R_BILLING_DUP_IDENTICAL
            prev = lines[line_key]
            anomalies.add(rule, "deduped", "billing", "%s@%s" % line_key,
                          "billed_kwh/credit_flag",
                          "%s/%s" % (prev["payload"][0], prev["payload"][1]),
                          "%s/%s" % payload,
                          "duplicate billing line for this device+period%s"
                          % ("" if not conflict else
                             "; DISAGREEING values - kept %s/%s per DECISIONS 10.3"
                             % payload))
            stats["deduped"] += 1
            dup_values.setdefault(line_key, []).append(payload)
            continue
        lines[line_key] = {"device_id": key, "billing_period": period["period"],
                           "billed_kwh": kwh["value"], "credit": flag["value"],
                           "payload": payload}
    stats["lines"] = len(lines)
    return lines, stats


# YYYY-MM compares correctly as text, so window checks are plain string ranges.


def reconcile_entity_presence(registry, telemetry, billing, anomalies):
    """Cross-source coverage: which devices appear in which sources.

    Three disjoint populations are reported rather than silently intersected:
      devices only            - registered but produced no readings and no bill
      telemetry only          - produced readings but is not in the register
      billing only            - billed but is not in the register
    Telemetry/billing-only devices are the classic cause of a count mismatch.
    """
    devices = set(registry)
    tel_devices = {key for key, _ in telemetry}
    bil_devices = {key for key, _ in billing}
    telemetry_only = sorted(tel_devices - devices)
    billing_only = sorted(bil_devices - devices)
    devices_only = sorted(devices - tel_devices - bil_devices)
    for key in telemetry_only:
        anomalies.add(R_CROSS_TELEMETRY_ONLY, "unknown_entity", "cross", key,
                      "device_id", key, "",
                      "readings exist but the device is absent from the register")
    for key in billing_only:
        anomalies.add(R_CROSS_BILLING_ONLY, "unknown_entity", "cross", key,
                      "device_id", key, "",
                      "billed but absent from the register (revenue attributable to "
                      "an unregistered device)")
    for key in devices_only:
        anomalies.add(R_CROSS_DEVICE_ONLY, "unknown_entity", "cross", key,
                      "device_id", key, "",
                      "registered device with no readings and no billing line in the window")
    return {"devices_only": devices_only, "telemetry_only": telemetry_only,
            "billing_only": billing_only,
            "matched_all_three": sorted(devices & tel_devices & bil_devices)}


def reconcile(devices, telemetry, billing, window=None):
    """Reconcile three record sets (lists of dicts) -> full result dict.

    Pure in-memory: no file access, so it is directly unit-testable.
    """
    anomalies = Anomalies()
    window = window or (WINDOW_FIRST_DAY, WINDOW_LAST_DAY)
    period_window = (window[0][:7], window[1][:7])
    registry, dev_stats = normalize_devices(devices, anomalies, window[1])
    readings, tel_stats = normalize_telemetry(telemetry, anomalies, window)
    lines, bil_stats = normalize_billing(billing, anomalies, period_window)
    coverage = reconcile_entity_presence(registry, readings, lines, anomalies)

    known = set(registry)
    counted = [key for key, rec in registry.items()
               if rec["status"] == _ACTIVE and not rec["excluded"]]
    raw_rows_active = sum(1 for r in devices
                          if canonical_status((r or {}).get("status")) == _ACTIVE)
    # Gap decomposition (DECISIONS 10.4): raw active ROWS vs canonical active
    # DEVICES has two independent causes, reported separately so the gap always
    # adds up: duplicate rows collapsing, and whole devices being disqualified.
    active_rows_by_key = Counter()
    for r in devices:
        k = canonical_device_id((r or {}).get("device_id"))
        if k and canonical_status((r or {}).get("status")) == _ACTIVE:
            active_rows_by_key[k] += 1
    counted_keys = set(counted)
    dup_active_rows = sum(c - 1 for k, c in active_rows_by_key.items()
                          if k in counted_keys and c > 1)
    disqualified_active_rows = sum(c for k, c in active_rows_by_key.items()
                                   if k not in counted_keys)
    # Rows that looked active but carried no usable id at all: never attributed
    # to a device, so they belong in the decomposition too.
    keyless_active_rows = sum(1 for r in devices
                              if not canonical_device_id((r or {}).get("device_id"))
                              and canonical_status((r or {}).get("status")) == _ACTIVE)

    energy_by_device, billed_by_device, credits_by_device = {}, {}, {}
    for (key, _ts), value in readings.items():
        if key in known:
            energy_by_device[key] = energy_by_device.get(key, 0.0) + value
    for (key, _period), line in lines.items():
        if key in known:
            billed_by_device[key] = billed_by_device.get(key, 0.0) + line["billed_kwh"]
            credits_by_device[key] = credits_by_device.get(key, 0) + int(line["credit"])

    capacity_total = sum(registry[k]["capacity_kw"] or 0.0 for k in counted
                        if registry[k]["capacity_valid"])
    anomalies.rows.sort(key=lambda r: (r["source"], r["rule"], r["record_key"]))
    summary = {
        "device_rows_in": dev_stats["rows"],
        "device_ids_unique": len(set(registry)),
        "device_rows_appearing_active": raw_rows_active,
        "duplicate_active_rows_collapsed": dup_active_rows,
        "devices_canonical_active": len(counted),
        "devices_excluded_total": sum(1 for r in registry.values() if r["excluded"]),
        "devices_capacity_valid_of_active": dev_stats.get("active_with_valid_capacity", 0),
        "telemetry_rows_in": tel_stats["rows"],
        "telemetry_readings_kept": tel_stats["readings"],
        "telemetry_deduped": tel_stats["deduped"],
        "telemetry_cadence_violations": tel_stats["cadence_violations"],
        "billing_rows_in": bil_stats["rows"],
        "billing_lines_kept": bil_stats["lines"],
        "billing_deduped": bil_stats["deduped"],
        "energy_kwh_total": round(sum(energy_by_device.get(k, 0.0) for k in counted), 6),
        "billed_kwh_total": round(sum(billed_by_device.get(k, 0.0) for k in counted), 6),
        "unbilled_active_devices": sorted(k for k in counted if k not in billed_by_device),
        "capacity_kw_total_active": round(capacity_total, 6),
        "window": {"first_day": window[0], "last_day": window[1]},
    }
    gap = {
        "raw_rows_active_vs_canonical": raw_rows_active - len(counted),
        "duplicate_rows_collapsed": dup_active_rows,
        "disqualified_active_rows": disqualified_active_rows,
        "keyless_active_rows": keyless_active_rows,
        "explained_by": anomalies.counts_by("rule"),
        "devices_only": coverage["devices_only"],
        "telemetry_only": coverage["telemetry_only"],
        "billing_only": coverage["billing_only"],
        "unbilled_active_devices": summary["unbilled_active_devices"],
        "energy_vs_billed_kwh": round(sum(energy_by_device.get(k, 0.0) for k in counted)
                                      - sum(billed_by_device.get(k, 0.0) for k in counted), 6),
    }
    return {"summary": summary, "gap": gap, "registry": registry,
            "readings": readings, "billing": lines, "coverage": coverage,
            "energy_by_device": energy_by_device,
            "billed_by_device": billed_by_device,
            "credits_by_device": credits_by_device,
            "anomalies": anomalies}


def write_report(result, path, anomalies_path=None):
    """Write the machine-readable report (JSON) plus optional flat CSV log."""
    payload = {"summary": result["summary"], "gap": result["gap"],
               "anomaly_counts": result["anomalies"].counts_by("rule"),
               "anomalies": result["anomalies"].rows}
    Path(path).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n",
                          encoding="utf-8")
    if anomalies_path:
        with open(anomalies_path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=ANOMALY_COLUMNS)
            writer.writeheader()
            writer.writerows(result["anomalies"].rows)


def _default_output(path):
    path = Path(path)
    return path.with_name(path.stem + ".reconciled.csv")


def reconcile_csvs(devices_path, telemetry_path, billing_path):
    """File-level wrapper: read the three CSVs, return (result, header dicts)."""
    dev_hdr, dev_rows = _read_csv(devices_path)
    tel_hdr, tel_rows = _read_csv(telemetry_path)
    bil_hdr, bil_rows = _read_csv(billing_path)
    result = reconcile(dev_rows, tel_rows, bil_rows)
    result["headers"] = {"devices": dev_hdr, "telemetry": tel_hdr, "billing": bil_hdr}
    return result


def _emit_reconciled(devices_path, telemetry_path, billing_path, result, out_path):
    """Devices + Q1 2024 energy + Q1 2024 billed energy, one row per canonical device."""
    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["device_id", "site_id", "capacity_kw", "commissioned_date",
                         "status", "energy_kwh", "billed_kwh", "credit_count",
                         "in_devices", "in_telemetry", "in_billing",
                         "canonical", "exclusion_reasons"])
        rows = []
        for key in sorted(set(result["registry"]) | set(result["coverage"]["telemetry_only"])
                          | set(result["coverage"]["billing_only"])):
            rec = result["registry"].get(key)
            rows.append([
                key, rec["site_id"] if rec else "",
                _fmt_num(rec["capacity_kw"]) if rec and rec["capacity_valid"] else "",
                rec["commissioned_date"] if rec else "",
                rec["status"] if rec else "",
                _fmt_num(result["energy_by_device"].get(key, 0.0)),
                _fmt_num(result["billed_by_device"].get(key, 0.0)),
                result["credits_by_device"].get(key, 0),
                "yes" if rec else "no",
                "yes" if key in {k for k, _ in result["readings"]} else "no",
                "yes" if key in {k for k, _ in result["billing"]} else "no",
                "no" if (rec and rec["excluded"]) else "yes",
                "|".join(sorted(rec["rules"])) if rec and rec["excluded"] else "",
            ])
        writer.writerows(rows)
    return out_path


def main(argv=None):
    """CLI. Defaults: data/devices.csv data/telemetry_q1.csv data/billing_raw.csv."""
    parser = argparse.ArgumentParser(
        prog="reconcile.py",
        description="Reconcile the device register against telemetry and billing "
                    "extracts, and explain every count discrepancy.")
    parser.add_argument("devices", nargs="?", default="data/devices.csv")
    parser.add_argument("telemetry", nargs="?", default="data/telemetry_q1.csv")
    parser.add_argument("billing", nargs="?", default="data/billing_raw.csv")
    parser.add_argument("--out", default=None,
                        help="reconciled CSV to write (default: <devices stem>.reconciled.csv)")
    parser.add_argument("--report", default="reconciliation_report.json",
                        help="machine-readable JSON report path")
    parser.add_argument("--anomalies-csv", default="reconciliation_anomalies.csv",
                        help="flat anomaly log path ('' disables)")
    parser.add_argument("--quiet", action="store_true", help="suppress the console summary")
    args = parser.parse_args(argv)

    missing = [p for p in (args.devices, args.telemetry, args.billing) if not Path(p).is_file()]
    if missing:
        parser.error("input file(s) not found: %s" % ", ".join(missing))

    result = reconcile_csvs(args.devices, args.telemetry, args.billing)
    out_path = args.out or _default_output(args.devices)
    _emit_reconciled(args.devices, args.telemetry, args.billing, result, out_path)
    write_report(result, args.report, args.anomalies_csv or None)

    if not args.quiet:
        s, g = result["summary"], result["gap"]
        print("reconciled: %s | report: %s | anomalies: %s"
              % (out_path, args.report, args.anomalies_csv))
        print("active devices counted: %d  (raw rows that looked active: %d)"
              % (s["devices_canonical_active"], s["device_rows_appearing_active"]))
        print("gap explained: %d device(s) removed from the active count" % g["raw_rows_active_vs_canonical"])
        for key, count in sorted(g["explained_by"].items()):
            if key.startswith(("DEVICE", "KEY")):
                print("  %-28s %d" % (key, count))
        print("telemetry readings kept %d/%d, deduped %d, cadence violations %d"
              % (s["telemetry_readings_kept"], s["telemetry_rows_in"],
                 s["telemetry_deduped"], s["telemetry_cadence_violations"]))
        print("billing lines kept %d/%d (deduped %d)"
              % (s["billing_lines_kept"], s["billing_rows_in"], s["billing_deduped"]))
        print("energy %.1f kWh vs billed %.1f kWh (delta %.1f)"
              % (s["energy_kwh_total"], s["billed_kwh_total"], g["energy_vs_billed_kwh"]))
        print("unknown-entity devices - telemetry only: %s | billing only: %s | register only: %d"
              % (",".join(g["telemetry_only"]) or "-",
                 ",".join(g["billing_only"]) or "-", len(g["devices_only"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
