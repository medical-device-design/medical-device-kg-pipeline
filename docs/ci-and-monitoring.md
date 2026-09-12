# CI and the maintenance bot

This is **Layer 1** of the "AI teammate" plan: mechanical enforcement of the
converter standard and repo structure, running in GitHub with no server, and
collecting diagnostics on every run so troubleshooting is fast. Layers 2 (VM +
lakeFS health from a runner on `.83`) and 3 (an LLM agent that writes the
human-facing guidance) build on the signals this layer produces.

## What runs (`.github/workflows/validate.yml`)

**`checks` — every pull request and push to `main`** (no FDA data needed):
- Python self-tests for `pipeline/iri.py`, `common.py`, `distribution_parser.py`
  (the IRI-minting and typed-literal contracts). A drift here fails the build.
- `riot --validate` on `ontology/*.ttl` and `ontology/shapes/*.ttl`.
- `scripts/structure_lint.sh` — no committed secrets, no data TTL outside
  `ontology/`, the lakeFS snapshot stays untracked, IRIs minted only in `iri.py`
  (warn), required files present.

**`smoke` — weekly (Mondays 06:00 UTC) and on manual dispatch**:
- `fetch_udi.py` (1 partition) → `udi_to_rdf.py` (500 records) → `scripts/validate.sh`
  (riot + SHACL + join check + competency questions). A real end-to-end run on
  fresh openFDA data, including the zero-orphan and zero-provenance guards.

Run the smoke on demand: Actions → **validate** → **Run workflow** → tick
"Also run the UDI smoke".

## Diagnostics it collects (for troubleshooting)

Every job writes a `_diag/` bundle and uploads it as a run artifact
(**Actions → the run → Artifacts**), kept 30 days:

- `env.txt` — timestamp, commit SHA, event, and tool versions (python, java, riot).
- `selftests.log`, `riot-ontology.log`, `structure-lint.log` — full step output.
- smoke also adds `fetch.log`, `transform.log`, `validate.log`, `triple-counts.txt`,
  and the generated `out/udi-smoke.ttl` sample.

When something breaks: open the failed run, read the step log inline, and download
`diagnostics-*` for the full bundle + the exact sample that failed — enough to
reproduce and fix without re-running blind. (Full step logs are also retained by
GitHub under the run itself.)

## Branch protection to turn on (Settings → Branches → Add rule, `main`)

Mechanical enforcement of the freeze and review rules:
- **Require a pull request before merging** (+ at least 1 approval).
- **Require status checks to pass** → select the **`checks`** job.
- **Require branches to be up to date before merging.**
- Add a **tag protection rule** (Settings → Tags) for `v*` so tags — which deploy
  to the live endpoint via lakeFS — can't be created casually.

These are the toggles that turn "please follow the standard" into "the standard is
enforced."

## Roadmap

- **Layer 2** — a self-hosted Actions runner (or cron) on `.83` runs `mdkg-check`,
  `verify_current_graph.sh`, disk/structure checks, and `lakectl` queries, posting
  a weekly health report. Reaches the VPN-locked VM and lakeFS that cloud runners
  cannot.
- **Layer 3** — a scheduled Claude agent reads the Layer 1/2 signals and writes the
  human-facing digest and nudges, and answers "where does X go?". Read-only by
  default; every destructive or publish action is proposed for a human to approve —
  it never stops a service, deletes data, or creates a lakeFS tag on its own.
