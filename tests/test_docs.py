"""Shared documentation assembly excludes non-reader repository material."""

from pathlib import Path

import pytest

from scripts.build_docs import read_pin, stage_docs


def test_pin_requires_exact_revision(tmp_path: Path) -> None:
    pin = tmp_path / "revision"
    pin.write_text("main\n")
    with pytest.raises(ValueError, match="40-character"):
        read_pin(pin)
    pin.write_text("a" * 40 + "\n")
    assert read_pin(pin) == "a" * 40


def test_stage_only_reader_sources(tmp_path: Path) -> None:
    pourpoint = tmp_path / "pourpoint"
    hfx = tmp_path / "hfx"
    for root in (pourpoint, hfx):
        (root / "docs/site").mkdir(parents=True)
        (root / "docs/site/index.md").write_text(root.name)
        (root / "docs/private.md").write_text("not published")
    destination = tmp_path / "staged"
    destination.mkdir()
    (destination / "stale.md").write_text("old output")
    stage_docs(pourpoint, hfx, destination)
    assert {str(p.relative_to(destination)) for p in destination.rglob("*.md")} == {"index.md", "hfx/index.md"}
    assert (destination / "hfx/index.md").read_text() == "hfx"


@pytest.mark.parametrize(
    "event,gate,expected",
    [
        ("release", "true", True),
        ("release", "", False),
        ("release", "false", False),
        ("push", "true", False),
        ("pull_request", "true", False),
    ],
)
def test_publication_requires_release_and_explicit_gate(event: str, gate: str, expected: bool) -> None:
    from scripts.publish_docs import publication_enabled

    assert publication_enabled(event, gate) is expected


def test_private_release_never_pushes_pages(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from scripts import publish_docs

    output = tmp_path / ".docs-build/site"
    output.mkdir(parents=True)
    (output / "index.html").write_text("reader docs")
    repository = tmp_path / "generated"
    repository.mkdir()
    (repository / ".git").mkdir()
    (repository / "old.html").write_text("stale")
    commands: list[list[str]] = []

    def record(command: list[str], *, check: bool) -> None:
        assert check
        commands.append(command)

    monkeypatch.setattr(publish_docs, "ROOT", tmp_path)
    monkeypatch.setattr(publish_docs.subprocess, "run", record)
    monkeypatch.setattr("sys.argv", ["publish_docs", "--repository", str(repository)])
    monkeypatch.setenv("GITHUB_EVENT_NAME", "release")
    monkeypatch.delenv("DOCS_PUBLICATION_ENABLED", raising=False)
    publish_docs.main()
    pushes = [command[-2:] for command in commands if "push" in command]
    assert pushes == [["origin", "HEAD:main"]]
    assert (repository / "index.html").read_text() == "reader docs"
    assert (repository / ".nojekyll").is_file()
    assert not (repository / "old.html").exists()
