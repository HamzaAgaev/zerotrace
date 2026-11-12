"""Versioned inputs and deterministic identities for P5."""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, replace
from math import isfinite

from analytics_support import digest  # noqa: F401 -- public helper re-exports
from analytics_support import ref_key as refkey  # noqa: F401 -- public helper re-export

VERSION = "1.1.2"
MAX_NS = (1 << 63) - 1


def integer(value):
    if type(value) is int:
        return value if 0 <= value <= MAX_NS else None
    if isinstance(value, str) and value.isascii() and value.isdecimal() and len(value) <= 19:
        n = int(value)
        return n if n <= MAX_NS else None
    return None


def asref(key):
    return dict(zip(("environment_id", "trace_id", "generation", "revision"), key))


@dataclass(frozen=True)
class Thresholds:
    version: str = VERSION
    min_cases: int = 5
    tolerance_ns: int = 20_000_000
    r1_relative: float = .05
    r2_relative: float = .02
    amp_medium: int = 4
    amp_high: int = 9
    no_backoff_p95_ns: int = 5_000_000
    jitter_cv: float = .05
    jitter_min_samples: int = 10
    budget_high_ratio: float = 1.2
    degradation_ratio: float = 1.5
    timeout_drift_relative: float = .10
    cluster_spread_relative: float = .10
    verification_min_calls: int = 30

    def __post_init__(self):
        if not isinstance(self.version, str) or not self.version:
            raise ValueError("threshold version required")
        for key, value in asdict(self).items():
            if key == "version":
                continue
            if type(value) not in (int, float) or not isfinite(value) or value < 0:
                raise ValueError(f"invalid threshold: {key}")
            if type(getattr(type(self), key)) is int and type(value) is not int:
                raise ValueError(f"integer threshold required: {key}")
        if min(self.min_cases, self.jitter_min_samples, self.verification_min_calls) < 1:
            raise ValueError("sample thresholds must be positive")
        if self.amp_high < self.amp_medium or self.budget_high_ratio < 1:
            raise ValueError("invalid severity thresholds")


@dataclass(frozen=True)
class ThresholdPolicy:
    defaults: Thresholds
    edges: dict

    def for_edge(self, edge):
        return self.edges.get(edge, self.defaults)


def load_thresholds(config=None):
    """Accept flat defaults or {version, defaults, overrides:[{edge_id, values}]}."""
    if isinstance(config, ThresholdPolicy):
        return config
    if isinstance(config, Thresholds):
        return ThresholdPolicy(config, {})
    config = dict(config or {})
    overrides = config.pop("overrides", [])
    defaults = config.pop("defaults", {})
    try:
        default = Thresholds(**{**defaults, **config})
        edges = {}
        for item in overrides:
            key = item["edge_id"]
            if not isinstance(key, str) or not key or key in edges:
                raise ValueError("threshold override requires one unique edge_id")
            edges[key] = replace(default, **item["values"])
    except (KeyError, TypeError) as exc:
        raise ValueError("invalid thresholds or override selector") from exc
    return ThresholdPolicy(default, edges)


RECOMMENDATIONS = {
    "OBS_RETRIES": "Проверьте подтверждённые группы попыток по примерам трасс.",
    "R1": "Проверьте задержки получателя и прекращение работы после завершения клиента.",
    "R2": "Проверьте передачу оставшегося дедлайна перед следующей попыткой.",
    "R3": "Проверьте возможность оставить повторы на одном уровне этой цепочки.",
    "R4": "Проверьте локальный бюджет политики повторов.",
    "R5": "Проверьте настройки пауз и jitter у владельца вызова.",
    "R6": "Проверьте серверную дедупликацию до включения повторов записи.",
    "R7": "Проверьте условия повтора по ответу, полученному клиентом.",
    "R8": "Согласуйте таймаут вызываемой стороны с доступным бюджетом.",
    "D1": "Проверьте активную ревизию конфига и источник наблюдения.",
}


class Assessment:
    """One rule, one edge. Denominators use distinct eligible trace revisions."""
    def __init__(self, rule, edge):
        self.rule, self.edge = rule, edge
        self.refs = set()
        self.affected = set()
        self.excluded = Counter()
        self.count = 0
        self.eligible_call_count = None
        self.hit_count = 0
        self.applicable = True
        self.finding = None

    def consider(self, refs=(), triggered=False):
        self.count += 1
        self.refs.update(refs)
        if triggered:
            self.hit_count += 1
            self.affected.update(refs)

    def reject(self, reason):
        self.excluded[reason] += 1

    def report(self, title, why, evidence, *, basis="observed", severity="medium",
               confidence="high", recommendation=None):
        self.finding = dict(title=title, explanation=why, evidence=evidence, basis=basis,
                            severity=severity, confidence=confidence, recommendations=[
                                recommendation or dict(kind="primary", text=RECOMMENDATIONS[self.rule],
                                    why=why, risks=[], preconditions=[], intents=[], changes=[],
                                    feasibility="unknown")])

    def status(self):
        if self.count:
            return "evaluated"
        return "insufficient_data" if self.applicable else "not_applicable"


def trace_evidence(**operands):
    return dict(kind="trace_observation", source="Наблюдения по зафиксированной телеметрии", **operands)
