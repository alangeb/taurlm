"""Compression steps package.

Focused modules:
- framework: CompressionStep, CompressionContext, _compress_wrapper
- utils: byte calc, boundary detection, tool validation, constants
- llm: LLM invocation helper for compression calls
- Algorithm implementations: prune_images, oversized_tool_redaction, drop_reasoning,
  tool_pruning, redact_blocks, full_reset, conversation_summary,
  blind_truncate
"""
