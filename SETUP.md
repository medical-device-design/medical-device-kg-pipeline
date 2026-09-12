# Creating the repository — one-time setup

Everything here is done once. After this, work is normal git.

---

## 1. Create the organization

github.com → **+** → **New organization** → Free plan.

| Field | Value |
|---|---|
| Handle | `medical-device-design` |
| Display name | Medical Device Design Knowledge Graph — Hofstra University |
| Contact email | an address you will hold indefinitely |

Use an email that outlives the award. The organization is meant to survive individual
students, and eventually individual grants.

Add Marco as a second **Owner** immediately. An organization with one owner is not
meaningfully different from a personal account.

---

## 2. Transfer the existing repository in

Prabhjot does this — about two minutes, and it is the last thing he is strictly required for.

On `github.com/Prabhadeus/Proto-OKN` → **Settings** → scroll to **Danger Zone** →
**Transfer ownership** → new owner `medical-device-design` → new name `medical-device-kg`.

Transfer rather than fork or copy, because it preserves:

- the full commit history — which is evidence of work performed under a federal award
- issues and pull requests, including PR #3 on `clinicaltrials-harvester`
- automatic redirects, so the URL currently published on the FRINK registry keeps working

Afterwards, add Prabhjot, Mustafa, Adelyn and Ajay as members with **Write** access. He loses
nothing.

---

## 3. Immediate fixes after transfer

**Change the default branch to `main`.** Settings → Branches → default branch. Right now it
is `master`, which was last touched in March and describes a MAUDE-only pipeline of ~96,000
triples. That obsolete README is what anyone opening the repo sees — including reviewers at
the September showcase.

**Make the repository public.** Settings → General → Danger Zone → Change visibility. The
FRINK registry lists this repository as the graph's Homepage, and it currently returns 404 to
anyone not on the team. The data is FDA public record; there is nothing to protect.

**Drop in the scaffold files** from this directory: `README.md`, `PUBLISHING.md`,
`CITATION.cff`, `LICENSE`, `.gitignore`, `scripts/`.

**Delete `FDA_Parse_TPLC_Pipeline/rdf/vocab.py`.** It references an undefined `MED` symbol on
every line and would raise `NameError` if imported. It is a leftover from the earlier rdflib
design that `turtle_writer.py` replaced. Nothing imports it; it exists only to confuse whoever
reads the code next.

**Reconcile the branches.** `main` and `clinicaltrials-harvester` are both ahead of `master`
and behind each other. Merge forward so there is one line of development, and either merge or
close PR #3.

---

## 4. Local clone (WSL2)

```bash
cd ~
git clone https://github.com/medical-device-design/medical-device-kg.git
cd medical-device-kg
chmod +x scripts/*.sh
```

### Tools worth having

```bash
# Apache Jena — riot validates Turtle and counts triples.
# This is the safety net for the namespace migration; do not skip it.
sudo apt update && sudo apt install -y openjdk-17-jre-headless
curl -LO https://dlcdn.apache.org/jena/binaries/apache-jena-5.2.0.tar.gz
tar xzf apache-jena-5.2.0.tar.gz
echo 'export PATH="$HOME/apache-jena-5.2.0/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc
riot --version

# s5cmd — uploads to lakeFS
curl -LO https://github.com/peak/s5cmd/releases/latest/download/s5cmd_2.2.2_Linux-64bit.tar.gz
tar xzf s5cmd_2.2.2_Linux-64bit.tar.gz s5cmd
sudo mv s5cmd /usr/local/bin/
s5cmd version

# pySHACL — for the shapes file (audit item, not yet written)
pip install pyshacl --break-system-packages
```

Check the Jena and s5cmd release pages for current version numbers before pasting.

### Credentials

```bash
mkdir -p ~/.config/medical-device-kg
cat > ~/.config/medical-device-kg/lakefs.env <<'EOF'
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
LAKEFS_ENDPOINT=https://repository.okn.us/
LAKEFS_REPO=medical-device-kg
EOF
chmod 600 ~/.config/medical-device-kg/lakefs.env
```

Load before publishing:

```bash
set -a; . ~/.config/medical-device-kg/lakefs.env; set +a
```

Request the keys from the FRINK data team — `the OKN Slack (FRINK team)`, or DM on the OKN Slack. Name the
dataset (`medical-device-kg`). Being listed as a contact on the registry page does not grant
access; contacts are display metadata, not permissions.

---

## 5. Verify the setup end to end, before you need it

Do this well before 25 September, on a throwaway file. The point is to find out now whether
your credentials work and whether you understand the lakeFS flow — not during the migration.

```bash
mkdir -p /tmp/ttl-test
cat > /tmp/ttl-test/smoke.ttl <<'EOF'
@prefix ex: <https://bmedesign.org/medical-device-kg/ns/> .
ex:SmokeTest a ex:MedicalDevice .
EOF

riot --validate /tmp/ttl-test/smoke.ttl   # should print nothing and exit 0
riot --count    /tmp/ttl-test/smoke.ttl   # should report 1 triple
```

Then log in to https://repository.okn.us/ and confirm you can see the `medical-device-kg`
repository and its `develop` branch. **Do not upload the smoke test.** Seeing the repository
is the whole test.

---

## 6. What is still missing

The scaffold intentionally leaves gaps that correspond to open findings in the data-quality
audit. Each is a small, self-contained piece of work:

| Path | Content | Audit item |
|---|---|---|
| `ontology/shapes.ttl` | SHACL constraints | §4 |
| `queries/competency.md` | 15–20 competency questions covering every source | §2.1 |
| `queries/*.rq` | runnable example queries for the registry | §2.1 |
| `docs/data-dictionary.md` | what every class and property means | §2.3 |
| `docs/schema-diagram.md` | generated Mermaid diagram | §2.2 |

The competency questions are the most useful of these to write first. They close a gap the
guidelines state numerically, and good competency questions double as demo material.
