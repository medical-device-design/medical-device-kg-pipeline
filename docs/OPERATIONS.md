# Operations: scheduled monitoring and refresh on the build VM

Since November 15, 2026 (v0.0.7). The scripts are in `scripts/schedule/` and run from the worktree
`/srv/medical-device-kg/work/v007` (override with `MDKG_WT`). Reports go to `/srv/medical-device-kg/monitor/`.
No scheduled job uploads to lakeFS, merges, tags or deletes data; releases stay a PI decision.

## Jobs (user crontab of `roche`, installed with `bash scripts/schedule/install_cron.sh`)

| when (America/New_York) | script | what it does | report |
|---|---|---|---|
| daily 06:40 | `daily_health.sh` | vm-status; free disk (alert below 130 GB); JVMs (ours, the other project's over 4 GB, the two Fuseki servers); lakeFS release watcher (crontab line and age of `logs/lakefs-watch.log`); 510(k) PDF crawl and its finalize; last weekly and monthly reports; one request to each source endpoint (status code and time only), including the radiation corrective-action search `cfPCD_RH/rh_res.cfm` (history in `monitor/radiation_corrective_actions.tsv`); retention candidates | `monitor/latest.md`, `monitor/daily/YYYY-MM-DD.md`, `monitor/alerts.log` |
| Saturday 01:10 | `weekly_fetch.sh` | fetch of the fast-changing sources (openFDA recalls, recall_db, k510, pma, registrationlisting; MAUDE bulk only when openFDA shows a new export date and the disk allows; recall_class, denovo; warning letters, safety communications, shortages; the four Data Dashboard endpoints) into `work/weekly-YYYY-MM-DD/`, transform without riot, change detection (`compare_snapshots.py`: records, file SHA-256, openFDA export dates) against the previous weekly folder or the release snapshot `work/out-2026-11-04` | `work/weekly-YYYY-MM-DD/REPORT.md`, `monitor/weekly/YYYY-MM-DD.md` |
| day 3 of the month 02:20 | `monthly_refresh.sh` | full refresh into `work/out-YYYY-MM-DD` (`snapshot_refresh.sh`: every source's own fetch + transform, MAUDE scrub and leak check, derived views), the 510(k) predicate and HUD designation transforms, then `fulltest.sh` (fallback files, tdb2.xloader into `work/tdb-YYYY-MM-DD`, query batteries 1-7) and a release-candidate report | `monitor/monthly/YYYY-MM-DD.md`, `work/fulltest-YYYY-MM-DD/RC_REPORT.md` |
| every 30 min, 00:00-08:59 and 12:00-23:59 | `k510_crawl_watch.sh` | status of the 510(k) summary PDF crawl with a disk projection; when the crawl has completed, starts `k510_pred_finalize.sh final` once | `monitor/k510_crawl_status.md`, `monitor/k510_predicates_finalize.md` |

The four older entries (Monday 05:30 recall weekly, 06:30 health check, 07:15 report publish, hourly :17 lakeFS
release watcher) are kept unchanged; `install_cron.sh` replaces only lines tagged `# mdkg-schedule:` and saves
the previous crontab to `monitor/crontab.bak-<time>`. `install_cron.sh --print` shows the result without
installing it.

## Rules every job follows (scripts/schedule/common.sh)

- `/srv/fda-products-kg/tools/bin/vm-status` (read-only) runs first; its output goes into the report.
- `flock` per job (`monitor/locks/<job>.lock`); the weekly fetch, the monthly refresh and the 510(k) finalize
  also share `monitor/locks/heavy.lock`, so none of them overlaps another.
- Deferred, with the reason in the report and in `monitor/alerts.log`, when another project's JVM over 4 GB
  runs or when free disk is below what the job needs plus the 100 GB reserve (weekly 6 GB, MAUDE bulk 25 GB,
  monthly: release set + 25 GB downloads + 85 GB MAUDE intermediates + 175 GB for the full-size load).
- The weekly fetch and the monthly refresh do not start while the 510(k) summary PDF crawl runs (same FDA
  host); the weekly job is skipped for that week, the monthly job writes a deferred report.
- Every JVM at or below 8 GB (xloader 8 GB, batteries 6 GB, riot and scratch stores 2 GB); `nice -n 10`
  and `ionice -c2 -n7`; start times outside 09:00-12:00.
- Retention: the newest two dated folders of each kind (out-, downloads-, weekly-, fulltest-, tdb-) are kept;
  older ones are listed in `monitor/retention_candidates.md` for deletion with the PI's approval. No job
  deletes them.
- The FDA Data Dashboard user and key are read from `/srv/medical-device-kg/.fda_dashboard.env` into the
  dashboard lane's environment only (`_ddapi.py` sends them as request headers). No script prints the file.

With the current disk (about 240 GB free, 106 GB release set) the monthly refresh needs about 390 GB plus the
reserve and will be deferred until the PI approves deleting older snapshots or stores listed in
`monitor/retention_candidates.md` (for example `work/tdb-v007d`, 131 GB, after the v0.0.7 upload).

## Dry runs

`bash scripts/schedule/<job>.sh --dry-run` checks the gates and writes the report with the plan, without
downloads (daily: no network requests; weekly: only the openFDA index; monthly: gates and plan;
watcher: status only). First dry runs: November 15, 2026 (daily, weekly, monthly, watcher).

## The 510(k) summary PDF crawl and k510_predicates.ttl

- Crawl (started November 15, 2026 22:25): `python3 -I scripts/schedule/src_run_pdf.py k510_predicates fetch
  work/out-2026-11-04`, log `logs/v007e-k510-crawl.log`, PDFs in
  `work/downloads-2026-11-04/k510_summaries/pdf/`, progress in `progress.json` there. One request per second,
  newest summaries first; about 93,000 PDFs, about 1.3 MB each.
- Resumable: a PDF already present is never requested again, a 404 is final, refused requests are retried on
  the next run. Disk guard: the fetch stops by itself before free space falls below 105 GB; the watcher alerts
  when the projected free space at the end falls below 100 GB. PDFs are not deleted; if space runs short,
  move them to the PI's archive (the extracted text in `text/` is what the transform needs once read).
- To resume after a stop (outside 09:00-12:00):
  `cd /srv/medical-device-kg/work/v007 && nohup setsid nice -n 10 ionice -c2 -n7 python3 -I
  scripts/schedule/src_run_pdf.py k510_predicates fetch /srv/medical-device-kg/work/out-2026-11-04 >>
  /srv/medical-device-kg/logs/v007e-k510-crawl.log 2>&1 &`
- Finalize: `scripts/schedule/k510_pred_finalize.sh [final|test|auto]` (started by the watcher; by hand with
  `scripts/v007e_pred_finalize.sh` through `run_remote.ps1`): transform -> riot -> self-tests ->
  `structure_lint.sh` -> `query_contract.py` -> scratch TDB2 link test in `work/tmp-v007e` -> (final, all
  checks passed) `RELEASE_INPUTS.txt` and `UPLOAD_PLAN.md` via `work/v007e/release_add.py` (backups first)
  -> summary. A failed final run writes `monitor/k510_finalize.failed`; delete it after the fix to let the
  watcher retry.
- Image-only PDFs are listed in `k510_predicates.ocr_backlog.tsv` (and `hud_designations.ocr_backlog.tsv`)
  for OCR on Star HPC; no OCR engine is installed on the VM.

## Files

- `monitor/latest.md` newest daily report; `monitor/alerts.log` one line per alert (time, job, message).
- `monitor/k510_crawl_status.md`, `monitor/k510_predicates_finalize.md` (`-test.md` for test runs).
- `monitor/retention_candidates.md`, `monitor/radiation_corrective_actions.tsv`.
- Logs: `logs/schedule-daily.log`, `schedule-weekly.log`, `schedule-monthly.log`, `schedule-k510-watch.log`,
  `k510-finalize.log`.

To stop the schedule: `crontab -e` and delete the lines tagged `# mdkg-schedule:` (or restore a
`monitor/crontab.bak-*` file with `crontab <file>`).
