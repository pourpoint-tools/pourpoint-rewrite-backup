"""Retain release documentation privately; update Pages only with explicit approval."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def publication_enabled(event: str, gate: str) -> bool:
    return event == "release" and gate == "true"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, required=True)
    args = parser.parse_args()
    if os.environ.get("GITHUB_EVENT_NAME") != "release":
        parser.error("Only a published release workflow may retain or publish documentation")
    repository = args.repository.resolve()
    for path in repository.iterdir():
        if path.name == ".git":
            continue
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
    shutil.copytree(ROOT / ".docs-build/site", repository, dirs_exist_ok=True)
    (repository / ".nojekyll").touch()

    def git(*arguments: str) -> None:
        subprocess.run(["git", "-C", str(repository), *arguments], check=True)

    git("config", "user.name", "github-actions[bot]")
    git("config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
    git("add", "--all")
    git("commit", "--allow-empty", "-m", "Update release documentation")
    git("push", "origin", "HEAD:main")
    if publication_enabled(os.environ["GITHUB_EVENT_NAME"], os.environ.get("DOCS_PUBLICATION_ENABLED", "")):
        git("push", "origin", "HEAD:gh-pages")


if __name__ == "__main__":
    main()
