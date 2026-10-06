# Project Instructions

## Purpose

Pourpoint is a Python watershed-delineation engine consuming compiled HFX datasets.
This is a fresh rewrite workspace; no engine behavior is implemented yet.
Use `../hfx/spec/HFX_SPEC.md` as the canonical input contract. Keep source-fabric
normalization outside the engine rather than inventing a parallel HFX contract.

The Rust comparison baseline is `CooperBigFoot/pourpoint` commit
`60e3d6e383546ea6ed20e68fe4d77959c816a48c`. See README.md for the link and
inspection command. This repository does not contain that Git history.
Investigate old behavior rather than assuming every result is correct.

## Python environment

Use `uv` exclusively for project dependencies and execution.

- Add dependencies: `uv add <package>`; development dependencies: `uv add --dev <package>`.
- Sync the environment: `uv sync`.
- Run commands: `uv run <command>`.
- Test: `uv run pytest`.
- Format: `uv run ruff format`.
- Lint: `uv run ruff check --fix`.
- Type-check: `uv run ty check src`.
- Build: `uv build`.

Python 3.13 is the development baseline. Commit `uv.lock` with dependency changes.
Do not add runtime dependencies speculatively.

## Library boundaries and types

Give each module a clear responsibility and pass only the dependencies its operations need, rather than broad configuration objects. Mathematical notation is optional when it clarifies a computation; module docstrings do not require mathematical signatures.

Resolve configuration, environment variables, paths, and resource wiring at explicit composition boundaries, including public library APIs as well as CLI entry points. Lower-level operations receive resolved dependencies instead of discovering application state themselves.

Parse external inputs at the boundary where they arrive, including provider responses received after API composition. Use domain types for identifiers, physical quantities, and configuration where invariants or units matter. Keep units explicit and preserve typed stage contracts. Do not wrap every value or bulk array; retain library carriers such as Polars frames and xarray datasets.

Represent named domain states with enums or literals, not ambiguous booleans. Incidental boolean flags do not need domain wrappers.

## Data fidelity and failures

Keep identifiers, units, coordinate reference systems, and unknowns explicit.
Parse HFX declarations according to the canonical specification. Do not substitute
defaults for required inputs or silently discard errors. Preserve useful context
when propagating failures. Engine failure policies will be designed with the
rewrite; do not impose the old engine's behavior or RivRetrieve's provider model.

## Public API docstrings

Use NumPy-style docstrings where public interfaces need explanation.
Apply this rule to public functions and user-facing returned types, methods,
and attributes, including those implemented under `_internal`.

Use judgement. If the name and signature fully explain an interface, no
docstring or prose summary is required. Do not add text or sections solely
for coverage or repeat information already clear from the signature.

Document what readers need to use and interpret the interface correctly:
non-obvious constraints, units, return contents, side effects, and failure
behavior. Include only applicable sections. A one-line summary is enough
when no further explanation is needed. Add examples when they clarify use.

Write for readers with hydrology knowledge and basic Python knowledge. Explain
unfamiliar software concepts where needed. Use concrete verbs, consistent terms,
and factual prose. Preserve qualifications and units. Avoid filler, promotional
claims, and repetition. Keep documentation accurate when changing an interface.

## Tests

Protect named behavior with an expectation that can detect the claimed defect,
at the simplest sufficient level. Run affected tests, fix change-related failures,
and rerun without step-by-step approval. Match broader checks to changed boundaries
and acceptance requirements. Use temporary cache and store roots. Shared setup
must not hide changed or corrupt inputs from validation. Prefer library-specific
assertions for arrays, datasets, and tables when those carriers are introduced.

Keep controlled data and credentials out of repositories, logs, and distributions.
Report unavailable required evidence as blocked rather than substituting weaker checks.

## Workflow

Use the installed PCE skills from `/Users/nicolaslazaro/Desktop/work/pce`:
`grill-me → to-vision → implement-vision`, or
`chart-program → grill-ticket → implement-vision → land-ticket`.
Do not copy skill definitions or create repository-local `.pce/` machinery.
Use discovery to settle outcome decisions before implementing engine behavior.
