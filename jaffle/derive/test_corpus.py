"""Tests of the published corpus. Standard library only.

They read jaffle/ as it is committed and need nothing but a checkout. reproduce.sh runs
them with the other tests of this directory:

    python3 -m unittest discover -s jaffle/derive -p 'test_corpus.py'

Two of them scan what a release ships for what must not be in it:
test_no_shipped_file_reads_as_american and test_no_shipped_file_holds_an_excluded_name read
every file release/ASSET_FILES lists except the license, which is kept verbatim. Two more
scan jaffle/README.md, which does not ship. The others check the scan's patterns, the data,
counts.py and run2.sql. The list of old names is the point of the scan, so it names them.
The word scan lives here and not in db/check_load.sql because that file ships: it would have
to carry the words.
"""

import csv
import decimal
import importlib.util
import os
import re
import unittest

import addresses
import localize

HERE = os.path.dirname(os.path.abspath(__file__))
JAFFLE = os.path.dirname(HERE)
LICENSE = "LICENSE.jaffle-shop-generator"  # the license's own words, kept verbatim

# Whole words, any case: the place names, the tax and currency story, and the text jafgen
# wrote that a European corpus must not carry. kraut alone is also a slur; sauerkraut, the
# food, is not, and whole words leave it alone.
AMERICAN = re.compile(
    r"\b(philadelphia|brooklyn|chicago|san francisco|new orleans|los angeles|pittsburgh"
    r"|pennsylvania|new york|california|illinois|louisiana|america|american|usps|zip code"
    r"|zip codes|sales tax|dollar|dollars|usd|nutellaphone|nutella|flame impala|impala"
    r"|krautback|kraut|adele|kevin parker|vanilla ice|ghost pepper|agua fresca|al pastor|16oz"
    r"|catherine navarro|jessica lewis|kristin frank|melvin ingram|priya natarajan"
    r"|tomas alvarez)\b", re.IGNORECASE)
# Forms a word search does not catch: a US country code in a quoted value, a dollar amount,
# a USPS street suffix after a name, a US phone number. A 5-digit ZIP code is not in here,
# because plain numbers fill the data: the Addresses and Run2 tests hold every address to a
# Dutch postal code instead.
AMERICAN_FORMS = re.compile(
    r"'US'|\"US\"|,US\r?$|\$\d|\b[A-Z][a-z]+ (St|Ave|Blvd)\b|\b\d{3}-\d{3}-\d{4}\b",
    re.MULTILINE)
# Anchored at both ends, because assertRegex searches: unanchored, 13585 LG would pass.
POSTAL_CODE = re.compile(r"\A[1-9][0-9]{3} (?!SA|SD|SS)[A-Z]{2}\Z")
STREET = re.compile(r"\A[A-Z][a-z]+ [1-9][0-9]*\Z")
HOUSE_NUMBER = re.compile(r"\A[1-9][0-9]*\Z")
ROTTERDAM_POSTAL_CODE = re.compile(r"\A30[0-9]{2} ")
GUARD_ID = "d0000000-0000-4000-8000-000000000001"
COUNTS = {"customers": 232, "customer_addresses": 232, "stores": 6, "orders": 6837,
          "order_items": 10025, "products": 10, "supplies": 65, "tweets": 2161}


def rows(name):
    with open(os.path.join(JAFFLE, "data", name + ".csv"), newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def shipped():
    with open(os.path.join(JAFFLE, "release", "ASSET_FILES"), encoding="utf-8") as fh:
        return [line.strip() for line in fh if line.strip() and line.strip() != LICENSE]


class NothingAmerican(unittest.TestCase):
    def test_no_shipped_file_reads_as_american(self):
        for name in shipped():
            with open(os.path.join(JAFFLE, name), encoding="utf-8", newline="") as fh:
                text = fh.read()
            found = sorted({m.group(0) for m in AMERICAN.finditer(text)}
                           | {m.group(0) for m in AMERICAN_FORMS.finditer(text)})
            self.assertEqual(found, [], name)

    def test_the_readme_reads_as_european(self):
        with open(os.path.join(JAFFLE, "README.md"), encoding="utf-8") as fh:
            self.assertIsNone(AMERICAN.search(fh.read()))


class TheScan(unittest.TestCase):
    """The scan is what sees free text, so it must see these and leave the food alone."""

    def test_it_sees_a_name_a_product_a_place_and_a_slur(self):
        for text in ("Catherine Navarro", "a flame impala", "Best jaffles in Brooklyn",
                     "the krautback", "a kraut royale"):
            self.assertIsNotNone(AMERICAN.search(text), text)

    def test_it_leaves_sauerkraut_alone(self):
        self.assertIsNone(AMERICAN.search("house-pickled cabbage sauerkraut and mustard"))


class RealNames(unittest.TestCase):
    """A name localize.EXCLUDED_NAMES holds is in nothing a release ships."""

    NAMES = re.compile(r"\b(%s)\b" % "|".join(re.escape(n) for n in sorted(localize.EXCLUDED_NAMES)))

    def test_no_shipped_file_holds_an_excluded_name(self):
        for name in shipped():
            with open(os.path.join(JAFFLE, name), encoding="utf-8", newline="") as fh:
                self.assertEqual(sorted(set(m.group(0) for m in self.NAMES.finditer(fh.read()))), [], name)

    def test_the_readme_holds_none_either(self):
        with open(os.path.join(JAFFLE, "README.md"), encoding="utf-8") as fh:
            self.assertIsNone(self.NAMES.search(fh.read()))

    def test_the_names_taken_out_of_the_corpus_stay_out(self):
        for name in ("Mila Jansen", "Sanne van Dijk", "Tomasz Wrona", "Zoë Rossi"):
            self.assertIsNotNone(self.NAMES.search("a customer called %s." % name), name)


class Counts(unittest.TestCase):
    def test_row_counts_did_not_change(self):
        for name, count in COUNTS.items():
            self.assertEqual(len(rows(name)), count, name)


class Forms(unittest.TestCase):
    """Each form refuses what it must, so a loosened pattern fails before bad data arrives."""

    def refuses(self, pattern, *values):
        for value in values:
            self.assertIsNone(pattern.search(value), value)

    def test_a_postal_code_is_the_whole_value(self):
        self.assertRegex("3585 LG", POSTAL_CODE)
        self.refuses(POSTAL_CODE, "13585 LG", "3585 LGX", "3585 lg", "0585 LG", "3585 SA", "19118")

    def test_a_street_is_the_whole_value(self):
        self.assertRegex("Kaneelsingel 12", STREET)
        self.refuses(STREET, "1 Kaneelsingel 12", "Kaneelsingel 012", "Kaneelsingel 12a")

    def test_a_house_number_is_the_whole_value(self):
        self.assertRegex("41", HOUSE_NUMBER)
        self.refuses(HOUSE_NUMBER, "041", "41a", "a41")

    def test_a_rotterdam_postal_code_starts_with_30(self):
        self.assertRegex("3021 KV", ROTTERDAM_POSTAL_CODE)
        self.refuses(ROTTERDAM_POSTAL_CODE, "13021 KV", "3521 KV")


class Addresses(unittest.TestCase):
    def test_every_address_is_a_utrecht_one(self):
        for r in rows("customer_addresses"):
            self.assertRegex(r["street"], STREET, r)
            self.assertRegex(r["postal_code"], POSTAL_CODE, r)
            self.assertEqual((r["city"], r["country"]), ("Utrecht", "NL"), r)


class Vat(unittest.TestCase):
    def test_every_order_is_exact_at_its_stores_rate(self):
        rate = {s["id"]: decimal.Decimal(s["tax_rate"]) for s in rows("stores")}
        for o in rows("orders"):
            subtotal, tax = int(o["subtotal"]), int(o["tax_paid"])
            self.assertEqual(decimal.Decimal(tax), subtotal * rate[o["store_id"]], o)
            self.assertEqual(int(o["order_total"]), subtotal + tax, o)

    def test_rates_are_the_documented_ones(self):
        self.assertEqual({s["name"]: s["tax_rate"] for s in rows("stores")},
                         {"Utrecht": "0.09", "Rotterdam": "0.09", "Antwerp": "0.12",
                          "Lyon": "0.10", "Bologna": "0.10", "Valencia": "0.10"})


class Files(unittest.TestCase):
    def test_every_data_file_keeps_its_crlf_line_endings(self):
        for name in sorted(os.listdir(os.path.join(JAFFLE, "data"))):
            with open(os.path.join(JAFFLE, "data", name), "rb") as fh:
                data = fh.read()
            self.assertTrue(data.endswith(b"\r\n"), name)
            self.assertNotIn(b"\n", data.replace(b"\r\n", b""), name)


class CountsFile(unittest.TestCase):
    """counts.py refuses a reveal that leaves the European story."""

    def setUp(self):
        spec = importlib.util.spec_from_file_location("counts", os.path.join(JAFFLE, "expected", "counts.py"))
        self.counts = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.counts)
        with open(self.counts.DEFAULT_FILE, encoding="utf-8") as fh:
            self.text = fh.read()

    def problems(self, old="", new=""):
        self.assertIn(old, self.text)
        return "\n".join(self.counts.validate(self.counts.parse(self.text.replace(old, new))))

    def test_the_published_file_is_valid(self):
        self.assertEqual(self.problems(), "")

    def test_a_zip_code_is_refused(self):
        self.assertIn("reveal.before.postal_code", self.problems('"3585 LG"', '"19118"'))
        self.assertIn("reveal.deleted.before.postal_code", self.problems('"3577 EK"', '"19149"'))

    def test_a_lowercase_or_leading_zero_postal_code_is_refused(self):
        self.assertIn("postal_code", self.problems('"3585 LG"', '"3585 lg"'))
        self.assertIn("postal_code", self.problems('"3585 LG"', '"0585 LG"'))

    def test_a_postal_code_with_a_tail_is_refused(self):
        self.assertIn("reveal.before.postal_code", self.problems('"3585 LG"', '"3585 LGX"'))
        self.assertIn("reveal.after.postal_code", self.problems('"3021 KV"', '"3021 KVX"'))
        self.assertIn("reveal.deleted.before.postal_code", self.problems('"3577 EK"', '"3577 EKX"'))

    def test_the_move_must_reach_rotterdam(self):
        self.assertIn("reveal.after.city", self.problems('city: "Rotterdam"', 'city: "Amsterdam"'))
        self.assertIn("reveal.after.postal_code", self.problems('"3021 KV"', '"3521 KV"'))

    def test_the_reveal_is_what_the_data_and_run_2_hold(self):
        reveal = self.counts.parse(self.text)["reveal"]
        customers = {r["id"]: r["name"] for r in rows("customers")}
        address = {r["customer_id"]: r for r in rows("customer_addresses")}
        for block in (reveal, reveal["deleted"]):
            self.assertEqual(block["name"], customers[block["customer_id"]])
            self.assertEqual(dict(block["before"]), {k: address[block["customer_id"]][k]
                                                     for k in block["before"]})
        self.assertIn(tuple(reveal["after"][k] for k in ("street", "postal_code", "city", "country")),
                      run2_addresses(read_run2()))


def read_run2():
    with open(os.path.join(JAFFLE, "db", "run2.sql"), encoding="utf-8") as fh:
        return fh.read()


def run2_addresses(sql):
    """(street, postal code, city, country) of each address run2.sql writes. The move sets
    no country: it keeps the customer's NL."""
    found = re.findall(r"\('d0000000-[0-9a-f-]+', '[^']+',\s+'([^']+)', '([^']+)', '([^']+)', '([^']+)'\)", sql)
    found += [m + ("NL",) for m in re.findall(
        r"SET street = '([^']+)',\s+postal_code = '([^']+)',\s+city = '([^']+)'", sql)]
    return found


class Run2(unittest.TestCase):
    def setUp(self):
        self.sql = read_run2()

    def test_every_address_is_invented_and_dutch(self):
        found = run2_addresses(self.sql)
        self.assertEqual(len(found), 3, found)  # the two new customers and the move
        for street, postal_code, city, country in found:
            name, _, number = street.rpartition(" ")
            self.assertIn(name, addresses.STREETS, street)
            self.assertRegex(number, HOUSE_NUMBER, street)
            self.assertRegex(postal_code, POSTAL_CODE, street)
            self.assertEqual(country, "NL", street)
            if city == "Rotterdam":
                self.assertRegex(postal_code, ROTTERDAM_POSTAL_CODE, street)
            else:
                self.assertEqual(city, "Utrecht", street)
                self.assertIn(int(postal_code[:4]), addresses.POSTAL_DIGITS, street)

    def test_the_rename_starts_from_the_name_the_corpus_gives(self):
        renamed = re.search(r"SET name = '([^']+)'\s+WHERE id = '([0-9a-f-]+)'", self.sql)
        before = {r["id"]: r["name"] for r in rows("customers")}[renamed.group(2)]
        self.assertTrue(renamed.group(1).startswith(before + "-"), renamed.groups())

    def test_a_new_customer_has_a_name_of_their_own(self):
        names = re.findall(r"\('d0000000-0000-4000-8000-00000000000[12]', '([^']+)'", self.sql)
        self.assertEqual(len(names), 2, names)
        self.assertEqual(set(names) & {r["name"] for r in rows("customers")}, set())

    def test_each_customer_it_names_is_the_one_the_corpus_holds(self):
        customers = {r["id"]: r["name"] for r in rows("customers")}
        comments = "\n".join(l for l in self.sql.split("\n") if l.lstrip().startswith("--"))
        for customer in ("8224248d-b37a-4e73-8571-24b7d3435150", "09790bf5-a40a-4b7a-8446-a1a224fcb07b",
                         "6350f2b4-88cb-4522-ab80-bce28ec10633", "e15678d3-3632-4e50-a703-f0ff9014081d"):
            self.assertIn(customers[customer], comments, customer)

    def test_the_guards_marker_is_a_row_run_2_inserts(self):
        guard = re.search(r"IF EXISTS \(SELECT FROM sales\.customers WHERE id = '([^']+)'\)", self.sql)
        self.assertIsNotNone(guard)
        self.assertEqual(guard.group(1), GUARD_ID)
        self.assertRegex(self.sql, r"INSERT INTO sales\.customers [^;]*'" + GUARD_ID + "'")

    def test_the_stack_fixture_can_read_run_2(self):
        # The six statements stack's _jaffle_run2_fact matches, each exactly once, with
        # comments stripped as it strips them.
        sql = "\n".join(l for l in self.sql.split("\n") if not l.lstrip().startswith("--"))
        uuid = r"'([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})'"
        for fact in (r"DELETE FROM sales\.orders\s+WHERE id\s*=\s*" + uuid,
                     r"DELETE FROM marketing\.tweets\s+WHERE id\s*=\s*" + uuid,
                     r"UPDATE sales\.customers\s+SET name\s*=\s*'[^']+'\s+WHERE id\s*=\s*" + uuid,
                     r"UPDATE sales\.customers\s+SET name\s*=\s*'([^']+)'\s+WHERE id",
                     r"UPDATE catalog\.products\s+SET price\s*=\s*\d+\s+WHERE sku\s*=\s*'([^']+)'",
                     r"UPDATE catalog\.products\s+SET price\s*=\s*(\d+)\s+WHERE sku"):
            self.assertEqual(len(re.findall(fact, sql)), 1, fact)


if __name__ == "__main__":
    unittest.main()
