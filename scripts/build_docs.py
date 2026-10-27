"""Assemble reader documentation from Pourpoint and one explicit HFX revision."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read_pin(path: Path) -> str:
    revision = path.read_text().strip()
    if re.fullmatch(r"[0-9a-f]{40}", revision) is None:
        raise ValueError("HFX revision must be a 40-character lowercase Git commit SHA")
    return revision


def stage_docs(pourpoint: Path, hfx: Path, destination: Path) -> None:
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(pourpoint / "docs/site", destination)
    shutil.copytree(hfx / "docs/site", destination / "hfx")


def revision(repository: Path) -> str:
    return subprocess.check_output(["git", "-C", str(repository), "rev-parse", "HEAD"], text=True).strip()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--hfx-source",
        type=Path,
        required=True,
        help="HFX checkout; its HEAD must match the pin unless checking a proposal",
    )
    parser.add_argument(
        "--proposed-hfx", action="store_true", help="Check proposed HFX content instead of the release pin"
    )
    args = parser.parse_args()
    hfx = args.hfx_source.resolve()
    hfx_revision = revision(hfx)
    pin = read_pin(ROOT / "docs/hfx-revision")
    if not args.proposed_hfx and hfx_revision != pin:
        parser.error(f"HFX checkout {hfx_revision} differs from pinned revision {pin}")
    output = ROOT / ".docs-build"
    stage_docs(ROOT, hfx, output / "docs")
    subprocess.run(
        [sys.executable, "-m", "mkdocs", "build", "--strict", "--config-file", str(ROOT / "mkdocs.yml")],
        cwd=ROOT,
        check=True,
    )
    (output / "site/source-revisions.json").write_text(
        json.dumps({"pourpoint": revision(ROOT), "hfx": hfx_revision}, indent=2) + "\n"
    )


if __name__ == "__main__":
    main()
