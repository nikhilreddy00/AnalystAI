from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # LLM
    anthropic_api_key: str = ""
    groq_api_key: str = ""
    e2b_api_key: str = ""
    llm_model: str = "claude-sonnet-4-6"   # pinned; changing it requires a full eval run
    trace_dir: str = "./traces"

    # PostgreSQL (Supabase session-mode pooler, port 5432)
    database_url: str = "postgresql+asyncpg://analystai:analystai@localhost:5432/analystai"

    # Supabase — auth and the database above live in one project
    supabase_url: str = ""                 # https://<ref>.supabase.co
    supabase_jwt_secret: str = ""          # legacy HS256 projects + the test suite

    # Encryption at rest for persisted connector secrets (Fernet key)
    encryption_key: str = ""

    # Qdrant
    qdrant_host: str = "localhost"
    qdrant_port: int = 6333

    # Ollama
    ollama_host: str = "http://localhost:11434"
    embedding_model: str = "nomic-embed-text"

    # App
    app_env: str = "development"
    frontend_url: str = "http://localhost:3000"
    cors_origins: str = "http://localhost:3000"   # comma-separated
    session_data_dir: str = "./session_data"

    @field_validator("supabase_url")
    @classmethod
    def _strip_trailing_slash(cls, v: str) -> str:
        # The token `iss` has no double slash; "https://ref.supabase.co/" would never match.
        return v.strip().rstrip("/")

    @property
    def cors_origin_list(self) -> list[str]:
        origins = {o.strip() for o in self.cors_origins.split(",") if o.strip()}
        if self.frontend_url:
            origins.add(self.frontend_url.strip())
        return sorted(origins)


settings = Settings()
