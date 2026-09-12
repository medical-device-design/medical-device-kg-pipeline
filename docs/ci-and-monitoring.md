# CI and the maintenance bot

This is **Layer 1** of the "AI teammate" plan: mechanical enforcement of the
converter standard and repo structure, running in GitHub with no server, and
collecting diagnostics on every run so troubleshooting is fast. Layer 2 (VM +
lakeFS health on `.83`, now live — see below) and Layer 3 (an LLM agent that
writes the human-facing guidance) build on the signals this layer produces.

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

## Branch protection (rulesets, `main` + `v*` tags)

Mechanical enforcement of the freeze and review rules, configured as repository
**rulesets** (Settings → Rules → Rulesets):

- **main protection** (target: default branch) — require a pull request with at
  least **1 approval**; require the **`checks`** status check to pass; require
  branches to be up to date before merging; block force pushes; restrict deletion.
  Repository admin is on the bypass list (the PI keeps an escape hatch).
- **tag protection (v\*)** (target: `v*` tags) — restrict creation, updates, and
  deletion, and block force pushes, since a `v*` tag deploys to the live endpoint
  via lakeFS. Repository admin bypass.

Both rulesets are set to **Active**. Note: GitHub does not *enforce* rulesets on a
private repository under the org's current (free) plan — enforcement turns on the
moment the repo is made public or the org is upgraded to GitHub Team. The rulesets
are already fully configured, so no rework is needed then.

## Layer 2 — VM + lakeFS health (live)

Cloud runners cannot see the VPN-locked VM or lakeFS, so Layer 2 runs *on* `.83`.
`scripts/vm_health_check.sh` produces a read-only report covering:

- **tooling** — java, python, `riot` (Jena), git, `s5cmd`, and whether `docker` is
  usable by the current user (expected: not, since `roche` is not in the docker group);
- **disk** — usage of `/srv/medical-device-kg` (WARN at 80%, FAIL at 90%) and the
  sizes of `repo/`, `downloads/`, `out/`, `nstest/`;
- **repo** — current branch and HEAD, whether it is behind/ahead of origin, and
  whether the working tree is clean;
- **structure lint** — runs `scripts/structure_lint.sh` (the same gate as Layer 1);
- **graph data on disk** — `riot --validate` + triple counts for `out/` and
  `nstest/`; `--full` also runs `verify_current_graph.sh` (the heavier arq pass);
- **lakeFS** — read-only reachability of `repository.okn.us` and whether publish
  credentials exist on the host;
- **mdkg-check** — outstanding items, if that helper is installed.

It writes a timestamped report to `$MDKG/health/`, refreshes `health/latest.txt`,
and keeps the last 20. It is strictly read-only: it never writes outside `health/`,
never touches lakeFS `main`/tags, and never starts or stops a service — safe for cron.

Run on demand: `bash scripts/vm_health_check.sh` (add `--full` for the deep graph pass).
Weekly schedule (Mondays 06:30 UTC, 30 min after the Layer 1 smoke) is installed with
`bash scripts/install_layer2_cron.sh`; cron output is appended to `$MDKG/health/cron.log`.

## Roadmap

- **Layer 3** — a scheduled Claude agent reads the Layer 1/2 signals and writes the
  human-facing digest and nudges, and answers "where does X go?". Read-only by
  default; every destructive or publish action is proposed for a human to approve —
  it never stops a service, deletes data, or creates a lakeFS tag on its own.
