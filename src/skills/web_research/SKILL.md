---
name: web-research
description: 'Fetch web pages and search DuckDuckGo using the Python stdlib. Use when fetching URLs or web search. Keywords: web, fetch, url, search, duckduckgo, http, html, scrape, research.'
category: system
keywords: 'web, fetch, url, search, duckduckgo, http, html, scrape, research'
---

# web-research

Fetch web pages and search DuckDuckGo using Python stdlib.


## Usage

```python
from skills.web_research import run as web_research

# Fetch a URL and get text content
web_research(action="fetch_url", url="https://example.com")

# Search DuckDuckGo
web_research(action="search", query="Python programming")
```

> **Import footgun:** loading this skill only PRINTS the doc (rlm/skills.py:_load_skill_into_namespace_inner) — nothing is auto-imported. Call the functions only after `from skills.<pkg> import ...` (verified: `PYTHONPATH=src`).

Notes: stdlib-only (urllib). `search` scrapes `html.duckduckgo.com/html/?q=` — brittle + rate-limited; for serious research use `requests` against real APIs. Returns dicts with `success` flag.

## Related Skills
- `shell` — curl/requests via subprocess when urllib is not enough
- `wiki` — persist findings so they outlive the session
- `spawn` — delegate multi-page research to keep your context lean
