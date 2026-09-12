"""Step 1: Image Pruning — replace image content blocks with text placeholders.

Scan right-to-left within 50% boundary. For each user message with image blocks,
replace all but the last image_url block with a text placeholder.
Images dominate context size when present.
"""

from __future__ import annotations

import json

from agent_context_compress.steps.framework import CompressionContext, make_compress_wrapper, _verbose


__all__ = ["compress_prune_images"]

def _compress_prune_images_impl(ctx: CompressionContext, **kwargs) -> tuple[list[dict], str]:
    """Core image pruning algorithm — replace all but last image per user message."""
    if not ctx.context:
        return ctx.context, "NO_REDUCTION"

    _, boundary_idx = ctx.split_point()

    # Skip the last message — it may be the in-progress user turn
    pruned_any = False
    last_idx = len(ctx.context) - 1
    for i in range(min(boundary_idx, last_idx - 1), -1, -1):
        msg = ctx.context[i]
        if msg.get("role") != "user":
            continue

        content = msg.get("content")
        if not isinstance(content, list):
            continue

        image_indices = [
            j for j, p in enumerate(content) if isinstance(p, dict) and p.get("type") == "image_url"
        ]
        if len(image_indices) <= 1:
            continue  # Only one image, keep it

        # Keep the LAST image, replace all others
        indices_to_remove = set(image_indices[:-1])

        new_content = []
        for j, part in enumerate(content):
            if j in indices_to_remove:
                new_content.append({
                    "type": "text",
                    "text": "[IMAGE: removed by compression]",
                })
            else:
                new_content.append(part)

        old_bytes = len(json.dumps(msg))
        ctx.context[i] = {**msg, "content": new_content}
        pruned_any = True
        new_bytes = len(json.dumps(ctx.context[i]))
        savings = old_bytes - new_bytes

        action_desc = f"pruned {len(indices_to_remove)} image(s) from user msg {i}: {old_bytes}B -> {new_bytes}B"
        ctx.add_action(action_desc, action_type="prune_images")

        if ctx.verbose:
            _verbose(f"  :: IMAGE_PRUNED msg #{i}: SAVED {savings:,} bytes")

        if ctx.at_target():
            break

    if ctx.at_target():
        status = "ACHIEVED"
    elif pruned_any:
        status = "REDUCED"
    else:
        status = "NO_REDUCTION"
    return ctx.context, status


compress_prune_images = make_compress_wrapper("PRUNE_IMAGES", _compress_prune_images_impl)
