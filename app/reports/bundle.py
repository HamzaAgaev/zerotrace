"""Immutable JSON and self-contained HTML representations of one analysis."""
from __future__ import annotations

import html
import json

from analytics_support import VERSION, digest, wire


def build(snapshot, *, graph=None, projection=None, comparison=None):
    payload = wire({"schema": "zerotrace.report", "schema_version": "1.0.0",
                    "producer_version": VERSION, "snapshot": snapshot,
                    "selected_graph": graph, "projection": projection, "comparison": comparison,
                    "journal_refs": snapshot["journal_summary"],
                    "sampling": snapshot["sampling"], "quality": snapshot["quality"]})
    payload["versions"] = {
        "producer": VERSION, "model": snapshot["model_version"],
        "normalization": snapshot.get("normalization_version"),
        "ruleset": snapshot["ruleset_version"], "thresholds": snapshot["thresholds_version"],
        "projection": (projection or {}).get("algorithm_version"),
        "comparison": (comparison or {}).get("algorithm_version"),
    }
    payload["input_refs"] = {"dataset": snapshot["dataset_ref"], "traces": snapshot["trace_refs"],
                             "configs": snapshot["config_refs"], "baselines": snapshot["baseline_refs"]}
    payload["input_hashes"] = {"dataset": snapshot["dataset_hash"], "thresholds": snapshot["thresholds_hash"],
                               "projection": (projection or {}).get("input_hash"),
                               "comparison": (comparison or {}).get("input_hash")}
    payload["limitations"] = {"snapshot": snapshot.get("limitations", []),
                              "projection": (projection or {}).get("warnings", []),
                              "comparison": (comparison or {}).get("warnings", [])}
    payload["assumptions"] = (projection or {}).get("assumptions", [])
    payload["report_id"] = digest(payload)
    return payload


def render_html(bundle):
    """All user-controlled content is text, including the full machine-readable report."""
    def esc(value):
        return html.escape(str(value), quote=True)

    state = bundle["snapshot"]
    articles = []
    for f in state["findings"]:
        recommendations = "".join("<li>" + esc(r["text"]) + "</li>" for r in f["recommendations"])
        articles.append(f"<article><h3>{esc(f['title'])}</h3><p>{esc(f['explanation'])}</p>"
                        f"<p>{esc(f['rule_id'])} · {esc(f['affected_count'])} / "
                        f"{esc(f['denominator_count'])}</p><ul>{recommendations}</ul></article>")
    rows = "".join(f"<tr><td>{esc(v['rule_id'])}</td><td>{esc(v['status'])}</td>"
                   f"<td>{esc(v['eligible_count'])}</td></tr>" for v in state["rule_evaluations"])
    parts = ["<!doctype html><html lang=ru><meta charset=utf-8><title>ZeroTrace — отчёт</title>",
             "<style>body{font:16px/1.5 system-ui;margin:2rem auto;max-width:75rem;padding:0 1rem}"
             "td,th{border-bottom:1px solid #aaa;text-align:left;padding:.3rem .8rem}"
             "pre{overflow:auto;background:#f4f5f7;padding:1rem}article{margin:1.5rem 0}</style>",
             "<main><h1>Отчёт ZeroTrace</h1>", f"<p>{esc(state['environment_id'])} · "
             f"{esc(state['dataset_ref'])} · {esc(state['generated_at'])}</p>",
             *articles, "<h2>Проверки</h2><table><tr><th>Правило</th><th>Статус</th>"
             f"<th>Пригодных наблюдений</th></tr>{rows}</table>"]
    for field, title in [("projection", "Моделирование"), ("comparison", "Сравнение")]:
        if bundle[field] is not None:
            parts += [f"<h2>{title}</h2><pre>{esc(json.dumps(bundle[field], ensure_ascii=False, indent=2))}</pre>"]
    parts.append("<details><summary>Полный JSON с ревизиями и основаниями</summary><pre>"
                 + esc(json.dumps(bundle, ensure_ascii=False, indent=2)) + "</pre></details></main></html>")
    return "".join(parts)
