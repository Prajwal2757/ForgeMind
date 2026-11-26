"""Settings, read from environment variables."""
import os
from dataclasses import dataclass, field


def _env(name: str, default: str = ""):
    return field(default_factory=lambda: os.getenv(name) or default)


def _int(name: str, default: int):
    def read():
        value = int(os.getenv(name) or default)
        if value <= 0:
            raise ValueError(f"{name} must be a positive integer")
        return value
    return field(default_factory=read)


@dataclass
class Settings:
    db_path: str = _env("FORGEMIND_DB", "forgemind.db")
    secret: str = _env("FORGEMIND_SECRET")
    llm_provider: str = _env("FORGEMIND_LLM", "extractive")  # extractive | anthropic | openai
    llm_model: str = _env("FORGEMIND_LLM_MODEL")
    anthropic_api_key: str = _env("ANTHROPIC_API_KEY")
    anthropic_base_url: str = _env("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
    openai_api_key: str = _env("OPENAI_API_KEY")
    openai_base_url: str = _env("OPENAI_BASE_URL", "https://api.openai.com/v1")
    embed_provider: str = _env("FORGEMIND_EMBEDDINGS", "hashing")  # hashing | openai
    embed_model: str = _env("FORGEMIND_EMBED_MODEL", "text-embedding-3-small")
    allowed_hosts: str = _env("FORGEMIND_ALLOWED_HOSTS", "*")
    metrics_token: str = _env("FORGEMIND_METRICS_TOKEN")
    embedded_worker: bool = field(
        default_factory=lambda: (os.getenv("FORGEMIND_EMBEDDED_WORKER") or "1") == "1")
    token_ttl: int = _int("FORGEMIND_TOKEN_TTL", 3600)
    max_upload_bytes: int = _int("FORGEMIND_MAX_UPLOAD", 10 * 1024 * 1024)
    max_pending_jobs: int = _int("FORGEMIND_MAX_PENDING_JOBS", 50)
    rate_limit_per_minute: int = _int("FORGEMIND_RATE_LIMIT", 60)
    login_attempts: int = _int("FORGEMIND_LOGIN_ATTEMPTS", 10)  # per user+IP per 15 minutes
    body_timeout: int = _int("FORGEMIND_BODY_TIMEOUT", 10)
    upload_timeout: int = _int("FORGEMIND_UPLOAD_TIMEOUT", 120)
    max_chunks_per_tenant: int = _int("FORGEMIND_MAX_CHUNKS_PER_TENANT", 50000)
    max_cached_chunks: int = _int("FORGEMIND_MAX_CACHED_CHUNKS", 100000)
    parse_timeout: int = _int("FORGEMIND_PARSE_TIMEOUT", 60)

    def require_secret(self) -> str:
        if len(self.secret) < 32:
            raise RuntimeError("FORGEMIND_SECRET must be set to at least 32 characters")
        return self.secret
