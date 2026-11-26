"""Settings, read from environment variables."""
import os
from dataclasses import dataclass, field


def _env(name: str, default: str = ""):
    return field(default_factory=lambda: os.getenv(name, default))


@dataclass
class Settings:
    db_path: str = _env("FORGEMIND_DB", "forgemind.db")
    secret: str = _env("FORGEMIND_SECRET")
    llm_provider: str = _env("FORGEMIND_LLM", "extractive")  # extractive | anthropic | openai
    llm_model: str = _env("FORGEMIND_LLM_MODEL")
    anthropic_api_key: str = _env("ANTHROPIC_API_KEY")
    openai_api_key: str = _env("OPENAI_API_KEY")
    openai_base_url: str = _env("OPENAI_BASE_URL", "https://api.openai.com/v1")
    embed_provider: str = _env("FORGEMIND_EMBEDDINGS", "hashing")  # hashing | openai
    embed_model: str = _env("FORGEMIND_EMBED_MODEL", "text-embedding-3-small")
    max_upload_bytes: int = field(
        default_factory=lambda: int(os.getenv("FORGEMIND_MAX_UPLOAD", str(10 * 1024 * 1024))))
    rate_limit_per_minute: int = field(
        default_factory=lambda: int(os.getenv("FORGEMIND_RATE_LIMIT", "60")))

    def require_secret(self) -> str:
        if len(self.secret) < 32:
            raise RuntimeError("FORGEMIND_SECRET must be set to at least 32 characters")
        return self.secret
