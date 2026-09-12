# Structure completion runbook — .83

Everything needed to take `mdkg-check` to all-green. Each item says WHO runs it
and the EXACT command. Already done: shared `/srv/medical-device-kg` tree, `mdkg`
group, tools (git/curl/riot/lakectl), login banner, per-user pointers, and the
`mdkg-check` audit itself.

Run `mdkg-check` at any point to see current state.

---

## Item 1 — canonical code as a git clone under `repo/`   (Roche)

Two steps: get the rebuild code onto GitHub, then let the VM read the private repo.

### 1a. Push the rebuild code  (from your laptop — needs your GitHub login)
Clone the org repo, branch `rebuild-foundation`, copy the scaffold in, run the three
self-tests, commit, push, open a PR.

### 1b. Give the VM read access to the private repo
Deploy keys are currently **disabled at the org level**. Pick one:

**Option A — enable deploy keys (cleanest for a shared VM).**
Org Settings → Repository → set "Deploy keys" to allowed, then add this key
(already generated on .83, read-only) to the repo's Deploy keys:

    (public key is at /srv/medical-device-kg/.git-deploy-key.pub on the VM)

Then on .83:

    cd /srv/medical-device-kg
    rm -rf repo && git clone git@github.com-mdkg:medical-device-design/medical-device-kg-pipeline.git repo
    sudo chgrp -R mdkg repo

**Option B — clone once with your own GitHub login** (ties the clone to you, fine short-term):

    cd /srv/medical-device-kg && rm -rf repo
    git clone https://github.com/medical-device-design/medical-device-kg-pipeline.git repo
    # authenticate when prompted

Either way, `mdkg-check` item [2] then goes green.

---

## Item 2 — retire the stranded home-dir copies   (Roche + Prabhjot/Mustafa)

Now SAFE: the TTLs are backed up in Teams (`ttl-files`), on Prabhjot's USB, and in
lakeFS. The home copies are the students' newer local regeneration (divergent
`productcode` convention) — disposable, since the rebuild regenerates everything.

Confirm the off-VM backups first, then remove the duplicates:

    # each owner runs their own (or Roche with sudo, after a go-ahead):
    rm -rf /home/mustafa/Proto-OKN/FDA_Parse_TPLC_Pipeline/output/digest_ttl
    rm -rf /home/raham/FDA_Parse_TPLC_Pipeline           # partial copy
    # KEEP /home/<user>/...  until the GraphDB is relocated (item 4 reads from it)

Reclaims ~1.3 GB per copy. Do NOT delete admin's copy yet — the running GraphDB
bind-mounts from there.

---

## Item 3 — place the lakeFS credential   (Roche)

One command in your VM session (the assistant is blocked from writing secrets):

    sudo install -m 640 -g mdkg /dev/stdin /srv/medical-device-kg/.lakectl.yaml <<'EOF'
    credentials:
      access_key_id: <ASK YAPHET / ROTATE>
      secret_access_key: <ASK YAPHET / ROTATE>
    server:
      endpoint_url: https://repository.okn.us/api/v1
    EOF

Then `mdkg-check` item [5] goes green. (Consider asking Yaphet to rotate this key
once placed, since it has travelled by email.)

---

## Item 4 — relocate GraphDB out of admin's home   (Prabhjot)

It runs in Docker with its data bind-mounted from `/home/<user>/graphdb-deployment`
— which is lost the day that account is cleaned (exactly how the last GraphDB
died). Move the persistent data to `/srv` so it survives. See
`scripts/relocate_graphdb.sh` — it stops the container, moves the data, re-points
the mount, and restarts, with a verification step. Prabhjot should run it (it
briefly stops his live GraphDB) at a quiet time.

---

## When all four are done

    mdkg-check      # should print: All checks passed

At that point: code is versioned in GitHub and cloned on the VM, no data is
stranded in a home dir, credentials are in place at mode 640, and the triple
store survives an account cleanup. The structure is self-sustaining — the banner
and `mdkg-check` keep it that way.
