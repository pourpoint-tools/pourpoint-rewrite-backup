# Building documentation

Pourpoint owns `mkdocs.yml` and the shared assembly script. Only `docs/site/` in
each source repository is staged; verification reports, planning and receipts are
not published. HFX content is not maintained as a second copy here.

Install the locked documentation tools with `uv sync --frozen --group docs`.
Provide an authorized HFX checkout at the exact commit in `docs/hfx-revision`, then:

```bash
uv run --frozen --group docs python scripts/build_docs.py --hfx-source /path/to/hfx
```

The command rejects a different HFX HEAD, builds with strict link/reference
validation, and writes `.docs-build/site/source-revisions.json` with both exact
source commits. Use clean checkouts for reproducible release builds. The output
is disposable; never commit `.docs-build/`.

When reviewing proposed HFX changes, use the same command with `--proposed-hfx`.
This explicitly builds the supplied HFX checkout rather than the pin and does not
change `docs/hfx-revision`. Advance that file only to an approved exact HFX commit
when selecting content for a subsequent Pourpoint release.

## CI and repository access

Pourpoint pushes and pull requests check the combined pinned build without
publishing. HFX checks call the same script against their proposed source revision.
Private cross-repository access requires dedicated read-only credentials:
`HFX_DOCS_READ_TOKEN` in Pourpoint reads HFX; `POURPOINT_DOCS_READ_TOKEN` in HFX reads
Pourpoint. `DOCS_SITE_WRITE_TOKEN` in Pourpoint writes only the private generated-site
repository. These interfaces require repository-scoped credentials, not broad account tokens.
Credential provisioning is pending maintainer authorization; cross-repository CI
and release writes are not operational until access is configured.
No secrets belong in documentation sources or generated output.

## Release output and future public publication

Only `release.published` in Pourpoint rebuilds the release tag with its pinned HFX
revision and retains a private Actions artifact and generated output on `main` in
`pourpoint-tools/pourpoint-tools.github.io`. HFX events never publish. The generated
repository must have an initialized `main` branch before the first release.
No release is manufactured to test this workflow.

Public Pages is **not configured** during development. `DOCS_PUBLICATION_ENABLED`
is unset (off) by default. The release script updates `gh-pages` only when that
Pourpoint repository variable is exactly `true` and the event is a release.
Normal pushes and checks cannot update the publication branch. `.nojekyll` keeps
Pages from interpreting the generated Material site as Jekyll source.

After a separate explicit maintainer decision, configure the generated repository's
Pages source to branch `gh-pages`, root directory, and enable the variable. That
branch is created by the next published Pourpoint release, not by an ordinary
build. GitHub Pages account/plan availability and repository visibility must be
checked then; private source repositories do not make a Pages site private.
Do not enable Pages, change visibility or set this variable merely to test builds.
The eventual address is https://pourpoint-tools.github.io/ with no custom domain.

The publication guard tests exercise the off-by-default gate without pushing any
output or contacting Pages. A public deployment is intentionally not validated
while publication remains prohibited.
