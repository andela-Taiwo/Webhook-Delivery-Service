"""Application settings, loaded from environment variables / .env.

Single source of truth for configuration. Every component reads from the
shared `settings` singleton at the bottom of this module rather than
touching `os.environ` directly.

Secrets are never committed and never baked into an image. They arrive at
runtime from the environment (in Kubernetes, from the Secret referenced by
`secretRef` in the Deployment). To make that structural rather than
aspirational, `_validate_deployed_environment` refuses to start a
staging/production process whose secrets are missing or still hold a
development default — a service that boots without a signing key would
deliver unsigned webhooks and look perfectly healthy doing it.
"""

from functools import lru_cache
from typing import Literal, Self
from urllib.parse import urlsplit, urlunsplit

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Minimum length for a key expected to carry real entropy. Anything shorter
# is a placeholder someone forgot to replace.
MIN_SECRET_LENGTH = 32

# Values that are fine locally and must never appear in a deployed
# environment. Matched case-insensitively against connection strings.
DEVELOPMENT_MARKERS = ("webhook:webhook", "localhost", "127.0.0.1", "changeme", "password")

DEPLOYED_ENVIRONMENTS = ("staging", "production")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="WEBHOOK_",
        extra="ignore",
    )

    # --- App identity ---
    app_name: str = "webhook-service"
    environment: Literal["local", "test", "staging", "production"] = "local"
    api_v1_prefix: str = "/api/v1"

    # --- Server ---
    host: str = "0.0.0.0"
    port: int = 8000

    # --- Logging ---
    log_level: str = "INFO"
    log_json: bool = True  # False gives pretty console output for local dev

    # --- Observability (OpenTelemetry) ---
    otel_enabled: bool = True
    otel_exporter_otlp_endpoint: str = "http://localhost:4317"
    otel_traces_sample_ratio: float = 1.0  # 1.0 = sample everything (dev only)

    # --- Metrics ---
    metrics_enabled: bool = True

    # --- Multi-tenancy ---
    tenant_header: str = "X-Tenant-ID"
    require_tenant_by_default: bool = True

    # --- Datastores (wired up in later components; declared here now so
    # every service reads config the same way from day one) ---
    database_url: str = "postgresql+asyncpg://webhook:webhook@localhost:5432/webhook-delivery"
    redis_url: str = "redis://localhost:6379/0"
    kafka_bootstrap_servers: str = "localhost:9092"

    # --- Connection pool ---
    # pool_size is per process; total Postgres connections is
    # pool_size * replicas, so this has to stay well under max_connections.
    db_pool_size: int = 10
    db_max_overflow: int = 5
    db_pool_timeout_seconds: int = 30
    # Recycle below any proxy/Postgres idle timeout so we retire connections
    # ourselves rather than discovering they were closed mid-query.
    db_pool_recycle_seconds: int = 1800
    db_echo: bool = False

    redis_max_connections: int = 20
    redis_socket_timeout_seconds: float = 2.0

    # Kafka admin calls during health checks must be short — the probe has to
    # answer well inside its own deadline.
    kafka_request_timeout_ms: int = 2000

    # --- Secrets ---
    #
    # SecretStr so the value is masked in reprs, logs, and tracebacks; the
    # plaintext is only reachable via .get_secret_value() at the point of use.
    # Both default to None so local development needs no provisioning, and
    # both are *required* in staging/production by the validator below.
    #
    # signing_secret_encryption_key: envelope-encrypts subscription signing
    # secrets at rest, so a database dump alone cannot forge webhooks.
    signing_secret_encryption_key: SecretStr | None = None
    # api_key_pepper: mixed into API key hashes. Held outside the database,
    # so a stolen dump can't be attacked offline with the hashes alone.
    api_key_pepper: SecretStr | None = None

    # --- Health checks ---
    # Readiness must answer faster than the probe's own timeout, otherwise a
    # slow dependency makes k8s kill the pod for being unresponsive rather
    # than merely marking it unready.
    health_check_timeout_seconds: float = 2.0

    @property
    def is_local(self) -> bool:
        return self.environment == "local"

    @property
    def is_deployed(self) -> bool:
        return self.environment in DEPLOYED_ENVIRONMENTS

    @property
    def safe_database_url(self) -> str:
        """The database URL with its password redacted, for logging.

        Connection strings get logged routinely (startup banners, error
        context); the password inside one should not follow it into the log
        aggregator.
        """
        parts = urlsplit(self.database_url)
        if parts.password is None:
            return self.database_url

        userinfo = f"{parts.username}:***" if parts.username else "***"
        host = parts.hostname or ""
        if parts.port:
            host = f"{host}:{parts.port}"
        return urlunsplit(
            (parts.scheme, f"{userinfo}@{host}", parts.path, parts.query, parts.fragment)
        )

    @model_validator(mode="after")
    def _validate_deployed_environment(self) -> Self:
        """Refuse to start a deployed process with missing or default secrets.

        Failing at startup is the point: a crash-looping pod is visible and
        blocks the rollout, whereas a service that quietly started with a
        development password stays broken until someone finds it the hard way.
        """
        if not self.is_deployed:
            return self

        errors: list[str] = []

        required_secrets = {
            "signing_secret_encryption_key": self.signing_secret_encryption_key,
            "api_key_pepper": self.api_key_pepper,
        }
        for name, secret in required_secrets.items():
            if secret is None or not secret.get_secret_value():
                errors.append(
                    f"{name} is required in {self.environment} and must be supplied at "
                    f"runtime via WEBHOOK_{name.upper()} (never baked into the image)"
                )
            elif len(secret.get_secret_value()) < MIN_SECRET_LENGTH:
                errors.append(
                    f"{name} must be at least {MIN_SECRET_LENGTH} characters; "
                    "a shorter value is a placeholder, not a key"
                )

        connection_settings = {
            "database_url": self.database_url,
            "redis_url": self.redis_url,
            "kafka_bootstrap_servers": self.kafka_bootstrap_servers,
        }
        for name, value in connection_settings.items():
            marker = next((m for m in DEVELOPMENT_MARKERS if m in value.lower()), None)
            if marker is not None:
                errors.append(
                    f"{name} contains the development default {marker!r}; "
                    f"it was not overridden for {self.environment}"
                )

        if errors:
            raise ValueError(
                "Invalid configuration for a deployed environment:\n  - " + "\n  - ".join(errors)
            )

        return self


@lru_cache
def get_settings() -> Settings:
    """Cached settings accessor — use this in FastAPI dependencies."""
    return Settings()


settings = get_settings()
