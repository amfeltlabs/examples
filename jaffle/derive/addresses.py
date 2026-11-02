#!/usr/bin/env python3
"""Give every Jaffle customer a deterministic, synthetic Utrecht address.

Usage: python3 addresses.py <data-dir> <out-dir>

<data-dir> is the published jaffle/data/. Its customers.csv is checked against the digest
in the SHA256SUMS beside it (<data-dir>/../SHA256SUMS) before anything is read. <out-dir>
must not exist or must be empty; customer_addresses.csv is written there and nothing else.

The output has the columns customer_id,street,postal_code,city,country, one row per
customer, in the order of customers.csv, with the CSV conventions of the other data files
(header row, CRLF line endings, minimal quoting). Every customer is a Utrecht customer:
only the Utrecht store trades in the pinned quarter. The later move to Rotterdam is
authored by hand elsewhere and is not modeled here.

These addresses are our own synthetic additions. jafgen did not write them.

Determinism. Every choice is a SHA-256 over a fixed, named salt and the customer id
string (UTF-8), reduced modulo the size of the list it picks from. There is no hash(),
random, clock or environment input, so the output is identical across runs, interpreter
versions and PYTHONHASHSEED values. Each field has its own salt, so the fields are
independent of each other.

An address reads the Dutch way: street, then house number, and a postal code of four
digits, a space and two letters. The postal code is drawn independently of the street.

Python standard library only.
"""

import csv
import hashlib
import io
import os
import re
import sys

# Invented street names, so no address here is a real one. None of them was found by
# name anywhere in the Netherlands in the national address register (BAG) on 2026-11-02,
# through the PDOK locatieserver:
#   curl -s --get https://api.pdok.nl/bzk/locatieserver/search/v3_1/free \
#     --data-urlencode 'q=straatnaam:"Kaneelsingel"' --data-urlencode 'fq=type:weg'
# Candidates it did find, such as Saffraanweg in Utrecht, were dropped.
STREETS = (
    "Kaneelsingel", "Pepermuntlaan", "Wafelstraat", "Krentenhof", "Gemberkade",
    "Anijsgracht", "Nootmuskaatlaan", "Kruidnagelhof", "Vanillekade", "Honingsingel",
    "Kaneelplein", "Stroopwafelhof", "Beschuitkade", "Spijsgracht", "Boterkoeklaan",
    "Peperkoekstraat", "Kruimelsteeg", "Koekjeskade", "Zuurdesemstraat", "Havermoutweg",
    "Speltsingel", "Marsepeinkade",
)

# The digits of a Utrecht postal code, and the letters of a Dutch one. The Dutch postal
# service does not use SA, SD or SS, so neither does this list.
POSTAL_DIGITS = tuple(range(3511, 3586))
POSTAL_LETTERS = tuple(a + b for a in "ABCEGHJKLMNPRSTVWXZ" for b in "ABCEGHJKLMNPRSTVWXZ"
                       if a + b not in ("SA", "SD", "SS"))

# House numbers run from 1 to HOUSE_MAX inclusive.
HOUSE_MAX = 299
CITY = "Utrecht"
COUNTRY = "NL"
HEADER = ["customer_id", "street", "postal_code", "city", "country"]

# One salt per field, so the fields are independent. Never change these: the published
# file is the output they produce.
SALT_HOUSE = "jaffle.address.house"
SALT_STREET = "jaffle.address.street"
SALT_POSTAL_DIGITS = "jaffle.address.postal.digits"
SALT_POSTAL_LETTERS = "jaffle.address.postal.letters"

CUSTOMERS = "customers.csv"
OUTPUT = "customer_addresses.csv"


class AddressError(Exception):
    pass


def pick(salt, customer_id, modulus):
    """A stable integer in [0, modulus) from the salt and the customer id."""
    digest = hashlib.sha256(("%s:%s" % (salt, customer_id)).encode("utf-8")).digest()
    return int.from_bytes(digest, "big") % modulus


def address(customer_id):
    """The address record of one customer."""
    house = 1 + pick(SALT_HOUSE, customer_id, HOUSE_MAX)
    street = STREETS[pick(SALT_STREET, customer_id, len(STREETS))]
    digits = POSTAL_DIGITS[pick(SALT_POSTAL_DIGITS, customer_id, len(POSTAL_DIGITS))]
    letters = POSTAL_LETTERS[pick(SALT_POSTAL_LETTERS, customer_id, len(POSTAL_LETTERS))]
    return [customer_id, "%s %d" % (street, house), "%d %s" % (digits, letters), CITY, COUNTRY]


def recorded_digest(sums_path, name):
    """The SHA-256 SHA256SUMS records for name; exactly one line must name it."""
    with open(sums_path, encoding="utf-8", newline="") as fh:
        lines = fh.read().split("\n")
    found = []
    for line in lines:
        if line == "":
            continue
        m = re.fullmatch(r"([0-9a-f]{64})  ([A-Za-z0-9_.-]+)", line)
        if not m:
            raise AddressError("%s holds a malformed line: %r" % (sums_path, line))
        if m.group(2) == name:
            found.append(m.group(1))
    if len(found) != 1:
        raise AddressError("%s lists %s %d times, not once" % (sums_path, name, len(found)))
    return found[0]


def read_customer_ids(data_dir):
    """Verify customers.csv against SHA256SUMS, then return its ids in file order."""
    path = os.path.join(data_dir, CUSTOMERS)
    sums = os.path.join(data_dir, os.pardir, "SHA256SUMS")
    if not os.path.isfile(path):
        raise AddressError("%s is missing" % path)
    with open(path, "rb") as fh:
        data = fh.read()
    want = recorded_digest(sums, CUSTOMERS)
    got = hashlib.sha256(data).hexdigest()
    if got != want:
        raise AddressError("%s: sha256 %s, SHA256SUMS records %s" % (CUSTOMERS, got, want))
    rows = list(csv.reader(io.StringIO(data.decode("utf-8"), newline="")))
    if not rows or rows[0] != ["id", "name"]:
        raise AddressError("%s: unexpected header" % CUSTOMERS)
    ids = [r[0] for r in rows[1:]]
    if len(set(ids)) != len(ids) or "" in ids:
        raise AddressError("%s: customer ids are not unique and non-empty" % CUSTOMERS)
    return ids


def render(ids):
    """The bytes of customer_addresses.csv for the given customer ids."""
    out = io.StringIO(newline="")
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(HEADER)
    for customer_id in ids:
        writer.writerow(address(customer_id))
    return out.getvalue().encode("utf-8")


def generate(data_dir, dest):
    """Verify the input, write customer_addresses.csv into dest, return its bytes."""
    if os.path.exists(dest) and (not os.path.isdir(dest) or os.listdir(dest)):
        raise AddressError("%s exists and is not an empty directory" % dest)
    result = render(read_customer_ids(data_dir))
    os.makedirs(dest, exist_ok=True)
    with open(os.path.join(dest, OUTPUT), "wb") as fh:
        fh.write(result)
    return result


def main(argv):
    if len(argv) != 3:
        sys.stderr.write("usage: python3 addresses.py <data-dir> <out-dir>\n")
        return 2
    try:
        result = generate(argv[1], argv[2])
    except AddressError as err:
        sys.stderr.write("addresses.py: FAIL: %s\n" % err)
        return 1
    print("%-24s %9d bytes  %s" % (OUTPUT, len(result), hashlib.sha256(result).hexdigest()))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
