"""Tests of check_contract.py. Run from ci/jaffle/: python3 -m unittest -v test_check_contract

Each negative case changes one thing in a contract that passes, and must fail for that
reason. Needs PyYAML and jsonschema, as check_contract.py does.
"""

import contextlib
import copy
import io
import os
import tempfile
import unittest

import yaml

import check_contract

HERE = os.path.dirname(os.path.abspath(__file__))
SHIPPED = os.path.join(HERE, "..", "..", "jaffle", "project", "contracts", "jaffle.odcs.yaml")


def prop(name, key=0):
    p = {"id": name, "name": name, "logicalType": "string", "physicalType": "text"}
    if key:
        p.update(primaryKey=True, primaryKeyPosition=key)
    return p


def obj(ns, name, columns, keys=(), relationships=None):
    o = {
        "id": "%s:%s" % (ns, name), "name": name, "logicalType": "object",
        "physicalType": "table", "physicalName": name,
        "properties": [prop(c, keys.index(c) + 1 if c in keys else 0) for c in columns],
        "customProperties": [{"property": "catalog_schema", "value": ns}],
    }
    if relationships:
        o["relationships"] = relationships
    return o


def ref(ns, name, column):
    return "schema/%s:%s/properties/%s" % (ns, name, column)


def good():
    return {
        "apiVersion": "v3.2.0", "kind": "DataContract", "id": "jaffle", "version": "v1.0.0",
        "status": "production", "contractCreatedTs": "2026-11-24T10:00:00Z",
        "schema": [
            obj("catalog", "supplies", ["id", "sku"], keys=("id", "sku")),
            obj("catalog", "supply_deliveries", ["id", "sku", "supply_id"], keys=("id",), relationships=[{
                "id": "supply_deliveries_supply_fkey", "type": "foreignKey",
                "from": [ref("catalog", "supply_deliveries", "supply_id"), ref("catalog", "supply_deliveries", "sku")],
                "to": [ref("catalog", "supplies", "id"), ref("catalog", "supplies", "sku")],
            }]),
            obj("sales", "stores", ["id"], keys=("id",)),
            obj("sales", "customers", ["id"], keys=("id",)),
            obj("sales", "store_transfers", ["id", "from_store_id", "to_store_id"], keys=("id",), relationships=[
                {"id": "store_transfers_from_store_id_fkey", "type": "foreignKey",
                 "from": ref("sales", "store_transfers", "from_store_id"), "to": ref("sales", "stores", "id")},
                {"id": "store_transfers_to_store_id_fkey", "type": "foreignKey",
                 "from": ref("sales", "store_transfers", "to_store_id"), "to": ref("sales", "stores", "id")},
            ]),
        ],
        "customProperties": [{"property": "provenance", "value": {
            "sourceDriver": "postgres", "introspectedAt": "2026-11-24T10:00:00Z"}}],
    }


def find(contract, name):
    return next(o for o in contract["schema"] if o["name"] == name)


class Base(unittest.TestCase):
    def fails(self, contract, text, kind="source"):
        problems = check_contract.check(kind, contract)
        self.assertTrue(any(text in p for p in problems),
                        "no problem holds %r: %r" % (text, problems))


class TestPasses(unittest.TestCase):
    def test_good_source(self):
        self.assertEqual(check_contract.check("source", good()), [])

    def test_good_target_needs_no_key_tables(self):
        contract = good()
        contract["schema"] = [find(contract, "stores")]
        self.assertEqual(check_contract.check("target", contract), [])

    def test_shipped_contract(self):
        """The project's contract is a v3.2.0 source contract with both key examples."""
        self.assertEqual(check_contract.check("source", check_contract.load(SHIPPED)), [])

    def test_load_keeps_timestamps_as_strings(self):
        contract = check_contract.load(SHIPPED)
        self.assertIsInstance(contract["contractCreatedTs"], str)


class TestCommon(Base):
    def test_api_version(self):
        contract = good()
        contract["apiVersion"] = "v3.0.0"
        self.fails(contract, "apiVersion is 'v3.0.0'")

    def test_root_provenance(self):
        contract = good()
        contract["provenance"] = {"sourceDriver": "postgres"}
        self.fails(contract, "provenance is a root key")
        self.fails(contract, "schema: (root)")

    def test_foreign_custom_property(self):
        contract = good()
        find(contract, "supply_deliveries")["properties"][1]["customProperties"] = [
            {"property": "foreign_object", "value": "supplies"}]
        self.fails(contract, "supply_deliveries.sku carries the v3.0.0 custom property foreign_object")

    def test_unresolved_end(self):
        contract = good()
        find(contract, "store_transfers")["relationships"][0]["to"] = ref("sales", "stores", "store_id")
        self.fails(contract, "does not resolve", kind="target")

    def test_unresolved_object(self):
        contract = good()
        find(contract, "store_transfers")["relationships"][0]["to"] = ref("sales", "shops", "id")
        self.fails(contract, "does not resolve", kind="target")

    def test_unresolved_reference_form(self):
        """An end is schema/<object id>/properties/<property id>, nothing else."""
        contract = good()
        find(contract, "store_transfers")["relationships"][0]["to"] = "tables/sales:stores/properties/id"
        self.fails(contract, "does not resolve", kind="target")

    def test_uneven_ends(self):
        contract = good()
        find(contract, "supply_deliveries")["relationships"][0]["to"].pop()
        self.fails(contract, "has 2 from and 1 to ends", kind="target")

    def test_validates_with_the_schemas_own_draft(self):
        """The schema states draft 2019-09, whose unevaluatedProperties draft 7 ignores: an
        unknown root key must fail as unevaluated, which only a 2019-09 validator reports."""
        import jsonschema
        with open(check_contract.SCHEMA, encoding="utf-8") as f:
            schema = __import__("json").load(f)
        self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2019-09/schema")
        self.assertIs(check_contract.validator_class(schema), jsonschema.Draft201909Validator)
        contract = good()
        contract["provenance"] = {"sourceDriver": "postgres"}
        self.assertIn("schema: (root): Unevaluated properties are not allowed ('provenance' was unexpected)",
                      check_contract.schema_errors(contract))

    def test_an_unknown_draft_is_refused(self):
        for schema in ({}, {"$schema": "https://example.com/draft/1"}):
            with self.assertRaises(ValueError):
                check_contract.validator_class(schema)

    def test_schema(self):
        contract = good()
        contract["status"] = 7
        self.fails(contract, "schema: status", kind="target")


class TestSource(Base):
    def test_no_provenance_property(self):
        contract = good()
        contract["customProperties"] = []
        self.fails(contract, "records no provenance custom property")

    def test_provenance_not_a_mapping(self):
        contract = good()
        contract["customProperties"] = [{"property": "provenance", "value": "postgres"}]
        self.fails(contract, "the provenance custom property is 'postgres', not a mapping")

    def test_composite_reversed(self):
        contract = good()
        rel = find(contract, "supply_deliveries")["relationships"][0]
        rel["from"].reverse()
        rel["to"].reverse()
        self.fails(contract, "catalog.supply_deliveries: the relationship is")

    def test_composite_in_column_order(self):
        """sku comes before supply_id in the table; the constraint lists supply_id first."""
        contract = good()
        rel = find(contract, "supply_deliveries")["relationships"][0]
        rel["from"] = [ref("catalog", "supply_deliveries", "sku"), ref("catalog", "supply_deliveries", "supply_id")]
        self.fails(contract, "catalog.supply_deliveries: the relationship is")

    def test_composite_split_in_two(self):
        contract = good()
        o = find(contract, "supply_deliveries")
        rel = o["relationships"][0]
        o["relationships"] = [dict(rel, id="a", **{"from": rel["from"][0], "to": rel["to"][0]}),
                              dict(rel, id="b", **{"from": rel["from"][1], "to": rel["to"][1]})]
        self.fails(contract, "catalog.supply_deliveries holds 2 relationships, not one")

    def test_composite_one_column(self):
        contract = good()
        rel = find(contract, "supply_deliveries")["relationships"][0]
        rel["from"], rel["to"] = rel["from"][0], rel["to"][0]
        self.fails(contract, "the relationship's from is")
        self.fails(contract, "the relationship's to is")

    def test_composite_wrong_target(self):
        contract = good()
        rel = find(contract, "supply_deliveries")["relationships"][0]
        rel["to"][1] = ref("catalog", "supplies", "id")
        self.fails(contract, "catalog.supply_deliveries: the relationship is")

    def test_composite_type(self):
        contract = good()
        find(contract, "supply_deliveries")["relationships"][0]["type"] = "references"
        self.fails(contract, "the relationship's type is 'references'")

    def test_composite_missing(self):
        contract = good()
        del find(contract, "supply_deliveries")["relationships"]
        self.fails(contract, "catalog.supply_deliveries holds 0 relationships, not one")

    def test_composite_table_missing(self):
        contract = good()
        contract["schema"] = [o for o in contract["schema"] if o["name"] != "supply_deliveries"]
        self.fails(contract, "no single object catalog.supply_deliveries")

    def test_separate_merged(self):
        contract = good()
        o = find(contract, "store_transfers")
        o["relationships"] = [{
            "id": "store_transfers_fkey", "type": "foreignKey",
            "from": [ref("sales", "store_transfers", "from_store_id"), ref("sales", "store_transfers", "to_store_id")],
            "to": [ref("sales", "stores", "id"), ref("sales", "stores", "id")],
        }]
        self.fails(contract, "sales.store_transfers holds 1 relationships, not two")

    def test_separate_one_missing(self):
        contract = good()
        find(contract, "store_transfers")["relationships"].pop()
        self.fails(contract, "sales.store_transfers holds 1 relationships, not two")

    def test_separate_twice_the_same(self):
        contract = good()
        rels = find(contract, "store_transfers")["relationships"]
        rels[1] = dict(rels[0], id="copy")
        self.fails(contract, "sales.store_transfers: the relationships are")

    def test_separate_wrong_target(self):
        contract = good()
        find(contract, "store_transfers")["relationships"][1]["to"] = ref("sales", "customers", "id")
        self.fails(contract, "sales.store_transfers: the relationships are")

    def test_separate_type(self):
        contract = good()
        find(contract, "store_transfers")["relationships"][1]["type"] = "references"
        self.fails(contract, "relationship store_transfers_to_store_id_fkey has type 'references'")

    def test_separate_table_missing(self):
        contract = good()
        contract["schema"] = [o for o in contract["schema"] if o["name"] != "store_transfers"]
        self.fails(contract, "no single object sales.store_transfers")

    def test_target_skips_the_key_tables(self):
        contract = good()
        del find(contract, "supply_deliveries")["relationships"]
        self.assertEqual(check_contract.check("target", copy.deepcopy(contract)), [])


class TestMain(unittest.TestCase):
    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = check_contract.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_usage(self):
        for argv in ([], ["source"], ["both", SHIPPED], ["source", SHIPPED, "x"]):
            self.assertEqual(self.run_main(argv)[0], 2, argv)

    def test_passes(self):
        code, out, _ = self.run_main(["source", SHIPPED])
        self.assertEqual(code, 0)
        self.assertIn("is a valid ODCS v3.2.0 source contract", out)

    def test_fails_with_one_line_per_problem(self):
        contract = good()
        contract["apiVersion"] = "v3.0.0"
        del find(contract, "supply_deliveries")["relationships"]
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "c.yaml")
            with open(path, "w", encoding="utf-8") as f:
                yaml.safe_dump(contract, f)
            code, out, err = self.run_main(["source", path])
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("\n  apiVersion is 'v3.0.0'", err)
        self.assertIn("\n  catalog.supply_deliveries holds 0 relationships, not one", err)


if __name__ == "__main__":
    unittest.main()
