from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path

from backend.contracts import NormalizedSpan, QualityIssue, SourceBinding, TraceRef
from backend.contracts.p2 import AssemblyState
from backend.ingest.normalize import NormalizedBatch

SCHEMA_VERSION = 2

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
    conflicting INTEGER NOT NULL DEFAULT 0,
    payload_version INTEGER NOT NULL DEFAULT 2,
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


def _same_observation(stored: str, version: int, candidate: NormalizedSpan) -> bool:
    original = json.loads(stored)
    if version == 1:
        # V1 did not capture these fields. Replays cannot enrich its immutable observation.
        added = ("service_namespace", "run_id", "logical_call_id", "attempt_index")
        candidate = replace(candidate, **{key: None for key in added if key not in original})
    return NormalizedSpan.from_dict(original) == candidate


class SpanReader:
    """The repository read interface, shared by the live store and pinned SQLite views."""

    def __init__(self, conn: sqlite3.Connection, schema_version: int = SCHEMA_VERSION) -> None:
        self._conn = conn
        self._schema_version = schema_version
        self._lock = threading.Lock()

    def _find_trace(self, ref: TraceRef) -> tuple[str, int, int, int, str] | None:
        rows = self._conn.execute(
            "SELECT project_id, revision, first_received_at_ns, last_received_at_ns, "
            "assembly_state FROM traces "
            "WHERE environment_id = ? AND trace_id = ? AND generation = ? LIMIT 2",
            (ref.environment_id, ref.trace_id, ref.generation),
        ).fetchall()
        if not rows:
            return None
        if len(rows) > 1:
            raise ValueError("trace identity is ambiguous across projects")
        row = rows[0]
        return (str(row[0]), int(row[1]), int(row[2]), int(row[3]), str(row[4]))

    def get_spans(self, ref: TraceRef) -> list[NormalizedSpan] | None:
        with self._lock:
            found = self._find_trace(ref)
            if found is None or found[1] != ref.revision:
                return None
            project_id = found[0]
            conflict_field = "conflicting" if self._schema_version >= 2 else "0"
            rows = self._conn.execute(
                f"SELECT payload, {conflict_field} FROM spans WHERE project_id = ? "
                "AND trace_id = ? AND generation = ? ORDER BY start_ns, span_id",
                (project_id, ref.trace_id, ref.generation),
            ).fetchall()
        spans = []
        for payload, conflicting in rows:
            span = NormalizedSpan.from_dict(json.loads(payload))
            if conflicting and "conflicting_span" not in span.diagnostics:
                span = replace(span, diagnostics=[*span.diagnostics, "conflicting_span"])
            spans.append(span)
        return spans

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
                "SELECT t.project_id, t.trace_id, t.generation, t.revision, "
                "t.first_received_at_ns, t.last_received_at_ns, t.assembly_state, "
                "(SELECT COUNT(*) FROM spans s "
                "WHERE s.project_id=t.project_id AND s.trace_id=t.trace_id AND "
                "s.generation=t.generation) FROM traces t WHERE t.environment_id = ? "
                "ORDER BY t.first_received_at_ns DESC, t.trace_id",
                (environment_id,),
            ).fetchall()
        metas: list[TraceMeta] = []
        for (
            project_id,
            trace_id,
            generation,
            revision,
            first_received,
            last_received,
            state,
            span_count,
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
                    span_count=int(span_count),
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


@contextmanager
def open_span_snapshot(path: Path) -> Iterator[SpanReader]:
    """Read an existing database without initializing or upgrading it."""
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, isolation_level=None)) as conn:
        conn.execute("BEGIN")
        # A deferred BEGIN pins the WAL view only on the first read.
        row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
        if row is None or int(row[0]) not in (1, SCHEMA_VERSION):
            raise ValueError("Unsupported span store schema")
        yield SpanReader(conn, int(row[0]))


class SpanStore(SpanReader):
    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        if str(self._path) != ":memory:":
            self._path = self._path.resolve()
            self._path.parent.mkdir(parents=True, exist_ok=True)
        super().__init__(
            sqlite3.connect(str(self._path), check_same_thread=False, isolation_level=None)
        )
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._migrate()

    @contextmanager
    def read_snapshot(self) -> Iterator[SpanReader]:
        if str(self._path) != ":memory:":
            with open_span_snapshot(self._path) as snapshot:
                yield snapshot
            return
        # An anonymous SQLite database cannot be reopened on another connection.
        with closing(sqlite3.connect(":memory:")) as snapshot_conn:
            with self._lock:
                self._conn.backup(snapshot_conn)
            yield SpanReader(snapshot_conn)

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
            if row is not None and int(row[0]) not in (1, SCHEMA_VERSION):
                raise RuntimeError(f"unsupported schema version {row[0]}")
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                columns = {r[1] for r in self._conn.execute("PRAGMA table_info(spans)")}
                if "payload_version" not in columns:
                    previous = int(row[0]) if row is not None else SCHEMA_VERSION
                    self._conn.execute(
                        "ALTER TABLE spans ADD COLUMN payload_version INTEGER NOT NULL "
                        f"DEFAULT {previous}"
                    )
                if "conflicting" not in columns:
                    self._conn.execute(
                        "ALTER TABLE spans ADD COLUMN conflicting INTEGER NOT NULL DEFAULT 0"
                    )
                self._conn.execute(
                    "CREATE TABLE IF NOT EXISTS trace_generations (environment_id TEXT NOT NULL, "
                    "trace_id TEXT NOT NULL, last_deleted_generation INTEGER NOT NULL, "
                    "PRIMARY KEY (environment_id, trace_id))"
                )
                self._conn.execute(
                    "INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', ?)",
                    (str(SCHEMA_VERSION),),
                )
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise

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
        # TraceRef carries only the environment; the storage key carries only the
        # project. Both identities must point to the same live trace.
        conflict = self._conn.execute(
            "SELECT 1 FROM traces WHERE trace_id = ? AND "
            "((project_id = ? AND environment_id != ?) OR "
            "(environment_id = ? AND project_id != ?)) LIMIT 1",
            (
                trace_id,
                binding.project_id,
                binding.environment_id,
                binding.environment_id,
                binding.project_id,
            ),
        ).fetchone()
        if conflict:
            raise ValueError("trace identity conflicts with an existing source binding")
        row = self._conn.execute(
            "SELECT generation, revision, last_received_at_ns FROM traces "
            "WHERE project_id = ? AND trace_id = ? ORDER BY generation DESC LIMIT 1",
            (binding.project_id, trace_id),
        ).fetchone()
        changed = False
        if row is None:
            deleted = self._conn.execute(
                "SELECT last_deleted_generation FROM trace_generations "
                "WHERE environment_id = ? AND trace_id = ?",
                (binding.environment_id, trace_id),
            ).fetchone()
            generation = deleted[0] + 1 if deleted else 1
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
                "SELECT payload, payload_version FROM spans WHERE project_id = ? AND trace_id = ? "
                "AND generation = ? AND span_id = ?",
                (binding.project_id, trace_id, generation, span.span_id),
            ).fetchone()
            if existing is None:
                self._insert_span(binding, trace_id, generation, span, payload, received_at_ns)
                changed = True
            elif existing[0] != payload and not _same_observation(existing[0], existing[1], span):
                # Persist only the diagnostic, never overwrite the accepted payload.
                marked = self._conn.execute(
                    "UPDATE spans SET conflicting = 1 WHERE project_id = ? AND trace_id = ? "
                    "AND generation = ? AND span_id = ? AND conflicting = 0",
                    (binding.project_id, trace_id, generation, span.span_id),
                ).rowcount
                changed = changed or bool(marked)
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
        if row is not None and changed:
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
            "received_at_ns, payload_version) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                binding.project_id,
                trace_id,
                generation,
                span.span_id,
                span.start_ns,
                payload,
                received_at_ns,
                SCHEMA_VERSION,
            ),
        )

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
                    "INSERT INTO trace_generations VALUES (?, ?, ?) "
                    "ON CONFLICT(environment_id, trace_id) DO UPDATE SET last_deleted_generation = "
                    "MAX(last_deleted_generation, excluded.last_deleted_generation)",
                    (ref.environment_id, ref.trace_id, expected_generation),
                )
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
