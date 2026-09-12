#!/usr/bin/env python3
"""
fetch_udi.py -- download the openFDA AccessGUDID (UDI) bulk partitions.

    python3 fetch_udi.py --output downloads/udi
    python3 fetch_udi.py --output downloads/udi --max-partitions 2   # smoke test

WHY BULK RATHER THAN THE API
----------------------------
The existing pipeline scrapes openFDA through the API with date-range chunking to
work around the 25,000-record skip ceiling. For UDI that would be ~52,000 requests
and many hours. openFDA publishes the same data as bulk .json.zip partitions:

    device/udi   5,182,695 records   1,895 MB compressed   52 partitions

The whole set downloads in under an hour on a normal connection, and the skip
ceiling stops being a concern at all. The same applies to every other endpoint --
all device endpoints together are 20.8 GB, and everything except UDI and MAUDE is
under 1 GB.

Downloads are resumable: existing files of the right size are skipped, so an
interrupted run can simply be repeated.
"""

import argparse
import json
import os
import sys
import time
import urllib.request

MANIFEST_URL = "https://api.fda.gov/download.json"
UA = "medical-device-kg/1.0 (Hofstra University; NSF Proto-OKN 2535091)"


def get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.load(resp)


def download(url, dest, expected_mb=None, retries=3):
    """Download one partition, skipping if already present at plausible size."""
    if os.path.exists(dest) and expected_mb:
        actual_mb = os.path.getsize(dest) / 1024 / 1024
        # allow 5% slack; manifest sizes are rounded
        if abs(actual_mb - float(expected_mb)) / float(expected_mb) < 0.05:
            return "skipped"

    tmp = dest + ".part"
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=600) as resp, \
                 open(tmp, "wb") as fh:
                while True:
                    chunk = resp.read(1 << 20)
                    if not chunk:
                        break
                    fh.write(chunk)
            os.replace(tmp, dest)
            return "downloaded"
        except Exception as exc:                      # noqa: BLE001
            if attempt == retries:
                raise
            wait = 5 * attempt
            print(f"      retry {attempt}/{retries - 1} after {wait}s ({exc})")
            time.sleep(wait)
    return "failed"


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--output", default="downloads/udi", help="destination directory")
    ap.add_argument("--endpoint", default="udi",
                    help="device endpoint key in the manifest "
                         "(udi, 510k, pma, classification, recall, "
                         "registrationlisting, enforcement, event)")
    ap.add_argument("--max-partitions", type=int, default=0,
                    help="download only the first N partitions (smoke test)")
    args = ap.parse_args()

    print(f"==> manifest: {MANIFEST_URL}")
    manifest = get_json(MANIFEST_URL)

    try:
        section = manifest["results"]["device"][args.endpoint]
    except KeyError:
        available = ", ".join(sorted(manifest["results"]["device"].keys()))
        sys.exit(f"error: unknown endpoint '{args.endpoint}'. Available: {available}")

    partitions = section.get("partitions", [])
    total_mb = sum(float(p.get("size_mb", 0)) for p in partitions)
    total_recs = section.get("total_records", "unknown")

    print(f"    endpoint      device/{args.endpoint}")
    print(f"    export_date   {section.get('export_date')}")
    print(f"    records       {total_recs:,}" if isinstance(total_recs, int)
          else f"    records       {total_recs}")
    print(f"    partitions    {len(partitions)}")
    print(f"    total size    {total_mb:,.0f} MB compressed")
    print("    NOTE: size_mb is the COMPRESSED .json.zip; expect 8-12x on unzip.\n")

    if args.max_partitions:
        partitions = partitions[:args.max_partitions]
        print(f"    (limited to first {len(partitions)} partition(s))\n")

    os.makedirs(args.output, exist_ok=True)

    done = skipped = 0
    for i, part in enumerate(partitions, 1):
        url = part["file"]
        name = os.path.basename(url)
        dest = os.path.join(args.output, name)
        size = part.get("size_mb")
        label = part.get("display_name", name)

        print(f"  [{i}/{len(partitions)}] {label} ({size} MB)")
        status = download(url, dest, expected_mb=size)
        if status == "skipped":
            skipped += 1
            print("      already present, skipped")
        else:
            done += 1

    print(f"\n==> {done} downloaded, {skipped} already present -> {args.output}")
    print("    Next: python3 udi_to_rdf.py "
          f"--input {args.output} --output out/udi.ttl")


if __name__ == "__main__":
    main()
