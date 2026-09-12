"""RLM-native Wiki — persistent knowledge store backed by markdown + git.

Compatible with the tau wiki format: stores knowledge in markdown at
$HOME/.local/tau/wiki/ with topic folders, INDEX.md, log.md, git backing.

Usage:
    import wiki
    wiki.status()
    wiki.search("query")
    wiki.add("topic", "content", entry_type="decision")
    wiki.retrieve("topic")            # capped view (default 4000 chars)
    wiki.retrieve_full("topic")       # uncapped view
    wiki.set_cut_marker("MY-SENTINEL")  # loud warning if retrieve would cut it
    wiki.idx()
    wiki.log("note")
"""

from __future__ import annotations

import fcntl
import os
import subprocess
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


class Wiki:
    """Persistent knowledge store backed by markdown files and git."""

    # Default retrieve() window; overridable per call via retrieve(max_chars=...).
    RETRIEVE_MAX_CHARS = 4000

    def __init__(self, path: str | None = None) -> None:
        if path:
            self._path = Path(path).expanduser().resolve()
        else:
            env = os.environ.get("TAU_WIKI_DIR")
            if env:
                self._path = Path(env).expanduser().resolve()
            else:
                self._path = Path.home() / ".local" / "tau" / "wiki"
        self._cut_marker = ""

    @staticmethod
    def _safe_topic(topic: str) -> str:
        """Validate topic is a safe directory name (no path traversal)."""
        if not topic or "/" in topic or chr(92) in topic or ".." in topic:
            raise ValueError(f"Invalid wiki topic: {topic!r} (must be a simple name, no paths)")
        if topic.startswith("-"):
            raise ValueError(f"Invalid wiki topic: {topic!r} (must not start with -)")
        return topic

    def _atomic_write(self, path: Path, text: str) -> None:
        """B2: write via tmp + os.replace so readers never see a torn file."""
        tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}.{os.getuid()}")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except BaseException:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            raise

    @contextmanager
    def _lock(self):
        """B2: advisory flock around read-modify-write so concurrent agents
        don't drop entries."""
        # Lock lives OUTSIDE the wiki dir so it never lands in the wiki git repo.
        lock_path = self._path.parent / ".wiki.lock"
        with open(lock_path, "a") as lf:
            fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lf.fileno(), fcntl.LOCK_UN)

    def get_path(self) -> str:
        """Return the resolved wiki directory path."""
        return str(self._path)

    def status(self) -> str:
        """Return wiki status: path, exists, file count, size, git branch."""
        if not self._path.exists():
            return f"Wiki: {self._path}\n  Status: NOT FOUND (run wiki.add() to create)"
        md_files = list(self._path.rglob("*.md"))
        total_size = sum(f.stat().st_size for f in md_files if f.is_file())
        branch = self._git("rev-parse", "--abbrev-ref", "HEAD")
        branch = branch.strip() if branch else "no-git"
        return (
            f"Wiki: {self._path}\n"
            f"  Files: {len(md_files)} .md\n"
            f"  Size: {total_size} bytes\n"
            f"  Branch: {branch}"
        )

    def search(self, query: str, limit: int = 20) -> str:
        """Search INDEX.md first, then all .md files for query."""
        if not self._path.exists():
            return "Wiki not found."
        results: list[str] = []
        # Search INDEX.md first
        idx = self._path / "INDEX.md"
        if idx.exists():
            for line in idx.read_text(errors="replace").splitlines():
                if query.lower() in line.lower():
                    results.append(f"[INDEX] {line.strip()}")
        # Search all .md files
        for md in sorted(self._path.rglob("*.md")):
            if md == idx:
                continue
            try:
                text = md.read_text(errors="replace")
            except OSError:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if query.lower() in line.lower():
                    rel = md.relative_to(self._path)
                    results.append(f"[{rel}:{i}] {line.strip()}")
                    if len(results) >= limit:
                        return "\n".join(results)
        return "\n".join(results) if results else f"No results for: {query}"

    def add(self, topic: str, content: str, entry_type: str = "session", title: str = "") -> str:
        """Create or append an entry for a topic. Updates INDEX.md and log.md, git commits."""
        topic = self._safe_topic(topic)
        self._path.mkdir(parents=True, exist_ok=True)
        topic_dir = self._path / topic
        topic_dir = topic_dir.resolve()
        if not topic_dir.is_relative_to(self._path.resolve()):
            raise ValueError(f"Invalid topic: {topic!r}")
        topic_dir.mkdir(exist_ok=True)
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        fname = f"{topic}-{date}.md"
        fpath = topic_dir / fname
        now_iso = datetime.now(timezone.utc).isoformat()

        with self._lock():
            if fpath.exists():
                # W1: errors='replace' so a stray non-UTF8 byte in an existing
                # entry doesn't abort the read-modify-write (matches search()).
                self._atomic_write(fpath, fpath.read_text(errors="replace") + f"\n\n## {now_iso}\n\n{content}\n")
            else:
                t = title or topic
                self._atomic_write(
                    fpath,
                    f"---\ntype: {entry_type}\ntitle: {t}\n"
                    f"created: {date}\nupdated: {date}\n---\n\n{content}\n",
                )

            # Update INDEX.md
            idx = self._path / "INDEX.md"
            if not idx.exists():
                self._atomic_write(idx, "# Wiki Index\n\n")
            idx_text = idx.read_text(errors="replace")
            if f"- [{topic}]({topic}/)" not in idx_text:
                self._atomic_write(idx, idx_text.rstrip() + f"\n- [{topic}]({topic}/)\n")

            # Update log.md
            log = self._path / "log.md"
            if not log.exists():
                self._atomic_write(log, "# Wiki Log\n\n")
            self._atomic_write(log, log.read_text(errors="replace") + f"\n## {now_iso}\n\nAdded {entry_type}: {topic}\n")

        self._git("add", "-A")
        self._git("commit", "-m", f"wiki: add {topic} ({entry_type})")
        return f"Added to wiki: {topic}/{fname}"

    def set_cut_marker(self, marker: str | None) -> None:
        """Register a sentinel string that MUST fit inside the retrieve() window.

        Living-doc pattern: the doc's contract ends with a unique token; if a
        truncation would cut it off, retrieve() returns LOUD WARNING lines
        instead of silently returning a cut-off contract. Empty/None disables.
        """
        self._cut_marker = marker or ""

    def retrieve(self, topic: str, max_chars: int = 0) -> str:
        """Return latest .md content for a topic (default cap: RETRIEVE_MAX_CHARS).

        max_chars <= 0 uses the class default. If a cut marker is registered
        (see set_cut_marker) and truncation would cut it out, the result is
        prefixed with a loud WARNING instead of silently truncating.
        """
        topic = self._safe_topic(topic)
        limit = max_chars if max_chars > 0 else self.RETRIEVE_MAX_CHARS
        topic_dir = self._path / topic
        if not topic_dir.exists():
            return f"No entries for topic: {topic}"
        md_files = sorted(topic_dir.glob("*.md"))
        if not md_files:
            return f"No entries for topic: {topic}"
        latest = md_files[-1]
        content = latest.read_text(errors="replace")
        header = f"[{latest.name}]\n"
        body_budget = max(limit - len(header), 0)
        if len(content) > body_budget:
            marker = getattr(self, "_cut_marker", "")
            if marker and marker not in content[:body_budget]:
                idx = content.find(marker)
                head = (
                    f"WARNING: wiki.retrieve('{topic}') TRUNCATED at {body_budget} chars "
                    f"but cut marker {marker!r} sits at char {idx} - the returned "
                    f"content is INCOMPLETE and the contract below the cut is missing. "
                    f"Use retrieve_full('{topic}') or pass max_chars>{idx + len(marker)}.\n"
                )
                return head + header + content[:body_budget] + "\n... [truncated]"
            content = content[:body_budget] + "\n... [truncated]"
        return header + content

    def retrieve_full(self, topic: str) -> str:
        """Return the LATEST file for a topic with NO truncation."""
        return self.retrieve(topic, max_chars=2**31 - 1)

    def idx(self) -> str:
        """Return INDEX.md content."""
        idx = self._path / "INDEX.md"
        if not idx.exists():
            return "No INDEX.md found."
        return idx.read_text(errors="replace")

    def log(self, content: str) -> str:
        """Append timestamped entry to log.md, git commit."""
        self._path.mkdir(parents=True, exist_ok=True)
        log = self._path / "log.md"
        now_iso = datetime.now(timezone.utc).isoformat()
        with self._lock():
            if not log.exists():
                self._atomic_write(log, "# Wiki Log\n\n")
            self._atomic_write(log, log.read_text(errors="replace") + f"\n## {now_iso}\n\n{content}\n")
        self._git("add", "-A")
        self._git("commit", "-m", "wiki: log entry")
        return f"Logged: {content[:80]}"

    def _git(self, *args: str) -> str:
        """Run git command in wiki dir. Best-effort, returns stdout or empty string."""
        if not self._path.exists():
            return ""
        try:
            r = subprocess.run(
                ["git"] + list(args),
                cwd=str(self._path),
                capture_output=True, text=True, timeout=10,
                stdin=subprocess.DEVNULL,
            )
            return r.stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            return ""


# Module-level singleton and wrapper functions
_wiki = Wiki()


def get_path() -> str:
    return _wiki.get_path()


def status() -> str:
    return _wiki.status()


def search(query: str, limit: int = 20) -> str:
    return _wiki.search(query, limit)


def add(topic: str, content: str, entry_type: str = "session", title: str = "") -> str:
    return _wiki.add(topic, content, entry_type, title)


def retrieve(topic: str, max_chars: int = 0) -> str:
    return _wiki.retrieve(topic, max_chars)


def retrieve_full(topic: str) -> str:
    return _wiki.retrieve_full(topic)


def set_cut_marker(marker: str) -> str:
    _wiki.set_cut_marker(marker)
    return f"Cut marker set: {marker!r}"


def idx() -> str:
    return _wiki.idx()


def log(content: str) -> str:
    return _wiki.log(content)
