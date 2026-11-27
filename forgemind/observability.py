"""JSON logs and Prometheus-style counters. Never logs bodies, tokens or query strings."""
import json
import logging
import threading
import time
from collections import Counter

FIELDS = ("request_id", "method", "route", "status", "ms")


class JsonFormatter(logging.Formatter):
    def format(self, record):
        out = {"ts": round(record.created, 3), "level": record.levelname,
               "logger": record.name, "message": record.getMessage()}
        out.update({f: getattr(record, f) for f in FIELDS if hasattr(record, f)})
        if record.exc_info:
            out["exception"] = self.formatException(record.exc_info)
        return json.dumps(out)


def configure_logging():
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger("forgemind")
    root.handlers[:] = [handler]
    root.setLevel(logging.INFO)
    root.propagate = False


class Metrics:
    """Per process: scrape each instance separately."""

    def __init__(self):
        self.requests, self.seconds = Counter(), Counter()
        self.lock, self.started = threading.Lock(), time.time()

    def record(self, method, route, status, seconds):
        with self.lock:
            self.requests[(method, route, status)] += 1
            self.seconds[route] += seconds

    def render(self):
        with self.lock:
            lines = ["# TYPE forgemind_requests_total counter"]
            lines += [f'forgemind_requests_total{{method="{m}",route="{r}",status="{s}"}} {n}'
                      for (m, r, s), n in sorted(self.requests.items())]
            lines += ["# TYPE forgemind_request_seconds_total counter"]
            lines += [f'forgemind_request_seconds_total{{route="{r}"}} {v:.6f}'
                      for r, v in sorted(self.seconds.items())]
        lines += ["# TYPE forgemind_uptime_seconds gauge",
                  f"forgemind_uptime_seconds {time.time() - self.started:.0f}"]
        return "\n".join(lines) + "\n"
