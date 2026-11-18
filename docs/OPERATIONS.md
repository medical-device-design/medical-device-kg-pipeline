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
| every 30 min, 00:00-08:59 and 12:00-23:59 | `k510_crawl_watch.sh` | status of the 510(k) summary PDF crawl with a disk projection; when the crawl has completed, starts `k510_pred_finalize.sh first` once (OCR backlog), then writes `monitor/k510_ocr_needed.md` with the command for the PI's PC (bulk OCR on Star, then `final`) | `monitor/k510_crawl_status.md`, `monitor/k510_predicates_finalize-first.md`, `monitor/k510_ocr_needed.md`, `monitor/k510_predicates_finalize.md` |

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
- Finalize: `scripts/schedule/k510_pred_finalize.sh [first|final|test|auto]` (by hand with
  `scripts/v007e_pred_finalize.sh` through `run_remote.ps1`): transform -> riot -> self-tests ->
  `structure_lint.sh` -> `query_contract.py` -> scratch TDB2 link test in `work/tmp-v007e` -> (final, all
  checks passed) `RELEASE_INPUTS.txt` and `UPLOAD_PLAN.md` via `work/v007e/release_add.py` (backups first)
  -> summary. `first` does the same without touching the release list or the upload plan. A failed final
  run writes `monitor/k510_finalize.failed`; delete it after the fix to let the watcher retry.

## OCR of image-only PDFs (Star HPC; PI approval November 16, 2026)

No OCR engine is installed on the VM. Image-only PDFs (fewer than 100 non-space characters from pdftotext)
are OCR'd on Star HPC (`ocrmypdf --force-ocr`, tesseract, English; project space
`/fs1/projects/device_design_knowledge`, account `device_design_knowledge`, CPU only, no node pinned, no GPU)
through the PI's PC, which holds the Star key: `scripts\star\ocr_batch.sh` (WSL; VM -> PC -> Star -> PC -> VM;
originals stay on the VM). The OCR text lands in `work/ocr-<batch>/out/txt/` on the VM.

- The transforms (`k510_predicates.py`, `hud_designations.py`; `text_of()`) read an image-only PDF from
  `<stem>.txt` under the OCR batch folders in `MDKG_OCR_DIR` (":"-separated, wildcards allowed; `common.sh`
  sets `work/ocr-*` unless it is set; `src_run_pdf.py --ocr-dir` overrides). Results from OCR text carry
  `ex:extractionMethod "ocrmypdf/tesseract+regex v1"` and one confidence level less (high -> medium,
  medium -> low); results from text layers are unchanged. `*.ocr_backlog.tsv` lists the PDFs still without
  usable text after OCR; `*.links.tsv` / `*.rows.tsv` have an `extraction_method` column.
- After the 510(k) crawl (automatic up to step 2):
  1. the watcher starts `k510_pred_finalize.sh first`: full transform and checks with the OCR text collected
     so far, writes `work/out-2026-11-04/k510_predicates.ocr_backlog.tsv` and `monitor/k510_finalize_first.done`;
  2. the watcher writes `monitor/k510_ocr_needed.md` (backlog count and size, Star estimate, the command) and
     raises one alert; cron on the VM cannot drive the WSL relay, so the next step runs on the PI's PC;
  3. on the PC (PowerShell, outside 09:00-12:00):
     `& 'A:\Big Data\Knowledge Graphs\medical-device-kg\scripts\star\post_crawl_ocr.ps1' -Batch <yyyy-mm-dd>-k510`
     runs `ocr_batch.sh` mklist -> stage -> submit (array of 100 PDFs per task, 8 CPUs / 16G each, at most
     4 at once = the account cap; about 4 min per task, inside QOS normal's 1 h) -> wait (polls every 5 min)
     -> collect (OCR text and logs; `-WithPdf` also brings the OCR'd PDFs) -> clean (PC relay copies) ->
     `k510_pred_finalize.sh final` on the VM with `MDKG_OCR_DIR=work/ocr-*`. Re-running with the same
     `-Batch` resumes; `-Step <name>` runs one step.
  4. `final` checks everything again and registers `k510_predicates.ttl` in `RELEASE_INPUTS.txt` /
     `UPLOAD_PLAN.md`; it removes `monitor/k510_ocr_needed.md`. Nothing is uploaded, merged or tagged.
  To finish without more OCR: `bash scripts/schedule/k510_pred_finalize.sh final` on the VM.
- The Star working copies (`ocr/batches/<batch>/`) have no backup and can be deleted once collected.

## Files

- `monitor/latest.md` newest daily report; `monitor/alerts.log` one line per alert (time, job, message).
- `monitor/k510_crawl_status.md`, `monitor/k510_predicates_finalize.md` (`-test.md` for test runs, `-first.md`
  for the first pass), `monitor/k510_finalize_first.done`, `monitor/k510_ocr_needed.md`.
- `monitor/retention_candidates.md`, `monitor/radiation_corrective_actions.tsv`.
- Logs: `logs/schedule-daily.log`, `schedule-weekly.log`, `schedule-monthly.log`, `schedule-k510-watch.log`,
  `k510-finalize-first.log`, `k510-finalize.log`.

To stop the schedule: `crontab -e` and delete the lines tagged `# mdkg-schedule:` (or restore a
`monitor/crontab.bak-*` file with `crontab <file>`).
