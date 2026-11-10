from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

DEFAULT_BODY_LIMIT_BYTES = 8 * 1024 * 1024
DEFAULT_MAX_SPANS_PER_BATCH = 10_000
DEFAULT_MAX_SPANS_PER_TRACE = 2_000
DEFAULT_MAX_DEPTH = 256
DEFAULT_MAX_DECOMPRESSED_BYTES = 32 * 1024 * 1024


@dataclass(frozen=True)
class IngestLimits:
    body_limit_bytes: int = DEFAULT_BODY_LIMIT_BYTES
    max_spans_per_batch: int = DEFAULT_MAX_SPANS_PER_BATCH
    max_spans_per_trace: int = DEFAULT_MAX_SPANS_PER_TRACE
    max_depth: int = DEFAULT_MAX_DEPTH
    max_decompressed_bytes: int = DEFAULT_MAX_DECOMPRESSED_BYTES
    gzip_supported: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "body_limit_bytes": self.body_limit_bytes,
            "max_spans_per_batch": self.max_spans_per_batch,
            "max_spans_per_trace": self.max_spans_per_trace,
            "max_depth": self.max_depth,
            "max_decompressed_bytes": self.max_decompressed_bytes,
            "gzip_supported": self.gzip_supported,
        }


ConnectionMode = Literal["existing_otlp", "file_import", "language_auto", "ebpf"]
SamplingMode = Literal["all_received", "head", "tail", "unknown"]


@dataclass(frozen=True)
class SourceCapabilities:
    source_id: str
    connection_mode: ConnectionMode
    requires_launcher_change: bool
    requires_dependency_install: bool
    requires_restart: bool
    required_permissions: list[str]
    protocols: list[str]
    propagation_verified: bool
    supported_fields: list[str]
    missing_fields: list[str]
    sampling: SamplingMode
    known_losses: str | None
    verified_at: str
    agent_name: str | None = None
    agent_version: str | None = None
    source_os: str | None = None
    architecture: str | None = None
    kernel_version: str | None = None
    runtime: str | None = None
    framework: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "connection_mode": self.connection_mode,
            "agent_name": self.agent_name,
            "agent_version": self.agent_version,
            "source_os": self.source_os,
            "architecture": self.architecture,
            "kernel_version": self.kernel_version,
            "runtime": self.runtime,
            "framework": self.framework,
            "requires_launcher_change": self.requires_launcher_change,
            "requires_dependency_install": self.requires_dependency_install,
            "requires_restart": self.requires_restart,
            "required_permissions": list(self.required_permissions),
            "protocols": list(self.protocols),
            "propagation_verified": self.propagation_verified,
            "supported_fields": list(self.supported_fields),
            "missing_fields": list(self.missing_fields),
            "sampling": self.sampling,
            "known_losses": self.known_losses,
            "verified_at": self.verified_at,
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> SourceCapabilities:
        return SourceCapabilities(
            source_id=d["source_id"],
            connection_mode=d["connection_mode"],
            agent_name=d.get("agent_name"),
            agent_version=d.get("agent_version"),
            source_os=d.get("source_os"),
            architecture=d.get("architecture"),
            kernel_version=d.get("kernel_version"),
            runtime=d.get("runtime"),
            framework=d.get("framework"),
            requires_launcher_change=bool(d["requires_launcher_change"]),
            requires_dependency_install=bool(d["requires_dependency_install"]),
            requires_restart=bool(d["requires_restart"]),
            required_permissions=list(d["required_permissions"]),
            protocols=list(d["protocols"]),
            propagation_verified=bool(d["propagation_verified"]),
            supported_fields=list(d["supported_fields"]),
            missing_fields=list(d["missing_fields"]),
            sampling=d["sampling"],
            known_losses=d.get("known_losses"),
            verified_at=d["verified_at"],
        )


@dataclass(frozen=True)
class SourceBinding:
    source_id: str
    project_id: str
    environment_id: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "project_id": self.project_id,
            "environment_id": self.environment_id,
        }
