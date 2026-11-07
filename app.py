import os
from contextlib import asynccontextmanager
from typing import Annotated

import psycopg
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, StringConstraints

from evaluation import Dataset, compare, load_dataset
from storage import Store
from worker import available_targets

Version = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class RunRequest(BaseModel):
    dataset_version: Version
    target: str


def create_app(dsn=None):
    store = Store(dsn or os.getenv("DATABASE_URL", ""))

    @asynccontextmanager
    async def lifespan(_app):
        if not store.dsn:
            raise RuntimeError("Set DATABASE_URL before starting the app")
        store.initialize()
        store.add_dataset(load_dataset())
        yield

    api = FastAPI(title="AI Evaluation & Release Gate", lifespan=lifespan)

    @api.exception_handler(psycopg.OperationalError)
    async def database_unavailable(_request, _error):
        return JSONResponse(status_code=503, content={"detail": "Database unavailable"})

    @api.get("/health")
    def health():
        with store.connect() as connection:
            connection.execute("SELECT 1")
        return {"status": "ok"}

    @api.get("/targets")
    def targets():
        return available_targets()

    @api.post("/datasets", status_code=201)
    def create_dataset(dataset: Dataset):
        return {"version": store.add_dataset(dataset), "total": len(dataset.cases)}

    @api.get("/datasets")
    def datasets():
        return store.datasets()

    @api.get("/datasets/{version}")
    def get_dataset(version: Version):
        dataset = store.dataset(version)
        if dataset is None:
            raise HTTPException(404, "Dataset not found")
        return dataset

    @api.post("/runs", status_code=202)
    def enqueue(payload: RunRequest):
        if payload.target not in available_targets():
            raise HTTPException(422, "Unknown or unconfigured target")
        run = store.enqueue(payload.dataset_version, payload.target)
        if run is None:
            raise HTTPException(404, "Dataset not found")
        return run

    @api.get("/runs")
    def runs():
        return store.runs()

    @api.get("/runs/{run_id}")
    def run_details(run_id: int):
        run = store.run(run_id)
        if run is None:
            raise HTTPException(404, "Run not found")
        return run

    @api.get("/comparison")
    def comparison(baseline: int, candidate: int):
        before, after = run_details(baseline), run_details(candidate)
        if before["status"] != "completed" or after["status"] != "completed":
            raise HTTPException(409, "Both runs must be complete")
        try:
            return compare(before["report"], after["report"])
        except ValueError as error:
            raise HTTPException(409, str(error)) from error

    return api


app = create_app()
