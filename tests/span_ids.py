"""Stable OTLP IDs for readable synthetic graph labels in tests."""
from hashlib import sha256

from analytics_support import valid_span_id


def sid(label):
    if label is None or valid_span_id(label):
        return label
    return sha256(label.encode()).hexdigest()[:16]
