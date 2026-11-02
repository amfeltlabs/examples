"""Tests for addresses.py. Standard library only.

The tests read the published jaffle/data/ and jaffle/SHA256SUMS, so they need nothing but
a checkout.

    python3 -m unittest discover -s jaffle/derive

Cross-interpreter check. JAFFLE_PYTHONS, a space-separated list of interpreter commands,
names the Pythons that must all write identical bytes. Without it the check covers the
running interpreter only. CI sets it to two minor versions and also sets
JAFFLE_REQUIRE_MINORS=2, which makes the check fail unless that many distinct minor
versions were really exercised, so a misconfigured job cannot pass on one version.
"""

import csv
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

import addresses

HERE = os.path.dirname(os.path.abspath(__file__))
JAFFLE = os.path.dirname(HERE)
DATA = os.path.join(JAFFLE, "data")
SCRIPT = os.path.join(HERE, "addresses.py")


def read_rows(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.out = os.path.join(self.tmp, "out")
        addresses.generate(DATA, self.out)
        self.rows = read_rows(os.path.join(self.out, addresses.OUTPUT))
        self.customers = read_rows(os.path.join(DATA, "customers.csv"))


class Coverage(Case):
    def test_every_customer_appears_exactly_once_and_no_other_id(self):
        ids = [r["customer_id"] for r in self.rows]
        self.assertEqual(ids, [c["id"] for c in self.customers])  # same set, same order
        self.assertEqual(len(ids), len(set(ids)))
        self.assertGreater(len(ids), 0)


class WellFormed(Case):
    def test_every_row(self):
        for r in self.rows:
            street, _, number = r["street"].partition(" ")
            self.assertTrue(number.isdigit() and 1 <= int(number) <= addresses.HOUSE_MAX, r)
            self.assertEqual(str(int(number)), number, r)
            self.assertIn(street, addresses.STREETS, r)
            digits, _, letters = r["postal_code"].partition(" ")
            self.assertIn(int(digits), addresses.POSTAL_DIGITS, r)
            self.assertIn(letters, addresses.POSTAL_LETTERS, r)
            self.assertEqual(r["city"], "Utrecht")
            self.assertEqual(r["country"], "NL")

    def test_lists_are_sound(self):
        self.assertEqual(len(set(addresses.STREETS)), len(addresses.STREETS))
        self.assertEqual(len(set(addresses.POSTAL_LETTERS)), len(addresses.POSTAL_LETTERS))
        for pair in addresses.POSTAL_LETTERS:
            self.assertRegex(pair, r"^[A-Z]{2}$")
            self.assertNotIn(pair, ("SA", "SD", "SS"))

    def test_file_conventions(self):
        with open(os.path.join(self.out, addresses.OUTPUT), "rb") as fh:
            data = fh.read()
        self.assertTrue(data.startswith(b"customer_id,street,postal_code,city,country\r\n"))
        self.assertTrue(data.endswith(b"\r\n"))
        self.assertNotIn(b"\n", data.replace(b"\r\n", b""))


class Determinism(unittest.TestCase):
    """Review Focus 1: identical bytes under different hash seeds and Python versions."""

    def run_script(self, python, seed):
        out = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, out, ignore_errors=True)
        dest = os.path.join(out, "o")
        env = {k: v for k, v in os.environ.items() if k != "PYTHONHASHSEED"}
        env["PYTHONHASHSEED"] = str(seed)
        subprocess.run(python.split() + [SCRIPT, DATA, dest], env=env, check=True,
                       stdout=subprocess.DEVNULL)
        with open(os.path.join(dest, addresses.OUTPUT), "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()

    def minor(self, python):
        res = subprocess.run(python.split() + ["-c", "import sys;print('%d.%d'%sys.version_info[:2])"],
                             check=True, capture_output=True, text=True)
        return res.stdout.strip()

    def test_identical_across_hash_seeds_and_python_versions(self):
        pythons = os.environ.get("JAFFLE_PYTHONS", "").split() or [sys.executable]
        minors, digests = set(), {}
        for python in pythons:
            minors.add(self.minor(python))
            for seed in (1, 4242):
                digests[(python, seed)] = self.run_script(python, seed)
        self.assertEqual(len(set(digests.values())), 1, digests)
        required = os.environ.get("JAFFLE_REQUIRE_MINORS")
        if required is None and os.environ.get("CI"):
            self.fail("JAFFLE_REQUIRE_MINORS must be set in CI so one Python version cannot pass")
        need = int(required or "1")
        self.assertGreaterEqual(len(minors), need, "exercised Python versions: %s" % sorted(minors))


class Pinned(Case):
    def test_output_equals_the_committed_file(self):
        with open(os.path.join(self.out, addresses.OUTPUT), "rb") as a, \
                open(os.path.join(DATA, addresses.OUTPUT), "rb") as b:
            self.assertEqual(a.read(), b.read())


class Distribution(Case):
    def test_not_degenerate(self):
        self.assertGreater(len({r["street"].partition(" ")[0] for r in self.rows}), 1)
        self.assertGreater(len({r["postal_code"] for r in self.rows}), 1)
        self.assertGreater(len({r["street"].partition(" ")[2] for r in self.rows}), 1)


class Refusal(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_tampered_customers_is_refused(self):
        root = os.path.join(self.tmp, "jaffle")
        shutil.copytree(DATA, os.path.join(root, "data"))
        shutil.copy(os.path.join(JAFFLE, "SHA256SUMS"), root)
        path = os.path.join(root, "data", "customers.csv")
        with open(path, "rb") as fh:
            data = fh.read()
        with open(path, "wb") as fh:
            fh.write(data.replace(b"Nikos Lefebvre", b"Nikos Lefebvrf", 1))
        out = os.path.join(self.tmp, "out")
        with self.assertRaises(addresses.AddressError):
            addresses.generate(os.path.join(root, "data"), out)
        self.assertFalse(os.path.exists(out))

    def test_missing_sums_line_is_refused(self):
        root = os.path.join(self.tmp, "jaffle")
        shutil.copytree(DATA, os.path.join(root, "data"))
        with open(os.path.join(root, "SHA256SUMS"), "w") as fh:
            fh.write("0" * 64 + "  orders.csv\n")
        with self.assertRaises(addresses.AddressError):
            addresses.generate(os.path.join(root, "data"), os.path.join(self.tmp, "out"))


if __name__ == "__main__":
    unittest.main()
