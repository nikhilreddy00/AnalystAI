from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # LLM
    anthropic_api_key: str = ""
    groq_api_key: str = ""
    e2b_api_key: str = ""

    # PostgreSQL
    database_url: str = "postgresql+asyncpg://analystai:analystai@localhost:5432/analystai"

    # Qdrant
    qdrant_host: str = "localhost"
    qdrant_port: int = 6333

    # Ollama
    ollama_host: str = "http://localhost:11434"
    embedding_model: str = "nomic-embed-text"

    # App
    app_env: str = "development"
    frontend_url: str = "http://localhost:3000"
    session_data_dir: str = "./session_data"
    firebase_project_id: str = ""
    jwt_secret: str = "supersecret_analystai_dev_key"


settings = Settings()
