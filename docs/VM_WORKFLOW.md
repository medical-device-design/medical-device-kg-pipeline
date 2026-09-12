# Medical Device KG — Standard Workflow on the VM (.83)

**Read this first. It replaces "work in your home directory."**

Everyone on the project now works in **one shared place**, under version control,
publishing through **one documented path**. No more three copies in three home
directories. No more files that vanish when someone's account is cleaned up.

Server: `biomed-dev-graphdb-2` = **10.22.12.83** (SSH, Hofstra VPN required)
Project root: **`/srv/medical-device-kg`** — call it `$MDKG` (set for you automatically)

---

## The one-time setup (each person, once)

1. **Log in** over the VPN: `ssh <your-username>@10.22.12.83`
2. **Confirm you're in the group** (gives you write access to the shared dir):
   ```bash
   groups | tr ' ' '\n' | grep mdkg    # should print: mdkg
   ```
   If it doesn't, log out and back in once (group membership needs a fresh login).
3. That's it. `$MDKG`, the tools, and a group-friendly `umask` are set for you by
   `/etc/profile.d/mdkg.sh` at login.

---

## The layout — what goes where

```
/srv/medical-device-kg/          ($MDKG)
├── repo/         the git clone of the pipeline code. THE ONLY place code lives.
├── tools/        riot (Jena) + lakectl, shared. Already on your PATH.
├── downloads/    raw FDA bulk files. Big, regenerable, NEVER committed to git.
├── out/          generated .ttl files. Regenerable. NEVER committed to git.
├── logs/         run logs.
└── .lakectl.yaml lakeFS credentials (publishing). Do not copy this elsewhere.
```

Two rules that keep it clean:
- **Code lives in `repo/` and only in `repo/`.** It is under git. Everything else is
  data — regenerable, and deliberately kept out of git.
- **Never put project work back in your home directory.** Home is for your dotfiles,
  nothing else now.

---

## The daily workflow

### 1. Get the latest code
```bash
cd $MDKG/repo
git pull
```

### 2. Fetch source data (only when refreshing a source)
```bash
# bulk download — NOT the API. The API caps at 25,000 records and silently truncates.
cd $MDKG
python3 repo/pipeline/sources/<source>.py fetch --dest downloads/
```

### 3. Transform to Turtle
```bash
python3 repo/pipeline/sources/<source>.py transform \
        --input downloads/ --output out/<source>.ttl
```

### 4. VALIDATE before you even think about publishing
```bash
# a) is it syntactically valid RDF?  (streams — handles any file size)
riot --validate out/<source>.ttl

# b) does it obey our SHACL shapes?
riot --validate --shapes repo/ontology/shapes/<source>.shacl out/<source>.ttl

# c) do the cross-file joins connect? (catches orphan nodes)
bash repo/scripts/check_joins.sh out/
```
If any of these fail, STOP. Do not publish. Fix it first.

### 5. Publish to lakeFS  (this is the ONLY route to FRINK)
```bash
export LAKECTL="lakectl --config $MDKG/.lakectl.yaml"

# work on a dated branch, never straight onto main
$LAKECTL branch create lakefs://medical-device-kg/refresh-$(date +%F) \
         --source lakefs://medical-device-kg/main

# upload only the files you changed
$LAKECTL fs upload lakefs://medical-device-kg/refresh-$(date +%F)/<source>.ttl \
         --source out/<source>.ttl

# commit with a real message (source, record count, date)
$LAKECTL commit lakefs://medical-device-kg/refresh-$(date +%F) \
         -m "Refresh <source>: <N> records, fetched $(date +%F)"

# merge into main once it's reviewed
$LAKECTL merge lakefs://medical-device-kg/refresh-$(date +%F) \
         lakefs://medical-device-kg/main
```

### 6. Deployment is triggered by a TAG — and a human does it
After the merge, the **FRINK Landing Zone bot** converts the upload to HDT and emails
the team. Only then does someone **tag** the release (`v0.MINOR.PATCH`), which is what
actually deploys to the query servers.

**Do NOT create tags casually.** A tag publishes to the live FRINK endpoint. Until the
namespace migration and rebuild are finished (after 25 September), **no new tags at
all** — ask Roche first, every time.

---

## Hard rules (the ones that caused problems before)

1. **Bulk downloads only.** The openFDA API stops at 25,000 records per query and
   returns no error when it truncates. That is why MAUDE sat at 0.06% coverage. Always
   use `download.json` bulk files.
2. **Validate before publish.** Every file passes riot + SHACL + join check first.
3. **`main` and tags are frozen until after 25 September.** Work on `refresh-*` branches.
4. **No project data in home directories.** It is not backed up there and it is lost
   when an account is cleaned. `$MDKG` is the home for all of it.
5. **Raw data is disposable, code is precious.** `downloads/` and `out/` can always be
   regenerated. The value is the code in `repo/` (under git) and the published graph in
   lakeFS. Back those up; ignore the rest.

---

## Where the real backup is

The published TTLs live in **lakeFS** (`repository.okn.us`), with full version history
and tags `v0.0.1`–`v0.0.4`. That — not any VM, not any laptop — is the source of truth.
If this VM died tomorrow, nothing published would be lost. Keep it that way: the moment
work matters, it belongs in git (code) or lakeFS (data), not only on the VM.
