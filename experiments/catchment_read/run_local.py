"""Sequential fresh processes with varied reader order. Never uploads data."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--prepared", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if subprocess.check_output(["git", "status", "--porcelain"], text=True).strip():
        raise RuntimeError("pin clean measured code before running")
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    args.output.mkdir(parents=True, exist_ok=False)
    selections = args.prepared / "selections.json"
    workloads = list(json.loads(selections.read_text())["workloads"])
    paths = {
        "0.3.0": args.source,
        "a-index": args.candidates / "candidate-a-index",
        "b-arrow": args.candidates / "candidate-b-arrow",
    }
    schedule: list[dict[str, object]] = []
    for repetition in range(2):
        for workload_index, workload in enumerate(workloads):
            order = list(paths)
            rotate = workload_index % 3
            order = order[rotate:] + order[:rotate]
            if repetition:
                order.reverse()
            for kind in order:
                name = f"{len(schedule):02d}-{kind}-{workload}"
                command = [
                    sys.executable,
                    "measure.py",
                    "--data",
                    str(paths[kind]),
                    "--selections",
                    str(selections),
                    "--workload",
                    workload,
                    "--output",
                    str(args.output / "runs" / f"{name}.json"),
                    "--witness-dir",
                    str(args.output / "witnesses" / name),
                    "--code-commit",
                    commit,
                ]
                if kind != "0.3.0":
                    command.append("--experimental")
                schedule.append(
                    {
                        "name": name,
                        "repetition": repetition,
                        "kind": kind,
                        "command": command,
                    }
                )
    (args.output / "schedule.json").write_text(json.dumps(schedule, indent=2) + "\n")
    for entry in schedule:
        print(entry["name"], flush=True)
        command = entry["command"]
        assert isinstance(command, list)
        subprocess.run(command, check=True)
    subprocess.run(
        [
            sys.executable,
            "evidence.py",
            "verify",
            str(args.prepared),
            str(args.output / "runs"),
            str(args.output / "correctness.json"),
        ],
        check=True,
    )


if __name__ == "__main__":
    main()
