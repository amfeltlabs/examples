#!/usr/bin/env python3
"""Move every timestamp of the trimmed Jaffle corpus forward by 365 weeks.

Usage: python3 shift.py <trim-dir> <out-dir>

<trim-dir> holds the seven files trim.py wrote. They are checked against trim.SHA256SUMS
(the digests of exactly those bytes, embedded below as TRIM_SHA256) before anything is
read, so this script derives its output from those bytes or from nothing. <out-dir> must
not exist or must be empty; the seven output files are written there and nothing else.
They are an intermediate: localize.py reads them, and none of them is published.

The shift is exactly SHIFT_DAYS = 2,555 days (365 weeks), added as whole days to the naive
datetimes. A whole number of weeks keeps every weekday and, because the values carry no
zone, every wall-clock time: no value is ever converted through a time zone, so a daylight
saving change never moves one. The first order, 2018-09-01T08:20:00, becomes
2025-08-30T08:20:00, and the three months of orders end in late November 2025.

Three columns are shifted: stores.opened_at, orders.ordered_at and tweets.tweeted_at. Each
value keeps its own format: the T separator, and microseconds exactly when it had them.
Nothing else is touched. Rows keep their order, columns their order, and the files their
CRLF line endings and CSV quoting; a line that is not shifted is written as the same
bytes it was read as.

Publication names. This script also gives the files their published names and the two
foreign-key columns their published names, so the published CSVs describe themselves:

    raw_customers.csv  ->  customers.csv
    raw_stores.csv     ->  stores.csv      (opened_at shifted)
    raw_orders.csv     ->  orders.csv      (ordered_at shifted; header customer -> customer_id)
    raw_items.csv      ->  order_items.csv
    raw_products.csv   ->  products.csv
    raw_supplies.csv   ->  supplies.csv
    raw_tweets.csv     ->  tweets.csv      (tweeted_at shifted; header user_id -> customer_id)

Python standard library only.
"""

import csv
import datetime
import hashlib
import io
import os
import re
import sys

# 365 weeks. Whole days, so the weekday and the wall-clock time survive.
SHIFT_DAYS = 365 * 7

# The seven trim outputs, by the SHA-256 recorded in trim.SHA256SUMS.
TRIM_SHA256 = {
    "raw_customers.csv": "e37835cc5f608c8adb6807ddf86d668a11d6622439f7ac2e540ec3d0ecd173d1",
    "raw_items.csv": "aa0a3dde4aed783b8050bf36da411d634221a8b33d3d0dc70acc1d6e0d49a694",
    "raw_orders.csv": "2793143119b62b7544a8b8078bcf5309862dadc3b7356771d7a9616c6981d621",
    "raw_products.csv": "01825b18e182090b6770cee6aaf7bd52d5781a0c74bb1ff2872aac4b47809f0d",
    "raw_stores.csv": "a83ac006ab8cab7cd7722d0cfeab87b01d7a9caa4cc373eb63de5e13764d5683",
    "raw_supplies.csv": "309a2d1cfd330da2b493b411d2fe5e0aef1b463ed0281760691793f8b423bde1",
    "raw_tweets.csv": "db01c77182570716f4044c78c2b2d33dabf7233adb2f0acb19163534b1e25c7f",
}

# input name -> (published name, timestamp column or None, header renames {old: new})
PUBLISH = {
    "raw_customers.csv": ("customers.csv", None, {}),
    "raw_stores.csv": ("stores.csv", "opened_at", {}),
    "raw_orders.csv": ("orders.csv", "ordered_at", {"customer": "customer_id"}),
    "raw_items.csv": ("order_items.csv", None, {}),
    "raw_products.csv": ("products.csv", None, {}),
    "raw_supplies.csv": ("supplies.csv", None, {}),
    "raw_tweets.csv": ("tweets.csv", "tweeted_at", {"user_id": "customer_id"}),
}

CRLF = b"\r\n"
# A timestamp as jafgen writes it: T separator, no zone, microseconds only when present.
STAMP = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d{6})?$")


class ShiftError(Exception):
    pass


def shift_value(text, days):
    """Add whole days to one timestamp, keeping its format."""
    m = STAMP.match(text)
    if not m:
        raise ShiftError("%r is not a zone-less ISO timestamp" % text)
    value = datetime.datetime.fromisoformat(text) + datetime.timedelta(days=days)
    return value.isoformat(timespec="microseconds" if m.group(1) else "seconds")


def serialize(record):
    out = io.StringIO(newline="")
    csv.writer(out, lineterminator="").writerow(record)
    return out.getvalue()


def shift_file(data, name, days, rename):
    """Return the bytes of one output file made from the bytes of one trim output."""
    _, column, renames = PUBLISH[name]
    if not data.endswith(CRLF):
        raise ShiftError("%s does not end with CRLF" % name)
    lines = data[: -len(CRLF)].split(CRLF)
    for n, line in enumerate(lines, 1):
        if b"\r" in line or b"\n" in line:
            raise ShiftError("%s line %d holds a bare CR or LF" % (name, n))
    texts = [line.decode("utf-8") for line in lines]

    header = next(csv.reader([texts[0]]))
    if serialize(header) != texts[0]:
        raise ShiftError("%s: the header does not survive a CSV round trip" % name)
    index = header.index(column) if column else None
    out_header = texts[0]
    if rename and renames:
        renamed = [renames.get(f, f) for f in header]
        if sum(a != b for a, b in zip(header, renamed)) != len(renames):
            raise ShiftError("%s: header lacks a column to rename" % name)
        out_header = serialize(renamed)

    out = [out_header]
    for n, text in enumerate(texts[1:], 2):
        if index is None:
            out.append(text)
            continue
        records = list(csv.reader(io.StringIO(text, newline="")))
        if len(records) != 1 or len(records[0]) != len(header):
            raise ShiftError("%s line %d is not exactly one %d-field record" % (name, n, len(header)))
        record = records[0]
        if serialize(record) != text:
            raise ShiftError("%s line %d does not survive a CSV round trip" % (name, n))
        record[index] = shift_value(record[index], days)
        out.append(serialize(record))
    return b"".join(t.encode("utf-8") + CRLF for t in out)


def shift(src, dest, days=SHIFT_DAYS, rename=True):
    """Verify the trim outputs in src, write the output files into dest.

    days and rename exist for the tests: days=0 with rename=False must reproduce the
    input byte for byte. The chain always runs this with the defaults."""
    if os.path.exists(dest) and (not os.path.isdir(dest) or os.listdir(dest)):
        raise ShiftError("%s exists and is not an empty directory" % dest)
    inputs = {}
    for name in sorted(TRIM_SHA256):
        path = os.path.join(src, name)
        if not os.path.isfile(path):
            raise ShiftError("%s is missing" % path)
        with open(path, "rb") as fh:
            inputs[name] = fh.read()
        got = hashlib.sha256(inputs[name]).hexdigest()
        if got != TRIM_SHA256[name]:
            raise ShiftError("%s: sha256 %s, trim wrote %s" % (name, got, TRIM_SHA256[name]))
    extra = sorted(set(os.listdir(src)) - set(TRIM_SHA256))
    if extra:
        raise ShiftError("%s holds files trim did not write: %s" % (src, ", ".join(extra)))

    results = {PUBLISH[n][0]: shift_file(d, n, days, rename) for n, d in inputs.items()}
    if not rename:
        results = {n: results[PUBLISH[n][0]] for n in inputs}
    os.makedirs(dest, exist_ok=True)
    for name in sorted(results):
        with open(os.path.join(dest, name), "wb") as fh:
            fh.write(results[name])
    return results


def main(argv):
    if len(argv) != 3:
        sys.stderr.write("usage: python3 shift.py <trim-dir> <out-dir>\n")
        return 2
    try:
        results = shift(argv[1], argv[2])
    except ShiftError as err:
        sys.stderr.write("shift.py: FAIL: %s\n" % err)
        return 1
    print("shifted by %d days (%d weeks)" % (SHIFT_DAYS, SHIFT_DAYS // 7))
    for name in sorted(results):
        print("%-16s %9d bytes  %s" % (name, len(results[name]), hashlib.sha256(results[name]).hexdigest()))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
