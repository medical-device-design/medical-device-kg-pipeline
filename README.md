# vm-reports

Weekly monitoring reports copied from the build VM by `scripts/publish_vm_reports.sh`.
Text reports only: no data, no credentials. Every file is redacted before publishing
(hostname -> build-vm, usernames -> <user>, home paths -> ~, credentials file paths ->
<credentials file>), and publishing is refused if anything identifying remains.

- `recalls/LATEST.md`: the latest recall monitor report (FDA totals vs graph, class coverage, new recalls, warnings, validation)
- `recalls/<date>/`: each week's report, `new_recalls.tsv` and `class_changes.tsv`
- `recalls/history.csv`: one line per weekly run
- `health/LATEST.txt`: the latest Layer 2 VM + lakeFS health report
- `lakefs/LATEST.md`: release watch (stable branches, tags, last action runs), from `scripts/lakefs_release_watch.sh`

Never merge this branch into `main`.
