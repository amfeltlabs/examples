#!/usr/bin/env python3
"""Derive the vendored jaffle shop corpus from the one full-year jafgen run.

Usage: python3 trim.py <full-year-dir> <out-dir>

<full-year-dir> holds the seven files the generation run recorded in PROVENANCE wrote.
They are checked against SOURCE_SHA256 below before anything is read, so this script
derives the corpus from exactly those bytes or from nothing. <out-dir> must not exist or
must be empty; the seven trimmed files are written there and nothing else.

The rule, applied to the source and nowhere else:

  * The window is the half-open interval [start, end) on raw_orders.ordered_at. start is
    the earliest ordered_at in the source. end is midnight on the first day of the
    calendar month MONTHS months after start's month. For this source that is
    [2018-09-01T08:20:00, 2018-12-01T00:00:00): September, October and November 2018.
  * raw_orders keeps the orders whose ordered_at lies in the window.
  * raw_items keeps the items whose order_id is a kept order.
  * raw_tweets keeps the tweets whose tweeted_at lies in the same window.
  * raw_customers keeps the customers a kept order names, and no other. In the source
    every customer has placed an order, and the trimmed corpus keeps that true: a
    snapshot taken at the end of the window holds no customer whose first order comes
    after it. Every kept tweet's user must be one of them, or the script fails.
  * raw_stores, raw_products and raw_supplies are jafgen's fixed reference tables,
    written whole on every run whatever was ordered. They are kept whole, byte for byte.

Kept rows are the source's own bytes, in the source's order, CRLF line endings included;
no row is re-serialized. Every file is checked to be one CSV record per CRLF-terminated
line before any row is kept. The script is deterministic: it draws nothing at random and
depends on nothing but its input bytes, so the same input always yields the same output.

Python standard library only.
"""

import csv
import datetime
import hashlib
import io
import os
import sys

# The seven files of the full-year run, by the SHA-256 its SHA256SUMS pinned.
SOURCE_SHA256 = {
    "raw_customers.csv": "d0633d42103841f51dfb05474bdc66b9962b3368f134bbf4d1dc2e15b9012292",
    "raw_items.csv": "1ddb9ccd5bfdbd1587bccf3b06c5e4c96d0a8647771b02637af58d9b1adf26f8",
    "raw_orders.csv": "19b051ac14ce299fc2b8d6a0088d1c9d88d0bd98e4d75432966666f4a219e5f5",
    "raw_products.csv": "01825b18e182090b6770cee6aaf7bd52d5781a0c74bb1ff2872aac4b47809f0d",
    "raw_stores.csv": "a83ac006ab8cab7cd7722d0cfeab87b01d7a9caa4cc373eb63de5e13764d5683",
    "raw_supplies.csv": "309a2d1cfd330da2b493b411d2fe5e0aef1b463ed0281760691793f8b423bde1",
    "raw_tweets.csv": "fb8f1adfe8bda55d753b5a9c01adde1fa94aa88d9a3b46b12957021e702b8861",
}
FILES = sorted(SOURCE_SHA256)

# How many calendar months the window spans.
MONTHS = 3

CRLF = b"\r\n"


def die(msg):
    sys.stderr.write("trim.py: FAIL: %s\n" % msg)
    sys.exit(1)


def read_table(path, name):
    """Return (header line, fields, [(raw line, record dict)]) for one verified file."""
    with open(path, "rb") as fh:
        data = fh.read()
    got = hashlib.sha256(data).hexdigest()
    if got != SOURCE_SHA256[name]:
        die("%s: sha256 %s, the full-year run wrote %s" % (name, got, SOURCE_SHA256[name]))
    if not data.endswith(CRLF):
        die("%s does not end with CRLF" % name)
    lines = data[: -len(CRLF)].split(CRLF)
    for n, line in enumerate(lines, 1):
        if b"\r" in line or b"\n" in line:
            die("%s line %d holds a bare CR or LF" % (name, n))
    fields = next(csv.reader([lines[0].decode("utf-8")]))
    rows = []
    for n, line in enumerate(lines[1:], 2):
        records = list(csv.reader(io.StringIO(line.decode("utf-8"), newline="")))
        if len(records) != 1 or len(records[0]) != len(fields):
            die("%s line %d is not exactly one %d-field record" % (name, n, len(fields)))
        rows.append((line, dict(zip(fields, records[0]))))
    # The same file read as one CSV stream must give the same records, so no record spans
    # two lines and no line holds two.
    whole = list(csv.reader(io.StringIO(data.decode("utf-8"), newline="")))
    if whole[1:] != [list(r.values()) for _, r in rows]:
        die("%s: per-line and whole-file CSV parses disagree" % name)
    return lines[0], fields, rows


def timestamp(text, where):
    value = datetime.datetime.fromisoformat(text)
    if value.tzinfo is not None:
        die("%s: %r carries an offset; the source's timestamps carry none" % (where, text))
    return value


def window_end(start):
    month0 = start.month - 1 + MONTHS
    return datetime.datetime(start.year + month0 // 12, month0 % 12 + 1, 1)


def main(argv):
    if len(argv) != 3:
        sys.stderr.write("usage: python3 trim.py <full-year-dir> <out-dir>\n")
        return 2
    src, out = argv[1], argv[2]
    if os.path.exists(out) and (not os.path.isdir(out) or os.listdir(out)):
        die("%s exists and is not an empty directory" % out)

    t = {name: read_table(os.path.join(src, name), name) for name in FILES}
    rows = {name: t[name][2] for name in FILES}

    start = min(timestamp(r["ordered_at"], "raw_orders") for _, r in rows["raw_orders.csv"])
    end = window_end(start)

    def in_window(text, where):
        return start <= timestamp(text, where) < end

    keep = {}
    keep["raw_orders.csv"] = [
        x for x in rows["raw_orders.csv"] if in_window(x[1]["ordered_at"], "raw_orders")
    ]
    order_ids = {r["id"] for _, r in keep["raw_orders.csv"]}
    customer_ids = {r["customer"] for _, r in keep["raw_orders.csv"]}
    keep["raw_items.csv"] = [x for x in rows["raw_items.csv"] if x[1]["order_id"] in order_ids]
    keep["raw_tweets.csv"] = [
        x for x in rows["raw_tweets.csv"] if in_window(x[1]["tweeted_at"], "raw_tweets")
    ]
    keep["raw_customers.csv"] = [
        x for x in rows["raw_customers.csv"] if x[1]["id"] in customer_ids
    ]
    for name in ("raw_stores.csv", "raw_products.csv", "raw_supplies.csv"):
        keep[name] = rows[name]

    # Referential completeness of the result: every reference a kept row makes resolves.
    kept = {name: [r for _, r in keep[name]] for name in FILES}
    customers = {r["id"] for r in kept["raw_customers.csv"]}
    stores = {r["id"] for r in kept["raw_stores.csv"]}
    skus = {r["sku"] for r in kept["raw_products.csv"]}
    checks = [
        ("raw_orders.customer", [r["customer"] for r in kept["raw_orders.csv"]], customers),
        ("raw_orders.store_id", [r["store_id"] for r in kept["raw_orders.csv"]], stores),
        ("raw_items.order_id", [r["order_id"] for r in kept["raw_items.csv"]], order_ids),
        ("raw_items.sku", [r["sku"] for r in kept["raw_items.csv"]], skus),
        ("raw_supplies.sku", [r["sku"] for r in kept["raw_supplies.csv"]], skus),
        ("raw_tweets.user_id", [r["user_id"] for r in kept["raw_tweets.csv"]], customers),
    ]
    for label, refs, targets in checks:
        missing = sorted(set(refs) - targets)
        if missing:
            die("%s: %d values resolve to no kept row, e.g. %s" % (label, len(missing), missing[0]))
    if len(customers) != len(customer_ids):
        die("raw_customers: kept %d rows for %d ids" % (len(customers), len(customer_ids)))

    os.makedirs(out, exist_ok=True)
    print("window  [%s, %s) on raw_orders.ordered_at" % (start.isoformat(), end.isoformat()))
    print("%-18s %9s %11s %9s %11s  %s" % ("file", "rows in", "bytes in", "rows out", "bytes out", "sha256 out"))
    for name in FILES:
        header = t[name][0]
        body = header + CRLF + b"".join(line + CRLF for line, _ in keep[name])
        with open(os.path.join(out, name), "wb") as fh:
            fh.write(body)
        size_in = os.path.getsize(os.path.join(src, name))
        print("%-18s %9d %11d %9d %11d  %s" % (
            name, len(rows[name]), size_in, len(keep[name]), len(body),
            hashlib.sha256(body).hexdigest()))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
