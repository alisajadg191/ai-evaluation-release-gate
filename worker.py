import argparse
import logging
import os
import time
from contextlib import contextmanager

import psycopg

from evaluation import ProbeError, evaluate
from probes import DEMO_TARGETS, replay, support_probe
from storage import LeaseLost, Store

logger = logging.getLogger(__name__)


def available_targets():
    live = [
        f"support-{profile.lower()}" for profile in ("BASELINE", "CANDIDATE")
        if all(os.getenv(f"SUPPORT_{profile}_{setting}") for setting in ("URL", "EMAIL", "PASSWORD"))
    ]
    return [*DEMO_TARGETS, *live]


@contextmanager
def target_probe(target):
    if target in DEMO_TARGETS:
        yield lambda question: replay(question, target)
        return
    if target not in available_targets():
        raise ProbeError("Target is not configured")
    profile = target.removeprefix("support-").upper()
    with support_probe(*(os.environ[f"SUPPORT_{profile}_{setting}"]
                         for setting in ("URL", "EMAIL", "PASSWORD"))) as probe:
        yield probe


def work_once(store):
    run = store.claim()
    if run is None:
        return False
    try:
        try:
            dataset = store.dataset(run["dataset_version"])
            with target_probe(run["target"]) as probe:
                report = evaluate(dataset, probe, lambda count: store.progress(run, count))
            store.finish(run, report=report)
            logger.info("Completed run %s with %s probe errors", run["id"], report["summary"]["errors"])
        except ProbeError as error:
            store.finish(run, error=str(error))
            logger.warning("Run %s failed to start: %s", run["id"], error)
    except LeaseLost:
        logger.warning("Run %s lease was reassigned", run["id"])
    return True


def main():
    parser = argparse.ArgumentParser(description="Process queued evaluations")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    store = Store(os.environ["DATABASE_URL"])
    store.initialize()
    if args.once:
        work_once(store)
        return
    while True:
        try:
            if work_once(store):
                continue
        except psycopg.OperationalError:
            logger.warning("Database unavailable. Waiting to resume.")
        time.sleep(2)


if __name__ == "__main__":
    main()
