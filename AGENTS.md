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

## Development guidelines

- Use `uv` for project dependencies and execution.
- Write strongly typed Python throughout, including internal code.
- Use Ruff for formatting and linting, and ty for type checking.
- Prefer suitable testing utilities, assertions, and fixtures provided by
  dependencies over hand-rolled equivalents. Add custom fixtures where
  project-specific behavior requires them.
