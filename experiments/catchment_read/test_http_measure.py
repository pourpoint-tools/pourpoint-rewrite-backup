import json
import subprocess
import sys
from pathlib import Path

import pyarrow as pa
from pyarrow import ipc

from http_measure import maximum_overlap


def test_overlap_sweeps_endpoints_not_completion_order() -> None:
    assert maximum_overlap([(3, 8), (0, 10), (1, 3), (4, 6)]) == 3
    assert maximum_overlap([(0, 1), (1, 2)]) == 1
    assert maximum_overlap([]) == 0


def test_worker_phase_accounting_and_witnesses(
    source: tuple[Path, pa.Table], tmp_path: Path
) -> None:

    root, table = source
    selections = tmp_path / "selections.json"
    selections.write_text(json.dumps({"workloads": {"single": [[3, 901, 3]]}}))
    output = tmp_path / "result.json"
    subprocess.run(
        [
            sys.executable,
            str(Path(__file__).with_name("http_measure.py")),
            "--data",
            str(root),
            "--selections",
            str(selections),
            "--workload",
            "single",
            "--output",
            str(output),
            "--witness-dir",
            str(tmp_path / "witnesses"),
            "--code-commit",
            "test",
            "--delay-seconds",
            "0",
        ],
        check=True,
        timeout=30,
    )
    result = json.loads(output.read_text())
    phases = result["transport"]["phases"]
    assert set(phases) == {"open", "prepare", "call-0", "call-1"}
    assert phases["open"]["head_requests"] > 0
    assert phases["prepare"]["get_requests"] > 0
    for phase in phases.values():
        assert phase["client_body_bytes"] == phase["server_payload_bytes"]
        assert phase["oversized_ranges"] == 0
    assert (
        phases["call-0"]["client_body_bytes"] == phases["call-1"]["client_body_bytes"]
    )
    for call in result["calls"]:
        actual = ipc.open_file(call["witnesses"][0]).read_all()
        assert actual.equals(table.take([3, 0, 3]), check_metadata=True)
