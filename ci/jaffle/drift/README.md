# The Jaffle Shop drift scenario

The acceptance scenario of ADR-168, task E1
([amfeltlabs/examples#63](https://github.com/amfeltlabs/examples/issues/63)): four schema
changes to the Jaffle Shop source, each resolved to its drift situation and its standard
class under the demo organization's auto-merge rule, with no `driftPolicy` in the project's
`amfelt.yaml`. No reader runs it.

| File | What it is |
| --- | --- |
| `../../../jaffle/org/amfelt-org.yaml` | The demo rule, the organization file the binding names |
| `mutations/*.sql` | The four mutations, the revert of the drop, and the lag case's retype |
| `scenario.py` | The scenario: `plan`, `check`, `mutations`, `gate` and `run` |
| `test_scenario.py` | Tests of the checks and of the assertions the live run makes |
| `REQUIRES` | The api and agent releases the live run needs |
| `unannotated.odcs.yaml` | The contract the quickstart shipped before E1, for the `tax_rate` negative case |

## What runs now

The CI workflow's `drift` job runs everything that needs no stack:

```bash
python3 -m pip install check-jsonschema==0.38.2 pyyaml==6.0.3
(cd ci/jaffle/drift && python3 -m unittest test_scenario)
check-jsonschema --schemafile schemas/amfelt-org-json-schema.json jaffle/org/amfelt-org.yaml
python3 ci/jaffle/drift/scenario.py check
(cd jaffle && docker compose up -d --wait source && python3 ../ci/jaffle/drift/scenario.py mutations)
python3 ci/jaffle/drift/scenario.py gate   # exit 3 while REQUIRES names no release
```

`scenario.py plan` prints every case and what it asserts.

## The live run, and what switches it on

`scenario.py run` drives a stack: the api, its control database, its Forgejo and an agent.
It creates an organization repository and a project repository on the stack's Forgejo,
connects the project environment to the project repository, seeds the organization
repository's `config.git_repo` row with one SQL insert, sets the binding through
`PUT /v1/orgs/{slug}/binding`, and then runs each mutation twice: found by a harvest, and
found by the run check of the next run. The project repository holds `jaffle/project/`, whose
contract is the one an annotated harvest writes (ADR-168 Open Question 22, interim); the
`tax_rate` case runs once more with `unannotated.odcs.yaml`, whose pull request must not merge
itself. The person who merges and the platform must be two git host accounts. The docstring
of `scenario.py` lists the environment it reads and the access each account needs.

It has not run yet. The api release it needs does not exist: A4 and A7 are not built, and
A11 is not merged. The agent release it needs, v5.21.0 with G3, is published, and
`REQUIRES` names it. To switch it on:

1. Once the api release that holds A1, A3, A4, A7, A8, A11, A12 and A12b exists, set
   `API_VERSION` in `REQUIRES`. `scenario.py gate` then passes, and the `drift` job's last
   step says so.
2. Run `scenario.py run` against a stack that runs those releases or later, such as the
   local stack of the stack repository, with a project of the demo organization that holds
   the source `jaffle` and the job `load` of `jaffle/project/amfelt.yaml`.
3. To run it in CI as well, add a job, or a scenario of the stack repository's validation
   estate, that applies such a stack and calls `scenario.py run`; this workflow starts no
   stack.
