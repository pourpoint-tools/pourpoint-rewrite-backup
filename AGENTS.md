# Project Instructions

## Purpose

Pourpoint is a Python watershed-delineation engine consuming compiled HFX datasets.
The reader/runtime foundation supports functional hydrological operations and
portable QGIS delivery.
Use `../hfx/docs/site/index.md` as the canonical input contract. Keep source-fabric
normalization outside the engine rather than inventing a parallel HFX contract.

The Rust comparison baseline is `CooperBigFoot/pourpoint` commit
`60e3d6e383546ea6ed20e68fe4d77959c816a48c`. See README.md for the link and
inspection command. This repository does not contain that Git history.
Investigate old behavior rather than assuming every result is correct.

## Development guidelines

- Use `uv` for project dependencies and execution.
- Write strongly typed Python throughout, including internal code.
- Use Ruff for formatting and linting, and ty for type checking.
- Prefer suitable testing utilities, assertions, and fixtures provided by
  dependencies over hand-rolled equivalents. Add custom fixtures where
  project-specific behavior requires them.

## Public API docstrings

Use NumPy-style docstrings for public operations and user-facing returned types,
methods and attributes, wherever they are implemented. Let typed signatures carry
types and defaults. Explain meanings, constraints and interactions rather than
repeat the signature; use only useful sections and examples.

Document units, CRS, output contents, absent values, source versus derived
quantities, lifecycle and side effects where applicable. Explain stable failure
codes and distinguish valid empty results, target-local batch failures and fatal
configuration or integrity errors. Keep prose explicit and readable for humans
and LLMs; concision must not remove necessary qualifications. A short summary or
no prose is enough when the name and signature fully explain the interface.

The [Python API vision](planning/visions/2026-10-20-python-api-and-qgis-workflows.md)
selects the supported surface. Source docstrings supply the future curated
mkdocstrings reference: group core workflows and returned results separately from
advanced records, relationships, traversal, native raster access, metadata and
diagnostics. Do not document every internal helper or maintain a parallel API
catalogue. Keep docstrings accurate when interfaces change.
