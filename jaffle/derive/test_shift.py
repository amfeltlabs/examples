"""Tests for shift.py. Standard library only.

The data tests read the trim outputs from the directory named by JAFFLE_TRIM_DIR, which
derive/reproduce.sh sets after it has run trim.py on the archive. Without it they fail
rather than skip: a silent skip would make a green run mean nothing.

    JAFFLE_TRIM_DIR=<trim output> python3 -m unittest discover -s jaffle/derive
"""

import csv
import datetime
import os
import shutil
import tempfile
import unittest

import shift

TRIM_DIR = os.environ.get("JAFFLE_TRIM_DIR")
SHIFTED = [("raw_stores.csv", "stores.csv", "opened_at"),
           ("raw_orders.csv", "orders.csv", "ordered_at"),
           ("raw_tweets.csv", "tweets.csv", "tweeted_at")]


def read_rows(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


class DataCase(unittest.TestCase):
    def setUp(self):
        if not TRIM_DIR or not os.path.isdir(TRIM_DIR):
            self.fail("set JAFFLE_TRIM_DIR to the directory trim.py wrote")
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.out = os.path.join(self.tmp, "out")


class Identity(DataCase):
    def test_zero_shift_without_renames_is_byte_identical(self):
        shift.shift(TRIM_DIR, self.out, days=0, rename=False)
        self.assertEqual(sorted(os.listdir(self.out)), sorted(shift.TRIM_SHA256))
        for name in shift.TRIM_SHA256:
            with open(os.path.join(TRIM_DIR, name), "rb") as a, open(os.path.join(self.out, name), "rb") as b:
                self.assertEqual(a.read(), b.read(), name)


class WeekdayAndWallClock(DataCase):
    def test_every_row_keeps_weekday_and_time(self):
        shift.shift(TRIM_DIR, self.out)
        for raw, pub, col in SHIFTED:
            before, after = read_rows(os.path.join(TRIM_DIR, raw)), read_rows(os.path.join(self.out, pub))
            self.assertEqual(len(before), len(after))
            for b, a in zip(before, after):
                old = datetime.datetime.fromisoformat(b[col])
                new = datetime.datetime.fromisoformat(a[col])
                self.assertEqual(old.weekday(), new.weekday(), (pub, b[col]))
                self.assertEqual(old.time(), new.time(), (pub, b[col]))
                self.assertEqual((new - old).days, 2555)
                self.assertEqual("." in b[col], "." in a[col], (pub, b[col]))

    def test_fixed_cases(self):
        self.assertEqual(shift.shift_value("2018-11-04T01:30:00", shift.SHIFT_DAYS), "2025-11-02T01:30:00")
        self.assertEqual(shift.shift_value("2018-09-01T08:20:00", shift.SHIFT_DAYS), "2025-08-30T08:20:00")
        self.assertEqual(shift.shift_value("2018-09-01T09:53:18.421237", shift.SHIFT_DAYS),
                         "2025-08-30T09:53:18.421237")

    def test_leap_days_do_not_change_the_weekday(self):
        # The 2020-02-29 and 2024-02-29 leap days lie inside every 2018 -> 2025 span.
        for text in ("2018-09-01T00:00:00", "2018-12-31T23:59:59", "2019-03-12T00:00:00"):
            old = datetime.datetime.fromisoformat(text)
            new = datetime.datetime.fromisoformat(shift.shift_value(text, shift.SHIFT_DAYS))
            self.assertEqual(old.weekday(), new.weekday())
        self.assertEqual(shift.SHIFT_DAYS, 2555)
        self.assertEqual(shift.SHIFT_DAYS % 7, 0)


class Window(DataCase):
    def test_published_window(self):
        shift.shift(TRIM_DIR, self.out)
        orders = [r["ordered_at"] for r in read_rows(os.path.join(self.out, "orders.csv"))]
        self.assertEqual(min(orders), "2025-08-30T08:20:00")
        self.assertEqual(max(orders)[:10], "2025-11-28")
        opened = {r["name"]: r["opened_at"] for r in read_rows(os.path.join(self.out, "stores.csv"))}
        self.assertEqual(opened["Philadelphia"][:10], "2025-08-30")
        self.assertEqual(opened["Brooklyn"], "2026-03-10T00:00:00")
        stores = {r["id"]: r["name"] for r in read_rows(os.path.join(self.out, "stores.csv"))}
        self.assertEqual(stores["c9471206-c14b-46c1-a93b-fe94ee386b24"], "Brooklyn")

    def test_publication_names_and_headers(self):
        shift.shift(TRIM_DIR, self.out)
        self.assertEqual(sorted(os.listdir(self.out)), sorted(
            ["customers.csv", "stores.csv", "orders.csv", "order_items.csv", "products.csv",
             "supplies.csv", "tweets.csv"]))
        self.assertIn("customer_id", read_rows(os.path.join(self.out, "orders.csv"))[0])
        self.assertIn("customer_id", read_rows(os.path.join(self.out, "tweets.csv"))[0])


class Refusal(DataCase):
    def test_tampered_input_fails_the_digest_check(self):
        tampered = os.path.join(self.tmp, "tampered")
        shutil.copytree(TRIM_DIR, tampered)
        path = os.path.join(tampered, "raw_orders.csv")
        with open(path, "rb") as fh:
            data = fh.read()
        with open(path, "wb") as fh:
            fh.write(data.replace(b"2018-09-01T08:20:00", b"2018-09-01T08:21:00", 1))
        with self.assertRaises(shift.ShiftError):
            shift.shift(tampered, self.out)
        self.assertFalse(os.path.exists(self.out))

    def test_missing_and_extra_files_are_refused(self):
        partial = os.path.join(self.tmp, "partial")
        shutil.copytree(TRIM_DIR, partial)
        os.remove(os.path.join(partial, "raw_tweets.csv"))
        with self.assertRaises(shift.ShiftError):
            shift.shift(partial, self.out)
        shutil.copy(os.path.join(TRIM_DIR, "raw_tweets.csv"), partial)
        with open(os.path.join(partial, "stray.csv"), "w") as fh:
            fh.write("x\r\n")
        with self.assertRaises(shift.ShiftError):
            shift.shift(partial, self.out)


class Values(unittest.TestCase):
    def test_a_zone_or_odd_format_is_refused(self):
        for bad in ("2018-09-01 08:20:00", "2018-09-01T08:20:00Z", "2018-09-01T08:20:00+02:00", "2018-09-01"):
            with self.assertRaises(shift.ShiftError):
                shift.shift_value(bad, 1)


if __name__ == "__main__":
    unittest.main()
