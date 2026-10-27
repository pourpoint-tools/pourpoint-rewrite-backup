# Pourpoint

Pourpoint is a Python watershed-delineation engine consuming compiled
[HFX datasets](https://github.com/pourpoint-tools/hfx/blob/main/docs/site/index.md).
It selects upstream sub-basins, refines point watersheds on native D8 grids,
selects river networks and writes portable QGIS projects.

## Setup

Use Python 3.13 or newer and [uv](https://docs.astral.sh/uv/). In an authorized checkout:

```bash
uv sync
```

Start with [installation and setup](docs/site/getting-started.md),
[hydrological concepts](docs/site/concepts.md) and [operation conventions](docs/site/operations.md).
The [documentation source](docs/site/index.md) and
[build instructions](docs/building.md) include the curated, docstring-generated API reference.
The eventual site address is https://pourpoint-tools.github.io/; it is not publicly
published during private development.

## Development

```bash
uv sync --group dev
uv run python -m pytest
uv run ruff format --check
uv run ruff check
uv run ty check src
uv build
```

See [AGENTS.md](AGENTS.md) for conventions. The canonical input contract is the
[HFX specification](https://github.com/pourpoint-tools/hfx/blob/main/docs/site/index.md).

The Rust comparison baseline is
[CooperBigFoot/pourpoint at `60e3d6e383546ea6ed20e68fe4d77959c816a48c`](https://github.com/CooperBigFoot/pourpoint/tree/60e3d6e383546ea6ed20e68fe4d77959c816a48c).
Its history is not copied here. In a checkout of that repository, inspect it with
`git show 60e3d6e383546ea6ed20e68fe4d77959c816a48c:crates/core/src/engine.rs`.
Treat previous behavior as evidence to investigate, not a requirement.

## License

[MIT](LICENSE). Source-data terms remain separate; GRIT data carry CC BY-NC 4.0
terms, attribution and source caveats.
