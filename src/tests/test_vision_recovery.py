"""Tests for vision error recovery in agent_llm_invoke.py."""

from agent_llm_invoke import _is_vision_error, _strip_image_blocks


class TestIsVisionError:
    """Test _isvision_error detection."""

    def test_vllm_at_most_zero_images(self):
        """Match the actual vLLM error message."""
        err = 'BadRequestError: {"error":{"message":"At most 0 image(s) may be provided in one prompt."}}'
        assert _is_vision_error(err) is True

    def test_image_not_supported(self):
        """Match generic 'image not supported' pattern."""
        err = "Error: image input is not supported by this model"
        assert _is_vision_error(err) is True

    def test_vision_not_supported(self):
        """Match 'image' + 'vision' pattern."""
        err = "Error: image vision capabilities are not supported"
        assert _is_vision_error(err) is True

    def test_context_overflow_is_not_vision(self):
        """Context overflow errors should NOT match."""
        err = "Error: context size has been exceeded"
        assert _is_vision_error(err) is False

    def test_generic_bad_request_is_not_vision(self):
        """Generic bad request errors should NOT match."""
        err = "Error: invalid parameter: temperature"
        assert _is_vision_error(err) is False

    def test_empty_string(self):
        assert _is_vision_error("") is False

    def test_image_word_alone_is_not_enough(self):
        """'image' alone without capability keywords should NOT match."""
        err = "Error: could not download image from URL"
        assert _is_vision_error(err) is False


class TestStripImageBlocks:
    """Test _strip_image_blocks helper."""

    def test_no_images_returns_none(self):
        """When there are no images, return None (no change needed)."""
        msgs = [{"role": "user", "content": "hello"}]
        assert _strip_image_blocks(msgs) is None

    def test_mixed_text_and_image_strips_image(self):
        """Strip image_url blocks, keep text blocks."""
        msgs = [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": "data:..."}},
                    {"type": "text", "text": "describe this image"},
                ],
            },
        ]
        result = _strip_image_blocks(msgs)
        assert result is not None
        assert len(result) == 1
        content = result[0]["content"]
        assert len(content) == 1
        assert content[0]["type"] == "text"
        assert content[0]["text"] == "describe this image"

    def test_pure_image_message_replaced_with_placeholder(self):
        """Pure-image messages get a text placeholder."""
        msgs = [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": "data:..."}},
                ],
            },
        ]
        result = _strip_image_blocks(msgs)
        assert result is not None
        assert len(result) == 1
        assert result[0]["content"] == "[image: model does not support vision]"

    def test_multiple_images_all_stripped(self):
        """Multiple image_url blocks are all stripped."""
        msgs = [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": "data:img1"}},
                    {"type": "image_url", "image_url": {"url": "data:img2"}},
                    {"type": "text", "text": "compare"},
                ],
            },
        ]
        result = _strip_image_blocks(msgs)
        assert result is not None
        content = result[0]["content"]
        assert len(content) == 1
        assert content[0]["type"] == "text"

    def test_multiple_messages_only_one_has_images(self):
        """Only messages with images are modified."""
        msgs = [
            {"role": "system", "content": "You are helpful."},
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": "data:..."}},
                    {"type": "text", "text": "what is this?"},
                ],
            },
            {"role": "assistant", "content": "It is a cat."},
        ]
        result = _strip_image_blocks(msgs)
        assert result is not None
        assert len(result) == 3
        # System message unchanged
        assert result[0]["content"] == "You are helpful."
        # User message: image stripped, text kept
        assert result[1]["content"] == [{"type": "text", "text": "what is this?"}]
        # Assistant unchanged
        assert result[2]["content"] == "It is a cat."

    def test_preserves_message_role_and_other_fields(self):
        """Role and other fields are preserved after stripping."""
        msgs = [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": "data:..."}},
                ],
                "name": "some_name",
            },
        ]
        result = _strip_image_blocks(msgs)
        assert result is not None
        assert result[0]["role"] == "user"
        assert result[0]["name"] == "some_name"

    def test_plain_list_input(self):
        """Works with plain list input."""
        msgs = [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": "data:..."}},
                ],
            },
        ]
        result = _strip_image_blocks(msgs)
        assert result is not None

    def test_empty_message_list(self):
        """Empty list returns None."""
        assert _strip_image_blocks([]) is None

    def test_image_only_then_text_message(self):
        """Two messages: first pure image, second text-only."""
        msgs = [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": "data:..."}},
                ],
            },
            {"role": "user", "content": "follow-up question"},
        ]
        result = _strip_image_blocks(msgs)
        assert result is not None
        assert len(result) == 2
        assert result[0]["content"] == "[image: model does not support vision]"
        assert result[1]["content"] == "follow-up question"


class TestStripImageBlocksIntegration:
    """Integration tests mimicking the retry loop behavior."""

    def test_strip_then_retry_pattern(self):
        """Simulate: error detected → strip → retry with stripped messages."""
        # Simulate context with image
        messages = [
            {"role": "system", "content": "You are helpful."},
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": "data:image;base64,abc"}},
                    {"type": "text", "text": "describe this"},
                ],
            },
        ]

        # Simulate vision error detection
        error_str = 'BadRequestError: {"error":{"message":"At most 0 image(s) may be provided in one prompt."}}'
        assert _is_vision_error(error_str)

        # Simulate recovery
        recovered = _strip_image_blocks(messages)
        assert recovered is not None
        assert len(recovered) == 2
        # System unchanged
        assert recovered[0] == {"role": "system", "content": "You are helpful."}
        # Image stripped, text kept
        assert recovered[1]["content"] == [{"type": "text", "text": "describe this"}]

    def test_no_strip_falls_through_to_fatal(self):
        """When no images exist, _strip_image_blocks returns None → fatal."""
        messages = [
            {"role": "user", "content": "no images here"},
        ]
        assert _strip_image_blocks(messages) is None
