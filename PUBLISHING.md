# Publishing the Medical Device Design Knowledge Graph

How this graph reaches `https://apps.okn.us/medical-device-kg/sparql`.

Nothing in this process goes through GitHub. FRINK does not read this repository — it is
listed on the registry page as "Homepage" and that is a hyperlink, nothing more. The data
travels through **lakeFS**.

---

## The three systems

| System | Holds | Controlled by |
|---|---|---|
| **lakeFS** — `https://repository.okn.us/` | The RDF data and its version history | FRINK / RENCI; per-person credentials |
| **`frink-okn/okn-registry`** (GitHub) | Registry metadata only — title, description, contacts, homepage | FRINK, via pull request |
| **This repository** | Pipeline code, ontology, queries, docs | Us |

---

## Getting credentials

lakeFS access is an **Access Key ID** and a **Secret Access Key**, issued per person.

Request from **the FRINK data team** — `the OKN Slack (FRINK team)`, or DM on the OKN Slack. State the
dataset name (`medical-device-kg`).

> Being listed as a contact on the registry page does **not** grant access. The contacts
> field is display metadata in a YAML file; it is not an access control list. Credentials
> must be requested explicitly.

**More than one person on the team must hold credentials.** If only one person can publish,
the project cannot publish when that person is unavailable.

Store keys in `~/.config/medical-device-kg/lakefs.env`, never in this repository.
`.gitignore` excludes `*.env`, but do not rely on that alone.

---

## The publication workflow

lakeFS uses a branch model deliberately similar to Git, but the branches mean different things.

```
develop  ──►  main  ──►  stable_vX_X_X  ──►  tag  ──►  LIVE
 upload      merge       auto-created      auto      deployed to
  here       here        by conversion    created    Fabric servers
```

### 1. Upload to `develop`

Files under 1 GB can go through the web UI at `https://repository.okn.us/` — but note the
**Path** field does not accept a local path; you must drag and drop, one file at a time.

For anything larger, use `s5cmd` with **no parallelization** (FRINK asks for this so their
server is not overwhelmed):

```bash
./scripts/publish_to_lakefs.sh path/to/ttl-directory
```

### 2. Commit

**The upload does not exist until you commit it.** In the lakeFS UI, open the
**Uncommitted Changes** tab and click **Commit Changes**. Write a message that says who you
are and what changed — multiple people upload to this repository.

### 3. Merge `develop` → `main`

This is the step that triggers automatic conversion to HDT, the format the Fabric query
servers use.

**If conversion fails, it fails here, and the live endpoint is untouched.** This is the
safety property of the whole process: a bad upload cannot break the running graph.

### 4. Wait for the `stable_vX_X_X` branch

Automation creates it from the converted data. Naming follows `stable_v0_90_1`.

### 5. Confirm the tag

The stable branch is tagged (`v0.90.1`). **The tag is what deploys.** No tag, no live update —
if the endpoint has not changed, check that the tag exists before assuming the upload failed.

---

## Supported input formats

`.rdf` `.xml` `.ttl` `.nt` `.nq` `.jsonld` `.json` `.rj` `.trig` `.trix` `.n3`

We publish `.ttl`. **File extensions must match actual content** — the automation is
sensitive to this and will fail on a mislabelled file.

---

## Rollback

lakeFS is version-controlled. The previous state remains on `main` and on its tag. To roll
back, re-merge the earlier commit and let it retag. You do not need a local backup to recover,
though keeping one costs nothing.

---

## Traceability — linking a published graph to the code that built it

Git and lakeFS are separate systems. The link between them is carried **inside the data**,
using provenance properties already declared in `ontology/ontology.ttl`:

| Property | Value to write |
|---|---|
| `ex:extractionVersion` | the **git commit SHA** of this repository at build time |
| `ex:extractedAt` | build timestamp |
| `ex:retrievedAt` | per-record retrieval timestamp |
| `ex:sourceRecordId` | the original FDA identifier |
| `ex:sourceUrl` | the FDA URL the record came from |

With the commit SHA embedded, any triple in the published graph can be traced to the exact
code state that produced it. Capture it at build time with:

```bash
git rev-parse --short HEAD
```

This is what makes the graph *reproducible* rather than merely *published*, and it is worth
saying out loud at review — most teams cannot answer "which code made this triple?"

---

## Where the pipeline should run

Not in GitHub Actions. A full 18-source openFDA harvest with date-range chunking around the
25,000-record skip ceiling will exceed job time limits and puts avoidable load on the API.
CI in this repository is for validating the ontology and queries, which are small.

**Do not run production builds from a personal laptop.** The 17.7M-triple graph currently
published was built that way, and the generated files consequently existed in exactly one
place, on one machine, belonging to one student.

### The Hofstra VMs

Two Ubuntu VMs are provisioned through the DeMatteis School, administered by Alexander
Rosenberg (`Alexander.J.Rosenberg@hofstra.edu`, x4347). Both are behind the CS VPN
(`cs.hofstra.edu/csvpn`, OpenVPN 2.4.12) and manageable via vSphere at
`https://vc11.cs.hofstra.edu/`.

Assignment confirmed by Roche to the team on 30 June 2026:

| VM | IP | Project | Storage |
|---|---|---|---|
| `r25-vm` | <r25-vm-ip> | **NIH R25** — bmedesign.org (Ajay) | original allocation |
| `build-vm` | <build-vm-ip> | **NSF subaward** — the KG (Prabhjot, Mustafa, Raham) | **185 GB / 172 GiB free** |

**`build-vm` is the build machine.** Alex expanded it on 29 June 2026 in response
to Prabhjot's request for 150–200 GB to hold the full FDA extraction plus intermediate JSON.
Disk is no longer a constraint. RAM and vCPU are still unconfirmed — worth asking, since the
original VM was provisioned at 4 GB / 2 vCPU for a 96k-triple graph.

**These VMs are not the published endpoint.** They run GraphDB for the team's own querying,
VPN-restricted. `apps.okn.us/medical-device-kg/sparql` is served by FRINK from lakeFS. The
two are independent copies and can drift apart; do not assume a query answered on the VM
would return the same result publicly.

### Reaching the VMs from Windows

Checked on Roche's machine 2026-09-07:

- **OpenVPN is not installed.** This is the only blocker. Install **OpenVPN GUI 2.4.12**
  specifically — Alex is explicit about the version — and obtain a VPN profile per
  `cs.hofstra.edu/csvpn` (step 9 on Windows). Use your own Hofstra credentials; the h-numbers
  quoted in the provisioning emails belong to individual students.
- **SSH from PowerShell, not WSL.** Windows OpenSSH is present at
  `C:\WINDOWS\System32\OpenSSH\ssh.exe`. WSL2 here runs in NAT mode (`.wslconfig` sets memory
  and processors but not `networkingMode`), so it will **not** inherit VPN routes and SSH from
  inside WSL will fail even with the tunnel up. Adding `networkingMode=mirrored` under
  `[wsl2]` fixes this on Windows 11; it requires `wsl --shutdown` to take effect.
- **Set up key-based auth once.** `ssh-keygen -t ed25519`, then append the public key to
  `~/.ssh/authorized_keys` on each VM. After that, no password prompts.

### Security note

The provisioning emails circulated default SSH passwords for both VMs in plaintext. Alex's
instructions require changing them at first login. **Confirm both were in fact rotated**, and
do not forward those PDFs further.
