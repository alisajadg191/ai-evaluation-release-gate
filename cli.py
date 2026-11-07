import argparse
import json
import os
from pathlib import Path

from evaluation import ProbeError, compare, evaluate, load_dataset
from probes import DEMO_TARGETS, replay, support_probe


def main():
    parser = argparse.ArgumentParser(description="Run or compare support assistant evaluations")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run")
    run.add_argument("target", choices=(*DEMO_TARGETS, "support"))
    run.add_argument("--dataset", type=Path)
    run.add_argument("--output", type=Path)
    gate = commands.add_parser("gate")
    gate.add_argument("baseline", type=Path)
    gate.add_argument("candidate", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "gate":
            report = compare(json.loads(args.baseline.read_text()), json.loads(args.candidate.read_text()))
            print(json.dumps(report, indent=2))
            return 0 if report["passed"] else 1

        dataset = load_dataset(args.dataset) if args.dataset else load_dataset()
        if args.target == "support":
            with support_probe(os.environ["SUPPORT_URL"], os.environ["SUPPORT_EMAIL"],
                               os.environ["SUPPORT_PASSWORD"]) as probe:
                report = evaluate(dataset, probe)
        else:
            report = evaluate(dataset, lambda question: replay(question, args.target))
        if args.output:
            args.output.write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report["summary"], indent=2))
        return 1 if report["summary"]["errors"] else 0
    except (KeyError, OSError, ValueError, ProbeError) as error:
        parser.exit(2, f"Evaluation failed: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
