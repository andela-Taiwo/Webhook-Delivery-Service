"""Secret handling and startup validation.

The project rule is that no secret is ever baked into a build, for any
environment. Discipline alone doesn't enforce that, so these tests pin the
structural guarantees:

- deployed environments refuse to start on a missing or default-valued secret
- local/test keep working without ceremony
- secrets never leak through logs, tracebacks, or the settings repr
"""

import pytest
from pydantic import SecretStr, ValidationError

from webhook_service.core.config import Settings

# A syntactically valid but obviously fake value, for tests that need the
# other secrets present while probing one specific failure.
DUMMY = "x" * 32


def _prod_env(**overrides: str) -> dict[str, str]:
    base = {
        "environment": "production",
        "database_url": "postgresql+asyncpg://u:p@db:5432/webhook-delivery",
        "redis_url": "redis://cache:6379/0",
        "kafka_bootstrap_servers": "kafka:9092",
        "signing_secret_encryption_key": DUMMY,
        "api_key_pepper": DUMMY,
    }
    base.update(overrides)
    return base


# --- Deployed environments must fail fast --------------------------------


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_missing_encryption_key_refuses_to_start(environment: str) -> None:
    """A missing signing key must crash the pod, not silently disable signing.

    Starting without it would mean delivering unsigned webhooks that
    subscribers cannot verify — a security failure that looks like success.
    """
    env = _prod_env(environment=environment)
    del env["signing_secret_encryption_key"]

    with pytest.raises(ValidationError, match="signing_secret_encryption_key"):
        Settings(**env)  # type: ignore[arg-type]


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_missing_api_key_pepper_refuses_to_start(environment: str) -> None:
    env = _prod_env(environment=environment)
    del env["api_key_pepper"]

    with pytest.raises(ValidationError, match="api_key_pepper"):
        Settings(**env)  # type: ignore[arg-type]


def test_development_default_credentials_rejected_in_production() -> None:
    """The compose file's throwaway `webhook:webhook` credentials must never
    reach a deployed environment — catching this at startup is the difference
    between a failed deploy and a production database nobody secured."""
    with pytest.raises(ValidationError, match="default|development"):
        Settings(
            **_prod_env(
                database_url="postgresql+asyncpg://webhook:webhook@localhost:5432/webhook-delivery"
            )  # type: ignore[arg-type]
        )


def test_localhost_dependencies_rejected_in_production() -> None:
    """localhost in a deployed environment means the config never got
    overridden — fail rather than serve with a broken dependency."""
    with pytest.raises(ValidationError, match="localhost|default"):
        Settings(**_prod_env(redis_url="redis://localhost:6379/0"))  # type: ignore[arg-type]


def test_short_encryption_key_rejected() -> None:
    """A key too short to be plausibly random is a placeholder someone left in."""
    with pytest.raises(ValidationError, match="32"):
        Settings(**_prod_env(signing_secret_encryption_key="tooshort"))  # type: ignore[arg-type]


def test_valid_production_config_starts() -> None:
    settings = Settings(**_prod_env())  # type: ignore[arg-type]
    assert settings.environment == "production"
    assert settings.api_key_pepper is not None


# --- Local development stays frictionless --------------------------------


def test_local_environment_does_not_require_secrets() -> None:
    """Developers must be able to run the stack without provisioning secrets;
    the strictness applies only where it protects real data."""
    settings = Settings(environment="local")
    assert settings.environment == "local"


def test_test_environment_does_not_require_secrets() -> None:
    settings = Settings(environment="test")
    assert settings.environment == "test"


# --- Secrets must not leak -----------------------------------------------


def test_secrets_are_secretstr_not_plain_strings() -> None:
    """SecretStr masks the value in reprs and tracebacks. A plain str would
    print in full the first time a stack trace touches Settings."""
    settings = Settings(**_prod_env())  # type: ignore[arg-type]
    assert isinstance(settings.signing_secret_encryption_key, SecretStr)
    assert isinstance(settings.api_key_pepper, SecretStr)


def test_secret_value_not_present_in_repr() -> None:
    secret = "supersecretvalue" + "y" * 16
    settings = Settings(**_prod_env(signing_secret_encryption_key=secret))  # type: ignore[arg-type]

    assert secret not in repr(settings)
    assert secret not in str(settings)
    assert secret not in repr(settings.signing_secret_encryption_key)


def test_secret_value_retrievable_when_actually_needed() -> None:
    secret = "z" * 32
    settings = Settings(**_prod_env(api_key_pepper=secret))  # type: ignore[arg-type]
    assert settings.api_key_pepper.get_secret_value() == secret


def test_database_password_masked_in_safe_url() -> None:
    """Logging a connection URL is routine; logging the password in it is not."""
    settings = Settings(
        **_prod_env(database_url="postgresql+asyncpg://user:hunter2@db:5432/webhook-delivery")  # type: ignore[arg-type]
    )
    assert "hunter2" not in settings.safe_database_url
    assert "db:5432" in settings.safe_database_url
