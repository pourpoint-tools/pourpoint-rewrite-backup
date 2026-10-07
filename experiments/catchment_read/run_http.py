"""Sequential fresh HTTP workers against immutable local data; no uploads."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--candidates", required=True)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--prepared", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if subprocess.check_output(["git", "status", "--porcelain"], text=True).strip():
        raise RuntimeError("pin clean measured code before running")
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    args.source = (
        args.source.rstrip("/") if args.receipt else str(Path(args.source).resolve())
    )
    args.candidates = (
        args.candidates.rstrip("/")
        if args.receipt
        else str(Path(args.candidates).resolve())
    )
    args.prepared = args.prepared.resolve()
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "logs").mkdir()
    from evidence import digest

    provenance = {
        "code_commit": commit,
        "source": str(args.source),
        "candidates": str(args.candidates),
        "prepared": str(args.prepared),
        "reference": json.loads((args.prepared / "reference.json").read_text()),
        "selections_sha256": digest(args.prepared / "selections.json"),
        "reference_sha256": digest(args.prepared / "reference.arrow"),
    }
    if (
        provenance["selections_sha256"] != provenance["reference"]["selections_sha256"]
        or provenance["reference_sha256"] != provenance["reference"]["sha256"]
    ):
        raise ValueError("prepared evidence changed")
    if args.receipt:
        from s3_access import validate_receipt

        args.receipt = args.receipt.resolve()
        receipt = json.loads(args.receipt.read_text())
        inherited_path = Path(__file__).with_name("results") / "provenance.json"
        inherited = json.loads(inherited_path.read_text())
        validate_receipt(receipt, args.source, args.candidates, inherited)
        if provenance["reference"] != inherited["source_reference"]:
            raise ValueError("independent reference differs from original experiment")
        provenance.update(
            receipt_sha256=digest(args.receipt),
            receipt=receipt,
            inherited_provenance_sha256=digest(inherited_path),
        )
    (args.output / "provenance.json").write_text(
        json.dumps(provenance, indent=2) + "\n"
    )
    selections = args.prepared / "selections.json"
    workloads = list(json.loads(selections.read_text())["workloads"])
    expected = {
        "single",
        "small_upstream",
        "larger_upstream",
        "overlap",
        "scattered",
        "large_geometry",
        "batch",
    }
    if set(workloads) != expected:
        raise ValueError("expected the seven unchanged prepared workloads")
    paths = {
        "0.3.0": args.source,
        "a-index": args.candidates + "/candidate-a-index",
        "b-arrow": args.candidates + "/candidate-b-arrow",
    }
    schedule: list[dict[str, object]] = []
    if args.smoke:
        workloads = ["single"]
    for iteration in range(1 if args.smoke else 2 if args.receipt else 4):
        delay = 0 if iteration < 2 else 0.02
        repetition = iteration % 2
        for workload_index, workload in enumerate(workloads):
            order = list(paths)
            rotate = workload_index % 3
            order = order[rotate:] + order[:rotate]
            if repetition:
                order.reverse()
            for kind in order:
                name = f"{len(schedule):02d}-delay{delay}-{kind}-{workload}"
                command = [
                    sys.executable,
                    str(Path(__file__).with_name("http_measure.py")),
                    "--delay-seconds",
                    str(delay),
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
                if args.receipt:
                    command.extend(["--receipt", str(args.receipt)])
                if kind != "0.3.0":
                    command.append("--experimental")
                schedule.append(
                    {
                        "name": name,
                        "repetition": repetition,
                        "delay_seconds": delay,
                        "kind": kind,
                        "command": command,
                    }
                )
    (args.output / "schedule.json").write_text(json.dumps(schedule, indent=2) + "\n")
    for entry in schedule:
        print(entry["name"], flush=True)
        command = entry["command"]
        assert isinstance(command, list)
        with (args.output / "logs" / f"{entry['name']}.log").open("w") as log:
            subprocess.run(command, check=True, stdout=log, stderr=subprocess.STDOUT)
    subprocess.run(
        [
            sys.executable,
            str(Path(__file__).with_name("evidence.py")),
            "verify",
            str(args.prepared),
            str(args.output / "runs"),
            str(args.output / "correctness.json"),
        ],
        check=True,
    )


if __name__ == "__main__":
    main()
