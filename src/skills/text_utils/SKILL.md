---
name: text-utils
description: 'Text processing utilities: count, search, replace, extract URLs. Use when counting or transforming text. Keywords: text, count, search, replace, urls, extract, summarize, words.'
category: data
keywords: 'text, count, search, replace, urls, extract, summarize, words'
---

# text-utils

Text processing utilities (count, search, replace, extract URLs).


## Usage

```python
from skills.text_utils import run as text_utils

# Count text stats
text_utils(action="count", text="Hello world")

# Search for pattern
text_utils(action="search", text="...", pattern="\\w+")

# Replace text
text_utils(action="replace", text="...", old="foo", new="bar")

# Extract URLs
text_utils(action="extract_urls", text="Visit https://example.com")

# Summarize
text_utils(action="summarize", text="...", max_lines=5)
```

> **Import footgun:** loading this skill only PRINTS the doc (rlm/skills.py:_load_skill_into_namespace_inner) — nothing is auto-imported. Call the functions only after `from skills.<pkg> import ...` (verified: `PYTHONPATH=src`).

## Related Skills
- `data-processing` — structured JSON/CSV, not free text
- `code-search` — find patterns across files, not inside one string
- `file-ops` — read the file before you transform it
