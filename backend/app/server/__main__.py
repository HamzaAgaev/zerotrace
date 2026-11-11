from __future__ import annotations

import argparse
import atexit
import json
import signal
import sys
import threading
import time
from pathlib import Path

from waitress import serve

from .application import create_app


def main():
    parser = argparse.ArgumentParser(description="ZeroTrace common A/B/C server")
    parser.add_argument("--config", default="deploy/config.json")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text())
    config["server_port"] = args.port
    app = create_app(config)
    stopping = threading.Event()

    def maintain():
        while not stopping.wait(1):
            app.extensions["telemetry"].advance_assembly(time.time_ns())

    maintenance = threading.Thread(target=maintain, daemon=True)
    maintenance.start()

    def shutdown():
        stopping.set()
        maintenance.join(timeout=2)
        app.extensions["audit_jobs"].close()
        app.extensions["lab"].shutdown()
        app.extensions["records"].close()
        app.extensions["telemetry"].close()

    atexit.register(shutdown)

    def stop(_signum, _frame):
        sys.exit(0)

    signal.signal(signal.SIGTERM, stop)
    serve(app, host=args.host, port=args.port, threads=32)


if __name__ == "__main__":
    main()
