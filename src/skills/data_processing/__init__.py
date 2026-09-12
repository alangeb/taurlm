"""Data Processing Skill — JSON, CSV, text processing."""

import csv
import json


__all__ = [
    'count_lines',
    'read_csv',
    'read_json',
    'write_csv',
    'write_json'
]

def read_json(path: str) -> dict:
    """Read a JSON file.

    Args:
        path: Path to the JSON file

    Returns:
        Parsed JSON data as dict
    """
    with open(path, 'r') as f:
        return json.load(f)


def write_json(path: str, data: dict) -> str:
    """Write data to a JSON file.

    Args:
        path: Path to the output file
        data: Data to write

    Returns:
        Success message
    """
    with open(path, 'w') as f:
        json.dump(data, f, indent=2)
    return f"Wrote JSON to {path}"


def read_csv(path: str, delimiter: str = ',') -> list:
    """Read a CSV file and return list of dicts.

    Args:
        path: Path to the CSV file
        delimiter: CSV delimiter (default: ',')

    Returns:
        List of dicts (one per row)
    """
    with open(path, 'r') as f:
        reader = csv.DictReader(f, delimiter=delimiter)
        return list(reader)


def write_csv(path: str, data: list, fieldnames: list = None) -> str:
    """Write list of dicts to CSV.

    Args:
        path: Path to the output file
        data: List of dicts to write
        fieldnames: CSV column names (inferred from data if None)

    Returns:
        Success message
    """
    if not data:
        return "No data to write"
    if fieldnames is None:
        fieldnames = list(data[0].keys())
    with open(path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(data)
    return f"Wrote {len(data)} rows to {path}"


def count_lines(path: str) -> int:
    """Count lines in a file.

    Args:
        path: Path to the file

    Returns:
        Number of lines
    """
    with open(path, 'r') as f:
        return sum(1 for _ in f)


# Skill metadata
__skill_name__ = "data_processing"
__skill_description__ = "Data processing: JSON, CSV, text"
__skill_commands__ = ["read_json", "write_json", "read_csv", "write_csv", "count_lines"]
