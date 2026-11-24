"""Application settings, read from environment variables (prefix HISTBIAS_) or `.env`."""

from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="HISTBIAS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    data_dir: Path = Path("./data")
    host: str = "127.0.0.1"
    port: int = 8000
    secret_key: SecretStr | None = None
    max_concurrency: int = 4
    request_timeout: float = 120
    provider_concurrency: int = 2  # per provider; override with provider extra `max_concurrency`
    retry_base_delay: float = 2.0  # seconds; exponential backoff for 429/5xx/timeouts
    run_worker: bool = True  # start the background run worker with the web app
    in_container: bool = False  # set by the Docker image; changes the non-local bind warning
    debug: bool = False
    log_level: str = "INFO"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "histbias.db"

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.db_path}"

    @property
    def is_local_bind(self) -> bool:
        return self.host in LOOPBACK_HOSTS

    def bind_warning(self) -> str | None:
        """Warning to log when the server is reachable from other machines (docs/07-security.md)."""
        if self.is_local_bind:
            return None
        if self.in_container:
            return (
                f"Bound to {self.host} inside a container. Publish the port on the host's loopback only "
                "(docker run -p 127.0.0.1:8000:8000 ...): anyone who can reach it can use your API keys."
            )
        return f"Bound to {self.host}: anyone who can reach this port can use your stored API keys."

    def ensure_data_dir(self) -> Path:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        return self.data_dir


@lru_cache
def get_settings() -> Settings:
    return Settings()
