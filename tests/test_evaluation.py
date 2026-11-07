import httpx
import pytest

from evaluation import Answer, Case, Dataset, Passage, ProbeError, compare, evaluate, load_dataset, score_case
from probes import replay, support_probe


def demo(target):
    return evaluate(load_dataset(), lambda question: replay(question, target))


def test_demo_gate_catches_four_missing_returns_cases():
    baseline = demo("demo-baseline")
    candidate = demo("demo-regression")
    decision = compare(baseline, candidate)
    assert len(baseline["cases"]) == 36
    assert baseline["summary"]["citation_correct"] == 1
    assert candidate["summary"]["citation_correct"] == pytest.approx(5 / 6)
    assert decision["passed"] is False
    assert decision["regressions"] == [f"policy-{i:02}" for i in range(1, 5)]
    assert compare(baseline, demo("demo-fixed"))["passed"]


def test_dataset_version_ignores_order_and_changes_with_labels():
    dataset = load_dataset()
    reordered = Dataset(cases=list(reversed(dataset.cases)))
    assert reordered.version == dataset.version
    reordered.cases[0] = reordered.cases[0].model_copy(update={"question": "A new unanswerable question?"})
    assert reordered.version != dataset.version
    with pytest.raises(ValueError, match="same dataset"):
        compare(demo("demo-baseline"), {"dataset_version": reordered.version})
    with pytest.raises(ValueError, match="Case IDs must be unique"):
        Dataset(cases=[dataset.cases[0], dataset.cases[0]])


def test_citation_provenance_requires_exact_retrieved_passage():
    case = Case(id="sample", question="Can I return it?", expected_sources=["Returns"])
    found = Passage(document_id=1, title="Returns", position=1, text="Actual policy")
    forged = found.model_copy(update={"text": "Different passage"})
    result = score_case(case, [found], Answer(status="answered", answer="Draft", citations=[forged]))
    assert result["retrieval_hit"] and not result["citation_correct"]


def test_probe_failures_are_counted_and_block_release():
    def unavailable(_):
        raise ProbeError("offline")

    report = evaluate(load_dataset(), unavailable)
    assert report["summary"]["errors"] == 36
    assert report["summary"]["passed"] == 0
    assert not compare(demo("demo-baseline"), report)["passed"]


def test_support_probe_uses_login_search_ask_and_logout():
    calls = []
    passage = {"document_id": 1, "title": "Returns", "position": 1, "text": "Policy"}
    routes = {
        "/auth/login": {"token": "session"},
        "/search": [passage],
        "/ask": {"status": "answered", "answer": "Policy", "citations": [passage]},
    }

    def handler(request):
        path = request.url.path
        calls.append(path)
        assert request.method == "POST"
        if path != "/auth/login":
            assert request.headers["Authorization"] == "Bearer session"
        if path == "/auth/logout":
            return httpx.Response(204)
        return httpx.Response(200, json=routes[path])

    with support_probe("http://support.test", "example@test", "password", httpx.MockTransport(handler)) as probe:
        search, answer = probe("Can I return it?")
    assert search == answer.citations
    assert calls == ["/auth/login", "/search", "/ask", "/auth/logout"]
