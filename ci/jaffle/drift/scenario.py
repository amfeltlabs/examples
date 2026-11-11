#!/usr/bin/env python3
"""The Jaffle Shop drift scenario: the acceptance scenario of ADR-168, task E1.

Usage:
  scenario.py plan      print every case, its steps and what each asserts; needs nothing
  scenario.py check     check the scenario's inputs offline: the project file has no
                        driftPolicy, the organization file is the demo rule, the dropped
                        column has no dependents and the renamed table has one, REQUIRES is
                        well formed, and the projections block the scenario pushes is valid
  scenario.py mutations apply every mutation to the Jaffle source in a transaction that is
                        rolled back, and check what each changes; needs the source of
                        jaffle/compose.yaml running (docker compose up -d --wait source)
  scenario.py gate      exit 0 when REQUIRES names the api and agent releases the live run
                        needs, and 3, naming what is missing, while it does not
  scenario.py run       the live scenario, against a running stack; refuses unless the gate
                        passes and the stack runs those releases or later

The live run binds two repositories it creates on the stack's git host (Forgejo): an
organization repository holding jaffle/org/amfelt-org.yaml as org/amfelt-org.yaml, which
the organization's binding names through PUT /v1/orgs/{slug}/binding, and a project
repository whose root holds jaffle/project/: amfelt.yaml, with no driftPolicy, and
contracts/jaffle.odcs.yaml, the contract an annotated harvest of the source writes, which
carries precision and scale on tax_rate, so the file and the harvest agree (ADR-168 Open
Question 22, interim). The tax_rate case runs once more with the contract the quickstart
shipped before, unannotated.odcs.yaml beside this script: its tax_rate pull request must
not merge itself. The live run changes no tracked file of this repository.

The organization repository's config.git_repo row is seeded with one SQL INSERT in the
stack's control database, because no route registers a customer-connected repository
that no project environment binds (ADR-168 Open Question 31, the recorded gap). The row
copies the URL of the organization repository and the credential reference of the project
environment's own connected repository, so the stack's vault gets no new secret and the
organization gets no scratch project. The insert runs with app.tenant_id set to the
organization's id, so a role under row-level security writes only into that tenant; a
role that bypasses it (a superuser, or one with BYPASSRLS) is scoped by the insert's own
WHERE clause. CONTROL_DB_URL is read from the environment and handed to psql as PG*
variables, never on its command line.

Two git host accounts take part, and they must differ, because a merge counts as a
person's only when the git host names another merger than the platform's own account
(drift situation catalog, the import on merge; ADR-168 Open Question 24, interim):
  - the platform's account, FORGEJO_PLATFORM_TOKEN: the project environment's connection
    stores it, and the platform pushes, opens and merges its pull requests with it. It
    also reads the organization repository, through the seeded row's credential reference.
    The scenario adds it as a collaborator: write on the project repository, read on the
    organization repository.
  - the person's account, FORGEJO_TOKEN: it creates both repositories under FORGEJO_OWNER,
    so it must be allowed to, writes their files, pushes the projections block onto the
    description pull request and merges it.

The live run needs, in its environment:
  AMFELT_API_URL       the api, for example http://localhost:8080
  AMFELT_API_TOKEN     a bearer token of an owner or an admin of the organization
  AMFELT_ORG_SLUG      the organization, which must hold no other drift scenario
  AMFELT_PROJECT_ID    a project of that organization with the source jaffle and the job
                       load of jaffle/project/amfelt.yaml, whose environment has an agent
  AMFELT_ENVIRONMENT   the environment slug; development by default
  FORGEJO_URL          the git host's address as this machine reaches it
  FORGEJO_TOKEN        the person's token
  FORGEJO_PLATFORM_TOKEN  the platform's token, of another account than FORGEJO_TOKEN's
  FORGEJO_OWNER        the user or organization the two repositories are created under
  CONTROL_DB_URL       a PostgreSQL URL of the control database, for the seed only
and optionally:
  FORGEJO_URL_FOR_API  the git host's address as the api reaches it; FORGEJO_URL by default
  SOURCE_PSQL          the psql command that reaches the source the project's source reads;
                       by default psql in the source container of jaffle/compose.yaml
  SOURCE_RELOAD        the shell command that reloads that source freshly; by default
                       ci/jaffle/steps/99-reset.sh, then ci/jaffle/steps/01-start.sh
  AMFELT_BIN           the amfelt CLI whose version is checked; amfelt by default
  SCENARIO_TIMEOUT     seconds to wait for one harvest, run or pull request; 600 by default
  SCENARIO_SETTLE      seconds a pull request that must not merge is watched; 120 by default

Python standard library and PyYAML; check also needs jsonschema, which check-jsonschema
installs.
"""

import base64
import json
import os
import re
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

try:
    import yaml
except ImportError:  # pragma: no cover - CI installs it
    sys.exit("scenario.py: PyYAML is required: python3 -m pip install pyyaml")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", "..", ".."))
JAFFLE = os.path.join(ROOT, "jaffle")
STEPS = os.path.join(ROOT, "ci", "jaffle", "steps")
MUTATIONS = os.path.join(HERE, "mutations")
REQUIRES = os.path.join(HERE, "REQUIRES")
ORG_FILE = os.path.join(JAFFLE, "org", "amfelt-org.yaml")
PROJECT_FILE = os.path.join(JAFFLE, "project", "amfelt.yaml")
SHIPPED_CONTRACT = os.path.join(JAFFLE, "project", "contracts", "jaffle.odcs.yaml")
UNANNOTATED_CONTRACT = os.path.join(HERE, "unannotated.odcs.yaml")
PROJECTIONS_SCHEMA = os.path.join(ROOT, "schemas", "projections-schema.json")

# The folder the organization binding names in the organization repository.
ORG_FOLDER = "org"

# The demo rule, as the drift policy reference page gives it (The demo rule).
DEMO_RULE = {
    "version": 2,
    "autoMerge": {
        "environments": ["development", "test", "acceptance", "production"],
        "situations": [
            "table.added.selected",
            "column.added.nullable",
            "column.type.widened",
            "column.type.physical-widened",
            "column.nullability.loosened",
            "column.enum.value-added",
            "column.metadata.changed",
        ],
    },
}

# The organization file of the lag case: an organization with no auto-merge rule.
NO_RULE = "driftPolicy:\n  version: 2\n  autoMerge: false\n"

GATE_UNMET = 3


class Fail(Exception):
    """A failed assertion or a refused precondition."""


def say(text):
    print(f"\n==> {text}", flush=True)


def ok(text):
    print(f"ok: {text}", flush=True)


# ---------------------------------------------------------------------------------------
# What the scenario asserts
# ---------------------------------------------------------------------------------------


class Change:
    """One change a drift event must carry: its object, column, situation and class."""

    def __init__(self, schema, table, column, situation, klass, confidence=None):
        self.schema, self.table, self.column = schema, table, column
        self.situation, self.klass, self.confidence = situation, klass, confidence

    def where(self):
        name = f"{self.schema}.{self.table}"
        return f"{name}.{self.column}" if self.column else name

    def matches(self, got):
        """got is one DriftChangeResolution of the drift-events response."""
        return (
            object_is(got.get("object", ""), self.schema, self.table)
            and (got.get("column") or None) == self.column
            and got.get("situation") == self.situation
            and got.get("class") == self.klass
            and (self.confidence is None or got.get("confidence") == self.confidence)
        )

    def __str__(self):
        extra = f", confidence {self.confidence}" if self.confidence else ""
        return f"{self.where()}: {self.situation}, {self.klass.upper()}{extra}"


def object_is(name, schema, table):
    """Whether an object name of a drift event names schema.table: the event names it by
    its qualified name, which is accepted as schema.table, schema_table or the bare table."""
    return name in (table, f"{schema}.{table}", f"{schema}_{table}")


EMAIL = Change("sales", "customers", "email", "column.added.nullable", "auto")
TAX_RATE = Change("sales", "stores", "tax_rate", "column.type.widened", "auto")
DESCRIPTION = Change("catalog", "products", "description", "column.dropped", "gate")
TWEETS = Change("marketing", "tweets", None, "table.dropped.with-dependents", "repair", "medium")
POSTS = Change("marketing", "posts", None, "table.added.selected", "auto")
EMAIL_INTEGER = Change("sales", "customers", "email", "column.type.changed", "repair")


class Case:
    """One mutation of the ADR's table, or the four at once."""

    def __init__(self, name, mutations, changes, overall, baseline, pull_request):
        self.name, self.mutations, self.changes = name, mutations, changes
        self.overall, self.baseline, self.pull_request = overall, baseline, pull_request

    @property
    def halts(self):
        return self.overall in ("gate", "repair")


# baseline: (schema, table, column, present) facts of the stored contract after the
# harvest; column None is the table itself. pull_request: what the platform's pull request
# does, "merges" (merges itself), "waits" (stays open for a person), or None.
CASES = [
    Case("email", ["email"], [EMAIL], "auto",
         [("sales", "customers", "email", True)], "merges"),
    Case("tax_rate", ["tax-rate"], [TAX_RATE], "auto",
         [("sales", "stores", "tax_rate", "numeric(8,4)")], "merges"),
    Case("description", ["description"], [DESCRIPTION], "gate",
         [("catalog", "products", "description", True)], "waits"),
    Case("tweets", ["tweets"], [TWEETS, POSTS], "repair",
         [("marketing", "tweets", None, True), ("marketing", "posts", None, False)], "merges"),
]
ALL_FOUR = Case("all four", ["email", "tax-rate", "description", "tweets"],
                [EMAIL, TAX_RATE, DESCRIPTION, TWEETS, POSTS], "repair",
                [("sales", "customers", "email", False), ("catalog", "products", "description", True),
                 ("marketing", "tweets", None, True)], None)

# What each mutation file changes at the source, for `mutations`: a probe query, its
# answer before and after the mutation.
PROBES = {
    "email": ("SELECT data_type, is_nullable FROM information_schema.columns "
              "WHERE table_schema = 'sales' AND table_name = 'customers' AND column_name = 'email'",
              "", "text|YES"),
    "tax-rate": ("SELECT numeric_precision, numeric_scale FROM information_schema.columns "
                 "WHERE table_schema = 'sales' AND table_name = 'stores' AND column_name = 'tax_rate'",
                 "6|4", "8|4"),
    "description": ("SELECT count(*) FROM information_schema.columns "
                    "WHERE table_schema = 'catalog' AND table_name = 'products' "
                    "AND column_name = 'description'",
                    "1", "0"),
    "tweets": ("SELECT string_agg(table_name, ',' ORDER BY table_name) FROM information_schema.tables "
               "WHERE table_schema = 'marketing'",
               "tweets", "posts"),
}
# The revert of the drop: the column as it was, with its place among the table's columns,
# its type, its nullability and its comment, which a harvest reads as its description. The
# place is its rank, not ordinal_position: PostgreSQL never reuses a dropped column's
# number, so the column added back has a higher one.
DESCRIPTION_AS_IT_WAS = (
    "SELECT place, data_type, is_nullable, col_description('catalog.products'::regclass, ordinal_position) "
    "FROM (SELECT *, row_number() OVER (ORDER BY ordinal_position) AS place "
    "FROM information_schema.columns WHERE table_schema = 'catalog' AND table_name = 'products') c "
    "WHERE column_name = 'description'"
)


def plan():
    print("The Jaffle Shop drift scenario (ADR-168, task E1)")
    print("Organization file: jaffle/org/amfelt-org.yaml as org/amfelt-org.yaml, the demo rule.")
    print("Project: jaffle/project/amfelt.yaml, with no driftPolicy, and the shipped contract, the")
    print("one an annotated harvest writes, at the root of the project repository.\n")
    print("Setup: check that the person's and the platform's git host accounts differ; create the")
    print("two repositories; connect the project environment to the project repository with the")
    print("platform's token; seed the organization repository's config.git_repo row; set the")
    print("binding through PUT /v1/orgs/{slug}/binding; reload the source; harvest: the stored")
    print("contract records tax_rate as numeric(6,4); harvest again: no change.\n")
    print("Each case starts from a freshly loaded source and the setup's baseline.\n")
    for case in CASES + [ALL_FOUR]:
        print(f"Case {case.name}: apply {', '.join(m + '.sql' for m in case.mutations)}")
        for change in case.changes:
            print(f"  - {change}")
        print(f"  overall {case.overall.upper()}; the run {'halts' if case.halts else 'continues'}")
        for schema, table, column, fact in case.baseline:
            print(f"  - baseline: {schema}.{table}{'.' + column if column else ''} {baseline_text(fact)}")
        if case.pull_request:
            print(f"  - pull request: {pr_text(case)}")
        print("  asserted by a harvest, and again, on a freshly loaded source, by the run check")
        print("  of the next run with no harvest before it: the same situations, class and")
        print("  baseline; a run whose check halts reports stopped and loads no row, one whose")
        print("  check continues runs.")
        if case.name == "tweets":
            print("  after the posts pull request merged, the source is still halted: no merge ends")
            print("  a REPAIR halt.")
        if case.name == "description":
            print("  then a person pushes the projections block and merges the pull request with a")
            print("  merge commit: the platform imports the drop, the halt ends, run start is accepted.")
        print()
    print("Case tax_rate with the contract file the quickstart shipped before (unannotated):")
    print(f"  {TAX_RATE}, against the stored contract; against the file the change is")
    print("  column.type.unknown, so its pull request is not eligible and stays open.\n")
    print("Case description revert: after description.sql has halted, apply description-back.sql")
    print("  and harvest: no drift event, the halt ends, run start is accepted.\n")
    print("Case lag, in an organization with no auto-merge rule (autoMerge: false): apply")
    print(f"  email.sql and harvest: {EMAIL}, the pull request stays open, the baseline")
    print("  advances; then email-integer.sql and harvest:")
    print(f"  - {EMAIL_INTEGER}; the run halts, the baseline keeps email as text, the open")
    print("    pull request is closed; the retype's event proposes nothing, and no open pull")
    print("    request carries email.")


def baseline_text(fact):
    if fact is True:
        return "is in the stored contract"
    if fact is False:
        return "is not in the stored contract"
    return f"has the physical type {fact} in the stored contract"


def pr_text(case):
    if case.name == "tweets":
        return "posts is proposed, and merges itself; the REPAIR change is never proposed"
    if case.pull_request == "merges":
        return "proposed, and merges itself once the contract gate passes"
    return "proposed, and waits for a person"


# ---------------------------------------------------------------------------------------
# check: the scenario's inputs, offline
# ---------------------------------------------------------------------------------------


def load_yaml(path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def keys_named(node, name, path="$"):
    """Every path in node where a mapping holds the key name."""
    found = []
    if isinstance(node, dict):
        for k, v in node.items():
            if k == name:
                found.append(f"{path}.{k}")
            found.extend(keys_named(v, name, f"{path}.{k}"))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            found.extend(keys_named(v, name, f"{path}[{i}]"))
    return found


def values_equal(node, value, path="$"):
    """Every path in node whose scalar, or one of whose comma-separated names, is value."""
    found = []
    if isinstance(node, dict):
        for k, v in node.items():
            found.extend(values_equal(v, value, f"{path}.{k}"))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            found.extend(values_equal(v, value, f"{path}[{i}]"))
    elif isinstance(node, str) and value in [p.strip() for p in node.split(",")]:
        found.append(path)
    return found


def check_project(project):
    """The project file holds no driftPolicy, so the classes are the standard profile's, and
    its job load names tweets, with eventTimeCol tweeted_at, so the renamed table has
    dependents, and names description nowhere, so the dropped column has none."""
    problems = []
    if keys_named(project, "driftPolicy"):
        problems.append("jaffle/project/amfelt.yaml has a driftPolicy at "
                        + ", ".join(keys_named(project, "driftPolicy")))
    jobs = [j for j in project.get("jobs") or [] if j.get("name") == "load"]
    if len(jobs) != 1:
        problems.append("jaffle/project/amfelt.yaml has no single job named load")
    else:
        tweets = ((jobs[0].get("select") or {}).get("objects") or {}).get("tweets")
        if not isinstance(tweets, dict) or tweets.get("eventTimeCol") != "tweeted_at":
            problems.append("the job load does not name tweets with eventTimeCol tweeted_at, "
                            "so the renamed table would have no dependents")
        if values_equal(jobs, "description"):
            problems.append("the job load names description at " + ", ".join(values_equal(jobs, "description"))
                            + ", so the dropped column would have dependents; name another column")
    return problems


def check_contract(contract):
    """The dropped column is in the contract, and it is no key and no foreign key's end."""
    problems = []
    products = [o for o in contract.get("schema") or [] if o.get("name") == "products"]
    columns = [p for o in products for p in o.get("properties") or [] if p.get("name") == "description"]
    if len(columns) != 1:
        return ["the shipped contract has no single column products.description"]
    column = columns[0]
    if column.get("primaryKey") or column.get("primaryKeyPosition"):
        problems.append("products.description is a primary key in the shipped contract")
    for prop in column.get("customProperties") or []:
        if prop.get("property") in ("businessKey", "foreign_object", "foreign_property"):
            problems.append(f"products.description carries {prop.get('property')} in the shipped contract")
    for obj in contract.get("schema") or []:
        for prop in obj.get("properties") or []:
            custom = {c.get("property"): c.get("value") for c in prop.get("customProperties") or []}
            if custom.get("foreign_object") == "products" and custom.get("foreign_property") == "description":
                problems.append(f"{obj.get('name')}.{prop.get('name')} references products.description")
    return problems


def tax_rate_dimensions(contract):
    """The precision and scale the contract records on stores.tax_rate, or None."""
    obj = next((o for o in contract.get("schema") or [] if o.get("name") == "stores"), {})
    prop = next((p for p in obj.get("properties") or [] if p.get("name") == "tax_rate"), None)
    return dimensions(prop) if prop else None


def check_annotation(shipped, unannotated):
    """The shipped contract is the one an annotated harvest writes: it records tax_rate's
    precision and scale, 6 and 4, as the source declares numeric(6,4). The fixture of the
    tax_rate negative case is the contract shipped before, which records neither."""
    problems = []
    if tax_rate_dimensions(shipped) != (6, 4):
        problems.append("the shipped contract does not record tax_rate's precision 6 and scale 4: "
                        "it is not the contract an annotated harvest writes")
    if tax_rate_dimensions(unannotated) is not None:
        problems.append("unannotated.odcs.yaml records tax_rate's dimensions")
    return problems


def check_org(org):
    if not isinstance(org, dict) or set(org) != {"driftPolicy"}:
        return ["jaffle/org/amfelt-org.yaml must hold driftPolicy and nothing else"]
    if org["driftPolicy"] != DEMO_RULE:
        return ["jaffle/org/amfelt-org.yaml is not the demo rule of the drift policy reference page"]
    return []


def read_requires(path=REQUIRES):
    """REQUIRES as a dict; exactly API_VERSION and AGENT_VERSION, each empty or X.Y.Z."""
    values = {}
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            m = re.fullmatch(r"([A-Z_]+)=(\d+\.\d+\.\d+)?", line)
            if not m:
                raise Fail(f"{path}:{n}: not KEY=X.Y.Z or KEY=: {line}")
            if m.group(1) in values:
                raise Fail(f"{path}:{n}: {m.group(1)} is set twice")
            values[m.group(1)] = m.group(2) or ""
    if set(values) != {"API_VERSION", "AGENT_VERSION"}:
        raise Fail(f"{path} must set exactly API_VERSION and AGENT_VERSION, not {sorted(values)}")
    return values


def projections_entry(from_version):
    """The projections block a person pushes onto the description pull request: the drop of
    catalog.products.description, with no value carried either way (ADR-167)."""
    return {
        "fromVersion": from_version,
        "changes": [{
            "object": "catalog_products",
            "column": "description",
            "forward": {"strategy": "none", "reason": "The source dropped the column."},
            "backward": {"strategy": "none", "reason": "The source no longer holds the column's values."},
        }],
    }


def check_projections():
    try:
        import jsonschema
    except ImportError:
        return ["jsonschema is not installed; python3 -m pip install check-jsonschema"]
    with open(PROJECTIONS_SCHEMA, encoding="utf-8") as f:
        schema = json.load(f)
    errors = sorted(jsonschema.Draft201909Validator(schema).iter_errors(projections_entry("v1.0.0")),
                    key=lambda e: list(e.path))
    return [f"the projections block is invalid: {e.message}" for e in errors]


def check():
    problems = check_project(load_yaml(PROJECT_FILE))
    problems += check_contract(load_yaml(SHIPPED_CONTRACT))
    problems += check_annotation(load_yaml(SHIPPED_CONTRACT), load_yaml(UNANNOTATED_CONTRACT))
    problems += check_org(load_yaml(ORG_FILE))
    problems += check_projections()
    try:
        read_requires()
    except Fail as e:
        problems.append(str(e))
    for name in sorted(PROBES) + ["description-back", "email-integer"]:
        if not os.path.isfile(os.path.join(MUTATIONS, name + ".sql")):
            problems.append(f"mutations/{name}.sql is missing")
    if problems:
        raise Fail("; ".join(problems))
    ok("the project file has no driftPolicy; the job load names tweets and not description")
    ok("products.description is no key and no foreign key's end in the shipped contract")
    ok("the shipped contract records tax_rate as precision 6, scale 4; unannotated.odcs.yaml does not")
    ok("jaffle/org/amfelt-org.yaml is the demo rule")
    ok("the projections block is valid against schemas/projections-schema.json")
    ok("REQUIRES is well formed")


# ---------------------------------------------------------------------------------------
# mutations: each mutation against the source, rolled back
# ---------------------------------------------------------------------------------------


def source_psql():
    command = os.environ.get("SOURCE_PSQL")
    if command:
        return shlex.split(command)
    return ["docker", "compose", "exec", "-T", "source",
            "psql", "-X", "-v", "ON_ERROR_STOP=1", "-U", "jaffle", "-d", "jaffle"]


def psql(sql, command=None, env=None):
    """Run sql through psql; return its unaligned, tuples-only output."""
    result = subprocess.run((command or source_psql()) + ["-tA", "-q"], input=sql, text=True,
                            capture_output=True, cwd=JAFFLE, check=False, env=env)
    if result.returncode != 0:
        raise Fail(f"psql failed: {result.stderr.strip()}")
    return result.stdout.strip()


def mutation_sql(name):
    with open(os.path.join(MUTATIONS, name + ".sql"), encoding="utf-8") as f:
        return f.read()


def in_rollback(names, probe):
    """Apply the mutation files names in one transaction, answer probe, and roll back."""
    body = "".join(mutation_sql(n) for n in names)
    out = psql(f"BEGIN;\n{body}\n{probe};\nROLLBACK;\n")
    return out.splitlines()[-1] if out else ""


def mutations():
    for name, (probe, before, after) in PROBES.items():
        got = psql(probe + ";")
        if got != before:
            raise Fail(f"before {name}.sql the source answers {got!r}, not {before!r}: "
                       "reload it with ci/jaffle/steps/99-reset.sh and 01-start.sh")
        got = in_rollback([name], probe)
        if got != after:
            raise Fail(f"after {name}.sql the source answers {got!r}, not {after!r}")
        ok(f"{name}.sql: {before or 'none'} -> {after}")
    for name, (probe, _, after) in PROBES.items():
        got = in_rollback(list(PROBES), probe)
        if got != after:
            raise Fail(f"with the four mutations at once, {name} answers {got!r}, not {after!r}")
    ok("the four mutations apply at once")
    want = psql(DESCRIPTION_AS_IT_WAS + ";")
    got = in_rollback(["description", "description-back"], DESCRIPTION_AS_IT_WAS)
    if got != want or not want:
        raise Fail(f"description-back.sql gives {got!r}, not the column as it was, {want!r}")
    ok(f"description-back.sql restores the column as it was: {want}")
    probe = ("SELECT data_type FROM information_schema.columns WHERE table_schema = 'sales' "
             "AND table_name = 'customers' AND column_name = 'email'")
    got = in_rollback(["email", "email-integer"], probe)
    if got != "integer":
        raise Fail(f"email-integer.sql after email.sql gives {got!r}, not integer")
    ok("email-integer.sql retypes the new column to integer")


# ---------------------------------------------------------------------------------------
# gate
# ---------------------------------------------------------------------------------------


def version_tuple(text):
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", text or "")
    return tuple(int(x) for x in m.groups()) if m else None


def gate(path=REQUIRES):
    """The releases REQUIRES names: an empty value is a release that does not exist yet."""
    values = read_requires(path)
    missing = [k for k, v in values.items() if not v]
    return values, missing


# ---------------------------------------------------------------------------------------
# run: the live scenario
# ---------------------------------------------------------------------------------------


def env(name, default=None):
    value = os.environ.get(name, default)
    if value is None or value == "":
        raise Fail(f"{name} is not set; scenario.py's docstring lists what the live run needs")
    return value


class Http:
    def __init__(self, base, header):
        self.base, self.header = base.rstrip("/"), header

    def call(self, method, path, body=None, ok_codes=(200,)):
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(self.base + path, data=data, method=method)
        request.add_header("Authorization", self.header)
        request.add_header("Accept", "application/json")
        if data is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                code, raw = response.status, response.read()
        except urllib.error.HTTPError as e:
            code, raw = e.code, e.read()
        payload = json.loads(raw) if raw.strip() else None
        if code not in ok_codes:
            raise Fail(f"{method} {path} answered {code}: {raw[:400].decode(errors='replace')}")
        return code, payload


class Stack:
    """The api, the git host and the source of one live run."""

    def __init__(self):
        self.api = Http(env("AMFELT_API_URL"), "Bearer " + env("AMFELT_API_TOKEN"))
        self.org = env("AMFELT_ORG_SLUG")
        self.project = env("AMFELT_PROJECT_ID")
        self.environment = env("AMFELT_ENVIRONMENT", "development")
        self.source, self.job = "jaffle", "load"
        self.git = Http(env("FORGEJO_URL") + "/api/v1", "token " + env("FORGEJO_TOKEN"))
        self.platform_git = Http(env("FORGEJO_URL") + "/api/v1", "token " + env("FORGEJO_PLATFORM_TOKEN"))
        self.git_for_api = env("FORGEJO_URL_FOR_API", os.environ.get("FORGEJO_URL"))
        self.platform_token = env("FORGEJO_PLATFORM_TOKEN")
        self.owner = env("FORGEJO_OWNER")
        self.control_db = env("CONTROL_DB_URL")
        self.timeout = int(env("SCENARIO_TIMEOUT", "600"))
        self.settle = int(env("SCENARIO_SETTLE", "120"))
        stamp = time.strftime("%Y%m%d%H%M%S")
        self.org_repo, self.project_repo = f"jaffle-drift-org-{stamp}", f"jaffle-drift-project-{stamp}"
        self.initial_contract = None

    # -- the api ---------------------------------------------------------------------

    def catalog(self, suffix=""):
        return (f"/v1/projects/{self.project}/environments/{self.environment}"
                f"/catalog/sources/{self.source}{suffix}")

    def events(self):
        return self.api.call("GET", self.catalog("/drift-events?limit=50"))[1] or []

    def contract(self):
        _, body = self.api.call("GET", self.catalog("/contract"))
        return body

    def halted(self):
        _, body = self.api.call("GET", f"/v1/projects/{self.project}/environments/{self.environment}"
                                       "/catalog/sources")
        for source in (body or {}).get("data") or []:
            if source.get("id") == self.source and source.get("role") == "source":
                return bool(source.get("halted"))
        raise Fail(f"the catalog lists no source with the slug {self.source}")

    def wait(self, what, predicate):
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            value = predicate()
            if value:
                return value
            time.sleep(3)
        raise Fail(f"timed out after {self.timeout}s waiting for {what}")

    def holds(self, what, predicate):
        """predicate() holds every time it is asked, for SCENARIO_SETTLE seconds."""
        deadline = time.monotonic() + self.settle
        while True:
            if not predicate():
                raise Fail(what)
            if time.monotonic() >= deadline:
                return
            time.sleep(3)

    def harvest(self):
        """Discover the source and return the drift events the harvest wrote, newest first.
        The harvest is done when the source's read reports its discover done: the agent
        read the source and the api stored, or held, its contract."""
        seen = {e["id"] for e in self.events()}
        _, body = self.api.call("POST", self.catalog("/discover"), ok_codes=(202,))
        command = body["command_id"]

        def done():
            _, sources = self.api.call("GET", f"/v1/projects/{self.project}/sources")
            for source in (sources or {}).get("data") or []:
                discover = source.get("auto_discover") or {}
                if source.get("source_slug") == self.source and discover.get("command_id") == command:
                    if discover.get("status") == "failed":
                        raise Fail(f"the discover {command} failed: {discover.get('reason')}")
                    return discover.get("status") == "done"
            return False
        self.wait(f"the discover {command}", done)
        return [e for e in self.events() if e["id"] not in seen]

    def trigger(self):
        """Start a run of the job; its command id."""
        _, body = self.api.call("POST", f"/v1/projects/{self.project}/runs",
                                {"environment_id": self.environment, "job_name": self.job},
                                ok_codes=(202,))
        return body["cmd_id"]

    def finished_run(self, cmd_id):
        def done():
            query = urllib.parse.urlencode({"project": self.project, "env": self.environment})
            _, body = self.api.call("GET", f"/v1/runs?{query}")
            for run in (body or {}).get("data") or []:
                if run.get("cmd_id") == cmd_id and run.get("finished_at"):
                    return run
            return None
        return self.wait(f"the run of command {cmd_id}", done)

    # -- the git host ----------------------------------------------------------------

    def create_repo(self, name):
        _, me = self.git.call("GET", "/user")
        path = "/user/repos" if me.get("login") == self.owner else f"/orgs/{self.owner}/repos"
        self.git.call("POST", path, {"name": name, "auto_init": True, "default_branch": "main",
                                     "private": True}, ok_codes=(201,))
        return f"{self.git_for_api.rstrip('/')}/{self.owner}/{name}.git"

    def grant(self, repo, login, permission):
        self.git.call("PUT", f"/repos/{self.owner}/{repo}/collaborators/{login}",
                      {"permission": permission}, ok_codes=(204,))

    def read_file(self, repo, path, ref="main"):
        code, body = self.git.call("GET", f"/repos/{self.owner}/{repo}/contents/{path}?ref={ref}",
                                   ok_codes=(200, 404))
        if code == 404:
            return None, None
        return base64.b64decode(body["content"]).decode(), body["sha"]

    def write_files(self, repo, files, message, branch="main"):
        """Create or update files, a dict of path to text, in one commit on branch."""
        changes = []
        for path, text in files.items():
            current, sha = self.read_file(repo, path, branch)
            if current == text:
                continue
            change = {"operation": "update" if sha else "create", "path": path,
                      "content": base64.b64encode(text.encode()).decode()}
            if sha:
                change["sha"] = sha
            changes.append(change)
        if changes:
            self.git.call("POST", f"/repos/{self.owner}/{repo}/contents",
                          {"branch": branch, "message": message, "files": changes}, ok_codes=(200, 201))

    def open_pulls(self):
        _, body = self.git.call("GET", f"/repos/{self.owner}/{self.project_repo}/pulls?state=open&limit=50")
        return body or []

    def pull(self, number):
        _, body = self.git.call("GET", f"/repos/{self.owner}/{self.project_repo}/pulls/{number}")
        return body

    def head_contract(self, number):
        """The contract file at the head of pull request number."""
        text, _ = self.read_file(self.project_repo, "contracts/jaffle.odcs.yaml", self.pull(number)["head"]["ref"])
        return text or ""

    # -- the source and the control database ------------------------------------------

    def reload_source(self):
        command = os.environ.get("SOURCE_RELOAD") or (
            f"sh {shlex.quote(os.path.join(STEPS, '99-reset.sh'))} && "
            f"sh {shlex.quote(os.path.join(STEPS, '01-start.sh'))}")
        if subprocess.run(["sh", "-c", command], cwd=ROOT, check=False).returncode != 0:
            raise Fail("the source did not reload")

    def mutate(self, names):
        psql("BEGIN;\n" + "".join(mutation_sql(n) for n in names) + "COMMIT;\n")

    def control_psql(self, sql):
        """Run sql in the control database. CONTROL_DB_URL reaches psql as PG* variables,
        so no password is on a command line."""
        return psql(sql, ["psql", "-X"], env=pg_environment(self.control_db))


def pg_environment(url):
    """The PG* variables of a postgres:// URL, added to this process's environment."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("postgres", "postgresql") or not parts.path.strip("/"):
        raise Fail("CONTROL_DB_URL is not a postgres://user:password@host:port/database URL")
    names = {"PGHOST": parts.hostname, "PGPORT": parts.port, "PGDATABASE": parts.path.lstrip("/"),
             "PGUSER": urllib.parse.unquote(parts.username or ""),
             "PGPASSWORD": urllib.parse.unquote(parts.password or "")}
    for key, value in urllib.parse.parse_qsl(parts.query):
        if key == "sslmode":
            names["PGSSLMODE"] = value
    out = dict(os.environ)
    out.update({k: str(v) for k, v in names.items() if v not in (None, "")})
    return out


def seed_sql(tenant_id, project_id, environment, name, url):
    """The insert of the organization repository's config.git_repo row, in one transaction
    under app.tenant_id: it copies the credential reference of the project environment's
    connected repository, and returns the new row's id."""
    return (
        "\\set ON_ERROR_STOP on\n"
        "BEGIN;\n"
        f"SET LOCAL app.tenant_id = {sql_text(tenant_id)};\n"
        "INSERT INTO config.git_repo (tenant_id, display_name, git_repo_url, credential_ref)\n"
        f"SELECT g.tenant_id, {sql_text(name)}, {sql_text(url)}, g.credential_ref\n"
        "  FROM config.project_environment pe\n"
        "  JOIN config.git_repo g ON g.id = pe.git_repo_id\n"
        "  JOIN core.environment e ON e.id = pe.environment_id\n"
        f" WHERE pe.tenant_id = {sql_text(tenant_id)}::uuid AND pe.project_id = {sql_text(project_id)}::uuid\n"
        f"   AND e.slug = {sql_text(environment)} AND g.project_id IS NULL\n"
        "RETURNING id;\n"
        "COMMIT;\n"
    )


def stored_object(contract_yaml, table):
    contract = yaml.safe_load(contract_yaml) or {}
    for obj in contract.get("schema") or []:
        if table in (obj.get("name"), obj.get("physicalName")):
            return obj
    return None


def stored_column(contract_yaml, table, column):
    obj = stored_object(contract_yaml, table) or {}
    for prop in obj.get("properties") or []:
        if prop.get("name") == column:
            return prop
    return None


def dimensions(prop):
    """A column's precision and scale as the contract records them: as keys of the column,
    or as the custom properties an annotated harvest writes. None when it records neither."""
    custom = {c.get("property"): c.get("value") for c in prop.get("customProperties") or []}
    precision = prop.get("precision", custom.get("precision"))
    scale = prop.get("scale", custom.get("scale"))
    if precision is None:
        return None
    return int(precision), int(scale or 0)


def physical_type(prop):
    """A column's physical type, written without spaces, with the dimensions the contract
    records in place of any it writes in the type."""
    text = re.sub(r"\s+", "", str(prop.get("physicalType", "")))
    dims = dimensions(prop)
    if dims:
        text = f"{text.split('(')[0]}({dims[0]},{dims[1]})"
    return text


def check_annotated(stored):
    """The setup's precondition: the stored contract records tax_rate's precision and scale,
    so it is the contract an annotated harvest writes."""
    column = stored_column(stored, "stores", "tax_rate")
    if not column or dimensions(column) != (6, 4):
        raise Fail("the stored contract does not record tax_rate's precision 6 and scale 4: "
                   "the first harvest was not annotated")


def assert_baseline(stack, case):
    stored = stack.contract()["contract_yaml"]
    for schema, table, column, fact in case.baseline:
        where = f"{schema}.{table}{'.' + column if column else ''}"
        found = stored_column(stored, table, column) if column else stored_object(stored, table)
        if fact is True and not found:
            raise Fail(f"{case.name}: the stored contract lacks {where}")
        if fact is False and found:
            raise Fail(f"{case.name}: the stored contract has {where}")
        if isinstance(fact, str) and (not found or physical_type(found) != fact):
            raise Fail(f"{case.name}: the stored contract has {where} as "
                       f"{physical_type(found) if found else 'nothing'}, not {fact}")
        ok(f"{case.name}: baseline: {where} {baseline_text(fact)}")


def assert_event(case, events, detected_by, cmd_id=None):
    """The one drift event of the harvest or the check, with the case's changes. A run
    check's event must name the run it belongs to."""
    if len(events) != 1:
        raise Fail(f"{case.name}: {len(events)} drift events, not one")
    event = events[0]
    got = event.get("situations") or []
    for change in case.changes:
        if not any(change.matches(g) for g in got):
            raise Fail(f"{case.name}: the event has no change {change}; it has {json.dumps(got)}")
        ok(f"{case.name}: {change}")
    if event.get("overall_class") != case.overall:
        raise Fail(f"{case.name}: the overall class is {event.get('overall_class')}, not {case.overall}")
    if bool(event.get("halts")) != case.halts:
        raise Fail(f"{case.name}: the event {'does not halt' if case.halts else 'halts'}")
    if event.get("detected_by") != detected_by:
        raise Fail(f"{case.name}: the event was found by {event.get('detected_by')}, not {detected_by}")
    if detected_by == "run_check" and (not cmd_id or (event.get("run") or {}).get("command_id") != cmd_id):
        raise Fail(f"{case.name}: the run check's event names run {event.get('run')}, not {cmd_id}")
    ok(f"{case.name}: overall {case.overall.upper()}, {'halts' if case.halts else 'continues'}, "
       f"found by {detected_by}")
    return event


def event_pull_request(stack, event_id):
    for event in stack.events():
        if event["id"] == event_id:
            return event.get("pull_request") or {}
    raise Fail(f"the drift event {event_id} is gone")


def assert_pull_request(stack, case, event):
    """merges: the platform itself merges the event's pull request, merged_by_platform, and
    the git host shows it merged; waits: the pull request is open, on the api and on the git
    host, and stays open for SCENARIO_SETTLE seconds."""
    if case.pull_request == "merges":
        stack.wait(f"{case.name}: the platform's merge of its pull request",
                   lambda: event_pull_request(stack, event["id"]).get("state") == "merged_by_platform")
        pr = event_pull_request(stack, event["id"])
        if not stack.pull(pr["number"]).get("merged"):
            raise Fail(f"{case.name}: the git host does not show #{pr['number']} merged")
        ok(f"{case.name}: the pull request merged itself")
        return pr
    if case.pull_request == "waits":
        pr = stack.wait(f"{case.name}: the pull request", lambda: event_pull_request(stack, event["id"]) or None)
        stack.holds(f"{case.name}: pull request #{pr.get('number')} did not wait for a person",
                    lambda: event_pull_request(stack, event["id"]).get("state") == "open"
                    and stack.pull(pr["number"]).get("state") == "open"
                    and not stack.pull(pr["number"]).get("merged"))
        ok(f"{case.name}: pull request #{pr['number']} is open and waits for a person")
        return pr
    return None


def add_projections(contract_text, block):
    """The contract text with the projections block appended to its top-level
    customProperties, every other line unchanged, so the push changes nothing else."""
    entry = yaml.safe_dump([{"property": "projections", "value": block}], sort_keys=False)
    entry = "".join("  " + line + "\n" for line in entry.splitlines())
    lines = contract_text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if line.rstrip("\n") == "customProperties:":
            return "".join(lines[:i + 1]) + entry + "".join(lines[i + 1:])
    return contract_text.rstrip("\n") + "\ncustomProperties:\n" + entry


def person_merges(stack, pr):
    """The person pushes the projections block onto the pull request and merges it."""
    head = stack.pull(pr["number"])["head"]["ref"]
    base_text, _ = stack.read_file(stack.project_repo, "contracts/jaffle.odcs.yaml")
    head_text, _ = stack.read_file(stack.project_repo, "contracts/jaffle.odcs.yaml", head)
    from_version = (yaml.safe_load(base_text) or {}).get("version")
    stack.write_files(stack.project_repo,
                      {"contracts/jaffle.odcs.yaml": add_projections(head_text, projections_entry(from_version))},
                      "Add the projections block the contract gate requires", branch=head)
    stack.git.call("POST", f"/repos/{stack.owner}/{stack.project_repo}/pulls/{pr['number']}/merge",
                   {"Do": "merge"}, ok_codes=(200,))
    ok(f"the person pushed the projections block onto #{pr['number']} and merged it with a merge commit")


def check_releases(values, api_version, agent_version):
    """Refuse an api older than API_VERSION or an agent older than AGENT_VERSION; a version
    that cannot be read counts as older."""
    for name, got in (("API_VERSION", api_version), ("AGENT_VERSION", agent_version)):
        if (version_tuple(got) or (0, 0, 0)) < version_tuple(values[name]):
            raise Fail(f"the stack runs {got!r}, older than {name} {values[name]}")


def check_accounts(person, platform):
    """The person and the platform are two accounts of the git host: a merge by the
    platform's own account is never a person's."""
    if not person or not platform or person == platform:
        raise Fail(f"FORGEJO_TOKEN and FORGEJO_PLATFORM_TOKEN must be two accounts; both are {person!r}"
                   if person == platform else "a git host token names no account")


def setup(stack):
    say("Checking the releases the stack runs, and the two git host accounts")
    _, health = stack.api.call("GET", "/v1/health")
    agent = subprocess.run([os.environ.get("AMFELT_BIN", "amfelt"), "version"], capture_output=True,
                           text=True, check=False).stdout.strip()
    check_releases(read_requires(), health.get("version"), agent)
    person = (stack.git.call("GET", "/user")[1] or {}).get("login")
    platform = (stack.platform_git.call("GET", "/user")[1] or {}).get("login")
    check_accounts(person, platform)
    ok(f"api {health.get('version')}, {agent}; the person is {person}, the platform {platform}")

    say("Creating the organization repository and the project repository")
    org_url = stack.create_repo(stack.org_repo)
    project_url = stack.create_repo(stack.project_repo)
    stack.grant(stack.org_repo, platform, "read")
    stack.grant(stack.project_repo, platform, "write")
    with open(ORG_FILE, encoding="utf-8") as f:
        stack.write_files(stack.org_repo, {f"{ORG_FOLDER}/amfelt-org.yaml": f.read()},
                          "Add the demo organization's drift policy")
    with open(PROJECT_FILE, encoding="utf-8") as f, open(SHIPPED_CONTRACT, encoding="utf-8") as c:
        stack.write_files(stack.project_repo,
                          {"amfelt.yaml": f.read(), "contracts/jaffle.odcs.yaml": c.read()},
                          "Add the Jaffle project with the contract an annotated harvest writes")
    ok(f"{org_url} holds {ORG_FOLDER}/amfelt-org.yaml; {project_url} holds jaffle/project at its root")

    say("Connecting the project environment to the project repository, with the platform's token")
    stack.api.call("PUT", f"/v1/projects/{stack.project}/environments/{stack.environment}/repo",
                   {"git_repo_url": project_url, "display_name": stack.project_repo, "branch": "main",
                    "token": stack.platform_token})

    say("Seeding the organization repository's config.git_repo row (ADR-168 Open Question 31)")
    _, org = stack.api.call("GET", f"/v1/orgs/{stack.org}")
    seed = stack.control_psql(seed_sql(org["id"], stack.project, stack.environment, stack.org_repo, org_url))
    rows = [line for line in seed.splitlines() if re.fullmatch(r"[0-9a-f-]{36}", line)]
    if len(rows) != 1:
        raise Fail(f"the seed returned no single repository id: {seed!r}")
    repository_id = rows[0]
    ok(f"config.git_repo {repository_id} names {org_url}")

    say("Binding the organization to org/ in its repository (PUT /v1/orgs/{slug}/binding)")
    stack.api.call("PUT", f"/v1/orgs/{stack.org}/binding", {"repository_id": repository_id, "path": ORG_FOLDER})
    _, binding = stack.api.call("GET", f"/v1/orgs/{stack.org}/binding")
    if binding.get("repository_id") != repository_id or binding.get("path") != ORG_FOLDER:
        raise Fail(f"the binding reads back as {binding}")
    ok(f"the organization reads {ORG_FOLDER}/amfelt-org.yaml at {binding.get('branch')}")

    say("Reloading the source and harvesting it: the stored contract is annotated")
    stack.reload_source()
    stack.harvest()
    stored = stack.contract()["contract_yaml"]
    check_annotated(stored)
    stack.initial_contract = stored
    if stack.harvest() or stack.halted():
        raise Fail("a second harvest of the unchanged source finds drift or halts")
    ok("the stored contract records tax_rate as numeric(6,4), as the shipped file does; no drift")


def sql_text(value):
    return "'" + str(value).replace("'", "''") + "'"


def reset(stack, org_text=None, contract_file=None):
    """A freshly loaded source and the setup's baseline: the stored contract and the project
    repository's contract file put back, the halt ended by a harvest that finds no change."""
    with open(ORG_FILE, encoding="utf-8") as f:
        stack.write_files(stack.org_repo, {f"{ORG_FOLDER}/amfelt-org.yaml": org_text or f.read()},
                          "Set the organization's drift policy for the next case")
    with open(contract_file or SHIPPED_CONTRACT, encoding="utf-8") as f:
        stack.write_files(stack.project_repo, {"contracts/jaffle.odcs.yaml": f.read()},
                          "Put the contract file of the next case in place")
    stack.reload_source()
    if stack.contract()["contract_yaml"] != stack.initial_contract:
        stack.api.call("PUT", stack.catalog("/contract"),
                       {"contract_yaml": stack.initial_contract, "role": "source"}, ok_codes=(200,))
    stack.harvest()
    if stack.halted():
        raise Fail("the source is still halted after the reset")


def by_harvest(stack, case):
    say(f"Case {case.name}, found by a harvest")
    reset(stack)
    stack.mutate(case.mutations)
    event = assert_event(case, stack.harvest(), "harvest")
    if stack.halted() != case.halts:
        raise Fail(f"{case.name}: the source is {'not ' if case.halts else ''}halted")
    assert_baseline(stack, case)
    pr = assert_pull_request(stack, case, event)
    if case.name == "tweets":
        # No merge ends a REPAIR halt: the posts pull request merged, and the source is
        # still halted, with the baseline held.
        if not stack.halted():
            raise Fail("tweets: the merge of the posts pull request ended the REPAIR halt")
        assert_baseline(stack, case)
        ok("tweets: after the posts merge the source is still halted and the baseline held")
    if case.name == "description":
        person_merges(stack, pr)
        stack.wait("the import of the merged drop", lambda: imported_by_person(event_pull_request(stack, event["id"])))
        if stack.halted():
            raise Fail("description: the halt did not end with the import")
        stack.finished_run(stack.trigger())
        ok("description: the platform imported the person's merge, the halt ended, run start was accepted")


def imported_by_person(pr):
    return pr.get("state") == "merged_by_person" and (pr.get("import") or {}).get("outcome") == "imported"


def by_run_check(stack, case):
    say(f"Case {case.name}, found by the run check of the next run")
    reset(stack)
    stack.mutate(case.mutations)
    seen = {e["id"] for e in stack.events()}
    cmd = stack.trigger()
    run = stack.finished_run(cmd)
    assert_event(case, [e for e in stack.events() if e["id"] not in seen], "run_check", cmd)
    assert_baseline(stack, case)
    assert_run(case, run)


def assert_run(case, run):
    """A run whose check halts reports stopped and loads no row; one whose check continues
    runs to finished."""
    if case.halts:
        if run.get("status") != "stopped" or run.get("total_rows") not in (0, None):
            raise Fail(f"{case.name}: the run is {run.get('status')} with {run.get('total_rows')} rows, "
                       "not stopped with none")
        ok(f"{case.name}: the run reports stopped and loaded no row")
    else:
        if run.get("status") != "finished":
            raise Fail(f"{case.name}: the run is {run.get('status')}; its check should have let it run")
        ok(f"{case.name}: the run ran: {run.get('status')}, {run.get('total_rows')} rows")


def tax_rate_with_the_old_file(stack):
    """With the contract the quickstart shipped before E1, which records no dimension on
    tax_rate, the tax_rate change is column.type.unknown against the file (ADR-168, the E1
    row), so its pull request is not eligible and does not merge itself. Against the stored
    contract it is still column.type.widened, AUTO."""
    say("Case tax_rate against the unannotated contract file")
    reset(stack, contract_file=UNANNOTATED_CONTRACT)
    stack.mutate(["tax-rate"])
    case = Case("tax_rate, unannotated file", ["tax-rate"], [TAX_RATE], "auto",
                [("sales", "stores", "tax_rate", "numeric(8,4)")], "waits")
    event = assert_event(case, stack.harvest(), "harvest")
    assert_pull_request(stack, case, event)
    ok("tax_rate, unannotated file: the pull request did not merge itself")


def revert(stack):
    say("Case description revert")
    reset(stack)
    stack.mutate(["description"])
    case = CASES[2]
    assert_event(case, stack.harvest(), "harvest")
    if not stack.halted():
        raise Fail("description revert: the drop did not halt")
    stack.mutate(["description-back"])
    assert_reverted(stack.harvest(), stack.halted())
    stack.finished_run(stack.trigger())
    ok("the column came back as it was: no drift event, the halt ended, run start was accepted")


def assert_reverted(new_events, halted):
    if new_events:
        raise Fail(f"the harvest after the revert wrote {len(new_events)} drift events, not none")
    if halted:
        raise Fail("the halt did not end when the column came back")


def lag(stack):
    say("Case lag, in an organization with no auto-merge rule")
    reset(stack, NO_RULE)
    stack.mutate(["email"])
    case = Case("lag: email", ["email"], [EMAIL], "auto", [("sales", "customers", "email", True)], "waits")
    first = assert_event(case, stack.harvest(), "harvest")
    pr = assert_pull_request(stack, case, first)
    assert_baseline(stack, case)
    stack.mutate(["email-integer"])
    retyped = Case("lag: email integer", ["email-integer"], [EMAIL_INTEGER], "repair",
                   [("sales", "customers", "email", True)], None)
    second = assert_event(retyped, stack.harvest(), "harvest")
    assert_email_held(stack.contract()["contract_yaml"])
    if not stack.halted():
        raise Fail("lag: the source is not halted")
    stack.wait("the platform's close of the email pull request",
               lambda: stack.pull(pr["number"]).get("state") == "closed" and not stack.pull(pr["number"]).get("merged"))
    assert_not_proposed(second, stack.open_pulls(), stack.head_contract)
    ok("lag: REPAIR against the stored contract; email held as text; the pull request closed; email not proposed")
    reset(stack)


def assert_email_held(stored):
    """The held baseline keeps email as the text column the first harvest stored."""
    email = stored_column(stored, "customers", "email")
    if not email or email.get("logicalType") != "string" or physical_type(email) != "text":
        raise Fail(f"lag: the stored contract has email as {email}, not as text")


def assert_not_proposed(event, open_pulls, head_contract):
    """email is not proposed: the REPAIR event opened no pull request, and no open pull
    request of the project repository carries customers.email in its contract file."""
    if event.get("pull_request"):
        raise Fail(f"lag: the retype's event proposes pull request {event['pull_request']}")
    for pr in open_pulls:
        if stored_column(head_contract(pr["number"]), "customers", "email"):
            raise Fail(f"lag: open pull request #{pr['number']} proposes email")


def run():
    _, missing = gate()
    if missing:
        raise Fail("REQUIRES does not name " + " and ".join(missing) + " yet; scenario.py gate says why")
    stack = Stack()
    setup(stack)
    for case in CASES:
        by_harvest(stack, case)
    tax_rate_with_the_old_file(stack)
    revert(stack)
    by_harvest(stack, ALL_FOUR)
    for case in CASES + [ALL_FOUR]:
        by_run_check(stack, case)
    lag(stack)
    say("The Jaffle Shop drift scenario passed")


def main(argv):
    if len(argv) != 2 or argv[1] not in ("plan", "check", "mutations", "gate", "run"):
        print(__doc__, file=sys.stderr)
        return 2
    try:
        if argv[1] == "plan":
            plan()
        elif argv[1] == "check":
            check()
        elif argv[1] == "mutations":
            mutations()
        elif argv[1] == "gate":
            _, missing = gate()
            if missing:
                print("The live drift scenario waits for releases that do not exist yet: REQUIRES names "
                      "no " + " and no ".join(missing) + ". ci/jaffle/drift/README.md says what to set.")
                return GATE_UNMET
            ok("REQUIRES names the api and agent releases the live run needs")
        else:
            run()
    except Fail as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
