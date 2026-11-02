#!/usr/bin/env python3
"""Make the shifted Jaffle corpus European.

Usage: python3 localize.py <shift-dir> <out-dir>

<shift-dir> holds the seven files shift.py wrote. They are checked against
shift.SHA256SUMS beside this script before anything is read. <out-dir> must not exist or
must be empty; the seven localized files are written there and nothing else.

This step replaces what reads as American or names a real brand or person. All ids,
timestamps, row counts, row order, SKUs, prices and subtotals stay as shift.py wrote them,
and every line this step leaves alone stays the same bytes.

    customers.csv    every name is drawn again from the lists below
    stores.csv       each store gets a European city and a VAT rate; dates stay
    orders.csv       tax_paid and order_total are computed again at the store's rate
    products.csv     some names and descriptions change; SKUs and prices stay
    supplies.csv     some names change; ids, costs and SKUs stay
    tweets.csv       the old product names become the new ones
    order_items.csv  passes through

Currency and VAT. Amounts are integer euro cents. subtotal excludes VAT, tax_rate is the
VAT rate of the store, tax_paid is subtotal * tax_rate and order_total is subtotal +
tax_paid. One flat rate per store covers the whole menu, because the corpus has one rate
column and no VAT category on a product. Every subtotal is a whole number of euros, so at
two-decimal rates tax_paid is a whole number of cents and nothing rounds; a value that
would need rounding is refused. The rates are illustrative hospitality rates, not tax
advice. Only the two Dutch stores trade, so only 0.09 reaches an order.

Determinism. A customer's name is a SHA-256 over a fixed salt, the customer id and an
attempt number, reduced modulo the list sizes, with the attempt raised until the full name
is neither taken nor on EXCLUDED_NAMES. There is no hash(), random, clock or environment
input.

Python standard library only.
"""

import csv
import decimal
import hashlib
import io
import os
import re
import sys

SUMS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shift.SHA256SUMS")
FILES = ("customers.csv", "order_items.csv", "orders.csv", "products.csv", "stores.csv",
         "supplies.csv", "tweets.csv")
CRLF = "\r\n"

SALT_FIRST = "jaffle.name.first"
SALT_LAST = "jaffle.name.last"

# The city and the VAT rate of each store, by store id. Opening dates stay as they are.
STORES = {
    "2efff8c6-64e3-4e79-8257-c78ffd3395d0": ("Utrecht", "0.09"),
    "c9471206-c14b-46c1-a93b-fe94ee386b24": ("Rotterdam", "0.09"),
    "a8462999-6677-4b21-8f67-666c4f62a74e": ("Antwerp", "0.12"),
    "dd56587a-b704-40df-9408-551538eecbf6": ("Lyon", "0.10"),
    "a4d4c884-42bb-4271-ace9-5fd502956f73": ("Bologna", "0.10"),
    "06591c99-a5fb-4863-bb70-22b31b5c0fe1": ("Valencia", "0.10"),
}

# Names drawn for a shop in the Netherlands: mostly Dutch, with the rest of Europe in it.
FIRST_NAMES = (
    "Anouk", "Bram", "Daan", "Emma", "Eva", "Fenna", "Floor", "Hugo", "Isa", "Jasper",
    "Joris", "Julia", "Koen", "Lars", "Lieke", "Lotte", "Luuk", "Maartje", "Marit", "Milan",
    "Mila", "Nina", "Noa", "Olivier", "Pieter", "Quinten", "Roos", "Ruben", "Sanne", "Sem",
    "Sofie", "Stijn", "Tess", "Thijs", "Vera", "Wout", "Yara", "Zoë", "Léa", "Mateusz",
    "Inês", "Giulia", "Søren", "Aoife", "Nikos", "Elif",
)
LAST_NAMES = (
    "de Vries", "Jansen", "Bakker", "Visser", "Smit", "Meijer", "de Boer", "Mulder",
    "de Groot", "Bos", "Vos", "Peters", "Hendriks", "van Dijk", "Dekker", "Brouwer", "Kok",
    "Jacobs", "Vermeulen", "van den Berg", "van Leeuwen", "Maes", "Claes", "Wouters",
    "Schmitt", "Keller", "Novak", "Kowalski", "Rossi", "Moreau", "Lefebvre", "Lindqvist",
    "Nielsen", "Costa", "Papadakis",
)

# Full names that must not be drawn. The people in this data are invented, but a drawn pair
# can still match a real person: the lists are of common names and this screening is not
# exhaustive. The bar is that a name is not a well-known person's. On 2026-11-02 every pair
# of the two lists above was looked up as a Wikipedia article title in twelve languages and
# as a Wikidata human, each also spelled without diacritics, and each pair found is here,
# whoever it belongs to. Of the 232 names then drawn, 225 were searched for as an exact
# phrase in English, Dutch and German Wikipedia text and 168 on the web, the four drawn
# names run 2 tells its story with among them. A hit from those searches, on the exact name
# or a near one, is generally here too. Run 2's names only have to meet the bar, so an
# obscure exact match can stay. Tomasz Wrona is on neither list: the entry keeps that name
# out of run 2. A name added here redraws its customer and cannot return, and
# derive/test_corpus.py keeps it out of what a release ships.
#   curl -s -G https://en.wikipedia.org/w/api.php --data-urlencode action=query \
#     --data-urlencode format=json --data-urlencode redirects=1 \
#     --data-urlencode 'titles=Emma Kok'
EXCLUDED_NAMES = frozenset((
    "Anouk Brouwer", "Anouk Claes", "Anouk Dekker", "Anouk Lefebvre", "Anouk Peters",
    "Anouk Visser", "Anouk Vos", "Anouk Wouters", "Anouk de Boer", "Anouk de Groot",
    "Anouk de Vries", "Anouk van Dijk", "Bram Bakker", "Bram Dekker", "Bram Hendriks",
    "Bram Jacobs", "Bram Jansen", "Bram Mulder", "Bram Peters", "Bram Vermeulen",
    "Bram Visser", "Bram Wouters", "Bram de Groot", "Bram van Dijk", "Bram van Leeuwen",
    "Bram van den Berg", "Daan Bakker", "Daan Bos", "Daan Brouwer", "Daan Dekker",
    "Daan Jacobs", "Daan Jansen", "Daan Kok", "Daan Mulder", "Daan Nielsen", "Daan Vermeulen",
    "Daan Visser", "Daan Vos", "Daan Wouters", "Daan de Boer", "Daan de Groot",
    "Daan de Vries", "Daan van Dijk", "Daan van Leeuwen", "Emma Costa", "Emma Jacobs",
    "Emma Keller", "Emma Kok", "Emma Lefebvre", "Emma Lindqvist", "Emma Meijer", "Emma Mulder",
    "Emma Nielsen", "Emma Peters", "Emma Rossi", "Emma Vermeulen", "Eva Bos", "Eva Brouwer",
    "Eva Costa", "Eva Jacobs", "Eva Jansen", "Eva Keller", "Eva Kok", "Eva Lefebvre",
    "Eva Maes", "Eva Meijer", "Eva Mulder", "Eva Nielsen", "Eva Novak", "Eva Papadakis",
    "Eva Peters", "Eva Schmitt", "Eva Smit", "Eva Visser", "Eva de Boer", "Eva de Vries",
    "Eva van Dijk", "Eva van Leeuwen", "Fenna Jansen", "Fenna Meijer", "Fenna de Vries",
    "Floor Bos", "Floor Brouwer", "Floor Jansen", "Floor Keller", "Floor Maes", "Floor Meijer",
    "Floor Peters", "Floor Vermeulen", "Floor Visser", "Floor van Leeuwen",
    "Floor van den Berg", "Giulia Costa", "Giulia Rossi", "Hugo Bakker", "Hugo Bos",
    "Hugo Brouwer", "Hugo Costa", "Hugo Jansen", "Hugo Keller", "Hugo Lefebvre",
    "Hugo Lindqvist", "Hugo Maes", "Hugo Meijer", "Hugo Nielsen", "Hugo Peters", "Hugo Rossi",
    "Hugo Schmitt", "Hugo Smit", "Hugo Visser", "Hugo Wouters", "Hugo de Boer",
    "Hugo de Groot", "Hugo de Vries", "Hugo van den Berg", "Inês Costa", "Inês Keller",
    "Inês Mulder", "Inês Novak", "Inês Rossi", "Inês Smit", "Inês van Dijk", "Isa Costa",
    "Isa Dekker", "Isa Jacobs", "Isa Jansen", "Isa Kok", "Isa de Vries", "Isa van den Berg",
    "Jasper Bakker", "Jasper Bos", "Jasper Hendriks", "Jasper Jacobs", "Jasper Jansen",
    "Jasper Kok", "Jasper Meijer", "Jasper Mulder", "Jasper Smit", "Jasper Wouters",
    "Jasper de Boer", "Jasper de Groot", "Jasper de Vries", "Jasper van Dijk", "Joris Claes",
    "Joris Jacobs", "Joris Jansen", "Joris Lindqvist", "Joris Mulder", "Joris Peters",
    "Joris Smit", "Joris Vermeulen", "Joris Vos", "Joris de Boer", "Joris de Groot",
    "Joris de Vries", "Joris van Dijk", "Joris van Leeuwen", "Joris van den Berg", "Julia Bos",
    "Julia Claes", "Julia Costa", "Julia Jacobs", "Julia Jansen", "Julia Keller",
    "Julia Kowalski", "Julia Maes", "Julia Nielsen", "Julia Novak", "Julia Peters",
    "Julia Schmitt", "Julia Smit", "Julia Visser", "Julia Vos", "Julia Wouters",
    "Julia de Boer", "Julia de Groot", "Julia de Vries", "Julia van Dijk",
    "Julia van den Berg", "Koen Bakker", "Koen Bos", "Koen Brouwer", "Koen Claes",
    "Koen Jacobs", "Koen Jansen", "Koen Maes", "Koen Moreau", "Koen Mulder", "Koen Smit",
    "Koen Vos", "Koen de Groot", "Koen de Vries", "Koen van Dijk", "Koen van Leeuwen",
    "Lars Bakker", "Lars Bos", "Lars Hendriks", "Lars Jansen", "Lars Keller", "Lars Lindqvist",
    "Lars Maes", "Lars Mulder", "Lars Nielsen", "Lars Peters", "Lars Schmitt", "Lars Vos",
    "Lars van Dijk", "Lars van den Berg", "Lieke Brouwer", "Lieke Maes", "Lieke Vermeulen",
    "Lieke Wouters", "Lieke de Boer", "Lieke de Vries", "Lieke van Dijk", "Lotte Bakker",
    "Lotte Bos", "Lotte Brouwer", "Lotte Claes", "Lotte Dekker", "Lotte Hendriks",
    "Lotte Jansen", "Lotte Keller", "Lotte Kok", "Lotte Meijer", "Lotte Nielsen",
    "Lotte Peters", "Lotte Vermeulen", "Lotte de Vries", "Lotte van Leeuwen",
    "Lotte van den Berg", "Luuk Brouwer", "Luuk Dekker", "Luuk Maes", "Luuk Meijer",
    "Luuk Mulder", "Luuk Peters", "Luuk Vos", "Luuk Wouters", "Luuk de Groot", "Luuk van Dijk",
    "Luuk van Leeuwen", "Léa Jacobs", "Léa Jansen", "Léa Keller", "Léa Lefebvre", "Léa Maes",
    "Léa Novak", "Léa Rossi", "Léa Schmitt", "Léa de Boer", "Léa van den Berg",
    "Maartje Bakker", "Maartje Brouwer", "Maartje Costa", "Maartje Jacobs", "Maartje Nielsen",
    "Maartje Peters", "Maartje Vermeulen", "Maartje Visser", "Maartje Wouters",
    "Maartje de Boer", "Maartje de Vries", "Maartje van Dijk", "Maartje van den Berg",
    "Marit Brouwer", "Marit Dekker", "Marit Kok", "Marit Lindqvist", "Marit van Dijk",
    "Marit van den Berg", "Mateusz Kowalski", "Mila Dekker", "Mila Jansen", "Mila Kok",
    "Mila Kowalski", "Mila Maes", "Mila Novak", "Milan Bakker", "Milan Dekker", "Milan Jansen",
    "Milan Nielsen", "Milan Novak", "Milan Smit", "Milan Vermeulen", "Milan van den Berg",
    "Nikos Papadakis", "Nina Bakker", "Nina Claes", "Nina Costa", "Nina Jacobs", "Nina Jansen",
    "Nina Keller", "Nina Novak", "Nina Peters", "Nina Schmitt", "Nina Smit", "Nina Wouters",
    "Nina de Boer", "Nina de Vries", "Nina van Dijk", "Nina van den Berg", "Noa Jansen",
    "Noa Meijer", "Noa Vos", "Noa de Vries", "Noa van Dijk", "Olivier Bakker", "Olivier Costa",
    "Olivier Hendriks", "Olivier Jacobs", "Olivier Jansen", "Olivier Keller", "Olivier Kok",
    "Olivier Lefebvre", "Olivier Maes", "Olivier Moreau", "Olivier Nielsen", "Olivier Peters",
    "Olivier Schmitt", "Olivier Smit", "Olivier Wouters", "Pieter Bakker", "Pieter Bos",
    "Pieter Brouwer", "Pieter Claes", "Pieter Dekker", "Pieter Hendriks", "Pieter Jacobs",
    "Pieter Jansen", "Pieter Kok", "Pieter Maes", "Pieter Meijer", "Pieter Mulder",
    "Pieter Novak", "Pieter Peters", "Pieter Smit", "Pieter Vermeulen", "Pieter Visser",
    "Pieter Vos", "Pieter de Boer", "Pieter de Groot", "Pieter de Vries", "Pieter van Dijk",
    "Pieter van Leeuwen", "Pieter van den Berg", "Quinten Dekker", "Quinten Jacobs",
    "Quinten van den Berg", "Roos Bos", "Roos Hendriks", "Roos Jansen", "Roos Meijer",
    "Roos Smit", "Roos Vos", "Roos Wouters", "Roos de Vries", "Roos van Dijk", "Ruben Bos",
    "Ruben Claes", "Ruben Jacobs", "Ruben Jansen", "Ruben Kok", "Ruben Meijer",
    "Ruben Nielsen", "Ruben Smit", "Ruben Visser", "Sanne Bakker", "Sanne Brouwer",
    "Sanne Dekker", "Sanne Hendriks", "Sanne Jansen", "Sanne Maes", "Sanne Meijer",
    "Sanne Peters", "Sanne Smit", "Sanne Visser", "Sanne de Boer", "Sanne van Dijk",
    "Sanne van den Berg", "Sem Bakker", "Sem Jacobs", "Sem Maes", "Sem Smit", "Sem van Dijk",
    "Sofie Claes", "Sofie Jacobs", "Sofie Keller", "Sofie Lefebvre", "Sofie Nielsen",
    "Sofie Peters", "Sofie Schmitt", "Sofie Vermeulen", "Sofie Visser", "Sofie Wouters",
    "Sofie van den Berg", "Stijn Brouwer", "Stijn Keller", "Stijn Meijer", "Stijn Peters",
    "Stijn Wouters", "Stijn de Groot", "Stijn de Vries", "Søren Nielsen", "Tess Costa",
    "Tess Jansen", "Tess de Vries", "Tess van Dijk", "Thijs Brouwer", "Thijs Dekker",
    "Thijs Hendriks", "Thijs Jansen", "Thijs Peters", "Thijs Vermeulen", "Thijs Visser",
    "Thijs Vos", "Thijs de Boer", "Thijs de Groot", "Thijs de Vries", "Thijs van Dijk",
    "Thijs van Leeuwen", "Thijs van den Berg", "Tomasz Wrona", "Vera Costa", "Vera Hendriks",
    "Vera Jansen", "Vera Keller", "Vera Mulder", "Vera Nielsen", "Vera Novak", "Vera Peters",
    "Vera Schmitt", "Vera Vos", "Vera de Groot", "Vera de Vries", "Wout Hendriks",
    "Wout Jansen", "Wout Peters", "Wout Visser", "Wout de Vries", "Wout van Dijk",
    "Yara Costa", "Yara Rossi", "Zoë Brouwer", "Zoë Maes", "Zoë Peters", "Zoë Rossi",
    "Zoë van Dijk",
))

# name, description by SKU. A SKU not listed keeps both.
PRODUCTS = {
    "JAF-001": ("hazelnut hotline", "chocolate-hazelnut spread and banana jaffle"),
    "JAF-003": ("bratwurst royale", None),
    "JAF-004": ("flame fandango",
                "pulled pork and pineapple marinated in piri-piri sauce, our hottest jaffle"),
    "BEV-003": ("vanilla frost", "iced coffee with house-made vanilla syrup"),
    "BEV-005": ("kiwi cooler", "a sparkling kiwi and lime cooler"),
}

# The names jafgen wrote, as they appear in tweets.
TWEET_NAMES = {
    "nutellaphone who dis?": "hazelnut hotline",
    "the krautback": "bratwurst royale",
    "flame impala": "flame fandango",
    "vanilla ice": "vanilla frost",
    "adele-ade": "kiwi cooler",
}

SUPPLIES = {
    "16oz compostable clear cup": "475ml compostable clear cup",
    "16oz compostable clear lid": "475ml compostable clear lid",
    "french vanilla syrup": "vanilla syrup",
    "ghost pepper sauce": "piri-piri sauce",
    "nutella": "chocolate-hazelnut spread",
}


class LocalizeError(Exception):
    pass


def pick(salt, customer_id, attempt, modulus):
    """A stable integer in [0, modulus) from the salt, the customer id and the attempt."""
    text = "%s:%s:%d" % (salt, customer_id, attempt)
    return int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest(), "big") % modulus


def draw_name(customer_id, used):
    """The first name and surname for customer_id that is neither taken nor excluded."""
    attempt = 0
    while True:
        name = "%s %s" % (FIRST_NAMES[pick(SALT_FIRST, customer_id, attempt, len(FIRST_NAMES))],
                          LAST_NAMES[pick(SALT_LAST, customer_id, attempt, len(LAST_NAMES))])
        if name not in used and name not in EXCLUDED_NAMES:
            return name
        attempt += 1


def vat(subtotal, rate):
    """The VAT on subtotal, in whole cents; a figure that would need rounding is refused."""
    tax = decimal.Decimal(subtotal) * decimal.Decimal(rate)
    if tax != tax.to_integral_value():
        raise LocalizeError("VAT on %s at %s is not a whole number of cents" % (subtotal, rate))
    return int(tax)


def parse(name, text):
    """Header and records of one file; it must survive a CSV round trip unchanged."""
    if not text.endswith(CRLF):
        raise LocalizeError("%s does not end with CRLF" % name)
    rows = list(csv.reader(io.StringIO(text, newline="")))
    if render(rows) != text:
        raise LocalizeError("%s does not survive a CSV round trip" % name)
    return rows[0], rows[1:]


def render(rows):
    out = io.StringIO(newline="")
    csv.writer(out, lineterminator=CRLF).writerows(rows)
    return out.getvalue()


def localize_customers(rows, header):
    if header != ["id", "name"]:
        raise LocalizeError("customers.csv: unexpected header")
    used = set()
    for row in rows:
        row[1] = draw_name(row[0], used)
        used.add(row[1])


def localize_stores(rows, header):
    if header != ["id", "name", "opened_at", "tax_rate"]:
        raise LocalizeError("stores.csv: unexpected header")
    if {row[0] for row in rows} != set(STORES) or len(rows) != len(STORES):
        raise LocalizeError("stores.csv: the store ids are not the ones STORES names")
    for row in rows:
        row[1], row[3] = STORES[row[0]]


def localize_orders(rows, header):
    want = ["id", "customer_id", "ordered_at", "store_id", "subtotal", "tax_paid", "order_total"]
    if header != want:
        raise LocalizeError("orders.csv: unexpected header")
    for row in rows:
        if row[3] not in STORES:
            raise LocalizeError("orders.csv: order %s names an unknown store" % row[0])
        subtotal = int(row[4])
        tax = vat(subtotal, STORES[row[3]][1])
        row[5], row[6] = str(tax), str(subtotal + tax)


def localize_products(rows, header):
    if header != ["sku", "name", "type", "price", "description"]:
        raise LocalizeError("products.csv: unexpected header")
    found = set()
    for row in rows:
        if row[0] in PRODUCTS:
            found.add(row[0])
            name, description = PRODUCTS[row[0]]
            row[1] = name
            row[4] = row[4] if description is None else description
    if found != set(PRODUCTS):
        raise LocalizeError("products.csv: no SKU %s" % sorted(set(PRODUCTS) - found))


def localize_supplies(rows, header):
    if header != ["id", "name", "cost", "perishable", "sku"]:
        raise LocalizeError("supplies.csv: unexpected header")
    found = set()
    for row in rows:
        if row[1] in SUPPLIES:
            found.add(row[1])
            row[1] = SUPPLIES[row[1]]
    if found != set(SUPPLIES):
        raise LocalizeError("supplies.csv: no supply %s" % sorted(set(SUPPLIES) - found))


def localize_tweets(rows, header):
    if header != ["id", "customer_id", "tweeted_at", "content"]:
        raise LocalizeError("tweets.csv: unexpected header")
    for row in rows:
        for old, new in TWEET_NAMES.items():
            row[3] = row[3].replace(old, new)


STEPS = {
    "customers.csv": localize_customers,
    "stores.csv": localize_stores,
    "orders.csv": localize_orders,
    "products.csv": localize_products,
    "supplies.csv": localize_supplies,
    "tweets.csv": localize_tweets,
}


def recorded_digests():
    """The digests of shift.SHA256SUMS, by file name."""
    digests = {}
    with open(SUMS, encoding="utf-8") as fh:
        for line in fh:
            m = re.fullmatch(r"([0-9a-f]{64})  ([A-Za-z0-9_.-]+)\n", line)
            if not m:
                raise LocalizeError("%s holds a malformed line: %r" % (SUMS, line))
            digests[m.group(2)] = m.group(1)
    if sorted(digests) != sorted(FILES):
        raise LocalizeError("%s does not name exactly the seven files" % SUMS)
    return digests


def localize(src, dest):
    """Verify the shift outputs in src, write the localized files into dest."""
    if os.path.exists(dest) and (not os.path.isdir(dest) or os.listdir(dest)):
        raise LocalizeError("%s exists and is not an empty directory" % dest)
    digests = recorded_digests()
    extra = sorted(set(os.listdir(src)) - set(FILES))
    if extra:
        raise LocalizeError("%s holds files shift did not write: %s" % (src, ", ".join(extra)))
    results = {}
    for name in FILES:
        path = os.path.join(src, name)
        if not os.path.isfile(path):
            raise LocalizeError("%s is missing" % path)
        with open(path, "rb") as fh:
            data = fh.read()
        got = hashlib.sha256(data).hexdigest()
        if got != digests[name]:
            raise LocalizeError("%s: sha256 %s, shift wrote %s" % (name, got, digests[name]))
        if name not in STEPS:
            results[name] = data
            continue
        header, rows = parse(name, data.decode("utf-8"))
        STEPS[name](rows, header)
        results[name] = render([header] + rows).encode("utf-8")
    os.makedirs(dest, exist_ok=True)
    for name in sorted(results):
        with open(os.path.join(dest, name), "wb") as fh:
            fh.write(results[name])
    return results


def main(argv):
    if len(argv) != 3:
        sys.stderr.write("usage: python3 localize.py <shift-dir> <out-dir>\n")
        return 2
    try:
        results = localize(argv[1], argv[2])
    except LocalizeError as err:
        sys.stderr.write("localize.py: FAIL: %s\n" % err)
        return 1
    for name in sorted(results):
        print("%-16s %9d bytes  %s" % (name, len(results[name]), hashlib.sha256(results[name]).hexdigest()))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
