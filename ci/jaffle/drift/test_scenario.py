"""Tests of scenario.py's offline parts: its checks of the inputs, its reading of REQUIRES,
and the assertions the live run makes on what the api answers.

Run from this directory: python3 -m unittest test_scenario
"""

import contextlib
import copy
import io
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

    def test_description_as_a_relationship_target_fails(self):
        c = contract()
        supplies = next(o for o in c["schema"] if o["name"] == "supplies")
        products = next(o for o in c["schema"] if o["name"] == "products")
        supplies["relationships"][0]["to"] = "schema/%s/properties/description" % products["id"]
        self.assertTrue(any("supplies references products.description" in m for m in scenario.check_contract(c)))

    def test_description_in_a_composite_end_fails(self):
        c = contract()
        products = next(o for o in c["schema"] if o["name"] == "products")
        products["relationships"] = [{
            "id": "products_description_fkey", "type": "foreignKey",
            "from": ["schema/%s/properties/sku" % products["id"], "schema/%s/properties/description" % products["id"]],
            "to": ["schema/%s/properties/sku" % products["id"], "schema/%s/properties/name" % products["id"]],
        }]
        self.assertTrue(any("products references" in m and "products.description" in m
                            for m in scenario.check_contract(c)))

    def test_the_shipped_contract_has_relationships(self):
        """The check reads relationships: the shipped contract records nine, and none ends at
        products.description."""
        ends = list(scenario.relationship_ends(contract()))
        self.assertEqual(len(ends), 9)
        self.assertIn(("supply_deliveries", [("supply_deliveries", "supply_id"), ("supply_deliveries", "sku")],
                       [("supplies", "id"), ("supplies", "sku")]), ends)

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

    def test_an_older_stack_is_refused(self):
        values = {"API_VERSION": "6.30.0", "AGENT_VERSION": "5.21.0"}
        scenario.check_releases(values, "6.30.0", "amfelt 5.21.0")
        scenario.check_releases(values, "v6.31.2", "amfelt 5.22.0 (abc)")
        for api, agent in (("6.29.9", "amfelt 5.21.0"), ("6.30.0", "amfelt 5.20.6"), ("dev", "amfelt 5.21.0"),
                           ("6.30.0", "")):
            with self.subTest(api=api, agent=agent), self.assertRaises(scenario.Fail):
                scenario.check_releases(values, api, agent)

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

    def test_a_held_baseline_lacks_the_added_table(self):
        def stored(*tables):
            text = yaml.safe_dump({"schema": [{"name": t, "properties": [{"name": "id"}]} for t in tables]})

            class Fake:
                def contract(self):
                    return {"contract_yaml": text}
            return Fake()
        tweets = scenario.CASES[3]
        scenario.assert_baseline(stored("tweets"), tweets)
        with self.assertRaises(scenario.Fail):
            scenario.assert_baseline(stored("tweets", "posts"), tweets)
        with self.assertRaises(scenario.Fail):
            scenario.assert_baseline(stored("posts"), tweets)

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



EMAIL_TEXT = yaml.safe_dump({"schema": [
    {"name": "customers", "properties": [{"name": "id"}, {"name": "email", "logicalType": "string",
                                                          "physicalType": "text"}]},
    {"name": "stores", "properties": [{"name": "tax_rate", "physicalType": "numeric(6,4)", "customProperties": [
        {"property": "precision", "value": 6}, {"property": "scale", "value": 4}]}]},
    {"name": "tweets", "properties": [{"name": "id"}]},
]})


class FakeApi:
    def __init__(self, stack):
        self.stack = stack

    def call(self, method, path, body=None, ok_codes=(200,)):
        self.stack.calls.append((method, path, body))
        if path.endswith("/catalog/sources"):
            return 200, {"data": self.stack.catalog_sources}
        return 200, {}


class FakeStack:
    """A stack whose answers a test scripts: drift events, the stored contract, the halt,
    the runs, and the pull requests."""

    def __init__(self, **over):
        self.calls, self.mutations = [], []
        self.event_list = []
        self.stored = EMAIL_TEXT
        self.halt = False
        self.halt_after = {}
        self.run = {"status": "finished", "total_rows": 10, "finished_at": "x"}
        self.pulls = {}
        self.harvests = []
        self.catalog_sources = []
        self.api = FakeApi(self)
        self.org_repo, self.project_repo = "org", "project"
        self.__dict__.update(over)
        self.initial_contract = self.stored

    def events(self):
        return self.event_list

    def contract(self):
        return {"contract_yaml": self.stored}

    def halted(self):
        return self.halt

    def harvest(self):
        new = self.harvests.pop(0) if self.harvests else []
        self.event_list = new + self.event_list
        return new

    def trigger(self):
        return "c1"

    def finished_run(self, cmd):
        return self.run

    def wait(self, what, predicate):
        for _ in range(3):
            value = predicate()
            if value:
                return value
        raise scenario.Fail(f"timed out waiting for {what}")

    def holds(self, what, predicate):
        for _ in range(3):
            if not predicate():
                raise scenario.Fail(what)

    def pull(self, number):
        return self.pulls[number]

    def mutate(self, names):
        self.mutations.append(names)

    def reload_source(self):
        pass

    def write_files(self, *args, **kwargs):
        pass

    def catalog(self, suffix=""):
        return "/catalog" + suffix


def gate_event(**over):
    base = {"id": "d1", "situations": [{"object": "catalog_products", "column": "description",
                                        "situation": "column.dropped", "class": "gate"}],
            "overall_class": "gate", "halts": True, "detected_by": "harvest"}
    base.update(over)
    return base


class LiveRun(unittest.TestCase):
    description = scenario.CASES[2]
    email = scenario.CASES[0]

    def test_halts_counts_gate_and_repair(self):
        for klass, halts in (("auto", False), ("notify", False), ("gate", True), ("repair", True)):
            self.assertEqual(scenario.Case("c", [], [], klass, [], None).halts, halts, klass)

    def test_a_halting_run_reports_stopped_and_loads_nothing(self):
        scenario.assert_run(self.description, {"status": "stopped", "total_rows": 0})
        scenario.assert_run(self.description, {"status": "stopped"})
        for run in ({"status": "stopped", "total_rows": 5}, {"status": "finished", "total_rows": 0},
                    {"status": "failed", "total_rows": 0}):
            with self.subTest(run=run), self.assertRaises(scenario.Fail):
                scenario.assert_run(self.description, run)

    def test_a_continuing_run_runs(self):
        scenario.assert_run(self.email, {"status": "finished", "total_rows": 10})
        for status in ("stopped", "failed", "finished_with_error", "aborted"):
            with self.subTest(status=status), self.assertRaises(scenario.Fail):
                scenario.assert_run(self.email, {"status": status, "total_rows": 0})

    def test_by_run_check(self):
        check = gate_event(detected_by="run_check", run={"command_id": "c1"})
        stack = FakeStack(harvests=[[]], run={"status": "stopped", "total_rows": 0, "finished_at": "x"})
        stack.stored = stack.initial_contract = yaml.safe_dump(
            {"schema": [{"name": "products", "properties": [{"name": "sku"}, {"name": "description"}]}]})

        def trigger():
            stack.event_list = [check] + stack.event_list
            return "c1"
        stack.trigger = trigger
        scenario.by_run_check(stack, self.description)
        self.assertEqual(stack.mutations, [["description"]])
        stack.run = {"status": "stopped", "total_rows": 3, "finished_at": "x"}
        stack.harvests = [[]]
        stack.event_list = []
        with self.assertRaises(scenario.Fail):
            scenario.by_run_check(stack, self.description)

    def test_a_run_check_event_needs_its_run(self):
        e = gate_event(detected_by="run_check")
        for cmd in ("c1", None):
            with self.subTest(cmd=cmd), self.assertRaises(scenario.Fail):
                scenario.assert_event(self.description, [e], "run_check", cmd)
        with self.assertRaises(scenario.Fail):
            scenario.assert_event(self.description, [dict(e, run={"command_id": "c1"})], "run_check", None)

    def test_a_merge_by_the_platform(self):
        stack = FakeStack(event_list=[{"id": "e1", "pull_request": {"number": 7, "state": "merged_by_platform"}}],
                          pulls={7: {"state": "closed", "merged": True}})
        scenario.assert_pull_request(stack, self.email, {"id": "e1"})
        for state, merged in (("merged_by_person", True), ("open", False), ("merged_by_platform", False)):
            stack.event_list[0]["pull_request"]["state"] = state
            stack.pulls[7]["merged"] = merged
            with self.subTest(state=state, merged=merged), self.assertRaises(scenario.Fail):
                scenario.assert_pull_request(stack, self.email, {"id": "e1"})

    def test_a_pull_request_that_waits(self):
        stack = FakeStack(event_list=[{"id": "d1", "pull_request": {"number": 8, "state": "open"}}],
                          pulls={8: {"state": "open", "merged": False}})
        scenario.assert_pull_request(stack, self.description, {"id": "d1"})
        for state, git_state, merged in (("merged_by_platform", "closed", True), ("open", "closed", False),
                                         ("closed_by_platform", "open", False), ("open", "open", True)):
            stack.event_list[0]["pull_request"]["state"] = state
            stack.pulls[8] = {"state": git_state, "merged": merged}
            with self.subTest(state=state, git=git_state, merged=merged), self.assertRaises(scenario.Fail):
                scenario.assert_pull_request(stack, self.description, {"id": "d1"})

    def test_only_a_persons_import_counts(self):
        self.assertTrue(scenario.imported_by_person({"state": "merged_by_person", "import": {"outcome": "imported"}}))
        for pr in ({"state": "merged_by_platform", "import": {"outcome": "imported"}},
                   {"state": "merged_by_person", "import": {"outcome": "not_imported"}},
                   {"state": "merged_by_person"}):
            self.assertFalse(scenario.imported_by_person(pr), pr)

    def test_no_merge_ends_the_repair_halt(self):
        tweets = scenario.CASES[3]
        event = {"id": "t1", "situations": [
            {"object": "marketing_tweets", "situation": "table.dropped.with-dependents", "class": "repair",
             "confidence": "medium"},
            {"object": "marketing_posts", "situation": "table.added.selected", "class": "auto"}],
            "overall_class": "repair", "halts": True, "detected_by": "harvest",
            "pull_request": {"number": 9, "state": "merged_by_platform"}}
        stack = FakeStack(harvests=[[], [event]], halt=True, pulls={9: {"state": "closed", "merged": True}})
        halts = iter([False, True, False])
        stack.halted = lambda: next(halts)
        with self.assertRaises(scenario.Fail) as raised:
            scenario.by_harvest(stack, tweets)
        self.assertIn("ended the REPAIR halt", str(raised.exception))
        stack = FakeStack(harvests=[[], [event]], halt=True, pulls={9: {"state": "closed", "merged": True}})
        halts = iter([False, True, True])
        stack.halted = lambda: next(halts)
        scenario.by_harvest(stack, tweets)

    def test_a_revert_writes_no_event_and_ends_the_halt(self):
        scenario.assert_reverted([], False)
        with self.assertRaises(scenario.Fail):
            scenario.assert_reverted([{"id": "x"}], False)
        with self.assertRaises(scenario.Fail):
            scenario.assert_reverted([], True)

    def test_the_lag_case_holds_email_as_text(self):
        scenario.assert_email_held(EMAIL_TEXT)
        for replaced in (EMAIL_TEXT.replace("physicalType: text", "physicalType: int4"),
                         EMAIL_TEXT.replace("logicalType: string", "logicalType: integer"),
                         EMAIL_TEXT.replace("name: email", "name: mail")):
            with self.assertRaises(scenario.Fail):
                scenario.assert_email_held(replaced)

    def test_the_lag_case_proposes_no_email(self):
        no_email = EMAIL_TEXT.replace("name: email", "name: mail")
        scenario.assert_not_proposed({"id": "x"}, [{"number": 1}], lambda n: no_email)
        with self.assertRaises(scenario.Fail):
            scenario.assert_not_proposed({"id": "x", "pull_request": {"number": 2}}, [], lambda n: "")
        with self.assertRaises(scenario.Fail):
            scenario.assert_not_proposed({"id": "x"}, [{"number": 1}], lambda n: EMAIL_TEXT)

    def test_the_setup_needs_an_annotated_harvest(self):
        scenario.check_annotated(EMAIL_TEXT)
        unannotated = yaml.safe_dump({"schema": [{"name": "stores", "properties": [
            {"name": "tax_rate", "physicalType": "numeric(6,4)"}]}]})
        for text in (unannotated, EMAIL_TEXT.replace("value: 6", "value: 8")):
            with self.assertRaises(scenario.Fail):
                scenario.check_annotated(text)

    def test_the_shipped_contract_is_annotated_and_the_fixture_is_not(self):
        shipped, old = scenario.load_yaml(scenario.SHIPPED_CONTRACT), scenario.load_yaml(scenario.UNANNOTATED_CONTRACT)
        self.assertEqual(scenario.check_annotation(shipped, old), [])
        self.assertEqual(len(scenario.check_annotation(old, shipped)), 2)

    def test_two_accounts(self):
        scenario.check_accounts("anna", "amfelt-platform")
        for person, platform in (("anna", "anna"), ("", "x"), ("x", None)):
            with self.subTest(person=person, platform=platform), self.assertRaises(scenario.Fail):
                scenario.check_accounts(person, platform)

    def test_the_halt_is_read_by_slug(self):
        stack = scenario.Stack.__new__(scenario.Stack)
        stack.project, stack.environment, stack.source = "p", "development", "jaffle"
        fake = FakeStack(catalog_sources=[{"id": "other", "name": "jaffle", "role": "source", "halted": True},
                                          {"id": "jaffle", "name": "Jaffle Shop", "role": "source", "halted": False}])
        stack.api = fake.api
        self.assertFalse(stack.halted())
        fake.catalog_sources = [{"id": "x", "name": "jaffle", "role": "source", "halted": True}]
        with self.assertRaises(scenario.Fail):
            stack.halted()

    def test_the_seed(self):
        sql = scenario.seed_sql("t-1", "p-1", "development", "org", "http://h/o/r.git")
        self.assertIn("SET LOCAL app.tenant_id = 't-1';", sql)
        self.assertIn("pe.tenant_id = 't-1'::uuid", sql)
        self.assertIn("g.project_id IS NULL", sql)
        self.assertLess(sql.index("BEGIN;"), sql.index("SET LOCAL"))
        env = scenario.pg_environment("postgres://ad%40min:s3cr%2Ft@db.local:5433/amfelt_control?sslmode=disable")
        self.assertEqual((env["PGUSER"], env["PGPASSWORD"], env["PGHOST"], env["PGPORT"], env["PGDATABASE"],
                          env["PGSSLMODE"]), ("ad@min", "s3cr/t", "db.local", "5433", "amfelt_control", "disable"))
        with self.assertRaises(scenario.Fail):
            scenario.pg_environment("mysql://x/y")

class ShippedState(unittest.TestCase):
    def test_the_gate_waits_for_the_api_alone(self):
        values, missing = scenario.gate()
        self.assertEqual(missing, ["API_VERSION"])
        self.assertEqual(values["AGENT_VERSION"], "5.21.0")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = scenario.main(["scenario.py", "gate"])
        self.assertEqual(code, scenario.GATE_UNMET)
        self.assertIn("names no API_VERSION.", out.getvalue())
        self.assertNotIn("AGENT_VERSION", out.getvalue())

    def test_the_old_file_case_waits(self):
        written = []
        event = {"id": "u1", "situations": [{"object": "sales_stores", "column": "tax_rate",
                                             "situation": "column.type.widened", "class": "auto"}],
                 "overall_class": "auto", "halts": False, "detected_by": "harvest",
                 "pull_request": {"number": 4, "state": "open"}}
        stack = FakeStack(harvests=[[], [event]], pulls={4: {"state": "open", "merged": False}})
        stack.write_files = lambda repo, files, message, branch="main": written.append(files)
        scenario.tax_rate_with_the_old_file(stack)
        with open(scenario.UNANNOTATED_CONTRACT, encoding="utf-8") as f:
            self.assertIn({"contracts/jaffle.odcs.yaml": f.read()}, written)
        self.assertEqual(stack.mutations, [["tax-rate"]])
        stack = FakeStack(harvests=[[], [dict(event, pull_request={"number": 4, "state": "merged_by_platform"})]],
                          pulls={4: {"state": "closed", "merged": True}})
        with self.assertRaises(scenario.Fail):
            scenario.tax_rate_with_the_old_file(stack)


if __name__ == "__main__":
    unittest.main()
