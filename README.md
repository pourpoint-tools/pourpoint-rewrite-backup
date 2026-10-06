# pourpoint

Private workspace for a fresh Python rewrite of pourpoint, a watershed
delineation engine consuming [HFX](https://github.com/CooperBigFoot/hfx)
datasets. The engine is not implemented yet. This repository starts with Python
development tooling and does not provide the released Rust engine's API.

## Development

Use Python 3.13 and [uv](https://docs.astral.sh/uv/):

```bash
uv sync
uv run pytest
uv run ruff format --check
uv run ruff check
uv run ty check src
uv build
```

See [AGENTS.md](AGENTS.md) for development conventions. The local package version
`0.0.0.dev0` is a setup placeholder, not a release plan. No package publishing or
CI is configured.

## Rust reference

The previous implementation remains in
[CooperBigFoot/pourpoint](https://github.com/CooperBigFoot/pourpoint).
Use commit [`60e3d6e383546ea6ed20e68fe4d77959c816a48c`](https://github.com/CooperBigFoot/pourpoint/tree/60e3d6e383546ea6ed20e68fe4d77959c816a48c)
as the stable comparison baseline. Its source, tests, and fixtures are evidence,
not a requirement to preserve every behavior or design choice.

This repository does not copy the Rust Git history. In a checkout of the old
repository, inspect a file with:

```bash
git show 60e3d6e383546ea6ed20e68fe4d77959c816a48c:crates/core/src/engine.rs
```

The canonical input contract is the
[HFX specification](https://github.com/CooperBigFoot/hfx/blob/main/spec/HFX_SPEC.md),
also available locally at `../hfx/spec/HFX_SPEC.md` when using sibling checkouts.

## License

[MIT](LICENSE).
