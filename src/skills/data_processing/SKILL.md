---
name: data-processing
description: 'Process structured data: read/write JSON and CSV, parse tables. Use when reading or transforming JSON/CSV. Keywords: json, csv, data, parse, process, read, write, table.'
category: data
keywords: 'json, csv, data, parse, process, read, write, table'
---

# Data Processing Skill

Process structured data: JSON, CSV, and text files.


## Functions

- `read_json(path)` — Read a JSON file and return dict
- `write_json(path, data)` — Write data to a JSON file
- `read_csv(path, delimiter=',')` — Read a CSV file and return list of dicts
- `write_csv(path, data, fieldnames=None)` — Write list of dicts to CSV
- `count_lines(path)` — Count lines in a file

## Example

> **Import footgun:** loading this skill only PRINTS the doc (rlm/skills.py:_load_skill_into_namespace_inner) — nothing is auto-imported. Call the functions only after `from skills.<pkg> import ...` (verified: `PYTHONPATH=src`).


```python
from skills.data_processing import read_json, write_json, read_csv, write_csv, count_lines

# Read JSON
data = read_json("config.json")

# Write JSON
write_json("output.json", {"key": "value"})

# Read CSV
rows = read_csv("data.csv")

# Write CSV
write_csv("output.csv", [{"name": "Alice", "age": 30}])

# Count lines
lines = count_lines("large_file.txt")
```

## Related Skills
- `file-ops` — raw read/write/list/find
- `text-utils` — count/search/replace on text
- `code-analysis` — parse Python structure, not data files
