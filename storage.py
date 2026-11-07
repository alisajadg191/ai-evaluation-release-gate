from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from evaluation import Dataset

MAX_ATTEMPTS = 3
LEASE_SECONDS = 180
PUBLIC_RUN = "id, dataset_version, target, status, progress, total, attempts, error, created_at, finished_at"


class LeaseLost(Exception):
    pass


class Store:
    def __init__(self, dsn):
        self.dsn = dsn

    def connect(self):
        return psycopg.connect(self.dsn, row_factory=dict_row, connect_timeout=5)

    def initialize(self):
        with self.connect() as connection:
            # Serialize startup when the API and worker start together.
            connection.execute("SELECT pg_advisory_xact_lock(381625)")
            connection.execute(Path(__file__).with_name("schema.sql").read_text())

    def add_dataset(self, dataset):
        with self.connect() as connection:
            connection.execute("""
                INSERT INTO datasets (version, cases) VALUES (%s, %s)
                ON CONFLICT (version) DO NOTHING
            """, (dataset.version, Jsonb(dataset.model_dump()["cases"])))
        return dataset.version

    def datasets(self):
        with self.connect() as connection:
            return connection.execute("""
                SELECT version, jsonb_array_length(cases) AS total, created_at
                FROM datasets ORDER BY created_at DESC LIMIT 100
            """).fetchall()

    def dataset(self, version):
        with self.connect() as connection:
            row = connection.execute("SELECT cases FROM datasets WHERE version = %s", (version,)).fetchone()
        return Dataset.model_validate(row) if row else None

    def enqueue(self, version, target):
        with self.connect() as connection:
            return connection.execute(f"""
                INSERT INTO runs (dataset_version, target, total)
                SELECT version, %s, jsonb_array_length(cases) FROM datasets WHERE version = %s
                RETURNING {PUBLIC_RUN}
            """, (target, version)).fetchone()

    def runs(self):
        with self.connect() as connection:
            return connection.execute(f"SELECT {PUBLIC_RUN} FROM runs ORDER BY id DESC LIMIT 100").fetchall()

    def run(self, run_id):
        with self.connect() as connection:
            return connection.execute(
                f"SELECT {PUBLIC_RUN}, report FROM runs WHERE id = %s", (run_id,)
            ).fetchone()

    def claim(self):
        with self.connect() as connection:
            connection.execute("""
                UPDATE runs SET status = 'failed', error = 'Worker attempts exhausted',
                                lease_token = NULL, lease_until = NULL, finished_at = now()
                WHERE status = 'running' AND lease_until < now() AND attempts >= %s
            """, (MAX_ATTEMPTS,))
            return connection.execute("""
                WITH next_run AS (
                    SELECT id FROM runs
                    WHERE status = 'queued'
                       OR (status = 'running' AND lease_until < now() AND attempts < %s)
                    ORDER BY id LIMIT 1 FOR UPDATE SKIP LOCKED
                )
                UPDATE runs SET status = 'running', attempts = attempts + 1, progress = 0,
                                lease_token = %s, lease_until = now() + make_interval(secs => %s)
                WHERE id = (SELECT id FROM next_run)
                RETURNING id, dataset_version, target, lease_token
            """, (MAX_ATTEMPTS, uuid4(), LEASE_SECONDS)).fetchone()

    def progress(self, run, count):
        with self.connect() as connection:
            changed = connection.execute("""
                UPDATE runs SET progress = %s, lease_until = now() + make_interval(secs => %s)
                WHERE id = %s AND lease_token = %s AND status = 'running'
            """, (count, LEASE_SECONDS, run["id"], run["lease_token"])).rowcount
            if not changed:
                raise LeaseLost

    def finish(self, run, *, report=None, error=None):
        with self.connect() as connection:
            changed = connection.execute("""
                UPDATE runs SET status = %s, report = %s, error = %s, finished_at = now(),
                                lease_token = NULL, lease_until = NULL
                WHERE id = %s AND lease_token = %s AND status = 'running'
            """, ("failed" if error else "completed", Jsonb(report) if report else None,
                  error, run["id"], run["lease_token"])).rowcount
            if not changed:
                raise LeaseLost
