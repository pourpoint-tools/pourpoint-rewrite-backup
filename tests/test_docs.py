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
        (root / "docs/site/index.md").write_text("old overview")
        (root / "README.md").write_text(f"# {root.name}\n![Map](assets/map.png)\n[Licence](LICENSE)\n")
        (root / "assets").mkdir()
        (root / "assets/map.png").write_bytes(b"map image")
        (root / "assets/unpublished.svg").write_text("not referenced")
        (root / "docs/site/details.md").write_text("detailed documentation")
        (root / "docs/private.md").write_text("not published")
    destination = tmp_path / "staged"
    destination.mkdir()
    (destination / "stale.md").write_text("old output")
    stage_docs(pourpoint, hfx, destination)
    assert {str(p.relative_to(destination)) for p in destination.rglob("*.md")} == {
        "index.md",
        "details.md",
        "hfx/index.md",
        "hfx/details.md",
    }
    for name, staged in (("pourpoint", destination), ("hfx", destination / "hfx")):
        assert (staged / "index.md").read_text() == (
            f"# {name}\n![Map](assets/map.png)\n"
            f"[Licence](https://github.com/pourpoint-tools/{name}/blob/main/LICENSE)\n"
        )
        assert (staged / "assets/map.png").read_bytes() == b"map image"
        assert not (staged / "assets/unpublished.svg").exists()


def test_readme_documentation_links_stay_in_the_built_site(tmp_path: Path) -> None:
    from scripts.build_docs import stage_readme

    repository = tmp_path / "source"
    repository.mkdir()
    destination = tmp_path / "staged"
    destination.mkdir()
    (repository / "README.md").write_text(
        "[Concepts](https://pourpoint-tools.github.io/concepts/#refinement)\n"
        "[Schemas](docs/site/vector-data.md#fields)\n"
        "[Guide](docs/building.md)\n"
        "[Paper](https://doi.org/example)\n"
    )
    stage_readme(repository, destination, "hfx")
    assert (destination / "index.md").read_text() == (
        "[Concepts](../concepts.md#refinement)\n"
        "[Schemas](vector-data.md#fields)\n"
        "[Guide](https://github.com/pourpoint-tools/hfx/blob/main/docs/building.md)\n"
        "[Paper](https://doi.org/example)\n"
    )


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
