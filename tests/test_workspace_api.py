"""Phase 5.6b workspace API: uploads become dataset versions, threads are bound to one dataset, the
sidebar's rename / pin / archive / delete, and every endpoint is private to its owner."""
import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI

from api.auth import UserProfile, get_current_user
from api.config import settings
from api.rate_limit import limiter
from api.routes import chat, connectors, dashboard, datasets, runs, sessions
from orchestrator.runner import in_memory_deps
from tests.test_durable_runs import replay  # noqa: F401  (the recorded-run fixture)


def _app(deps, uid="u1") -> FastAPI:
    app = FastAPI()
    app.state.limiter = limiter
    app.state.deps, app.state.run_store = deps, deps.run_store
    app.state.session_store, app.state.vector_store = deps.session_store, deps.vector_store
    for r, prefix in ((chat, "/chat"), (runs, "/runs"), (connectors, "/connectors"),
                      (sessions, "/api/sessions"), (datasets, "/api/datasets"),
                      (dashboard, "/dashboard")):
        app.include_router(r.router, prefix=prefix)
    app.dependency_overrides[get_current_user] = lambda: UserProfile(uid=uid)
    return app


def _client(deps, uid="u1"):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=_app(deps, uid)),
                             base_url="http://t")


@pytest.fixture
async def ws(replay, monkeypatch):  # noqa: F811
    monkeypatch.setattr(settings, "encryption_key", Fernet.generate_key().decode())
    path, _ = replay
    deps = in_memory_deps()
    limiter.reset()
    async with _client(deps) as client, _client(deps, "u2") as other:
        yield client, other, deps, path.read_bytes()


async def _upload(client, content: bytes, name="orders.csv", dataset_id=None):
    data = {"dataset_id": dataset_id} if dataset_id else {}
    return await client.post("/connectors/upload", files={"file": (name, content, "text/csv")},
                             data=data)


def _changed(csv: bytes) -> bytes:
    """The same columns with one more row: a new version of the same file."""
    lines = csv.decode().splitlines()
    return ("\n".join(lines + [lines[-1]]) + "\n").encode()


def _uploads(deps) -> list:
    from connectors.csv_connector import user_upload_dir
    d = user_upload_dir("u1")
    return sorted(d.iterdir()) if d.exists() else []


async def _ask(client, **body):
    return await client.post("/chat", json={"message": "Give me an overview of this dataset.",
                                            **body})


# ── Uploads → datasets ─────────────────────────────────────────────────────────

async def test_uploads_become_versions_of_a_dataset(ws):
    client, _, deps, csv = ws
    first = (await _upload(client, csv)).json()
    assert (first["status"], first["version"], first["dataset_name"]) == (
        "new_dataset", 1, "orders")
    assert "file_path" not in str(first)   # the client never sees a server path
    again = (await _upload(client, csv, name="copy.csv")).json()
    assert (again["status"], again["dataset_version_id"]) == (
        "existing", first["dataset_version_id"])
    assert len(_uploads(deps)) == 1   # identical bytes: no second file
    v2 = (await _upload(client, _changed(csv))).json()
    assert (v2["status"], v2["dataset_id"], v2["version"]) == ("new_version", first["dataset_id"], 2)
    [ds] = (await client.get("/api/datasets")).json()
    assert [v["version"] for v in ds["versions"]] == [1, 2]
    assert ds["latest"]["rows"] == first["dataset"]["latest"]["rows"] + 1


async def test_a_file_that_is_not_a_table_is_refused_at_upload(ws):
    client, _, deps, _ = ws
    r = await _upload(client, b"", name="empty.csv")
    assert r.status_code == 400 and "empty" in r.json()["detail"]
    assert _uploads(deps) == []


async def test_upload_new_version_only_to_your_own_dataset(ws):
    client, other, deps, csv = ws
    mine = (await _upload(client, csv)).json()
    r = await _upload(other, _changed(csv), dataset_id=mine["dataset_id"])
    assert r.status_code == 404
    from connectors.csv_connector import user_upload_dir
    assert not user_upload_dir("u2").exists()   # nothing written for the refused upload


# ── Threads on a dataset ───────────────────────────────────────────────────────

async def test_a_new_thread_names_its_dataset_and_gets_a_title(ws):
    client, other, deps, csv = ws
    up = (await _upload(client, csv)).json()
    assert (await _ask(other, dataset_version_id=up["dataset_version_id"])).status_code == 404
    assert (await _ask(other, dataset_id=up["dataset_id"])).status_code == 404
    run = (await _ask(client, dataset_version_id=up["dataset_version_id"])).json()
    [t] = (await client.get("/api/sessions")).json()
    assert (t["session_id"], t["dataset_id"], t["dataset_name"], t["run_status"]) == (
        run["session_id"], up["dataset_id"], "orders", "queued")
    assert t["title"] == "orders · Give me an overview of this dataset."
    assert t["questions"] == "Give me an overview of this dataset."
    assert t["updated_at"].endswith("Z") or t["updated_at"].endswith("+00:00")
    session = await deps.session_store.load(run["session_id"])
    assert session.dataset_version_id == up["dataset_version_id"]
    assert session.connector_config.file_path == str(_uploads(deps)[0])
    assert (await other.get("/api/sessions")).json() == []


async def test_one_dataset_per_thread(ws):
    client, _, deps, csv = ws
    a = (await _upload(client, csv)).json()
    b = (await _upload(client, b"x,y\n1,2\n3,4\n", name="other.csv")).json()
    run = (await _ask(client, dataset_version_id=a["dataset_version_id"])).json()
    await client.post(f"/runs/{run['run_id']}/cancel")
    r = await _ask(client, session_id=run["session_id"],
                   dataset_version_id=b["dataset_version_id"])
    assert r.status_code == 409 and "another dataset" in r.json()["detail"]
    # The same dataset's newer version: switched in place ("re-run on current data").
    v2 = (await _upload(client, _changed(csv))).json()
    r = await _ask(client, session_id=run["session_id"],
                   dataset_version_id=v2["dataset_version_id"])
    assert r.status_code == 200
    s = await deps.session_store.load(run["session_id"])
    assert (s.dataset_version_id, s.overview_done) == (v2["dataset_version_id"], False)


async def test_a_post_to_an_unknown_thread_never_creates_it(ws):
    client, _, deps, csv = ws
    up = (await _upload(client, csv)).json()
    r = await _ask(client, session_id="made-up", dataset_version_id=up["dataset_version_id"])
    assert r.status_code == 404
    assert await deps.session_store.load("made-up") is None


# ── Sidebar actions ────────────────────────────────────────────────────────────

async def test_rename_pin_archive(ws):
    client, other, _, csv = ws
    up = (await _upload(client, csv)).json()
    sid = (await _ask(client, dataset_version_id=up["dataset_version_id"])).json()["session_id"]
    for bad in ("", "   ", "x" * 121):
        assert (await client.patch(f"/api/sessions/{sid}", json={"title": bad})).status_code == 422
    assert (await client.patch(f"/api/sessions/{sid}", json={})).status_code == 422
    r = await client.patch(f"/api/sessions/{sid}",
                           json={"title": "  Board   numbers ", "pinned": True, "archived": True})
    assert (r.json()["title"], r.json()["pinned"], r.json()["archived"]) == (
        "Board numbers", True, True)
    assert (await other.patch(f"/api/sessions/{sid}", json={"pinned": False})).status_code == 404


async def test_delete_hides_the_thread_and_stops_its_run(ws):
    client, other, deps, csv = ws
    up = (await _upload(client, csv)).json()
    run = (await _ask(client, dataset_version_id=up["dataset_version_id"])).json()
    sid = run["session_id"]
    assert (await other.delete(f"/api/sessions/{sid}")).status_code == 404
    assert (await client.delete(f"/api/sessions/{sid}")).status_code == 202
    assert (await client.get("/api/sessions")).json() == []
    assert (await deps.run_store.get(run["run_id"])).status == "cancelled"
    assert (await client.delete(f"/api/sessions/{sid}")).status_code == 404
    assert (await _ask(client, session_id=sid)).status_code == 404
    assert (await client.get(f"/api/sessions/{sid}/results")).status_code == 404
    rid = run["run_id"]
    for r in (await client.get(f"/runs/{rid}"), await client.get(f"/runs/{rid}/events"),
              await client.post(f"/runs/{rid}/cancel"), await client.post(f"/runs/{rid}/retry"),
              await client.get(f"/runs/{rid}/report.pptx"),
              await client.get(f"/api/sessions/{sid}/thread")):
        assert r.status_code == 404   # a deleted thread's runs are gone too


async def test_pins_only_on_your_own_live_thread(ws):
    client, other, _, csv = ws
    up = (await _upload(client, csv)).json()
    sid = (await _ask(client, dataset_version_id=up["dataset_version_id"])).json()["session_id"]
    chart = {"id": "c1", "chart_type": "BarChart", "title": "t", "data": [], "x_key": "a",
             "y_keys": ["b"], "source_finding": "f"}
    assert (await other.post("/dashboard/pin", json={"session_id": sid, "chart": chart})
            ).status_code == 404
    assert (await client.post("/dashboard/pin", json={"session_id": sid, "chart": chart})
            ).status_code == 200
    page = (await client.get(f"/api/datasets/{up['dataset_id']}")).json()
    assert [c["id"] for c in page["charts"]] == ["c1"]
    assert [t["session_id"] for t in page["threads"]] == [sid]


# ── Datasets ───────────────────────────────────────────────────────────────────

async def test_dataset_rename_split_and_privacy(ws):
    client, other, _, csv = ws
    v1 = (await _upload(client, csv)).json()
    v2 = (await _upload(client, _changed(csv))).json()
    did = v1["dataset_id"]
    assert (await client.patch(f"/api/datasets/{did}", json={"name": " "})).status_code == 422
    assert (await client.patch(f"/api/datasets/{did}", json={"name": "Orders 2026"})
            ).json()["name"] == "Orders 2026"
    for r in (await other.get(f"/api/datasets/{did}"),
              await other.patch(f"/api/datasets/{did}", json={"name": "x"}),
              await other.delete(f"/api/datasets/{did}"),
              await other.post(f"/api/datasets/versions/{v2['dataset_version_id']}/split")):
        assert r.status_code == 404
    assert (await other.get("/api/datasets")).json() == []
    assert (await client.post(f"/api/datasets/versions/{v1['dataset_version_id']}/split")
            ).status_code == 409
    new = (await client.post(f"/api/datasets/versions/{v2['dataset_version_id']}/split")).json()
    assert new["id"] != did and [v["version"] for v in new["versions"]] == [1]
    assert len((await client.get("/api/datasets")).json()) == 2


async def test_deleting_a_dataset_hides_its_threads(ws):
    client, _, deps, csv = ws
    up = (await _upload(client, csv)).json()
    run = (await _ask(client, dataset_version_id=up["dataset_version_id"])).json()
    r = await client.delete(f"/api/datasets/{up['dataset_id']}")
    assert (r.status_code, r.json()["threads"]) == (202, 1)
    assert (await client.get("/api/datasets")).json() == []
    assert (await client.get("/api/sessions")).json() == []
    assert (await deps.run_store.get(run["run_id"])).status == "cancelled"
    assert (await _ask(client, dataset_version_id=up["dataset_version_id"])).status_code == 404


async def test_a_deleted_datasets_file_cant_be_reused_by_path(ws):
    """Security review: a raw connector_config naming a deleted (not yet purged) dataset's file
    must not start a thread on it; a live version's path is that version."""
    client, _, deps, csv = ws
    up = (await _upload(client, csv)).json()
    path = str(_uploads(deps)[0])
    run = (await _ask(client, connector_config={"type": "csv", "file_path": path})).json()
    s = await deps.session_store.load(run["session_id"])
    assert (s.dataset_id, s.dataset_version_id) == (up["dataset_id"], up["dataset_version_id"])
    await client.delete(f"/api/datasets/{up['dataset_id']}")
    r = await _ask(client, connector_config={"type": "csv", "file_path": path})
    assert r.status_code == 404
