from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Literal

CANONICALIZATION_VERSION = "v1"


@dataclass(frozen=True)
class ServiceKey:
    project_id: str
    environment: str
    namespace: str
    service_name: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "environment": self.environment,
            "namespace": self.namespace,
            "service_name": self.service_name,
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> ServiceKey:
        return ServiceKey(
            project_id=d["project_id"],
            environment=d["environment"],
            namespace=d["namespace"],
            service_name=d["service_name"],
        )


@dataclass(frozen=True)
class OperationKey:
    protocol: str
    route_or_name: str
    source: str
    normalization_version: str
    method: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol": self.protocol,
            "method": self.method,
            "route_or_name": self.route_or_name,
            "source": self.source,
            "normalization_version": self.normalization_version,
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> OperationKey:
        return OperationKey(
            protocol=d["protocol"],
            method=d.get("method"),
            route_or_name=d["route_or_name"],
            source=d["source"],
            normalization_version=d["normalization_version"],
        )


@dataclass(frozen=True)
class TraceRef:
    environment_id: str
    trace_id: str
    generation: int
    revision: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "environment_id": self.environment_id,
            "trace_id": self.trace_id,
            "generation": self.generation,
            "revision": self.revision,
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> TraceRef:
        return TraceRef(
            environment_id=d["environment_id"],
            trace_id=d["trace_id"],
            generation=int(d["generation"]),
            revision=int(d["revision"]),
        )


EndpointKind = Literal["service", "external", "db", "unknown"]


@dataclass(frozen=True)
class EndpointKey:
    kind: EndpointKind
    service: ServiceKey | None = None
    address_alias: str | None = None
    system: str | None = None

    @staticmethod
    def service_ep(service: ServiceKey) -> EndpointKey:
        return EndpointKey(kind="service", service=service)

    @staticmethod
    def external(address_alias: str) -> EndpointKey:
        return EndpointKey(kind="external", address_alias=address_alias)

    @staticmethod
    def db(system: str) -> EndpointKey:
        return EndpointKey(kind="db", system=system)

    @staticmethod
    def unknown() -> EndpointKey:
        return EndpointKey(kind="unknown")

    def to_dict(self) -> dict[str, Any]:
        if self.kind == "service":
            assert self.service is not None
            return {"kind": "service", "service": self.service.to_dict()}
        if self.kind == "external":
            return {"kind": "external", "address_alias": self.address_alias}
        if self.kind == "db":
            return {"kind": "db", "system": self.system}
        return {"kind": "unknown"}

    @staticmethod
    def from_dict(d: dict[str, Any]) -> EndpointKey:
        kind = d["kind"]
        if kind == "service":
            return EndpointKey.service_ep(ServiceKey.from_dict(d["service"]))
        if kind == "external":
            return EndpointKey.external(d["address_alias"])
        if kind == "db":
            return EndpointKey.db(d["system"])
        return EndpointKey.unknown()


@dataclass(frozen=True)
class EdgeKey:
    caller: ServiceKey
    caller_operation: OperationKey
    callee: EndpointKey
    callee_operation: OperationKey

    def to_dict(self) -> dict[str, Any]:
        return {
            "caller": self.caller.to_dict(),
            "caller_operation": self.caller_operation.to_dict(),
            "callee": self.callee.to_dict(),
            "callee_operation": self.callee_operation.to_dict(),
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> EdgeKey:
        return EdgeKey(
            caller=ServiceKey.from_dict(d["caller"]),
            caller_operation=OperationKey.from_dict(d["caller_operation"]),
            callee=EndpointKey.from_dict(d["callee"]),
            callee_operation=OperationKey.from_dict(d["callee_operation"]),
        )

    def canonical_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    def edge_id(self) -> str:
        payload = f"{CANONICALIZATION_VERSION}:{self.canonical_json()}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def label(self) -> str:
        return f"{self.caller.service_name}->{endpoint_label(self.callee)}"


def endpoint_label(ep: EndpointKey) -> str:
    if ep.kind == "service" and ep.service is not None:
        return ep.service.service_name
    if ep.kind == "external":
        return ep.address_alias or "external"
    if ep.kind == "db":
        return ep.system or "db"
    return "unknown"
