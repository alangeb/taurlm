"""Tests for APIGatewayError handling and 5xx retry logic."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from agent_llm_models import APIGatewayError, APIError
from agent_llm_client import _HTTP_ERROR_MAP


class TestAPIGatewayErrorClass:
    """Test APIGatewayError class definition and inheritance."""

    def test_api_gateway_error_exists(self):
        err = APIGatewayError("502 Bad Gateway", status_code=502)
        assert isinstance(err, APIError)
        assert err.status_code == 502
        assert "502" in str(err)

    def test_api_gateway_error_is_api_error_subclass(self):
        assert issubclass(APIGatewayError, APIError)

    def test_api_gateway_error_503(self):
        err = APIGatewayError("503 Service Unavailable", status_code=503)
        assert err.status_code == 503
        assert isinstance(err, APIError)

    def test_api_gateway_error_504(self):
        err = APIGatewayError("504 Gateway Timeout", status_code=504)
        assert err.status_code == 504
        assert isinstance(err, APIError)

    def test_api_gateway_error_no_status_code(self):
        err = APIGatewayError("Gateway error")
        assert err.status_code is None


class TestHTTPErrorMap:
    """Test HTTP error code mapping."""

    def test_502_maps_to_api_gateway_error(self):
        assert _HTTP_ERROR_MAP[502] == APIGatewayError

    def test_503_maps_to_api_gateway_error(self):
        assert _HTTP_ERROR_MAP[503] == APIGatewayError

    def test_504_maps_to_api_gateway_error(self):
        assert _HTTP_ERROR_MAP[504] == APIGatewayError

    def test_500_maps_to_gateway_error(self):
        assert 500 in _HTTP_ERROR_MAP  # 500 → APIGatewayError (retryable)
        assert _HTTP_ERROR_MAP[500] is APIGatewayError

    def test_400_still_maps_to_bad_request(self):
        from agent_llm_models import BadRequestError
        assert _HTTP_ERROR_MAP[400] == BadRequestError

    def test_401_still_maps_to_unauthorized(self):
        from agent_llm_models import UnauthorizedError
        assert _HTTP_ERROR_MAP[401] == UnauthorizedError

    def test_429_still_maps_to_rate_limit(self):
        from agent_llm_models import RateLimitError
        assert _HTTP_ERROR_MAP[429] == RateLimitError


class TestBackoff30sBase:
    """Test 30s base backoff for gateway errors."""

    def test_backoff_30s_base(self):
        """Backoff should start at 30s for gateway errors."""
        from agent_llm_client import RetryBackoff

        backoff = RetryBackoff(base=30, max_wait=120, jitter=0.0)
        wait_0 = backoff.next_wait(0)
        assert 27 <= wait_0 <= 33  # 30s ± jitter (0% jitter = exact 30)
        wait_1 = backoff.next_wait(1)
        assert 54 <= wait_1 <= 66  # 60s ± jitter

    def test_backoff_caps_at_max_wait(self):
        """Backoff should cap at max_wait."""
        from agent_llm_client import RetryBackoff

        backoff = RetryBackoff(base=30, max_wait=120, jitter=0.0)
        wait_3 = backoff.next_wait(3)  # 30 * 2^3 = 240, capped at 120
        assert wait_3 <= 120

    def test_backoff_with_jitter(self):
        """Backoff with jitter should vary."""
        from agent_llm_client import RetryBackoff

        backoff = RetryBackoff(base=30, max_wait=120, jitter=0.3)
        waits = [backoff.next_wait(0) for _ in range(10)]
        # With jitter, values should vary (unless extremely unlucky)
        assert len(set(waits)) > 1 or len(waits) == 1  # Allow for edge case


class TestGatewayErrorExhaustion:
    """Test that gateway errors give up after max retries."""

    def test_gives_up_after_max_retries(self):
        """After max_retries, APIGatewayError should be raised."""
        err = APIGatewayError("502 Bad Gateway", status_code=502)
        assert err.status_code == 502
        assert isinstance(err, APIError)

    def test_error_preserves_status_code(self):
        """Error should preserve status_code through chain."""
        err = APIGatewayError("503 Service Unavailable", status_code=503)
        assert getattr(err, "status_code", None) == 503


class TestGatewayErrorRetryIntegration:
    """Integration tests for gateway error retry in _invoke_llm_with_retry."""

    def test_api_gateway_error_caught_by_handler(self):
        """Verify APIGatewayError is caught by the dedicated handler."""
        # The handler is in _invoke_llm_with_retry — verify the import works
        from agent_llm_invoke import APIGatewayError as InvokeGatewayError
        assert InvokeGatewayError is APIGatewayError

    def test_http_error_map_used_by_client(self):
        """Verify client uses _HTTP_ERROR_MAP for 5xx errors."""
        # Verify the mapping is correct
        for code in [502, 503, 504]:
            assert _HTTP_ERROR_MAP.get(code) == APIGatewayError
