import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.conninfo import make_conninfo

from app import create_app
from evaluation import load_dataset
from storage import LeaseLost, Store
from worker import work_once


@pytest.fixture
def store():
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("Set TEST_DATABASE_URL to test the PostgreSQL queue")
    schema = "test_" + uuid4().hex
    base = Store(dsn)
    with base.connect() as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    try:
        scoped = Store(make_conninfo(dsn, options=f"-c search_path={schema}"))
        scoped.initialize()
        yield scoped
    finally:
        with base.connect() as connection:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def test_workers_claim_once_and_reassign_expired_lease(store):
    version = store.add_dataset(load_dataset())
    job = store.enqueue(version, "demo-baseline")
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: store.claim(), range(2)))
    first, = [result for result in results if result]
    assert first["id"] == job["id"]
    store.progress(first, 1)
    assert store.run(job["id"])["progress"] == 1
    with store.connect() as connection:
        connection.execute("UPDATE runs SET lease_until = now() - interval '1 second' WHERE id = %s", (job["id"],))
    replacement = store.claim()
    assert replacement["lease_token"] != first["lease_token"]
    with pytest.raises(LeaseLost):
        store.finish(first, error="Old worker")
    store.finish(replacement, error="New worker")
    assert store.run(job["id"])["error"] == "New worker"


def test_dashboard_api_queues_completes_and_compares_runs(store):
    with TestClient(create_app(store.dsn)) as client:
        assert "AI Evaluation" in client.get("/").text
        assert client.get("/health").status_code == 200
        version = client.get("/datasets").json()[0]["version"]
        payload = {"dataset_version": version}
        assert client.post("/runs", json={**payload, "target": "http://unknown.test"}).status_code == 422
        jobs = []
        for target in ("demo-baseline", "demo-regression"):
            response = client.post("/runs", json={**payload, "target": target})
            assert response.status_code == 202, response.text
            jobs.append(response.json()["id"])
        comparison = f"/comparison?baseline={jobs[0]}&candidate={jobs[1]}"
        assert client.get(comparison).status_code == 409
        assert work_once(store)
        assert work_once(store)
        assert not work_once(store)
        candidate = client.get(f"/runs/{jobs[1]}").json()
        assert candidate["status"] == "completed"
        assert candidate["progress"] == candidate["total"] == 36
        result = client.get(comparison)
        assert result.status_code == 200, result.text
        assert result.json()["passed"] is False
