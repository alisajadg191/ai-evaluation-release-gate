# AI Evaluation & Release Gate

Compare two versions of a support assistant against the same labeled questions. Inspect the exact cases that regressed and use the CLI exit code to decide whether a candidate can ship.

## Try the dashboard

With Docker running:

```bash
git clone https://github.com/alisajadg191/ai-evaluation-release-gate.git
cd ai-evaluation-release-gate
docker compose up -d --build --wait
```

Open [the dashboard](http://127.0.0.1:8010/) or [the API docs](http://127.0.0.1:8010/docs). The 36-case dataset is loaded at startup.

1. Queue **demo-baseline** and **demo-regression**. Run history shows progress.
2. Compare the two completed runs. The gate fails because the regression omits the Returns policy. Retrieval hit rate and citation match fall from 100% to 83.3%, and four questions are flagged.
3. Expand a regression to inspect the baseline and candidate passages and citations.
4. Queue **demo-fixed** and compare it with the same baseline. The gate passes.

These targets use deterministic synthetic responses. They demonstrate the evaluation pipeline without starting Ollama or paying for API calls. Their scores are properties of the demo fixtures. Stop the stack with `docker compose down`; PostgreSQL retains reports in the Compose volume.

## Run the gate from a terminal

The CLI demo only needs Python 3.13.

```bash
python3.13 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
reports=$(mktemp -d)
.venv/bin/python -m cli run demo-baseline --output "$reports/baseline.json"
.venv/bin/python -m cli run demo-regression --output "$reports/regression.json"
.venv/bin/python -m cli gate "$reports/baseline.json" "$reports/regression.json"
.venv/bin/python -m cli run demo-fixed --output "$reports/fixed.json"
.venv/bin/python -m cli gate "$reports/baseline.json" "$reports/fixed.json"
```

A gate exits with **0** on pass, **1** on regression, and **2** when the comparison cannot be performed. GitHub Actions exercises both the failing and passing fixture scenarios.

## Evaluate the real support assistant

Start the [AI Customer Support & Knowledge Assistant](https://github.com/alisajadg191/ai-customer-support-knowledge-assistant) with Ollama and its local pgvector database. Use its staff account to index the six documents in [fixtures/policies.json](fixtures/policies.json) in a fresh synthetic corpus. Then set `SUPPORT_URL`, `SUPPORT_EMAIL`, and `SUPPORT_PASSWORD` in your shell with a demo customer's credentials. Use the assistant's URL, typically `http://127.0.0.1:8000` when running the CLI on the Mac.

```bash
.venv/bin/python -m cli run support --output "$reports/live-baseline.json"
# Point SUPPORT_URL at the candidate (or rerun after changing the assistant).
.venv/bin/python -m cli run support --output "$reports/live-candidate.json"
.venv/bin/python -m cli gate "$reports/live-baseline.json" "$reports/live-candidate.json"
```

The probe signs in once, calls `/search` and `/ask` per case, and signs out. To use the dashboard instead, set `SUPPORT_BASELINE_URL`, `SUPPORT_BASELINE_EMAIL`, and `SUPPORT_BASELINE_PASSWORD` before starting Compose. Use the corresponding `SUPPORT_CANDIDATE_*` variables for the second instance. On Docker Desktop the app typically reaches host-published ports via `host.docker.internal`. Only operator-configured targets can be queued. Keep credentials out of Git and use synthetic data because reports persist questions, returned passages and responses.

## What the metrics mean

| Metric | Check |
|---|---|
| Retrieval hit@5 | At least one labeled source title appears in the first five results for an answerable question. |
| Citation match | An answer cites exactly the labeled source titles, and each citation matches the document ID, title, position and text of a retrieved passage. This measures citation provenance and source match. |
| Abstention | An unanswerable case produces the insufficient-evidence status with neither an answer nor citations. |
| p95 latency | The nearest-rank 95th percentile of paired search and answer probe times. This metric is reported for comparison. |

The gate requires the first three scores to be at least **90%** and to fall by at most **5 percentage points** from the baseline. A probe error on either run blocks release. Two reports must share the same dataset version.

## Architecture and tests

FastAPI manages datasets and runs. PostgreSQL stores datasets by a hash of their normalized content and persists run reports. A separate worker claims runs with a row lock, updates progress per case, and renews its lease as it works. Another worker can retry an expired run from the beginning. A lease token prevents the old worker from overwriting the new result. After three expired attempts a run is marked failed.

Import a JSON array in the dashboard or pass it to the CLI via `--dataset`. Each case has an `id`, `question`, and `expected_sources` list. An empty list labels a case as unanswerable. A dataset needs examples of both kinds. See [fixtures/cases.json](fixtures/cases.json).

```bash
.venv/bin/python -m pytest -q
TEST_DATABASE_URL=postgresql://evaluation:evaluation-local@127.0.0.1:55433/evaluation \
  .venv/bin/python -m pytest -q
```

CI runs the PostgreSQL tests, checks the browser script, and queues and compares all three demo targets through the Compose stack. PostgreSQL tests use temporary schemas. The dashboard is a local, single-operator demo, and the services bind to loopback by default.
