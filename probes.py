import json
import re
from contextlib import contextmanager

import httpx

from evaluation import Answer, Passage, ProbeError, ROOT

DEMO_TARGETS = ("demo-baseline", "demo-regression", "demo-fixed")
KEYWORDS = (
    r"return", r"delivery|tracking", r"warranty|manufacturing defect",
    r"invoice", r"password|reset", r"cancel",
)


def replay(question, target):
    policies = json.loads((ROOT / "fixtures/policies.json").read_text())
    for index, (policy, pattern) in enumerate(zip(policies, KEYWORDS, strict=True), 1):
        if re.search(pattern, question, re.IGNORECASE):
            if target == "demo-regression" and policy["title"] == "Returns":
                break
            passage = Passage(document_id=index, position=1, title=policy["title"], text=policy["body"])
            return [passage], Answer(status="answered", answer=passage.text, citations=[passage])
    return [], Answer(status="insufficient_evidence", answer=None, citations=[])


@contextmanager
def support_probe(base_url, email, password, transport=None):
    with httpx.Client(base_url=base_url, timeout=35, transport=transport) as client:
        try:
            login = client.post("/auth/login", json={"email": email, "password": password})
            login.raise_for_status()
            token = login.json()["token"]
            if not isinstance(token, str) or not token:
                raise ValueError("Empty session token")
        except (httpx.HTTPError, KeyError, ValueError) as error:
            raise ProbeError("Support assistant login failed") from error
        client.headers["Authorization"] = f"Bearer {token}"

        def probe(question):
            try:
                payload = {"question": question}
                search = client.post("/search", json=payload)
                search.raise_for_status()
                answer = client.post("/ask", json=payload)
                answer.raise_for_status()
                return [Passage.model_validate(hit) for hit in search.json()], Answer.model_validate(answer.json())
            except (httpx.HTTPError, TypeError, ValueError) as error:
                raise ProbeError("Support assistant request failed") from error

        try:
            yield probe
        finally:
            try:
                client.post("/auth/logout")
            except httpx.HTTPError:
                pass
