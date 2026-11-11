"""Builds fixtures/product/*.json — real data API responses for the frontend.

Runs the full B pipeline (OTLP ingest -> SQLite -> data_api handlers) over
the OTLP fixtures and records envelopes with a fixed generated_at, so D can
develop against contract-exact responses before the server is wired.

Run: python fixtures/build_product_fixtures.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.data_api.adapter import SpanStoreAdapter, make_graph_builder
from app.data_api.handlers import ApiError, get_trace, list_traces
from app.otlp_api import handle_traces
from contracts import IngestLimits, SourceBinding

ROOT = Path(__file__).resolve().parent
OTLP = ROOT / "otlp"
OUT = ROOT / "product"
GENERATED_AT = "2026-11-11T03:00:00Z"
T0 = 1_763_003_000_000_000_000

SCENARIOS = [
    ("s00_healthy", "shop"),
    ("s01_retry", "sandbox"),
]

BINDINGS = {
    "shop": SourceBinding(source_id="src-shop", project_id="zerotrace-demo", environment_id="shop"),
    "sandbox": SourceBinding(
        source_id="src-sandbox", project_id="zerotrace-demo", environment_id="sandbox"
    ),
}


class StaticRegistry:
    def __init__(self, binding: SourceBinding) -> None:
        self._binding = binding

    def resolve(self, token: str | None) -> SourceBinding | None:
        return self._binding


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        from repository import SpanStore

        store = SpanStore(Path(tmp) / "product.db")
        adapters = {env: SpanStoreAdapter(store) for env in BINDINGS}
        for name, env in SCENARIOS:
            body = (OTLP / f"{name}.json").read_bytes()
            response = handle_traces(
                store,
                StaticRegistry(BINDINGS[env]),
                body,
                {"Authorization": "Bearer tok", "Content-Type": "application/json"},
                IngestLimits(),
                T0,
            )
            if response.status != 200:
                raise SystemExit(f"ingest of {name} failed: {response.status}")

        for env in BINDINGS:
            builder = make_graph_builder(store)
            listing = list_traces(
                adapters[env],
                env,
                graph_builder=builder,
                generated_at=GENERATED_AT,
            )
            if isinstance(listing, ApiError):
                raise SystemExit(f"list_traces({env}) failed: {listing.code}")
            (OUT / f"{env}-trace-list.json").write_text(
                json.dumps(listing, indent=2, ensure_ascii=False) + "\n"
            )
            for item in listing["payload"]["items"]:
                trace_id = item["trace_id"]
                detail = get_trace(
                    adapters[env],
                    builder,
                    env,
                    trace_id,
                    generated_at=GENERATED_AT,
                )
                if isinstance(detail, ApiError):
                    raise SystemExit(f"get_trace({trace_id}) failed: {detail.code}")
                (OUT / f"{env}-trace-{trace_id[:8]}-detail.json").write_text(
                    json.dumps(detail, indent=2, ensure_ascii=False) + "\n"
                )
        store.close()
    print(f"product fixtures written to {OUT}")


if __name__ == "__main__":
    main()
