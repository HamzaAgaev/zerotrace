from __future__ import annotations

import hmac
import os
import secrets
import threading
from pathlib import Path

from contracts import SourceBinding

from .http_config import ProductError


class Registry:
    def __init__(self, config: dict, telemetry=None):
        self.config = config
        self.telemetry = telemetry
        self.environments = config.get("environments", [])
        self.services: dict[tuple[str, str], dict] = {}
        self.tokens: dict[str, SourceBinding] = {}
        self.lock = threading.RLock()
        self.token_directory = Path(config.get("token_directory", "/tokens"))
        self.token_directory.mkdir(parents=True, exist_ok=True)
        for item in config.get("sources", []):
            self.register_source(item["environment_id"], item["source_id"])
        for item in config.get("services", []):
            self.register_service(item)

    def register_source(self, environment: str, source_id: str) -> str:
        if not self._valid_identifier(environment) or not self._valid_identifier(source_id):
            raise ValueError("invalid environment or source identifier")
        directory = self.token_directory / environment
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / source_id
        with self.lock:
            if not path.exists():
                try:
                    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                except FileExistsError:
                    pass
                else:
                    with os.fdopen(fd, "w") as stream:
                        stream.write(secrets.token_urlsafe(32))
            token = path.read_text().strip()
            # B keys stored traces by project and trace ID. Give each environment
            # an isolated storage project even when an exporter reuses a trace ID.
            project = f"{self.config.get('project_id', 'zerotrace-demo')}:{environment}"
            binding = SourceBinding(source_id, project, environment)
            self.tokens[token] = binding
            return token

    def resolve(self, token: str | None) -> SourceBinding | None:
        if not token:
            return None
        with self.lock:
            for known, binding in self.tokens.items():
                if hmac.compare_digest(known, token):
                    return binding
        return None

    def register_service(self, service: dict) -> None:
        if not isinstance(service, dict):
            raise ValueError("service definition must be an object")
        environment = service.get("environment_id")
        service_id = service.get("service_id")
        if not self._valid_identifier(environment) or not self._valid_identifier(service_id):
            raise ValueError("invalid environment or service identifier")
        key = environment, service_id
        with self.lock:
            self.services[key] = dict(service)

    def service(self, environment: str, service_id: str) -> dict | None:
        if not self._valid_identifier(environment) or not self._valid_identifier(service_id):
            return None
        with self.lock:
            value = self.services.get((environment, service_id))
            result = dict(value) if value else None
        if (
            result
            and result.get("control_url")
            and self.telemetry is not None
            and self.telemetry.has_ambiguous_service_namespace(environment, service_id)
        ):
            raise ProductError(
                409,
                "ambiguous_service_identity",
                "В окружении несколько namespaces с этим именем сервиса. "
                "Управление неоднозначно.",
            )
        return result

    @staticmethod
    def _valid_identifier(value: object) -> bool:
        return (
            isinstance(value, str)
            and bool(value)
            and value not in {".", ".."}
            and value.replace("-", "").replace("_", "").replace(".", "").isalnum()
        )

    def remove_lab(self) -> None:
        with self.lock:
            self.services = {
                key: value for key, value in self.services.items() if not value.get("is_lab")
            }
