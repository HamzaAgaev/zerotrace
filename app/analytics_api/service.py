"""Application use cases shared by the product router and the offline CLI."""
from __future__ import annotations

from collections import OrderedDict
from threading import Lock

import comparison
import projection
import rules
from analytics_support import VERSION, AnalyticsError, digest, ref_key, wire
from app.reports.bundle import build
from comparison.runs import validate_run_collections

from .provider import AnalyticsSource
from .storage import ResultStore


def _ids(request, left, right):
    values = [request.get(left), request.get(right)]
    if any(not isinstance(v, str) or not v for v in values):
        raise AnalyticsError("invalid_request", f"Expected {left} and {right}", 400)
    return values


def _trace(view, ref):
    key = ref_key(ref)
    graph = view.trace(ref)
    if ref_key(graph["trace_ref"]) != key:
        raise AnalyticsError("stale_revision", "Provider returned a different trace revision", 409)
    return graph


class AnalyticsService:
    def __init__(self, source: AnalyticsSource, results: ResultStore):
        self.source = source
        self.results = results
        self._findings_cache = OrderedDict()
        self._findings_lock = Lock()

    def findings_by_trace(self, view, environment_id):
        """Index occurrences by exact revision, including traces beyond the exemplar limit."""
        cache_key = view.audit_cache_key(environment_id) if hasattr(view, "audit_cache_key") else None
        if cache_key is not None:
            with self._findings_lock:
                index = self._findings_cache.get(cache_key)
                if index is not None:
                    self._findings_cache.move_to_end(cache_key)
                    return index
        _, audit = self._audit(view, environment_id)
        index = {ref_key(row["trace_ref"]): tuple(row["occurrence_ids"]) for row in audit["trace_findings"]}
        if cache_key is not None:
            with self._findings_lock:
                self._findings_cache[cache_key] = index
                if len(self._findings_cache) > 16:
                    self._findings_cache.popitem(last=False)
        return index

    def _audit(self, view, environment_id, mode=None):
        env = view.environment(environment_id)
        model = env["model"]
        if model["environment_id"] != environment_id:
            raise AnalyticsError("inconsistent_dataset", "Environment/model mismatch", 409)
        policies = env.get("policies", [])
        if mode is None:
            mode = "config_aware" if policies else "trace_only"
        if mode not in ("trace_only", "config_aware"):
            raise AnalyticsError("invalid_mode", "Expected trace_only or config_aware", 400)
        selected = policies if mode == "config_aware" else []
        result = rules.run(model, selected, env.get("thresholds"), env.get("baselines"))
        result["mode"] = mode
        result["capabilities"] = sorted(set(env.get("trace_capabilities", [])) |
                                        {p["source"] + "_config" for p in selected
                                         if p.get("source") in ("runtime", "declared")})
        entries = []
        for f in result["findings"]:
            for r in f["recommendations"]:
                key = digest([environment_id, f["occurrence_id"], r["recommendation_id"]])
                entries.append(("recommendation", key, {"environment_id": environment_id,
                                "finding_id": f["finding_id"], "occurrence_id": f["occurrence_id"],
                                "recommendation": r}))
        self.results.put_many(entries)
        return env, result

    def audit(self, environment_id, mode=None):
        with self.source.read() as view:
            return self._audit(view, environment_id, mode)[1]

    def recommendation(self, environment_id, occurrence_id, recommendation_id):
        return self.results.get("recommendation", digest([environment_id, occurrence_id, recommendation_id]))

    def _state(self, view, environment_id):
        env, result = self._audit(view, environment_id)
        model = env["model"]
        refs = sorted((trace["trace_ref"] for trace in model["traces"]), key=ref_key)
        state = {**result, "environment_id": environment_id, "trace_refs": refs,
                 "dataset_ref": model["dataset_ref"], "policies": env.get("policies", []),
                 "services": model.get("services", []), "edges": model.get("edges", []),
                 "sampling": model.get("sampling", {"mode": "unknown"}),
                 "quality": model.get("quality", {}), "journal_summary": env.get("journal_summary", []),
                 "capabilities": env.get("capabilities", {}), "producer_version": VERSION,
                 "normalization_version": model.get("normalization_version"),
                 "limitations": model.get("limitations", [])}
        state["snapshot_id"] = digest({k: v for k, v in state.items() if k != "generated_at"})
        self.results.put_many([("snapshot", state["snapshot_id"], state)])
        return self.results.get("snapshot", state["snapshot_id"])

    def state(self, environment_id):
        with self.source.read() as view:
            return self._state(view, environment_id)

    def project(self, request):
        with self.source.read() as view:
            graph = _trace(view, request.get("trace_ref"))
            result = projection.analyze(graph, request)
            return self._save_analysis("projection", result, graph["trace_ref"]["environment_id"],
                                       [graph["trace_ref"]], graph=graph)

    def _save_analysis(self, kind, result, env, refs, *, graph=None):
        result = wire(result)
        refs = sorted(refs, key=ref_key)
        key = digest([kind, env, refs, result])
        self.results.put_many([(kind, key, {"environment_id": env, "refs": refs,
                                          "result": result, "graph": graph})])
        return {**result, "analysis_id": key}

    def compare(self, request):
        run_keys = {"baseline_run_id", "candidate_run_id"}
        group_keys = {"baseline_trace_refs", "candidate_trace_refs"}
        if request.keys() & run_keys and request.keys() & group_keys:
            raise AnalyticsError("invalid_request", "Choose run IDs or trace groups", 400)
        with self.source.read() as view:
            if run_keys & request.keys():
                ids = _ids(request, "baseline_run_id", "candidate_run_id")
                runs = []
                refs = []
                for run_id in ids:
                    run = view.run(run_id)
                    if run.get("run_id") != run_id:
                        raise AnalyticsError("inconsistent_dataset", "Provider returned another run", 409)
                    runs.append(run)
                validate_run_collections(*runs)
                for i, run in enumerate(runs):
                    frozen = run["trace_collection"]["trace_refs"]
                    runs[i] = {**run, "traces": [_trace(view, ref) for ref in frozen]}
                    refs.extend(frozen)
                result = comparison.compare_runs(*runs)
            else:
                groups = [request.get(k) for k in ("baseline_trace_refs", "candidate_trace_refs")]
                if any(not isinstance(g, list) or not 1 <= len(g) <= 100 for g in groups):
                    raise AnalyticsError("invalid_cohorts", "Expected two lists of 1–100 trace references")
                refs = groups[0] + groups[1]
                traces = [_trace(view, ref) for ref in refs]
                result = comparison.compare(traces, request)
            env_ids = {ref_key(ref)[0] for ref in refs}
            if len(env_ids) != 1:
                raise AnalyticsError("incompatible_environments", "Select one environment")
            return self._save_analysis("comparison", result, env_ids.pop(), refs)

    def report(self, environment_id, *, snapshot_id=None, projection_id=None, comparison_id=None):
        if snapshot_id:
            state = self.results.get("snapshot", snapshot_id)
        else:
            state = self.state(environment_id)
        if state["environment_id"] != environment_id:
            raise AnalyticsError("report_scope_mismatch", "Snapshot belongs to another environment")
        included = {ref_key(ref) for ref in state["trace_refs"]}
        selected = {}
        analysis_refs = {}
        for kind, key in (("projection", projection_id), ("comparison", comparison_id)):
            if key:
                record = self.results.get(kind, key)
                if (record["environment_id"] != environment_id
                        or (kind == "projection" and any(ref_key(r) not in included for r in record["refs"]))):
                    raise AnalyticsError("report_scope_mismatch", "Analysis uses different input revisions")
                analysis_refs[kind] = record["refs"]
                selected[kind] = record["result"]
                if kind == "projection":
                    selected["graph"] = record["graph"]
        bundle = build(state, analysis_refs=analysis_refs, **selected)
        self.results.put_many([("report", bundle["report_id"], bundle)])
        return bundle

    def saved_report(self, report_id, environment_id=None):
        report = self.results.get("report", report_id)
        if environment_id is not None and report["snapshot"]["environment_id"] != environment_id:
            raise AnalyticsError("report_not_found", "Report unavailable in this environment", 404)
        return report
