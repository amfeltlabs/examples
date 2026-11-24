#!/usr/bin/env python3
"""Check a Jaffle data contract against the ODCS v3.2.0 schema and the source's keys.

Usage:
  check_contract.py source CONTRACT   a contract of the source, discovered by `amfelt import`
  check_contract.py target CONTRACT   a contract of the warehouse, harvested by `amfelt sync
                                      --target-contract`

Both kinds must state apiVersion v3.2.0 and validate against core's released schema,
schemas/odcs-json-schema-v3.2.0-20261029.json, a copy of the file core v3.51.0 embeds
(schemas/README.md). Neither may carry a root `provenance` key or a `foreign_*` custom
property, the places ODCS v3.0.0 contracts kept them; every relationship must resolve to
objects and properties the contract holds.

A source contract must, in addition, record the keys db/load.sql declares:
  - catalog.supply_deliveries holds exactly one relationship, of type foreignKey, from
    supply_id and sku to catalog.supplies id and sku: two-item lists, in the order of the
    constraint, which is not the order of the table's columns;
  - sales.store_transfers holds exactly two relationships, of type foreignKey, one from
    from_store_id and one from to_store_id, each to sales.stores id;
  - the contract records its provenance as a contract-level custom property.

A relationship end is a reference `schema/<object id>/properties/<property id>`. It is
resolved by looking the two ids up in the contract, never by reading a name out of an id.

Needs PyYAML and jsonschema; `pip install check-jsonschema==0.38.2 pyyaml==6.0.3` installs
both. Exits 0 when every check holds, 1 with one line per problem when one does not, and 2
on a usage error.
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCHEMA = os.path.join(HERE, "..", "..", "schemas", "odcs-json-schema-v3.2.0-20261029.json")
API_VERSION = "v3.2.0"

# The keys db/load.sql declares that a source contract must record. Each end is
# (namespace, table, column), in the constraint's order.
COMPOSITE = {
    "table": ("catalog", "supply_deliveries"),
    "from": [("catalog", "supply_deliveries", "supply_id"), ("catalog", "supply_deliveries", "sku")],
    "to": [("catalog", "supplies", "id"), ("catalog", "supplies", "sku")],
}
SEPARATE = {
    "table": ("sales", "store_transfers"),
    "pairs": [
        ([("sales", "store_transfers", "from_store_id")], [("sales", "stores", "id")]),
        ([("sales", "store_transfers", "to_store_id")], [("sales", "stores", "id")]),
    ],
}


def schema_errors(contract, schema_path=SCHEMA):
    import jsonschema

    with open(schema_path, encoding="utf-8") as f:
        schema = json.load(f)
    validator = jsonschema.Draft201909Validator(schema)
    errors = sorted(validator.iter_errors(contract), key=lambda e: list(e.absolute_path))
    return ["schema: %s: %s" % ("/".join(str(p) for p in e.absolute_path) or "(root)", e.message)
            for e in errors]


def custom(node):
    """A node's custom properties as a dict, property name to value."""
    return {c.get("property"): c.get("value") for c in node.get("customProperties") or []
            if isinstance(c, dict)}


def namespace(obj):
    return custom(obj).get("catalog_schema")


class Index:
    """The contract's objects by id, and each object's properties by id."""

    def __init__(self, contract):
        self.objects = {}
        for obj in contract.get("schema") or []:
            if isinstance(obj, dict) and obj.get("id") is not None:
                self.objects[obj["id"]] = obj

    def resolve(self, ref):
        """(namespace, table, column) for a reference, or None when it does not resolve."""
        if not isinstance(ref, str) or not ref.startswith("schema/"):
            return None
        head, sep, prop_id = ref[len("schema/"):].rpartition("/properties/")
        if not sep:
            return None
        obj = self.objects.get(head)
        if obj is None:
            return None
        for prop in obj.get("properties") or []:
            if isinstance(prop, dict) and prop.get("id") == prop_id:
                return (namespace(obj), obj.get("name"), prop.get("name"))
        return None


def ends(value):
    """A relationship's from or to as a list: one reference is a one-item list."""
    return value if isinstance(value, list) else [value]


def relationships(contract):
    """Every object-level relationship, as (object, relationship)."""
    for obj in contract.get("schema") or []:
        if not isinstance(obj, dict):
            continue
        for rel in obj.get("relationships") or []:
            yield obj, rel


def find_object(contract, ns, name):
    found = [o for o in contract.get("schema") or []
             if isinstance(o, dict) and o.get("name") == name and namespace(o) == ns]
    return found[0] if len(found) == 1 else None


def resolved(index, rel):
    return ([index.resolve(r) for r in ends(rel.get("from"))],
            [index.resolve(r) for r in ends(rel.get("to"))])


def check_common(contract):
    problems = []
    if contract.get("apiVersion") != API_VERSION:
        problems.append("apiVersion is %r, not %s" % (contract.get("apiVersion"), API_VERSION))
    if "provenance" in contract:
        problems.append("provenance is a root key; ODCS v3.2.0 keeps it as a contract-level custom property")
    for obj in contract.get("schema") or []:
        if not isinstance(obj, dict):
            continue
        for prop in obj.get("properties") or []:
            for key in custom(prop) if isinstance(prop, dict) else {}:
                if isinstance(key, str) and key.startswith("foreign_"):
                    problems.append("%s.%s carries the v3.0.0 custom property %s"
                                    % (obj.get("name"), prop.get("name"), key))
    index = Index(contract)
    for obj, rel in relationships(contract):
        froms, tos = resolved(index, rel)
        if None in froms or None in tos:
            problems.append("%s: relationship %s does not resolve: from %r to %r"
                            % (obj.get("name"), rel.get("id"), rel.get("from"), rel.get("to")))
        elif len(froms) != len(tos):
            problems.append("%s: relationship %s has %d from and %d to ends"
                            % (obj.get("name"), rel.get("id"), len(froms), len(tos)))
    return problems


def check_source(contract):
    problems = []
    index = Index(contract)

    if not isinstance(custom(contract).get("provenance"), dict):
        problems.append("the contract records no provenance custom property")

    obj = find_object(contract, *COMPOSITE["table"])
    if obj is None:
        problems.append("no single object %s.%s" % COMPOSITE["table"])
    else:
        rels = obj.get("relationships") or []
        if len(rels) != 1:
            problems.append("%s.%s holds %d relationships, not one" % (COMPOSITE["table"] + (len(rels),)))
        else:
            rel = rels[0]
            if rel.get("type") != "foreignKey":
                problems.append("%s.%s: the relationship's type is %r, not foreignKey"
                                % (COMPOSITE["table"] + (rel.get("type"),)))
            for side in ("from", "to"):
                if not isinstance(rel.get(side), list) or len(rel[side]) != 2:
                    problems.append("%s.%s: the relationship's %s is %r, not a two-item list"
                                    % (COMPOSITE["table"] + (side, rel.get(side))))
            froms, tos = resolved(index, rel)
            if froms != COMPOSITE["from"] or tos != COMPOSITE["to"]:
                problems.append("%s.%s: the relationship is %r -> %r, not %r -> %r"
                                % (COMPOSITE["table"] + (froms, tos, COMPOSITE["from"], COMPOSITE["to"])))

    obj = find_object(contract, *SEPARATE["table"])
    if obj is None:
        problems.append("no single object %s.%s" % SEPARATE["table"])
    else:
        rels = obj.get("relationships") or []
        if len(rels) != 2:
            problems.append("%s.%s holds %d relationships, not two" % (SEPARATE["table"] + (len(rels),)))
        else:
            got = sorted((resolved(index, rel) for rel in rels), key=repr)
            want = sorted(SEPARATE["pairs"], key=repr)
            if got != want:
                problems.append("%s.%s: the relationships are %r, not %r"
                                % (SEPARATE["table"] + (got, want)))
            for rel in rels:
                if rel.get("type") != "foreignKey":
                    problems.append("%s.%s: relationship %s has type %r, not foreignKey"
                                    % (SEPARATE["table"] + (rel.get("id"), rel.get("type"))))
    return problems


def check(kind, contract, schema_path=SCHEMA):
    if not isinstance(contract, dict):
        return ["the contract is not a mapping"]
    problems = schema_errors(contract, schema_path) + check_common(contract)
    if kind == "source":
        problems += check_source(contract)
    return problems


def load(path):
    """The contract at path, with every scalar YAML would read as a timestamp kept as the
    string it is, as JSON Schema sees it: contractCreatedTs is a string in the schema."""
    import yaml

    class Loader(yaml.SafeLoader):
        pass

    Loader.yaml_implicit_resolvers = {
        first: [(tag, regexp) for tag, regexp in resolvers if tag != "tag:yaml.org,2002:timestamp"]
        for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
    }
    with open(path, encoding="utf-8") as f:
        return yaml.load(f, Loader=Loader)


def main(argv):
    if len(argv) != 2 or argv[0] not in ("source", "target"):
        print("usage: check_contract.py source|target CONTRACT", file=sys.stderr)
        return 2
    kind, path = argv
    problems = check(kind, load(path))
    if problems:
        print("check_contract.py: %s is not the %s contract ODCS v3.2.0 and db/load.sql call for:\n  %s"
              % (path, kind, "\n  ".join(problems)), file=sys.stderr)
        return 1
    print("ok: %s is a valid ODCS %s %s contract" % (path, API_VERSION, kind))
    if kind == "source":
        print("ok: %s.%s holds one relationship, (supply_id, sku) -> catalog.supplies (id, sku)"
              % COMPOSITE["table"])
        print("ok: %s.%s holds two relationships, from_store_id and to_store_id, each -> sales.stores (id)"
              % SEPARATE["table"])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
