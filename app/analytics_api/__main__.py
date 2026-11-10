"""Local analytics CLI. Run python -m app.analytics_api --help."""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from wsgiref.simple_server import make_server

from analytics_support import AnalyticsError, wire
from app.reports.bundle import render_html
from .provider import FixtureSource
from .routes import AnalyticsApp
from .service import AnalyticsService
from .storage import LocalResultStore


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main(argv=None):
    parser = argparse.ArgumentParser(description="ZeroTrace backend analytics")
    inputs = parser.add_mutually_exclusive_group()
    inputs.add_argument("--data", type=Path, help="Offline dataset from A/B or a local fixture")
    inputs.add_argument("--span-store", type=Path, help="Existing SQLite span database from backend/repository")
    parser.add_argument("--store", default=":memory:", help="SQLite path for saved analysis results")
    sub = parser.add_subparsers(dest="command", required=True)
    for cmd in ("audit", "state", "export"):
        action = sub.add_parser(cmd)
        action.add_argument("environment")
        if cmd == "audit":
            action.add_argument("--mode", choices=("config_aware", "trace_only"))
            action.add_argument("--fail-on", choices=("low", "medium", "high"))
            action.add_argument("--require-rule", action="append", default=[])
        if cmd == "export":
            action.add_argument("--format", choices=("json", "html"), default="json")
            action.add_argument("--output", type=Path, required=True)
            for name in ("projection", "comparison", "snapshot"):
                action.add_argument("--" + name + "-id")
    for cmd in ("project", "compare"):
        sub.add_parser(cmd).add_argument("request", help="JSON request file")
    check = sub.add_parser("eval")
    check.add_argument("actual")
    check.add_argument("expected")
    serve = sub.add_parser("serve", help="Serve analytics (and trace data with --span-store) on loopback")
    serve.add_argument("--port", type=int, default=8001)
    options = parser.parse_args(argv)
    store = None
    try:
        if options.command == "eval":
            import evalkit
            value = evalkit.score(read_json(options.actual), read_json(options.expected))
            print(json.dumps(wire(value), ensure_ascii=False, indent=2, allow_nan=False))
            return {"passed": 0, "blocked": 3, "failed": 1}[value["status"]]
        if options.span_store is not None:
            from .repository_source import RepositorySource
            if not options.span_store.is_file():
                parser.error("--span-store must point to an existing SQLite file")
            if options.store != ":memory:" and Path(options.store).exists() and options.span_store.samefile(options.store):
                parser.error("--store must be separate from the input --span-store database")
            source = RepositorySource(options.span_store)
        elif options.data is not None:
            source = FixtureSource.from_file(options.data)
        else:
            parser.error("--span-store or --data is required for this command")
        store = LocalResultStore(options.store)
        service = AnalyticsService(source, store)
        code = 0
        if options.command == "serve":
            if options.span_store is not None:
                from .backend_routes import BackendApp
                app = BackendApp(service)
            else:
                app = AnalyticsApp(service)
            with make_server("127.0.0.1", options.port, app) as server:
                print(f"Analytics HTTP at http://127.0.0.1:{server.server_port}", flush=True)
                server.serve_forever()
            return 0
        if options.command == "project":
            value = service.project(read_json(options.request))
            if value.get("status") != "available":
                code = 3
        elif options.command == "compare":
            value = service.compare(read_json(options.request))
        elif options.command == "audit":
            value = service.audit(options.environment, options.mode)
            states = {r["rule_id"]: r["status"] for r in value["rule_evaluations"]}
            ranks = {"low": 1, "medium": 2, "high": 3}
            if any(states.get(rule) != "evaluated" for rule in options.require_rule):
                code = 3
            elif options.fail_on and any(ranks[f["severity"]] >= ranks[options.fail_on]
                                          for f in value["findings"]):
                code = 2
        elif options.command == "state":
            value = service.state(options.environment)
        else:
            value = service.report(options.environment, snapshot_id=options.snapshot_id,
                                   comparison_id=options.comparison_id, projection_id=options.projection_id)
            content = render_html(value) if options.format == "html" else json.dumps(value, ensure_ascii=False, indent=2)
            options.output.write_text(content, encoding="utf-8")
            value = {"report_id": value["report_id"], "output": str(options.output)}
        print(json.dumps(wire(value), ensure_ascii=False, indent=2, allow_nan=False))
        return code
    except AnalyticsError as exc:
        print(json.dumps(exc.body(), ensure_ascii=False), file=sys.stderr)
        return 3 if exc.code in {"insufficient_traces", "traces_pending"} else 1
    except (OSError, sqlite3.Error, ValueError, KeyError, TypeError) as exc:
        print(f"Invalid input: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0
    finally:
        if store:
            store.close()


if __name__ == "__main__":
    sys.exit(main())
