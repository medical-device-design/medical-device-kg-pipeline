#!/bin/bash
# recall_weekly.sh -- weekly refresh + validation + monitoring of the recall layer on VM .83.
#
# Installed in <user>'s crontab on the build VM (Mondays 05:30):
#   30 5 * * 1  /srv/medical-device-kg/work/rebuild-stage3-sources/scripts/recall_weekly.sh
#
# 1. bulk-downloads both openFDA recall sources (enforcement + recall database)
# 2. fetches FDA recall pages only for recalls that still lack a class (1 request/s, cached)
# 3. converts all three to Turtle in $OUT (latest) and keeps a gzipped weekly snapshot
# 4. validates (riot on every file, SHACL on recalls + recall_class)
# 5. writes the monitor report ($MON/<date>/report.md) and appends $MON/history.csv
# Nothing is published to lakeFS or okn.us.
set -u
MDKG=/srv/medical-device-kg
WT=$MDKG/work/rebuild-stage3-sources
DL=$MDKG/work/downloads
OUT=$MDKG/work/out-recalls
MON=$MDKG/monitor/recalls
D=$(date +%F)
LOG=$MDKG/logs/recall-weekly-$D.log
export PATH=$MDKG/tools/apache-jena-5.2.0/bin:$HOME/.local/bin:$PATH
export JVM_ARGS="-Xmx2400m"
mkdir -p $OUT $MON $MON/snapshots
exec >> $LOG 2>&1
echo "== recall weekly start $(date -Is)"
if pgrep -f "run.py --sources (event|udi|tplc)" >/dev/null; then echo "a large build is running; skipping this week"; exit 0; fi
cd $WT
for s in recalls recall_db recall_class; do
  python3 pipeline/run.py --sources $s --stage fetch --downloads $DL --out $OUT 2>&1 | tail -3
  python3 pipeline/run.py --sources $s --stage transform --downloads $DL --out $OUT 2>&1 | tail -3
done
V=$MON/$D-validation.txt; : > $V
for f in recalls recall_db recall_class; do
  if riot --validate $OUT/$f.ttl >/dev/null 2>&1; then
    echo "$f.ttl: riot OK, $(riot --count $OUT/$f.ttl 2>&1 | grep -o 'Triples = [0-9,]*')" >> $V
  else echo "$f.ttl: riot FAIL" >> $V; fi
done
for f in recalls recall_class; do
  for sh in ontology/shapes/postmarket.shacl.ttl ontology/shapes/identity.shacl.ttl; do
    r=$(shacl validate --shapes $sh --data $OUT/$f.ttl 2>&1)
    if echo "$r" | grep -q "sh:conforms  *true"; then echo "$f.ttl vs $(basename $sh): conforms" >> $V
    else echo "$f.ttl vs $(basename $sh): violations=$(echo "$r" | grep -c sh:Violation)" >> $V; fi
  done
done
python3 scripts/recall_monitor.py --out $OUT --monitor $MON --validation $V --date $D
mkdir -p $MON/snapshots/$D
for f in recalls recall_db recall_class; do gzip -c $OUT/$f.ttl > $MON/snapshots/$D/$f.ttl.gz; done
# keep 12 weekly snapshots
ls -1d $MON/snapshots/*/ 2>/dev/null | sort | head -n -12 | xargs -r rm -rf
# copy the text reports to the vm-reports branch on GitHub (no-op without the monitor key)
bash $WT/scripts/publish_vm_reports.sh
echo "== recall weekly end $(date -Is)"
