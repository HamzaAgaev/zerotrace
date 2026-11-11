"""Example customer traffic. This process is separate from measured lab runs."""
from __future__ import annotations

import argparse
import itertools
import time

import requests


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://gateway:8080")
    parser.add_argument("--interval", type=float, default=2)
    args = parser.parse_args()
    operations = [("GET", "/product"), ("POST", "/checkout"), ("POST", "/refund")]
    for method, path in itertools.cycle(operations):
        try:
            response = requests.request(method, args.url.rstrip("/") + path, timeout=15)
            print(f"{method} {path}: {response.status_code}", flush=True)
        except requests.RequestException as exc:
            print(f"{method} {path}: {type(exc).__name__}", flush=True)
        time.sleep(max(0.1, args.interval))


if __name__ == "__main__":
    main()
