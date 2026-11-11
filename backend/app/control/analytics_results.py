"""Persist C artifacts through A's metadata repository in the shared SQLite file."""

from analytics_support import AnalyticsError


class AnalyticsResults:
    def __init__(self, records):
        self.records = records

    def put_many(self, items):
        self.records.put_many([("analytics_" + kind, key, value) for kind, key, value in items])

    def get(self, kind, key):
        value = self.records.get("analytics_" + kind, key)
        if value is None:
            raise AnalyticsError("result_not_found", "Результат анализа не найден.", 404)
        return value
