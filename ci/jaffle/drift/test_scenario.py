"""Tests of scenario.py's offline parts: its checks of the inputs, its reading of REQUIRES,
and the assertions the live run makes on what the api answers.

Run from this directory: python3 -m unittest test_scenario
"""

import copy
import os
import tempfile
import unittest

import yaml

import scenario


def project():
    return copy.deepcopy(scenario.load_yaml(scenario.PROJECT_FILE))


def contract():
    return copy.deepcopy(scenario.load_yaml(scenario.SHIPPED_CONTRACT))


class Inputs(unittest.TestCase):
    def test_the_repository_files_pass(self):
        scenario.check()

    def test_a_project_driftpolicy_fails(self):
        p = project()
        p["driftPolicy"] = {"version": 2}
        self.assertTrue(any("driftPolicy" in m for m in scenario.check_project(p)))

    def test_a_source_driftpolicy_fails(self):
        p = project()
        p["sources"][0]["driftPolicy"] = {"onMajor": "stop"}
        self.assertTrue(any("driftPolicy" in m for m in scenario.check_project(p)))

    def test_tweets_without_its_event_time_fails(self):
        p = project()
        del p["jobs"][0]["select"]["objects"]["tweets"]["eventTimeCol"]
        self.assertTrue(any("tweets" in m for m in scenario.check_project(p)))

    def test_description_named_by_the_job_fails(self):
        p = project()
        p["jobs"][0]["select"]["objects"]["products"]["dedupKey"] = "sku, description"
        self.assertTrue(any("description" in m for m in scenario.check_project(p)))

    def test_a_shipped_project_passes(self):
        self.assertEqual(scenario.check_project(project()), [])

    def test_description_as_a_key_fails(self):
        c = contract()
        products = next(o for o in c["schema"] if o["name"] == "products")
        next(p for p in products["properties"] if p["name"] == "description")["primaryKey"] = True
        self.assertTrue(scenario.check_contract(c))

    def test_description_as_a_foreign_end_fails(self):
        c = contract()
        supplies = next(o for o in c["schema"] if o["name"] == "supplies")
        supplies["properties"][0].setdefault("customProperties", []).extend([
            {"property": "foreign_object", "value": "products"},
            {"property": "foreign_property", "value": "description"},
        ])
        self.assertTrue(any("references" in m for m in scenario.check_contract(c)))

    def test_a_shipped_contract_passes(self):
        self.assertEqual(scenario.check_contract(contract()), [])

    def test_the_org_file_must_be_the_demo_rule(self):
        org = scenario.load_yaml(scenario.ORG_FILE)
        self.assertEqual(scenario.check_org(org), [])
        fewer = copy.deepcopy(org)
        fewer["driftPolicy"]["autoMerge"]["situations"].remove("column.type.widened")
        self.assertTrue(scenario.check_org(fewer))
        extra = copy.deepcopy(org)
        extra["sources"] = []
        self.assertTrue(scenario.check_org(extra))
        one_env = copy.deepcopy(org)
        one_env["driftPolicy"]["autoMerge"]["environments"] = ["development"]
        self.assertTrue(scenario.check_org(one_env))


class Requires(unittest.TestCase):
    def read(self, text):
        with tempfile.NamedTemporaryFile("w", suffix=".requires", delete=False) as f:
            f.write(text)
        self.addCleanup(os.unlink, f.name)
        return f.name

    def test_empty_values_are_unmet(self):
        path = self.read("# comment\nAPI_VERSION=\nAGENT_VERSION=5.21.0\n")
        values, missing = scenario.gate(path)
        self.assertEqual(values, {"API_VERSION": "", "AGENT_VERSION": "5.21.0"})
        self.assertEqual(missing, ["API_VERSION"])

    def test_both_set_is_met(self):
        path = self.read("API_VERSION=6.30.0\nAGENT_VERSION=5.21.0\n")
        self.assertEqual(scenario.gate(path)[1], [])

    def test_refusals(self):
        for text in ("API_VERSION=v6.30.0\nAGENT_VERSION=\n",
                     "API_VERSION=\nAPI_VERSION=\nAGENT_VERSION=\n",
                     "API_VERSION=\n",
                     "API_VERSION=\nAGENT_VERSION=\nOTHER=\n"):
            with self.subTest(text=text), self.assertRaises(scenario.Fail):
                scenario.read_requires(self.read(text))

    def test_versions_compare_as_numbers(self):
        self.assertLess(scenario.version_tuple("v6.9.0"), scenario.version_tuple("6.10.0"))
        self.assertEqual(scenario.version_tuple("amfelt 5.21.0 (abc)"), (5, 21, 0))
        self.assertIsNone(scenario.version_tuple("dev"))


def event(**over):
    base = {
        "id": "e1",
        "situations": [{"object": "sales_customers", "column": "email", "situation": "column.added.nullable",
                        "severity": "minor", "class": "auto", "halts": False}],
        "overall_class": "auto",
        "halts": False,
        "detected_by": "harvest",
    }
    base.update(over)
    return base


class Assertions(unittest.TestCase):
    case = scenario.CASES[0]

    def test_the_email_event_passes(self):
        scenario.assert_event(self.case, [event()], "harvest")

    def test_every_object_form_matches(self):
        for name in ("customers", "sales.customers", "sales_customers"):
            self.assertTrue(scenario.object_is(name, "sales", "customers"))
        self.assertFalse(scenario.object_is("marketing_customers", "sales", "customers"))

    def test_a_wrong_event_fails(self):
        wrong = {
            "class": event(situations=[dict(event()["situations"][0], **{"class": "notify"})]),
            "situation": event(situations=[dict(event()["situations"][0], situation="column.added.required")]),
            "column": event(situations=[dict(event()["situations"][0], column="name")]),
            "overall": event(overall_class="notify"),
            "halts": event(halts=True),
            "detected_by": event(detected_by="run_check"),
        }
        for what, e in wrong.items():
            with self.subTest(what=what), self.assertRaises(scenario.Fail):
                scenario.assert_event(self.case, [e], "harvest")

    def test_not_exactly_one_event_fails(self):
        for events in ([], [event(), event(id="e2")]):
            with self.subTest(n=len(events)), self.assertRaises(scenario.Fail):
                scenario.assert_event(self.case, events, "harvest")

    def test_a_run_check_names_its_run(self):
        e = event(detected_by="run_check", run={"command_id": "c1"})
        scenario.assert_event(self.case, [e], "run_check", "c1")
        with self.assertRaises(scenario.Fail):
            scenario.assert_event(self.case, [e], "run_check", "c2")

    def test_a_candidate_needs_its_confidence(self):
        tweets = scenario.TWEETS
        got = {"object": "marketing_tweets", "situation": "table.dropped.with-dependents", "class": "repair"}
        self.assertFalse(tweets.matches(dict(got, confidence="low")))
        self.assertTrue(tweets.matches(dict(got, confidence="medium")))

    def test_the_baseline(self):
        stored = yaml.safe_dump({"schema": [
            {"name": "customers", "properties": [{"name": "id"}, {"name": "email", "logicalType": "string"}]},
            {"name": "stores", "properties": [{"name": "tax_rate", "physicalType": "numeric", "precision": 8,
                                               "scale": 4}]},
        ]})

        class Fake:
            def contract(self):
                return {"contract_yaml": stored}
        scenario.assert_baseline(Fake(), scenario.CASES[0])
        scenario.assert_baseline(Fake(), scenario.CASES[1])
        for case in (scenario.CASES[2], scenario.ALL_FOUR):
            with self.subTest(case=case.name), self.assertRaises(scenario.Fail):
                scenario.assert_baseline(Fake(), case)

    def test_physical_types(self):
        self.assertEqual(scenario.physical_type({"physicalType": "numeric(8, 4)"}), "numeric(8,4)")
        self.assertEqual(scenario.physical_type({"physicalType": "numeric", "precision": 6, "scale": 4}),
                         "numeric(6,4)")
        self.assertEqual(scenario.physical_type({"physicalType": "text"}), "text")


class Projections(unittest.TestCase):
    block = scenario.projections_entry("v1.0.0")

    def test_inserted_under_the_top_level_custom_properties(self):
        text = "version: v1.0.0\ncustomProperties:\n  - property: other\n    value: x\nschema: []\n"
        out = scenario.add_projections(text, self.block)
        self.assertEqual(out.replace(out[len("version: v1.0.0\ncustomProperties:\n"):out.index("  - property: other")],
                                     ""), text)
        parsed = yaml.safe_load(out)
        self.assertEqual(parsed["customProperties"][0], {"property": "projections", "value": self.block})
        self.assertEqual(parsed["customProperties"][1], {"property": "other", "value": "x"})
        self.assertEqual(parsed["schema"], [])

    def test_appended_when_the_contract_has_none(self):
        text = "version: v1.0.0\nschema:\n  - name: products\n    customProperties:\n      - property: a\n"
        out = scenario.add_projections(text, self.block)
        self.assertTrue(out.startswith(text))
        parsed = yaml.safe_load(out)
        self.assertEqual(parsed["customProperties"], [{"property": "projections", "value": self.block}])
        self.assertEqual(parsed["schema"][0]["customProperties"], [{"property": "a"}])

    def test_the_block_names_the_dropped_column(self):
        self.assertEqual(self.block["changes"][0]["object"], "catalog_products")
        self.assertEqual(self.block["changes"][0]["column"], "description")
        self.assertEqual(scenario.check_projections(), [])


if __name__ == "__main__":
    unittest.main()
