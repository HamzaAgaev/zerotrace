"""Resolve backend.* when Python is started inside the backend source directory."""

from pathlib import Path

__path__ = [str(Path(__file__).resolve().parents[1])]
