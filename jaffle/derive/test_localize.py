"""Tests for localize.py. Standard library only.

The data tests read the trim outputs from the directory named by JAFFLE_TRIM_DIR, as
test_shift.py does, and make the shift output from them. Without it they fail rather than
skip.

    JAFFLE_TRIM_DIR=<trim output> python3 -m unittest discover -s jaffle/derive
"""

import csv
import decimal
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock

import localize
import shift

HERE = os.path.dirname(os.path.abspath(__file__))
TRIM_DIR = os.environ.get("JAFFLE_TRIM_DIR")


def read_rows(directory, name):
    with open(os.path.join(directory, name), newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


class Case(unittest.TestCase):
    def setUp(self):
        if not TRIM_DIR or not os.path.isdir(TRIM_DIR):
            self.fail("set JAFFLE_TRIM_DIR to the directory trim.py wrote")
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.shifted = os.path.join(self.tmp, "shift")
        self.out = os.path.join(self.tmp, "out")
        shift.shift(TRIM_DIR, self.shifted)
        localize.localize(self.shifted, self.out)

    def both(self, name):
        return read_rows(self.shifted, name), read_rows(self.out, name)


class Keeps(Case):
    def test_order_items_pass_through_byte_for_byte(self):
        with open(os.path.join(self.shifted, "order_items.csv"), "rb") as a, \
                open(os.path.join(self.out, "order_items.csv"), "rb") as b:
            self.assertEqual(a.read(), b.read())

    def test_only_the_named_columns_change(self):
        changing = {"customers.csv": {"name"}, "stores.csv": {"name", "tax_rate"},
                    "orders.csv": {"tax_paid", "order_total"},
                    "products.csv": {"name", "description"}, "supplies.csv": {"name"},
                    "tweets.csv": {"content"}}
        for name, columns in changing.items():
            before, after = self.both(name)
            self.assertEqual(len(before), len(after), name)
            for b, a in zip(before, after):
                for column in b:
                    if column not in columns:
                        self.assertEqual(b[column], a[column], (name, column))

    def test_a_line_left_alone_keeps_its_bytes(self):
        with open(os.path.join(self.shifted, "supplies.csv"), "rb") as a, \
                open(os.path.join(self.out, "supplies.csv"), "rb") as b:
            old, new = a.read().split(b"\r\n"), b.read().split(b"\r\n")
        self.assertEqual(len(old), len(new))
        self.assertGreater(sum(x == y for x, y in zip(old, new)), 40)


class Names(Case):
    def test_every_name_is_new_unique_and_from_the_lists(self):
        before, after = self.both("customers.csv")
        old = {r["name"] for r in before}
        names = [r["name"] for r in after]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(old & set(names), set())
        for name in names:
            first, _, last = name.partition(" ")
            self.assertIn(first, localize.FIRST_NAMES)
            self.assertIn(last, localize.LAST_NAMES)

    def test_a_taken_name_draws_again(self):
        customer = "8224248d-b37a-4e73-8571-24b7d3435150"
        first = localize.draw_name(customer, set())
        second = localize.draw_name(customer, {first})
        self.assertNotEqual(first, second)
        self.assertEqual(localize.draw_name(customer, set()), first)

    def test_an_excluded_name_draws_again(self):
        customer = "13909e87-a17a-4801-954c-fc5946d0a23e"
        with unittest.mock.patch.object(localize, "EXCLUDED_NAMES", frozenset()):
            self.assertEqual(localize.draw_name(customer, set()), "Mila Jansen")
        drawn = localize.draw_name(customer, set())
        self.assertNotEqual(drawn, "Mila Jansen")
        with unittest.mock.patch.object(localize, "EXCLUDED_NAMES", frozenset({"Mila Jansen", drawn})):
            self.assertNotIn(localize.draw_name(customer, set()), ("Mila Jansen", drawn))

    def test_no_excluded_name_reaches_the_corpus(self):
        names = {r["name"] for r in read_rows(self.out, "customers.csv")}
        self.assertEqual(names & localize.EXCLUDED_NAMES, set())


class Tweets(Case):
    def test_no_old_product_name_is_left_and_every_tweet_keeps_its_shape(self):
        before, after = self.both("tweets.csv")
        for b, a in zip(before, after):
            for old in localize.TWEET_NAMES:
                self.assertNotIn(old, a["content"])
            self.assertEqual(b["content"].split(" ")[:2], a["content"].split(" ")[:2])
        self.assertTrue(any("bratwurst royale" in r["content"] for r in after))

    def test_every_new_tweet_name_is_on_the_menu(self):
        menu = {r["name"] for r in read_rows(self.out, "products.csv")}
        self.assertLessEqual(set(localize.TWEET_NAMES.values()), menu)
        self.assertEqual(set(localize.TWEET_NAMES), {r["name"] for r in read_rows(self.shifted, "products.csv")}
                         - menu)


class Amounts(Case):
    def test_vat_is_computed_at_the_store_rate(self):
        before, after = self.both("orders.csv")
        rate = {s: decimal.Decimal(r) for s, (_, r) in localize.STORES.items()}
        for b, a in zip(before, after):
            self.assertEqual(int(a["tax_paid"]), int(b["subtotal"]) * rate[b["store_id"]])
            self.assertEqual(int(a["order_total"]), int(b["subtotal"]) + int(a["tax_paid"]))

    def test_a_figure_that_would_round_is_refused(self):
        self.assertEqual(localize.vat(1100, "0.09"), 99)
        self.assertEqual(localize.vat(0, "0.09"), 0)
        with self.assertRaises(localize.LocalizeError):
            localize.vat(1150, "0.09")


class Stores(Case):
    def test_every_store_gets_its_city_and_rate(self):
        for r in read_rows(self.out, "stores.csv"):
            self.assertEqual((r["name"], r["tax_rate"]), localize.STORES[r["id"]])


class Determinism(unittest.TestCase):
    def test_identical_under_two_hash_seeds(self):
        if not TRIM_DIR or not os.path.isdir(TRIM_DIR):
            self.fail("set JAFFLE_TRIM_DIR to the directory trim.py wrote")
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        src = os.path.join(tmp, "shift")
        shift.shift(TRIM_DIR, src)
        digests = set()
        for seed in ("1", "4242"):
            env = dict(os.environ, PYTHONHASHSEED=seed)
            dest = os.path.join(tmp, seed)
            subprocess.run([sys.executable, os.path.join(HERE, "localize.py"), src, dest],
                           env=env, check=True, stdout=subprocess.DEVNULL)
            found = []
            for name in localize.FILES:
                with open(os.path.join(dest, name), "rb") as fh:
                    found.append(hashlib.sha256(fh.read()).hexdigest())
            digests.add(tuple(found))
        self.assertEqual(len(digests), 1)


class Refusal(Case):
    def fresh(self):
        return os.path.join(self.tmp, "again")

    def test_tampered_input_is_refused(self):
        path = os.path.join(self.shifted, "orders.csv")
        with open(path, "ab") as fh:
            fh.write(b"x\r\n")
        with self.assertRaises(localize.LocalizeError):
            localize.localize(self.shifted, self.fresh())
        self.assertFalse(os.path.exists(self.fresh()))

    def test_missing_and_extra_files_are_refused(self):
        with open(os.path.join(self.shifted, "stray.csv"), "w") as fh:
            fh.write("x\r\n")
        with self.assertRaises(localize.LocalizeError):
            localize.localize(self.shifted, self.fresh())
        os.remove(os.path.join(self.shifted, "stray.csv"))
        os.remove(os.path.join(self.shifted, "tweets.csv"))
        with self.assertRaises(localize.LocalizeError):
            localize.localize(self.shifted, self.fresh())

    def test_an_occupied_output_is_refused(self):
        with self.assertRaises(localize.LocalizeError):
            localize.localize(self.shifted, self.out)


if __name__ == "__main__":
    unittest.main()
