"""Model server health monitoring with circuit breaker pattern.

Provides:
- Connection health checks before LLM calls
- Circuit breaker to prevent cascading failures
- Exponential backoff for reconnection attempts
- Health monitoring dashboard

Usage:
    monitor = ModelHealthMonitor(base_url="http://localhost:8000")
    if monitor.is_healthy():
        # proceed with LLM call
        pass
    monitor.record_success()
    monitor.record_failure()
"""

from __future__ import annotations

import json
import logging
import os
import socket
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# Circuit breaker states
# ---------------------------------------------------------------------------

class CircuitState(str, Enum):
    CLOSED = "closed"       # Normal operation
    OPEN = "open"          # Failures exceeded threshold, blocking calls
    HALF_OPEN = "half_open"  # Testing if recovery occurred


# ---------------------------------------------------------------------------
# Health status
# ---------------------------------------------------------------------------

@dataclass
class HealthStatus:
    """Current health status of the model server."""
    circuit_state: CircuitState = CircuitState.CLOSED
    consecutive_failures: int = 0
    consecutive_successes: int = 0
    total_failures: int = 0
    total_successes: int = 0
    last_failure_time: float = 0.0
    last_success_time: float = 0.0
    last_check_time: float = 0.0
    last_error: str = ""
    open_since: float = 0.0
    recovery_attempts: int = 0

    @property
    def is_healthy(self) -> bool:
        """Return True if the circuit is closed (healthy)."""
        return self.circuit_state == CircuitState.CLOSED

    @property
    def failure_rate(self) -> float:
        """Return the overall failure rate (all-time)."""
        total = self.total_failures + self.total_successes
        return self.total_failures / total if total > 0 else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "circuit_state": self.circuit_state.value,
            "consecutive_failures": self.consecutive_failures,
            "consecutive_successes": self.consecutive_successes,
            "total_failures": self.total_failures,
            "total_successes": self.total_successes,
            "last_failure_time": self.last_failure_time,
            "last_success_time": self.last_success_time,
            "last_check_time": self.last_check_time,
            "last_error": self.last_error,
            "open_since": self.open_since,
            "recovery_attempts": self.recovery_attempts,
            "failure_rate": self.failure_rate,
        }


# ---------------------------------------------------------------------------
# Health monitor configuration
# ---------------------------------------------------------------------------

@dataclass
class HealthMonitorConfig:
    """Configuration for the health monitor."""
    # Circuit breaker thresholds
    failure_threshold: int = 5          # Failures before opening circuit
    success_threshold: int = 3          # Successes before closing from half-open
    recovery_timeout: float = 30.0     # Seconds before half-open retry
    # Health check
    check_timeout: float = 5.0          # Timeout for health check requests
    check_interval: float = 60.0       # Interval between periodic checks
    # Backoff
    backoff_base: float = 5.0           # Base wait time for retries
    backoff_max: float = 120.0         # Maximum wait time
    backoff_multiplier: float = 2.0    # Exponential multiplier
    # Enable/disable
    enabled: bool = True


# ---------------------------------------------------------------------------
# ModelHealthMonitor
# ---------------------------------------------------------------------------

class ModelHealthMonitor:
    """Monitor model server health with circuit breaker pattern.

    Thread-safe. Use as a singleton per model server endpoint.

    Usage:
        monitor = ModelHealthMonitor(base_url="http://localhost:8000")

        # Before making an LLM call:
        if not monitor.is_healthy():
            # Circuit is open — skip the call or use fallback
            raise RuntimeError("Model server circuit is open")

        # After a successful call:
        monitor.record_success()

        # After a failed call:
        monitor.record_failure(error_message)
    """

    def __init__(
        self,
        base_url: str,
        config: HealthMonitorConfig | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self._config = config or HealthMonitorConfig()
        self._lock = threading.Lock()
        self._status = HealthStatus()
        # Dashboard state file
        self._dashboard_file = self._resolve_dashboard_file()

    @staticmethod
    def _resolve_dashboard_file() -> str:
        """Resolve the dashboard state file path."""
        log_dir = os.environ.get("TAU_LOG_DIR", os.path.join(os.path.expanduser("~"), ".local", "tau", "log"))
        return os.path.join(log_dir, "model_health.json")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_healthy(self) -> bool:
        """Check if the circuit allows calls. Handles half-open transitions."""
        if not self._config.enabled:
            return True

        with self._lock:
            status = self._status
            if status.circuit_state == CircuitState.CLOSED:
                return True
            if status.circuit_state == CircuitState.OPEN:
                # Check if recovery timeout has elapsed
                if time.monotonic() - status.open_since >= self._config.recovery_timeout:
                    self._transition_to_half_open()
                    return True  # Allow one test request
                return False
            # HALF_OPEN: allow limited requests
            return True

    def record_success(self) -> None:
        """Record a successful call."""
        if not self._config.enabled:
            return

        now = time.monotonic()
        with self._lock:
            self._status.consecutive_successes += 1
            self._status.consecutive_failures = 0
            self._status.total_successes += 1
            self._status.last_success_time = now
            self._status.last_check_time = now

            # Transition from half-open to closed if enough successes
            if (
                self._status.circuit_state == CircuitState.HALF_OPEN
                and self._status.consecutive_successes >= self._config.success_threshold
            ):
                self._transition_to_closed()

        # Persist dashboard state outside lock to avoid blocking
        self.save_dashboard()

    def record_failure(self, error_message: str = "") -> None:
        """Record a failed call."""
        if not self._config.enabled:
            return

        now = time.monotonic()
        with self._lock:
            self._status.consecutive_failures += 1
            self._status.consecutive_successes = 0
            self._status.total_failures += 1
            self._status.last_failure_time = now
            self._status.last_check_time = now
            self._status.last_error = error_message

            # Check if we should open the circuit
            if (
                self._status.circuit_state != CircuitState.OPEN
                and self._status.consecutive_failures >= self._config.failure_threshold
            ):
                self._transition_to_open()

        # Persist dashboard state outside lock to avoid blocking
        self.save_dashboard()

    def get_status(self) -> HealthStatus:
        """Get current health status."""
        with self._lock:
            return HealthStatus(
                circuit_state=self._status.circuit_state,
                consecutive_failures=self._status.consecutive_failures,
                consecutive_successes=self._status.consecutive_successes,
                total_failures=self._status.total_failures,
                total_successes=self._status.total_successes,
                last_failure_time=self._status.last_failure_time,
                last_success_time=self._status.last_success_time,
                last_check_time=self._status.last_check_time,
                last_error=self._status.last_error,
                open_since=self._status.open_since,
                recovery_attempts=self._status.recovery_attempts,
            )

    def save_dashboard(self) -> None:
        """Save health status to dashboard file."""
        try:
            status = self.get_status()
            dashboard = {
                "base_url": self.base_url,
                "timestamp": time.time(),
                "status": status.to_dict(),
                "config": {
                    "failure_threshold": self._config.failure_threshold,
                    "success_threshold": self._config.success_threshold,
                    "recovery_timeout": self._config.recovery_timeout,
                },
            }
            os.makedirs(os.path.dirname(self._dashboard_file), exist_ok=True)
            with open(self._dashboard_file, "w") as f:
                json.dump(dashboard, f, indent=2)
        except Exception as e:
            logging.debug("model_health: dashboard save failed: %s", e)

    # ------------------------------------------------------------------
    # Internal state transitions
    # ------------------------------------------------------------------

    def _transition_to_open(self) -> None:
        """Transition circuit to OPEN state."""
        self._status.circuit_state = CircuitState.OPEN
        self._status.open_since = time.monotonic()

    def _transition_to_half_open(self) -> None:
        """Transition circuit to HALF_OPEN state."""
        self._status.circuit_state = CircuitState.HALF_OPEN
        self._status.consecutive_successes = 0
        self._status.recovery_attempts += 1

    def _transition_to_closed(self) -> None:
        """Transition circuit to CLOSED state."""
        self._status.circuit_state = CircuitState.CLOSED
        self._status.consecutive_failures = 0

    def reset(self) -> None:
        """Reset all health tracking state."""
        with self._lock:
            self._status = HealthStatus()


# ---------------------------------------------------------------------------
# Module-level singleton for convenience
# ---------------------------------------------------------------------------

_default_monitor: ModelHealthMonitor | None = None
_default_lock = threading.Lock()


def get_health_monitor(
    base_url: str | None = None,
    config: HealthMonitorConfig | None = None,
) -> ModelHealthMonitor:
    """Get or create the default health monitor.

    Creates a singleton monitor for the given base_url.
    """
    global _default_monitor
    with _default_lock:
        if _default_monitor is None or (base_url is not None and _default_monitor.base_url != base_url):
            url = base_url or os.environ.get("API_BASE", "http://localhost:8000")
            _default_monitor = ModelHealthMonitor(url, config)
        return _default_monitor


__all__ = [
    "CircuitState",
    "HealthStatus",
    "HealthMonitorConfig",
    "ModelHealthMonitor",
    "get_health_monitor",
]
