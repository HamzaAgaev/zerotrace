from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass, field
from pathlib import Path

from contracts import NormalizedSpan, QualityIssue, SourceBinding, TraceRef
from contracts.p2 import AssemblyState
from ingest.normalize import NormalizedBatch

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS traces (
    project_id TEXT NOT NULL,
    environment_id TEXT NOT NULL,
    trace_id TEXT NOT NULL,
    generation INTEGER NOT NULL,
    revision INTEGER NOT NULL,
    first_received_at_ns INTEGER NOT NULL,
    last_received_at_ns INTEGER NOT NULL,
    assembly_state TEXT NOT NULL,
    PRIMARY KEY (project_id, trace_id, generation)
);
CREATE INDEX IF NOT EXISTS idx_traces_env
    ON traces (environment_id, first_received_at_ns DESC, trace_id);
CREATE TABLE IF NOT EXISTS spans (
    project_id TEXT NOT NULL,
    trace_id TEXT NOT NULL,
    generation INTEGER NOT NULL,
    span_id TEXT NOT NULL,
    start_ns INTEGER NOT NULL,
    payload TEXT NOT NULL,
    received_at_ns INTEGER NOT NULL,
    PRIMARY KEY (project_id, trace_id, generation, span_id)
);
"""


@dataclass(frozen=True)
class IngestResult:
    trace_refs: list[TraceRef]
    diagnostics: list[QualityIssue] = field(default_factory=list)


@dataclass(frozen=True)
class TraceMeta:
    trace_ref: TraceRef
    project_id: str
    first_received_at_ns: int
    last_received_at_ns: int
    assembly_state: AssemblyState
    span_count: int


def _canonical_payload(span: NormalizedSpan) -> str:
    return json.dumps(span.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class SpanStore:
    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        if str(self._path) != ":memory:":
            self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False, isolation_level=None)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._migrate()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> SpanStore:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _migrate(self) -> None:
        with self._lock:
            # executescript сам управляет транзакцией; соединение в autocommit-режиме.
            self._conn.executescript(_SCHEMA)
            row = self._conn.execute(
                "SELECT value FROM meta WHERE key = 'schema_version'"
            ).fetchone()
            if row is None:
                self._conn.execute(
                    "INSERT INTO meta (key, value) VALUES ('schema_version', ?)",
                    (str(SCHEMA_VERSION),),
                )
            elif int(row[0]) > SCHEMA_VERSION:
                raise RuntimeError(
                    f"unsupported schema version {row[0]}, expected <= {SCHEMA_VERSION}"
                )

    def ingest(
        self, batch: NormalizedBatch, binding: SourceBinding, received_at_ns: int
    ) -> IngestResult:
        by_trace: dict[str, list[NormalizedSpan]] = {}
        for span in batch.spans:
            by_trace.setdefault(span.trace_id, []).append(span)
        diagnostics: list[QualityIssue] = []
        refs: list[TraceRef] = []
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                for trace_id, spans in by_trace.items():
                    refs.append(
                        self._ingest_trace(binding, trace_id, spans, received_at_ns, diagnostics)
                    )
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
        return IngestResult(trace_refs=refs, diagnostics=diagnostics)

    def _ingest_trace(
        self,
        binding: SourceBinding,
        trace_id: str,
        spans: list[NormalizedSpan],
        received_at_ns: int,
        diagnostics: list[QualityIssue],
    ) -> TraceRef:
        row = self._conn.execute(
            "SELECT generation, revision, last_received_at_ns FROM traces "
            "WHERE project_id = ? AND trace_id = ? ORDER BY generation DESC LIMIT 1",
            (binding.project_id, trace_id),
        ).fetchone()
        inserted = 0
        if row is None:
            generation = 1
            revision = 1
            self._conn.execute(
                "INSERT INTO traces (project_id, environment_id, trace_id, generation, revision, "
                "first_received_at_ns, last_received_at_ns, assembly_state) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'open')",
                (
                    binding.project_id,
                    binding.environment_id,
                    trace_id,
                    generation,
                    revision,
                    received_at_ns,
                    received_at_ns,
                ),
            )
        else:
            generation, revision, last_received_at_ns = int(row[0]), int(row[1]), int(row[2])
        for span in spans:
            payload = _canonical_payload(span)
            existing = self._conn.execute(
                "SELECT payload FROM spans WHERE project_id = ? AND trace_id = ? "
                "AND generation = ? AND span_id = ?",
                (binding.project_id, trace_id, generation, span.span_id),
            ).fetchone()
            if existing is None:
                self._insert_span(binding, trace_id, generation, span, payload, received_at_ns)
                inserted += 1
            elif existing[0] != payload:
                diagnostics.append(
                    QualityIssue(
                        code="conflicting_span",
                        scope="span",
                        span_ids=[span.span_id],
                        details=(
                            f"span {span.span_id} of trace {trace_id} conflicts with the "
                            "previously accepted span; first accepted is kept"
                        ),
                    )
                )
        if row is not None and inserted > 0:
            revision += 1
            self._conn.execute(
                "UPDATE traces SET revision = ?, last_received_at_ns = ? "
                "WHERE project_id = ? AND trace_id = ? AND generation = ?",
                (
                    revision,
                    max(last_received_at_ns, received_at_ns),
                    binding.project_id,
                    trace_id,
                    generation,
                ),
            )
        return TraceRef(
            environment_id=binding.environment_id,
            trace_id=trace_id,
            generation=generation,
            revision=revision,
        )

    def _insert_span(
        self,
        binding: SourceBinding,
        trace_id: str,
        generation: int,
        span: NormalizedSpan,
        payload: str,
        received_at_ns: int,
    ) -> None:
        self._conn.execute(
            "INSERT INTO spans (project_id, trace_id, generation, span_id, start_ns, payload, "
            "received_at_ns) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                binding.project_id,
                trace_id,
                generation,
                span.span_id,
                span.start_ns,
                payload,
                received_at_ns,
            ),
        )

    def _find_trace(self, ref: TraceRef) -> tuple[str, int, int, int, str] | None:
        row = self._conn.execute(
            "SELECT project_id, revision, first_received_at_ns, last_received_at_ns, "
            "assembly_state FROM traces "
            "WHERE environment_id = ? AND trace_id = ? AND generation = ?",
            (ref.environment_id, ref.trace_id, ref.generation),
        ).fetchone()
        if row is None:
            return None
        return (str(row[0]), int(row[1]), int(row[2]), int(row[3]), str(row[4]))

    def get_spans(self, ref: TraceRef) -> list[NormalizedSpan] | None:
        with self._lock:
            found = self._find_trace(ref)
            if found is None or found[1] != ref.revision:
                return None
            project_id = found[0]
            rows = self._conn.execute(
                "SELECT payload FROM spans WHERE project_id = ? AND trace_id = ? "
                "AND generation = ? ORDER BY start_ns, span_id",
                (project_id, ref.trace_id, ref.generation),
            ).fetchall()
        return [NormalizedSpan.from_dict(json.loads(payload)) for (payload,) in rows]

    def get_trace_meta(self, ref: TraceRef) -> TraceMeta | None:
        with self._lock:
            found = self._find_trace(ref)
            if found is None:
                return None
            project_id, revision, first_received, last_received, assembly_state = found
            span_count = int(
                self._conn.execute(
                    "SELECT COUNT(*) FROM spans WHERE project_id = ? AND trace_id = ? "
                    "AND generation = ?",
                    (project_id, ref.trace_id, ref.generation),
                ).fetchone()[0]
            )
        return TraceMeta(
            trace_ref=TraceRef(
                environment_id=ref.environment_id,
                trace_id=ref.trace_id,
                generation=ref.generation,
                revision=revision,
            ),
            project_id=project_id,
            first_received_at_ns=first_received,
            last_received_at_ns=last_received,
            assembly_state=assembly_state,  # type: ignore[arg-type]
            span_count=span_count,
        )

    def resolve_trace(
        self,
        environment_id: str,
        trace_id: str,
        generation: int | None = None,
        revision: int | None = None,
    ) -> TraceMeta | None:
        with self._lock:
            if generation is None:
                row = self._conn.execute(
                    "SELECT project_id, generation, revision, first_received_at_ns, "
                    "last_received_at_ns, assembly_state FROM traces "
                    "WHERE environment_id = ? AND trace_id = ? "
                    "ORDER BY generation DESC LIMIT 1",
                    (environment_id, trace_id),
                ).fetchone()
            else:
                row = self._conn.execute(
                    "SELECT project_id, generation, revision, first_received_at_ns, "
                    "last_received_at_ns, assembly_state FROM traces "
                    "WHERE environment_id = ? AND trace_id = ? AND generation = ?",
                    (environment_id, trace_id, generation),
                ).fetchone()
            if row is None:
                return None
            project_id = str(row[0])
            resolved_generation = int(row[1])
            current_revision = int(row[2])
            if revision is not None and revision != current_revision:
                return None
            ref = TraceRef(
                environment_id=environment_id,
                trace_id=trace_id,
                generation=resolved_generation,
                revision=current_revision,
            )
            span_count = int(
                self._conn.execute(
                    "SELECT COUNT(*) FROM spans WHERE project_id = ? AND trace_id = ? "
                    "AND generation = ?",
                    (project_id, trace_id, resolved_generation),
                ).fetchone()[0]
            )
        return TraceMeta(
            trace_ref=ref,
            project_id=project_id,
            first_received_at_ns=int(row[3]),
            last_received_at_ns=int(row[4]),
            assembly_state=str(row[5]),  # type: ignore[arg-type]
            span_count=span_count,
        )

    def list_trace_metas(self, environment_id: str) -> list[TraceMeta]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT project_id, trace_id, generation, revision, first_received_at_ns, "
                "last_received_at_ns, assembly_state FROM traces WHERE environment_id = ? "
                "ORDER BY first_received_at_ns DESC, trace_id",
                (environment_id,),
            ).fetchall()
            counts = self._conn.execute(
                "SELECT project_id, trace_id, generation, COUNT(*) FROM spans "
                "GROUP BY project_id, trace_id, generation"
            ).fetchall()
        span_counts = {(p, t, g): int(c) for p, t, g, c in counts}
        metas: list[TraceMeta] = []
        for (
            project_id,
            trace_id,
            generation,
            revision,
            first_received,
            last_received,
            state,
        ) in rows:
            metas.append(
                TraceMeta(
                    trace_ref=TraceRef(
                        environment_id=environment_id,
                        trace_id=str(trace_id),
                        generation=int(generation),
                        revision=int(revision),
                    ),
                    project_id=str(project_id),
                    first_received_at_ns=int(first_received),
                    last_received_at_ns=int(last_received),
                    assembly_state=str(state),  # type: ignore[arg-type]
                    span_count=span_counts.get(
                        (str(project_id), str(trace_id), int(generation)), 0
                    ),
                )
            )
        return metas

    def list_traces(
        self, environment_id: str, limit: int, offset: int = 0
    ) -> list[tuple[TraceRef, int]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT trace_id, generation, revision, first_received_at_ns FROM traces "
                "WHERE environment_id = ? "
                "ORDER BY first_received_at_ns DESC, trace_id, generation DESC LIMIT ? OFFSET ?",
                (environment_id, limit, offset),
            ).fetchall()
        return [
            (
                TraceRef(
                    environment_id=environment_id,
                    trace_id=str(trace_id),
                    generation=int(generation),
                    revision=int(revision),
                ),
                int(first_received_at_ns),
            )
            for trace_id, generation, revision, first_received_at_ns in rows
        ]

    def delete_trace(self, ref: TraceRef, expected_generation: int, expected_revision: int) -> bool:
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                row = self._conn.execute(
                    "SELECT project_id, revision FROM traces "
                    "WHERE environment_id = ? AND trace_id = ? AND generation = ?",
                    (ref.environment_id, ref.trace_id, expected_generation),
                ).fetchone()
                if row is None or int(row[1]) != expected_revision:
                    self._conn.execute("ROLLBACK")
                    return False
                project_id = str(row[0])
                self._conn.execute(
                    "DELETE FROM spans WHERE project_id = ? AND trace_id = ? AND generation = ?",
                    (project_id, ref.trace_id, expected_generation),
                )
                self._conn.execute(
                    "DELETE FROM traces WHERE project_id = ? AND trace_id = ? AND generation = ?",
                    (project_id, ref.trace_id, expected_generation),
                )
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
        return True
