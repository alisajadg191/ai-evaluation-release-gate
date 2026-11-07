import hashlib
import json
import math
from collections.abc import Callable
from pathlib import Path
from time import perf_counter
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints, model_validator

ROOT = Path(__file__).resolve().parent
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=800)]
SourceTitle = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]


class Case(BaseModel):
    id: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,60}$")]
    question: Text
    expected_sources: list[SourceTitle] = Field(max_length=5)


class Dataset(BaseModel):
    cases: list[Case] = Field(min_length=2, max_length=200)

    @model_validator(mode="after")
    def validate_cases(self):
        ids = {case.id for case in self.cases}
        if len(ids) != len(self.cases):
            raise ValueError("Case IDs must be unique")
        if all(case.expected_sources for case in self.cases) or not any(
            case.expected_sources for case in self.cases
        ):
            raise ValueError("Include both answerable and unanswerable cases")
        return self

    @property
    def version(self):
        ordered = sorted(self.cases, key=lambda case: case.id)
        canonical = [
            {**case.model_dump(), "expected_sources": sorted(set(case.expected_sources))}
            for case in ordered
        ]
        payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()


class Passage(BaseModel):
    document_id: int
    title: str
    position: int
    text: str


class Answer(BaseModel):
    status: Literal["answered", "insufficient_evidence"]
    answer: str | None
    citations: list[Passage]


def load_dataset(path=ROOT / "fixtures/cases.json"):
    return Dataset.model_validate({"cases": json.loads(Path(path).read_text())})


def passage_key(passage):
    return passage.document_id, passage.title, passage.position, passage.text


def score_case(case, search, answer):
    expected = set(case.expected_sources)
    retrieved = {item.title for item in search[:5]}
    cited = {item.title for item in answer.citations}
    evidence = {passage_key(item) for item in search[:5]}
    abstained = answer.status == "insufficient_evidence" and not answer.answer and not answer.citations
    citation_correct = bool(
        answer.status == "answered" and answer.answer and answer.citations
        and cited == expected
        and all(passage_key(item) in evidence for item in answer.citations)
    )
    return {
        "retrieval_hit": bool(expected & retrieved) if expected else None,
        "citation_correct": citation_correct if expected else None,
        "abstention_correct": abstained if not expected else None,
        "passed": citation_correct if expected else abstained,
    }


def evaluate(dataset: Dataset, probe: Callable, on_progress: Callable | None = None):
    results = []
    for case in sorted(dataset.cases, key=lambda item: item.id):
        started = perf_counter()
        try:
            search, answer = probe(case.question)
            result = score_case(case, search, answer)
            result.update(search=[item.model_dump() for item in search], answer=answer.model_dump(), error=None)
        except (ProbeError, ValueError):
            result = {
                "retrieval_hit": False if case.expected_sources else None,
                "citation_correct": False if case.expected_sources else None,
                "abstention_correct": False if not case.expected_sources else None,
                "passed": False, "search": [], "answer": None, "error": "Probe failed",
            }
        result.update(
            id=case.id, question=case.question, expected_sources=case.expected_sources,
            latency_ms=round((perf_counter() - started) * 1000, 2),
        )
        results.append(result)
        if on_progress is not None:
            on_progress(len(results))

    summary = {}
    for metric in ("retrieval_hit", "citation_correct", "abstention_correct", "passed"):
        applicable = [row[metric] for row in results if row[metric] is not None]
        summary[metric] = sum(applicable) / len(applicable)
    latencies = sorted(row["latency_ms"] for row in results)
    summary["p95_ms"] = latencies[math.ceil(len(latencies) * 0.95) - 1]
    summary["errors"] = sum(row["error"] is not None for row in results)
    return {"dataset_version": dataset.version, "summary": summary, "cases": results}


class ProbeError(Exception):
    pass


def compare(baseline, candidate):
    if baseline["dataset_version"] != candidate["dataset_version"]:
        raise ValueError("Runs must use the same dataset version")
    before, after = baseline["summary"], candidate["summary"]
    failures = []
    metrics = ("retrieval_hit", "citation_correct", "abstention_correct")
    for metric in metrics:
        if after[metric] < 0.90:
            failures.append(f"{metric} is below the 90% floor")
        if before[metric] - after[metric] > 0.050000001:
            failures.append(f"{metric} dropped more than 5 percentage points")
    if before["errors"] or after["errors"]:
        failures.append("One or both runs contain probe errors")
    baseline_cases = {row["id"]: row for row in baseline["cases"]}
    regressions = [
        row["id"] for row in candidate["cases"]
        if baseline_cases[row["id"]]["passed"] and not row["passed"]
    ]
    return {
        "passed": not failures, "failures": failures, "regressions": regressions,
        "delta": {metric: round(after[metric] - before[metric], 4) for metric in (*metrics, "p95_ms")},
    }
