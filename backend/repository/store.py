from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from contracts import NormalizedSpan, QualityIssue, SourceBinding, TraceRef
from contracts.p2 import AssemblyState
from ingest.normalize import NormalizedBatch

SCHEMA_VERSION = 3

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
    forced_reason TEXT,
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
CREATE TABLE IF NOT EXISTS trace_runs (
    project_id TEXT NOT NULL,
    trace_id TEXT NOT NULL,
    generation INTEGER NOT NULL,
    run_id TEXT NOT NULL,
    PRIMARY KEY (project_id, trace_id, generation)
);
CREATE TABLE IF NOT EXISTS run_samples (
    run_id TEXT PRIMARY KEY,
    environment_id TEXT NOT NULL,
    expected_count INTEGER NOT NULL,
    deadline_at_ns INTEGER NOT NULL,
    status TEXT NOT NULL,
    frozen_refs_json TEXT,
    created_at_ns INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS frozen_trace_revisions (
    environment_id TEXT NOT NULL,
    trace_id TEXT NOT NULL,
    generation INTEGER NOT NULL,
    revision INTEGER NOT NULL,
    meta_json TEXT NOT NULL,
    spans_json TEXT NOT NULL,
    PRIMARY KEY (environment_id, trace_id, generation, revision)
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
    forced_reason: str | None = None


@dataclass(frozen=True)
class RunSample:
    run_id: str
    environment_id: str
    status: Literal["waiting", "ready", "partial"]
    expected_count: int
    received_count: int
    trace_refs: list[TraceRef]


def _canonical_payload(span: NormalizedSpan) -> str:
    return json.dumps(span.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _same_span_payload(stored: str, incoming: str) -> bool:
    if stored == incoming:
        return True
    original: dict[str, Any] = json.loads(stored)
    # Pre-P11 rows could not record laboratory labels. Compare all fields the
    # older serializer retained, without modifying the live or frozen payload.
    lab_fields = {"run_id", "probe", "call_ref", "logical_call_id", "attempt_index"}
    if lab_fields.isdisjoint(original):
        return original == {k: v for k, v in json.loads(incoming).items() if k not in lab_fields}
    return False


class SpanStore:
    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        if str(self._path) != ":memory:":
            self._path.parent.mkdir(parents=True, exist_ok=True)
        # RLock: collection helpers call resolve_trace and other read methods
        # while already holding the store lock.
        self._lock = threading.RLock()
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
                current = SCHEMA_VERSION
            else:
                current = int(row[0])
                if current > SCHEMA_VERSION:
                    raise RuntimeError(
                        f"unsupported schema version {row[0]}, expected <= {SCHEMA_VERSION}"
                    )
            if current < 3:
                columns = {
                    str(r[1]) for r in self._conn.execute("PRAGMA table_info(traces)").fetchall()
                }
                if "forced_reason" not in columns:
                    self._conn.execute("ALTER TABLE traces ADD COLUMN forced_reason TEXT")
                current = 3
            self._conn.execute(
                "UPDATE meta SET value = ? WHERE key = 'schema_version'",
                (str(current),),
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
                    ref = self._ingest_trace(binding, trace_id, spans, received_at_ns, diagnostics)
                    refs.append(ref)
                    run_id = batch.run_ids.get(trace_id)
                    if run_id is not None and trace_id not in batch.probe_trace_ids:
                        self._conn.execute(
                            "INSERT OR IGNORE INTO trace_runs "
                            "(project_id, trace_id, generation, run_id) VALUES (?, ?, ?, ?)",
                            (binding.project_id, trace_id, ref.generation, run_id),
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
            elif not _same_span_payload(existing[0], payload):
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

    def _find_trace(self, ref: TraceRef) -> tuple[str, int, int, int, str, str | None] | None:
        row = self._conn.execute(
            "SELECT project_id, revision, first_received_at_ns, last_received_at_ns, "
            "assembly_state, forced_reason FROM traces "
            "WHERE environment_id = ? AND trace_id = ? AND generation = ?",
            (ref.environment_id, ref.trace_id, ref.generation),
        ).fetchone()
        if row is None:
            return None
        return (
            str(row[0]),
            int(row[1]),
            int(row[2]),
            int(row[3]),
            str(row[4]),
            None if row[5] is None else str(row[5]),
        )

    def get_spans(self, ref: TraceRef) -> list[NormalizedSpan] | None:
        with self._lock:
            frozen = self._frozen_revision(ref)
            if frozen is not None:
                return [NormalizedSpan.from_dict(item) for item in json.loads(frozen[1])]
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
            frozen = self._frozen_meta(ref)
            if frozen is not None:
                return frozen
            found = self._find_trace(ref)
            if found is None:
                return None
            project_id, revision, first_received, last_received, assembly_state, reason = found
            if revision != ref.revision:
                return self._frozen_meta(ref)
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
            forced_reason=reason,
        )

    def _frozen_revision(self, ref: TraceRef) -> tuple[str, str] | None:
        row = self._conn.execute(
            "SELECT meta_json, spans_json FROM frozen_trace_revisions "
            "WHERE environment_id = ? AND trace_id = ? AND generation = ? AND revision = ?",
            (ref.environment_id, ref.trace_id, ref.generation, ref.revision),
        ).fetchone()
        return (str(row[0]), str(row[1])) if row is not None else None

    def _frozen_meta(self, ref: TraceRef) -> TraceMeta | None:
        row = self._frozen_revision(ref)
        if row is None:
            return None
        value = json.loads(row[0])
        return TraceMeta(
            trace_ref=TraceRef.from_dict(value["trace_ref"]),
            project_id=str(value["project_id"]),
            first_received_at_ns=int(value["first_received_at_ns"]),
            last_received_at_ns=int(value["last_received_at_ns"]),
            assembly_state=str(value["assembly_state"]),  # type: ignore[arg-type]
            span_count=int(value["span_count"]),
            forced_reason=value["forced_reason"],
        )

    def _archive_frozen_revision(self, ref: TraceRef) -> None:
        meta = self.get_trace_meta(ref)
        spans = self.get_spans(ref)
        if meta is None or spans is None:
            raise RuntimeError(f"trace revision {ref!r} changed while finalizing collection")
        meta_json = json.dumps(
            {
                "trace_ref": meta.trace_ref.to_dict(),
                "project_id": meta.project_id,
                "first_received_at_ns": meta.first_received_at_ns,
                "last_received_at_ns": meta.last_received_at_ns,
                "assembly_state": meta.assembly_state,
                "span_count": meta.span_count,
                "forced_reason": meta.forced_reason,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        spans_json = json.dumps(
            [span.to_dict() for span in spans], sort_keys=True, separators=(",", ":")
        )
        self._conn.execute(
            "INSERT OR IGNORE INTO frozen_trace_revisions "
            "(environment_id, trace_id, generation, revision, meta_json, spans_json) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (ref.environment_id, ref.trace_id, ref.generation, ref.revision, meta_json, spans_json),
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
                    "last_received_at_ns, assembly_state, forced_reason FROM traces "
                    "WHERE environment_id = ? AND trace_id = ? "
                    "ORDER BY generation DESC LIMIT 1",
                    (environment_id, trace_id),
                ).fetchone()
            else:
                row = self._conn.execute(
                    "SELECT project_id, generation, revision, first_received_at_ns, "
                    "last_received_at_ns, assembly_state, forced_reason FROM traces "
                    "WHERE environment_id = ? AND trace_id = ? AND generation = ?",
                    (environment_id, trace_id, generation),
                ).fetchone()
            if row is None:
                if generation is not None and revision is not None:
                    return self._frozen_meta(
                        TraceRef(environment_id, trace_id, generation, revision)
                    )
                return None
            project_id = str(row[0])
            resolved_generation = int(row[1])
            current_revision = int(row[2])
            if revision is not None and revision != current_revision:
                return self._frozen_meta(
                    TraceRef(environment_id, trace_id, resolved_generation, revision)
                )
            if revision is not None:
                frozen = self._frozen_meta(
                    TraceRef(environment_id, trace_id, resolved_generation, revision)
                )
                if frozen is not None:
                    return frozen
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
            forced_reason=None if row[6] is None else str(row[6]),
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

    def start_collection(
        self,
        run_id: str,
        environment_id: str,
        expected_count: int,
        deadline_at_ns: int,
    ) -> None:
        with self._lock:
            row = self._conn.execute(
                "SELECT environment_id, expected_count, deadline_at_ns FROM run_samples "
                "WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if row is not None:
                if (
                    str(row[0]) != environment_id
                    or int(row[1]) != expected_count
                    or int(row[2]) != deadline_at_ns
                ):
                    raise ValueError(f"run_id {run_id!r} already started with other parameters")
                return
            self._conn.execute(
                "INSERT INTO run_samples (run_id, environment_id, expected_count, "
                "deadline_at_ns, status, frozen_refs_json, created_at_ns) "
                "VALUES (?, ?, ?, ?, 'waiting', NULL, ?)",
                (run_id, environment_id, expected_count, deadline_at_ns, deadline_at_ns),
            )

    def get_sample(self, run_id: str) -> RunSample | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT environment_id, expected_count, deadline_at_ns, status, "
                "frozen_refs_json FROM run_samples WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if row is None:
                return None
            environment_id = str(row[0])
            if row[4] is None:
                refs = self._collect_run_refs(environment_id, run_id)
                return RunSample(
                    run_id=run_id,
                    environment_id=environment_id,
                    status=str(row[3]),  # type: ignore[arg-type]
                    expected_count=int(row[1]),
                    received_count=len(refs),
                    trace_refs=refs,
                )
        return self._row_to_sample(run_id, row)

    def finalize_collection(self, run_id: str, now_ns: int) -> RunSample | None:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._conn.execute(
                    "SELECT environment_id, expected_count, deadline_at_ns, status, "
                    "frozen_refs_json FROM run_samples WHERE run_id = ?",
                    (run_id,),
                ).fetchone()
                if row is None:
                    self._conn.execute("ROLLBACK")
                    return None
                environment_id, expected_count, deadline_at_ns, _status, frozen = row
                if frozen is not None:
                    sample = self._row_to_sample(run_id, row)
                    self._conn.execute("COMMIT")
                    return sample
                refs = self._collect_run_refs(str(environment_id), run_id)
                received = len(refs)
                resolved_status: Literal["waiting", "ready", "partial"]
                if received >= int(expected_count):
                    resolved_status = "ready"
                elif now_ns >= int(deadline_at_ns):
                    resolved_status = "partial"
                else:
                    resolved_status = "waiting"
                if resolved_status == "waiting":
                    self._conn.execute("COMMIT")
                    return RunSample(
                        run_id=run_id,
                        environment_id=str(environment_id),
                        status="waiting",
                        expected_count=int(expected_count),
                        received_count=received,
                        trace_refs=refs,
                    )
                for ref in refs:
                    self._archive_frozen_revision(ref)
                frozen_json = json.dumps([r.to_dict() for r in refs])
                self._conn.execute(
                    "UPDATE run_samples SET status = ?, frozen_refs_json = ? WHERE run_id = ?",
                    (resolved_status, frozen_json, run_id),
                )
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
        return RunSample(
            run_id=run_id,
            environment_id=str(environment_id),
            status=resolved_status,
            expected_count=int(expected_count),
            received_count=received,
            trace_refs=refs,
        )

    def _collect_run_refs(self, environment_id: str, run_id: str) -> list[TraceRef]:
        rows = self._conn.execute(
            "SELECT t.trace_id, t.generation, t.revision FROM trace_runs tr "
            "JOIN traces t ON t.project_id = tr.project_id AND t.trace_id = tr.trace_id "
            "AND t.generation = tr.generation "
            "WHERE tr.run_id = ? AND t.environment_id = ? "
            "ORDER BY t.first_received_at_ns, t.trace_id",
            (run_id, environment_id),
        ).fetchall()
        refs: list[TraceRef] = []
        seen: set[str] = set()
        for trace_id, generation, _revision in rows:
            if str(trace_id) in seen:
                continue
            seen.add(str(trace_id))
            meta = self.resolve_trace(environment_id, str(trace_id), int(generation))
            if meta is None:
                continue
            if not self._has_root_server_span(meta):
                continue
            refs.append(meta.trace_ref)
        return refs

    def _has_root_server_span(self, meta: TraceMeta) -> bool:
        rows = self._conn.execute(
            "SELECT payload FROM spans WHERE project_id = ? AND trace_id = ? "
            "AND generation = ? ORDER BY start_ns, span_id LIMIT 50",
            (meta.project_id, meta.trace_ref.trace_id, meta.trace_ref.generation),
        ).fetchall()
        has_root = False
        for (payload,) in rows:
            span = NormalizedSpan.from_dict(json.loads(payload))
            if span.parent_span_id is None and span.kind == "server":
                has_root = True
                break
        return has_root

    @staticmethod
    def _row_to_sample(run_id: str, row: Any) -> RunSample:
        environment_id, expected_count, _deadline, status, frozen = row
        refs: list[TraceRef] = []
        if frozen is not None:
            refs = [TraceRef.from_dict(d) for d in json.loads(str(frozen))]
        return RunSample(
            run_id=run_id,
            environment_id=str(environment_id),
            status=str(status),  # type: ignore[arg-type]
            expected_count=int(expected_count),
            received_count=len(refs),
            trace_refs=refs,
        )

    def advance_assembly(self, now_ns: int, quiet_after_ns: int = 5_000_000_000) -> int:
        """Close open traces silent for longer than quiet_after_ns -> 'quiet'.

        Returns the number of traces transitioned. Bounded scan; new spans
        reopen nothing — late spans raise the revision instead (B3 rules).
        """
        cutoff = now_ns - quiet_after_ns
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                rows = self._conn.execute(
                    "SELECT project_id, trace_id, generation FROM traces "
                    "WHERE assembly_state = 'open' AND last_received_at_ns <= ? LIMIT 1000",
                    (cutoff,),
                ).fetchall()
                self._conn.executemany(
                    "UPDATE traces SET assembly_state = 'quiet' "
                    "WHERE project_id = ? AND trace_id = ? AND generation = ?",
                    rows,
                )
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
        return len(rows)

    def force_close(self, ref: TraceRef, reason: str) -> bool:
        """Force-close a trace (span/trace limits reached); reason is recorded."""
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                row = self._conn.execute(
                    "SELECT revision FROM traces WHERE environment_id = ? AND trace_id = ? "
                    "AND generation = ?",
                    (ref.environment_id, ref.trace_id, ref.generation),
                ).fetchone()
                if row is None:
                    self._conn.execute("ROLLBACK")
                    return False
                self._conn.execute(
                    "UPDATE traces SET assembly_state = 'forced', forced_reason = ? "
                    "WHERE environment_id = ? AND trace_id = ? AND generation = ?",
                    (reason, ref.environment_id, ref.trace_id, ref.generation),
                )
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
        return True
