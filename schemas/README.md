# Schemas

`amfelt-project-json-schema-v0.0.0.json` is the JSON Schema of the Amfelt project file, format `v0.0.0`. It is a verbatim copy of `project/amfelt-project-json-schema-v0.0.0.json` in the core repository at commit `8f0d8a4044082589cb5784954816b3823091cd52`, where that file last changed in `b949a67ccc25e8dba6b957a9d8f7d31d9a982ffc`.

The `project` job of the CI workflow validates `jaffle/project/amfelt.yaml` against it. Replace the copy, and the two commits above, when the schema changes in core.

`drift-policy-schema.json` is the JSON Schema of the drift policy. It is a verbatim copy of `drift/policy.schema.json` in the core repository at commit `9be8b797760d4d71cccce72470828f24abffec4b`, where that file last changed in `a4f5fc786f99c84d1f49cf5b811e84a5bfa62951`. `amfelt-org-json-schema.json` is this repository's own schema of the organization file, `amfelt-org.yaml`: it checks the file's top-level `driftPolicy` against the copy's `OrgPolicy`. The `drift` job of the CI workflow validates `jaffle/org/amfelt-org.yaml` against it.

`projections-schema.json` is the JSON Schema of a contract's projections block. It is a verbatim copy of `drift/projections.schema.json` in the core repository at commit `9be8b797760d4d71cccce72470828f24abffec4b`, where that file last changed in `5c393bcfb7d71f17de07df9da61d8dd38f141e0e`. The drift scenario's offline check, `ci/jaffle/drift/scenario.py check`, validates the block the scenario pushes against it.

`odcs-json-schema-v3.2.0-20261029.json` is the JSON Schema of an ODCS v3.2.0 data contract. It is a verbatim copy of `contract/odcs-json-schema-v3.2.0-20261029.json` in the core repository at tag `v3.51.0`, commit `cf1e51ddae99d9c41ddda8bc98782a3ff8f9b0d6`, where that file last changed in `f884bb0ad15b6e5724021c2d838bd07a27fa16f9`. `ci/jaffle/check_contract.py` validates the tutorial's ready-made contract against it in the `project` job of the CI workflow, and the contracts the tutorial discovers and harvests in the `tutorial` job.

Replace each copy, and its two commits, when the schema changes in core.
