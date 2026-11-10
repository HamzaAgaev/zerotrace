"""Injection points for A/B and a read-only fixture adapter for local verification."""
from __future__ import annotations

import json
from contextlib import AbstractContextManager, nullcontext
from pathlib import Path
from typing import Protocol

from analytics_support import AnalyticsError, ref_key


class AnalyticsView(Protocol):
    def environment(self, environment_id: str) -> dict: ...
    def trace(self, ref: dict) -> dict: ...
    def run(self, run_id: str) -> dict: ...


class AnalyticsSource(Protocol):
    def read(self) -> AbstractContextManager[AnalyticsView]:
        """All reads within this context see the same repository snapshot."""
        ...


class FixtureSource:
    """One immutable input generation; no polling or fallback to newer revisions."""
    def __init__(self, document: dict):
        self.environments = document["environments"]
        self.runs = document.get("runs", {})
        self.traces = {}
        for env_id, env in self.environments.items():
            for trace in env["model"]["traces"]:
                key = ref_key(trace["trace_ref"])
                if key[0] != env_id or key in self.traces:
                    raise AnalyticsError("invalid_dataset", "Foreign or duplicate trace in environment")
                self.traces[key] = trace

        for trace in document.get("historical_traces", []):
            key = ref_key(trace["trace_ref"])
            if key[0] not in self.environments or key in self.traces:
                raise AnalyticsError("invalid_dataset", "Foreign or duplicate historical trace")
            self.traces[key] = trace

    @classmethod
    def from_file(cls, path: Path):
        return cls(json.loads(path.read_text(encoding="utf-8")))

    def read(self):
        return nullcontext(self)

    def environment(self, environment_id):
        if environment_id not in self.environments:
            raise AnalyticsError("unknown_environment", "Environment unavailable", 404)
        return self.environments[environment_id]

    def run(self, run_id):
        if run_id not in self.runs:
            raise AnalyticsError("run_not_found", "Run unavailable", 404)
        return self.runs[run_id]

    def trace(self, ref):
        key = ref_key(ref)
        if key not in self.traces:
            if any(k[:3] == key[:3] for k in self.traces):
                raise AnalyticsError("stale_revision", "Requested trace revision unavailable", 409, ref)
            raise AnalyticsError("trace_not_found", "Requested trace is unavailable", 404, ref)
        return self.traces[key]
