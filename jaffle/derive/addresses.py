#!/usr/bin/env python3
"""Give every Jaffle customer a deterministic, synthetic Philadelphia address.

Usage: python3 addresses.py <data-dir> <out-dir>

<data-dir> is the published jaffle/data/. Its customers.csv is checked against the digest
in the SHA256SUMS beside it (<data-dir>/../SHA256SUMS) before anything is read. <out-dir>
must not exist or must be empty; customer_addresses.csv is written there and nothing else.

The output has the columns customer_id,street,postal_code,city,country, one row per
customer, in the order of customers.csv, with the CSV conventions of the other data files
(header row, CRLF line endings, minimal quoting). Every customer is a Philadelphia
customer: only the Philadelphia store trades in the pinned quarter. The later move to
Brooklyn is authored by hand elsewhere and is not modeled here.

These addresses are our own synthetic additions. jafgen did not write them.

Determinism. Every choice is a SHA-256 over a fixed, named salt and the customer id
string (UTF-8), reduced modulo the size of the list it picks from. There is no hash(),
random, clock or environment input, so the output is identical across runs, interpreter
versions and PYTHONHASHSEED values. Each field has its own salt, so the fields are
independent of each other.

Street and ZIP are not geographically paired: a Market St address may carry a ZIP code
that does not contain Market St. That is acceptable for synthetic data.

Python standard library only.
"""

import csv
import hashlib
import io
import os
import re
import sys

# Real Philadelphia street names in USPS style (public knowledge of the city's street grid).
STREETS = (
    "Market St", "Chestnut St", "Walnut St", "Spruce St", "Pine St", "Locust St",
    "Arch St", "Race St", "Vine St", "South St", "Lombard St", "Christian St",
    "Washington Ave", "Girard Ave", "Fairmount Ave", "Spring Garden St", "Callowhill St",
    "Passyunk Ave", "Frankford Ave", "Germantown Ave", "Ridge Ave", "Lancaster Ave",
    "Baltimore Ave", "Broad St", "Bainbridge St",
)

# Philadelphia ZIP codes in 19102-19154. Established on 2026-10-10 by querying every code
# in that range against the GeoNames-based api.zippopotam.us: all 53 resolve to
# Philadelphia, PA. Four are left out because they are not residential delivery codes I
# can vouch for (19105, 19108 and 19110 are PO-box or single-organization codes; 19117 is
# a boundary code that serves Montgomery County addresses), leaving 49.
ZIP_CODES = (
    "19102", "19103", "19104", "19106", "19107", "19109", "19111", "19112", "19113",
    "19114", "19115", "19116", "19118", "19119", "19120", "19121", "19122", "19123",
    "19124", "19125", "19126", "19127", "19128", "19129", "19130", "19131", "19132",
    "19133", "19134", "19135", "19136", "19137", "19138", "19139", "19140", "19141",
    "19142", "19143", "19144", "19145", "19146", "19147", "19148", "19149", "19150",
    "19151", "19152", "19153", "19154",
)

# House numbers run from 1 to HOUSE_MAX inclusive.
HOUSE_MAX = 4999
CITY = "Philadelphia"
COUNTRY = "US"
HEADER = ["customer_id", "street", "postal_code", "city", "country"]

# One salt per field, so the fields are independent. Never change these: the published
# file is the output they produce.
SALT_HOUSE = "jaffle.address.house"
SALT_STREET = "jaffle.address.street"
SALT_ZIP = "jaffle.address.zip"

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
    postal = ZIP_CODES[pick(SALT_ZIP, customer_id, len(ZIP_CODES))]
    return [customer_id, "%d %s" % (house, street), postal, CITY, COUNTRY]


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
