#!/usr/bin/env bash
# relocate_graphdb.sh — move the GraphDB persistent data out of a personal home
# directory into the shared /srv area, so it survives an account cleanup.
#
# WHY: the GraphDB on .83 runs in Docker with its data bind-mounted from
# /home/<user>/graphdb-deployment. When that account is cleaned, the store is
# gone — which is exactly how the previous GraphDB died. Moving the data to
# /srv/medical-device-kg/graphdb fixes that.
#
# RUN THIS as the person who owns the deployment (admin/Prabhjot), at a quiet
# time. It BRIEFLY STOPS the running GraphDB. Read it before running.
#
#   bash relocate_graphdb.sh
#
set -euo pipefail

OLD=/home/<user>/graphdb-deployment
NEW=/srv/medical-device-kg/graphdb
CONTAINER="${GRAPHDB_CONTAINER:-graphdb}"   # override if your container has another name

echo "==> Preconditions"
command -v docker >/dev/null || { echo "docker not found" >&2; exit 1; }
[ -d "$OLD" ] || { echo "source $OLD not found — is this the right host/user?" >&2; exit 1; }

# find the actual container name if the default is wrong
if ! docker ps -a --format '{{.Names}}' | grep -qx "$CONTAINER"; then
  echo "    container '$CONTAINER' not found. Running GraphDB containers:"
  docker ps --filter "ancestor=" --format '    {{.Names}}\t{{.Image}}' | grep -i graphdb || true
  docker ps -a --format '{{.Names}}\t{{.Image}}' | grep -i graphdb || true
  echo "    Re-run with:  GRAPHDB_CONTAINER=<name> bash $0" >&2
  exit 2
fi

echo "==> 1. Stop the container (GraphDB goes offline here)"
docker stop "$CONTAINER"

echo "==> 2. Move the data (rsync, keeps ownership/perms, then verify)"
sudo mkdir -p "$NEW"
sudo rsync -aH --info=progress2 "$OLD"/ "$NEW"/
# integrity: compare file counts and byte totals
oc=$(sudo find "$OLD" -type f | wc -l); nc=$(sudo find "$NEW" -type f | wc -l)
ob=$(sudo du -sb "$OLD" | cut -f1); nb=$(sudo du -sb "$NEW" | cut -f1)
echo "    old: $oc files / $ob bytes    new: $nc files / $nb bytes"
if [ "$oc" != "$nc" ] || [ "$ob" != "$nb" ]; then
  echo "    MISMATCH — do not proceed. The old data is untouched at $OLD; restart with:" >&2
  echo "        docker start $CONTAINER" >&2
  exit 3
fi
sudo chgrp -R mdkg "$NEW" || true

echo "==> 3. Re-point the container at the new location"
cat <<EOF

    MANUAL STEP — update the bind mount, then recreate the container.

    If you used 'docker run', recreate it with the SAME flags but swap the
    volume path:  -v $NEW/home:/opt/graphdb/home   (was $OLD/home:...)

    If you used docker-compose, edit the compose file's volumes: line from
        $OLD/home:/opt/graphdb/home
    to
        $NEW/home:/opt/graphdb/home
    then:  docker compose up -d

    The script does NOT guess your run command — recreating with the wrong
    flags would misconfigure GraphDB. Do this step by hand.

EOF
read -r -p "Have you recreated the container pointing at $NEW ? [y/N] " ans
[ "$ans" = y ] || { echo "Left stopped. Old data still at $OLD. Finish the mount edit, then re-run from step 3."; exit 0; }

echo "==> 4. Verify it is serving again"
sleep 8
if curl -sf http://localhost:7200/rest/repositories >/dev/null; then
  echo "    GraphDB is up on :7200 and answering."
else
  echo "    GraphDB not responding yet — check 'docker logs $CONTAINER'." >&2
fi

echo
echo "==> DONE. Old data still at $OLD as a safety copy."
echo "    Remove it only after confirming the relocated store works AND a backup"
echo "    exists (Teams / USB / lakeFS):   sudo rm -rf $OLD"
echo "    Then run:  mdkg-check"
