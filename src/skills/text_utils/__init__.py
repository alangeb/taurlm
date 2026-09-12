"""text_utils: Text processing utilities (count, search, replace, extract URLs)."""
import re
import regex


__all__ = [
    'run'
]

def run(action="count", text="", **kwargs):
    """Process text.

    Args:
        action: count, search, replace, extract_urls, summarize
        text: Input text

    Returns:
        dict with results
    """
    if action == "count":
        return {
            "success": True,
            "chars": len(text),
            "words": len(text.split()),
            "lines": len(text.splitlines()),
            "sentences": len([s for s in re.split(r'[.!?]+', text) if s.strip()]),
        }

    elif action == "search":
        pattern = kwargs.get("pattern", "")
        case_sensitive = kwargs.get("case_sensitive", False)
        flags = 0 if case_sensitive else regex.IGNORECASE
        try:
            matches = regex.findall(pattern, text, flags, timeout=5)
        except TimeoutError:
            # P7-B11-03: this regex build raises the builtin TimeoutError on
            # timeout; regex.TimeoutError doesn't exist (AttributeError in the
            # handler). Builtin TimeoutError also covers OSError-timeout alias.
            return {"success": False, "error": "Regex timed out (possible ReDoS)"}
        return {"success": True, "matches": matches, "count": len(matches)}

    elif action == "replace":
        old = kwargs.get("old", "")
        new = kwargs.get("new", "")
        result = text.replace(old, new)
        return {"success": True, "result": result, "replacements": text.count(old)}

    elif action == "extract_urls":
        urls = re.findall(r'https?://\S+', text)
        return {"success": True, "urls": urls, "count": len(urls)}

    elif action == "summarize":
        lines = text.splitlines()
        max_lines = kwargs.get("max_lines", 10)
        return {"success": True, "summary": "\n".join(lines[:max_lines]), "total_lines": len(lines)}

    return {"success": False, "error": f"Unknown action: {action}"}
