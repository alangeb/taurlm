"""web_research: Fetch web pages and search DuckDuckGo using Python stdlib."""

import ipaddress
import re
import socket
import urllib.parse
import urllib.request
from html.parser import HTMLParser


def _ssrf_blocked(url: str) -> str | None:
    """P7-B11-04: best-effort SSRF guard. Returns an error string or None.

    Blocks private/loopback/link-local (incl. 169.254.169.254 cloud
    metadata)/reserved targets. LIMITS: best-effort only — DNS may resolve
    differently at fetch time (rebinding), and urllib follows redirects to
    unchecked hosts; a hardened proxy is the real boundary if that matters.
    """
    host = urllib.parse.urlparse(url).hostname
    if not host:
        return "URL has no host"
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return None  # unresolvable; let the fetch fail naturally
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return f"Blocked: {host} resolves to {ip} (private/internal range)"
    return None


__all__ = [
    'HTMLParser',
    'run'
]

class _SimpleHTMLParser(HTMLParser):
    """Simple HTML to text converter."""

    def __init__(self):
        super().__init__()
        self._text = []
        self._skip = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head"):
            self._skip = True

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head"):
            self._skip = False

    def handle_data(self, data):
        if not self._skip:
            self._text.append(data)

    def get_text(self):
        return "\n".join(self._text).strip()


def run(action="fetch_url", url="", query="", max_length=10000, max_results=5, **kwargs):
    """Execute web research operation.

    Args:
        action: fetch_url, search
        url: URL to fetch (for fetch_url action)
        query: Search query (for search action)
        max_length: Max characters to return (default 10000)
        max_results: Max search results (default 5)

    Returns:
        dict with 'success', 'content' (for fetch), or 'results' (for search)
    """
    if action == "fetch_url":
        if not url:
            return {"success": False, "error": "URL is required for fetch_url action"}
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return {"success": False, "error": f"Blocked URL scheme: {parsed.scheme!r} (only http/https allowed)"}
        blocked = _ssrf_blocked(url)
        if blocked:
            return {"success": False, "error": blocked}
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "TauRLM/1.0"})
            with urllib.request.urlopen(req, timeout=10) as response:
                html = response.read().decode("utf-8", errors="replace")
            parser = _SimpleHTMLParser()
            parser.feed(html)
            text = parser.get_text()
            text = re.sub(r"\s+", " ", text)
            return {"success": True, "content": text[:max_length]}
        except Exception as e:
            return {"success": False, "error": f"Failed to fetch {url}: {e}"}

    elif action == "search":
        if not query:
            return {"success": False, "error": "Query is required for search action"}
        try:
            search_url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(query)}"
            req = urllib.request.Request(search_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as response:
                html = response.read().decode("utf-8", errors="replace")
            results = re.findall(r'<a[^>]*class="result__a"[^>]*>([^<]+)</a>', html)
            return {"success": True, "results": [r.strip() for r in results[:max_results]]}
        except Exception as e:
            return {"success": False, "error": f"Search failed: {e}"}

    else:
        return {"success": False, "error": f"Unknown action: {action}"}
