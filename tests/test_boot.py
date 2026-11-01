"""Audit B4: production never runs on an in-memory store. Development may, and says so."""
import pytest
from cryptography.fernet import Fernet

from api.config import settings
from orchestrator.runner import open_deps

NO_DB = "postgresql+asyncpg://x:y@127.0.0.1:1/none"


@pytest.fixture
def prod(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "database_url", NO_DB)
    monkeypatch.setattr(settings, "supabase_url", "https://x.supabase.co")
    monkeypatch.setattr(settings, "encryption_key", Fernet.generate_key().decode())


async def test_the_api_refuses_to_boot_in_production_without_postgres(prod):
    from api.main import app, lifespan
    with pytest.raises(RuntimeError, match="PostgreSQL unavailable in production"):
        async with lifespan(app):
            pass


async def test_the_worker_refuses_to_start_without_postgres_in_any_env(prod, monkeypatch):
    monkeypatch.setattr(settings, "app_env", "development")
    from worker.__main__ import main
    with pytest.raises(RuntimeError, match="PostgreSQL unavailable"):
        await main()


async def test_development_falls_back_to_memory_and_says_so():
    with pytest.warns(UserWarning, match="in memory"):
        deps = await open_deps("development", NO_DB, vector=False)
    assert deps.persistent is False


async def test_health_reports_whether_work_is_saved():
    import httpx

    from api.main import app
    from orchestrator.runner import in_memory_deps
    app.state.deps = in_memory_deps()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://t") as c:
        assert (await c.get("/health")).json()["persistent"] is False


@pytest.fixture
def bad_layer(tmp_path, monkeypatch):
    import semantic_layer
    (tmp_path / "broken.yaml").write_text("tables: [{name: t, source: s.t, primary_key: [x], "
                                          "columns: [{name: y, type: fact}]}]\n")
    monkeypatch.setattr(semantic_layer, "SAMPLE_LAYERS", tmp_path)
    semantic_layer.file_layers.cache_clear()
    yield
    semantic_layer.file_layers.cache_clear()


async def test_an_invalid_flat_file_layer_stops_the_api(prod, bad_layer):
    from api.main import app, lifespan
    with pytest.raises(ValueError, match="invalid semantic layer"):
        async with lifespan(app):
            pass


async def test_an_invalid_flat_file_layer_stops_the_worker(prod, bad_layer):
    from worker.__main__ import main
    with pytest.raises(ValueError, match="invalid semantic layer"):
        await main()


async def test_unreadable_yaml_stops_boot(prod, tmp_path, monkeypatch):
    import semantic_layer
    (tmp_path / "x.yaml").write_text("tables: [unclosed")
    monkeypatch.setattr(semantic_layer, "SAMPLE_LAYERS", tmp_path)
    semantic_layer.file_layers.cache_clear()
    try:
        from worker.__main__ import main
        with pytest.raises(Exception, match="(?i)yaml|scanner|parser|flow"):
            await main()
    finally:
        semantic_layer.file_layers.cache_clear()


def test_the_shipped_layers_are_valid_and_found_from_any_directory(tmp_path, monkeypatch):
    import semantic_layer
    monkeypatch.chdir(tmp_path)   # the worker's cwd needn't be the repo
    semantic_layer.file_layers.cache_clear()
    assert len(semantic_layer.file_layers()) == 7
