import time

import httpx


def main():
    with httpx.Client(base_url="http://127.0.0.1:8010", timeout=5) as client:
        page = client.get("/")
        page.raise_for_status()
        assert "Compare releases" in page.text
        response = client.get("/datasets")
        response.raise_for_status()
        version = response.json()[0]["version"]
        runs = []
        for target in ("demo-baseline", "demo-regression", "demo-fixed"):
            response = client.post("/runs", json={"dataset_version": version, "target": target})
            response.raise_for_status()
            runs.append(response.json()["id"])
        for run in runs:
            for _ in range(30):
                response = client.get(f"/runs/{run}")
                response.raise_for_status()
                status = response.json()["status"]
                if status == "completed":
                    break
                assert status != "failed", response.text
                time.sleep(1)
            else:
                raise RuntimeError(f"Worker did not complete run {run}")
        for candidate, expected in zip(runs[1:], [False, True], strict=True):
            response = client.get("/comparison", params={"baseline": runs[0], "candidate": candidate})
            response.raise_for_status()
            assert response.json()["passed"] is expected, response.text
        print("Compose API and worker rejected the regression and accepted the fix")


if __name__ == "__main__":
    main()
