"""RLM Vision Support — Image loading for multimodal LLM calls.

Provides view_image() which reads an image file, base64-encodes it,
and queues it in the agent's _queued_images list. The existing
multimodal pipeline in agent_core.py (_inject_queued_images) flushes
queued images into the conversation context as image_url content blocks
on the next LLM call.

Trust Model
-----------
Images are read from local disk only. No network fetches.
File size is capped at 10 MB to protect context window.

Example
-------
    view_image("~/photos/cat.jpg", "What breed is this cat?")
    # -> "Queued cat.jpg (245KB, image/jpeg). 1 image(s) pending."
"""

from __future__ import annotations

import base64
import logging
import mimetypes
from pathlib import Path
from typing import Any, Callable

__all__ = ["make_view_image"]

# Supported image extensions (lowercase, with dot)
_IMAGE_EXTENSIONS: frozenset[str] = frozenset({
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tiff", ".tif",
})

# Maximum file size: 10 MB
_MAX_IMAGE_BYTES: int = 10 * 1024 * 1024


def make_view_image(agent: Any) -> Callable[..., str]:
    """Create a view_image closure bound to the given agent.

    Args:
        agent: TauErgon instance with _queued_images list attribute.

    Returns:
        A callable view_image(path, description="") -> str
    """

    def view_image(path: str, description: str = "") -> str:
        """Load an image file for the model to see.

        Args:
            path: Path to the image file (supports ~ expansion).
            description: Optional prompt/description for the image.

        Returns:
            Confirmation string, or error message if the file is invalid.
        """
        p = Path(path).expanduser().resolve()

        if not p.exists():
            return f"Error: file not found: {p}"

        if not p.is_file():
            return f"Error: not a file: {p}"

        suffix = p.suffix.lower()
        if suffix not in _IMAGE_EXTENSIONS:
            supported = ", ".join(sorted(_IMAGE_EXTENSIONS))
            return f"Error: unsupported image type '{suffix}'. Supported: {supported}"

        size = p.stat().st_size
        if size > _MAX_IMAGE_BYTES:
            return f"Error: {p.name} is {size // 1024 // 1024}MB (max 10MB)"

        mime = mimetypes.guess_type(p.name)[0] or "image/jpeg"
        data = base64.b64encode(p.read_bytes()).decode("ascii")
        data_uri = f"data:{mime};base64,{data}"

        desc = description or p.name
        agent._queued_images.append((data_uri, mime, desc))

        return f"{p.name} ({size // 1024}KB, {mime})"

    return view_image
