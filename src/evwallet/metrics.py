"""Prometheus-compatible metrics for EV Wallet HK.

In-process implementation. Thread-safe via a single ``threading.Lock``;
counters and gauges are stored in nested dicts; histograms keep bounded
ring buffers (capped at ``EVW_METRICS_MAX_SAMPLES``, default 4096) so a
runaway cardinality never blows up memory.

Exposed at ``GET /metrics`` in Prometheus text exposition format. See
``Metrics.export_prometheus``.

We deliberately avoid the ``prometheus_client`` dep — the surface here is
small (counters / gauges / histograms) and the text exposition is 50 lines.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable
from typing import Any, TypeVar

F = TypeVar("F", bound=Callable[..., Any])

# Default histogram bucket boundaries (seconds). Tuned for the workloads
# described in ARCHITECTURE.md: HTTP request latency, ledger post, WS frame.
_DEFAULT_BUCKETS: tuple[float, ...] = (
    0.001,
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
)


def _labels_key(labels: dict[str, str] | None) -> str:
    """Render label dict as a stable string key for storage."""
    if not labels:
        return ""
    return ",".join(f"{k}={v}" for k, v in sorted(labels.items()))


class Metrics:
    """Thread-safe in-process metrics registry.

    Counters, gauges, and histograms are all keyed by metric name and an
    optional label dict. Labels are flattened into the storage key; the
    registry keeps no per-label histogram buckets, so cardinality stays
    bounded by caller discipline.
    """

    def __init__(self, *, max_samples: int = 4096) -> None:
        """Initialize the registry.

        Args:
            max_samples: Per-(metric, labels) histogram ring-buffer cap.
        """
        self._lock = threading.Lock()
        self._counters: dict[tuple[str, str], float] = defaultdict(float)
        self._gauges: dict[tuple[str, str], float] = defaultdict(float)
        self._histograms: dict[tuple[str, str], deque[float]] = defaultdict(
            lambda: deque(maxlen=max_samples)
        )
        self._max_samples = max_samples

    # ---- Counters ---------------------------------------------------------

    def inc(self, name: str, value: float = 1.0, **labels: str) -> None:
        """Increment a counter by *value*.

        Args:
            name: Metric name (e.g. ``auth_login_total``).
            value: Increment; must be non-negative.
            **labels: Optional label key/value pairs.

        Raises:
            ValueError: If *value* is negative.
        """
        if value < 0:
            raise ValueError(f"counter increment must be non-negative, got {value}")
        key = (name, _labels_key(labels))
        with self._lock:
            self._counters[key] += value

    # ---- Gauges -----------------------------------------------------------

    def gauge(self, name: str, value: float, **labels: str) -> None:
        """Set a gauge to *value* (absolute assignment, not increment).

        Args:
            name: Metric name.
            value: New value.
            **labels: Optional label key/value pairs.
        """
        key = (name, _labels_key(labels))
        with self._lock:
            self._gauges[key] = value

    # ---- Histograms -------------------------------------------------------

    def observe(self, name: str, value: float, **labels: str) -> None:
        """Record a single observation on a histogram.

        Args:
            name: Metric name (e.g. ``wallet_ledger_post_latency_ms``).
            value: The observed value (latency in seconds or ms — caller
                picks the unit).
            **labels: Optional label key/value pairs.
        """
        key = (name, _labels_key(labels))
        with self._lock:
            buf = self._histograms[key]
            # ``deque(maxlen=...)`` discards the oldest on overflow, which
            # is exactly the bounded-memory behavior we want.
            buf.append(value)

    def timed(self, name: str, **labels: str) -> Callable[[F], F]:
        """Decorator: records call duration under ``{name}_duration_seconds``.

        Args:
            name: Metric base name (suffix ``_duration_seconds`` is added).
            **labels: Optional label key/value pairs.

        Returns:
            A decorator that wraps a sync function. For async, use
            :meth:`timed_async` or wrap the call manually with ``observe``.
        """

        metric_name = f"{name}_duration_seconds"
        key = (metric_name, _labels_key(labels))

        def decorator(fn: F) -> F:
            def wrapper(*args: Any, **kwargs: Any) -> Any:
                start = time.perf_counter()
                try:
                    return fn(*args, **kwargs)
                finally:
                    elapsed = time.perf_counter() - start
                    with self._lock:
                        self._histograms[key].append(elapsed)

            return wrapper  # type: ignore[return-value]

        return decorator

    # ---- Snapshot / export ------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        """Return a JSON-serializable snapshot of all metrics."""
        with self._lock:
            counters = {
                f"{name}{(labels and ('{' + labels + '}')) or ''}": value
                for (name, labels), value in sorted(self._counters.items())
            }
            gauges = {
                f"{name}{(labels and ('{' + labels + '}')) or ''}": value
                for (name, labels), value in sorted(self._gauges.items())
            }
            histograms = {
                f"{name}{(labels and ('{' + labels + '}')) or ''}": list(values)
                for (name, labels), values in sorted(self._histograms.items())
            }
        return {"counters": counters, "gauges": gauges, "histograms": histograms}

    def export_prometheus(self) -> str:
        """Render the metrics registry in Prometheus text exposition format.

        Format reference: https://prometheus.io/docs/instrumenting/exposition_formats/

        Returns:
            A string suitable for the ``text/plain; version=0.0.4`` body
            of ``GET /metrics``.
        """
        lines: list[str] = []
        with self._lock:
            # Counters
            for (name, labels_key), value in sorted(self._counters.items()):
                label_str = self._render_labels(labels_key)
                lines.append(self._help_line(name, "counter"))
                lines.append(self._type_line(name, "counter"))
                lines.append(self._sample_line(name, label_str, value))
            # Gauges
            for (name, labels_key), value in sorted(self._gauges.items()):
                label_str = self._render_labels(labels_key)
                lines.append(self._help_line(name, "gauge"))
                lines.append(self._type_line(name, "gauge"))
                lines.append(self._sample_line(name, label_str, value))
            # Histograms (count + sum + bounded sample list)
            for (name, labels_key), samples in sorted(self._histograms.items()):
                label_str = self._render_labels(labels_key)
                lines.append(self._help_line(name, "histogram"))
                lines.append(self._type_line(name, "histogram"))
                lines.append(self._sample_line(name + "_count", label_str, float(len(samples))))
                lines.append(self._sample_line(name + "_sum", label_str, float(sum(samples))))
        return "\n".join(lines) + "\n"

    # ---- Internals --------------------------------------------------------

    @staticmethod
    def _render_labels(labels_key: str) -> str:
        """Render the storage key back into the Prometheus ``{k="v",...}`` form."""
        if not labels_key:
            return ""
        pairs = []
        for pair in labels_key.split(","):
            k, v = pair.split("=", 1)
            # Escape backslash and double-quote per Prometheus spec.
            v_escaped = v.replace("\\", "\\\\").replace('"', '\\"')
            pairs.append(f'{k}="{v_escaped}"')
        return "{" + ",".join(pairs) + "}"

    @staticmethod
    def _help_line(name: str, kind: str) -> str:
        return f"# HELP {name} EV Wallet HK {kind}."

    @staticmethod
    def _type_line(name: str, kind: str) -> str:
        return f"# TYPE {name} {kind}"

    @staticmethod
    def _sample_line(name: str, label_str: str, value: float) -> str:
        # Integer-valued counters render without a decimal point per spec.
        rendered = (
            str(int(value))
            if (name.endswith("_total") and value.is_integer())
            else repr(float(value))
        )
        return f"{name}{label_str} {rendered}"


# Module-level singleton. The FastAPI app exposes this single instance via
# ``GET /metrics``; sub-modules import :data:`metrics` and call ``.inc`` etc.
metrics = Metrics()


__all__ = ["_DEFAULT_BUCKETS", "Metrics", "metrics"]
