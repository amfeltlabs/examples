#!/usr/bin/env python3
"""Check that the tutorial's project file is the one the expected counts were made for.

Usage:
  check_project.py [PROJECT] [COUNTS] [IMAGE]

PROJECT defaults to jaffle/project/amfelt.yaml, COUNTS to jaffle/expected/counts.yaml and
IMAGE to jaffle/project/IMAGE, all beside this script.

The project file passes when:
  - it holds one job, whose layer and schemaPrefix equal counts.yaml's job block, and whose
    select.objects equals that block's objects exactly: the same objects, and for each the
    same tableType, historyMode and eventTimeCol, with no other setting;
  - its PostgreSQL source plugin records the version IMAGE pins as SOURCE_POSTGRES_VERSION,
    and its DuckDB target plugin records none or TARGET_DUCKDB_VERSION, both with registry
    local, so the agent never looks for a plugin the image does not hold.

IMAGE must hold exactly its six KEY=value lines, well formed. A change to the history
modes therefore has to change counts.yaml, whose layer rules counts.py checks against the
modes, together with the project file. The JSON Schema check in CI covers the file's shape;
this one covers what the counts depend on.

Needs PyYAML for the project file; counts.yaml is read with counts.py's own parser.
"""

import importlib.util
import os
import re
import sys

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PROJECT = os.path.join(HERE, "project", "amfelt.yaml")
DEFAULT_COUNTS = os.path.join(HERE, "expected", "counts.yaml")
DEFAULT_IMAGE = os.path.join(HERE, "project", "IMAGE")

IMAGE_KEYS = (
    "AGENT_IMAGE", "AGENT_IMAGE_TAG", "AGENT_IMAGE_DIGEST", "AGENT_VERSION",
    "SOURCE_POSTGRES_VERSION", "TARGET_DUCKDB_VERSION",
)
VERSION = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
IMAGE_NAME = re.compile(r"[a-z0-9.-]+(/[a-z0-9._-]+)+\Z")


def load_counts_module():
    spec = importlib.util.spec_from_file_location("counts", os.path.join(HERE, "expected", "counts.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_image(path, problems):
    values = {}
    with open(path, encoding="utf-8") as handle:
        for number, raw in enumerate(handle, 1):
            line = raw.rstrip("\n")
            if not line.strip() or line.startswith("#"):
                continue
            m = re.match(r"([A-Z_]+)=(\S+)\Z", line)
            if not m:
                problems.append("%s:%d: expected KEY=value, got %r" % (path, number, line))
                continue
            if m.group(1) in values:
                problems.append("%s:%d: duplicate %s" % (path, number, m.group(1)))
            values[m.group(1)] = m.group(2)
    missing = [k for k in IMAGE_KEYS if k not in values]
    extra = sorted(k for k in values if k not in IMAGE_KEYS)
    if missing:
        problems.append("%s: missing %s" % (path, ", ".join(missing)))
    if extra:
        problems.append("%s: unexpected %s" % (path, ", ".join(extra)))
    if "AGENT_IMAGE" in values and not IMAGE_NAME.match(values["AGENT_IMAGE"]):
        problems.append("%s: AGENT_IMAGE is not an image name: %r" % (path, values["AGENT_IMAGE"]))
    if "AGENT_IMAGE_DIGEST" in values and not DIGEST.match(values["AGENT_IMAGE_DIGEST"]):
        problems.append("%s: AGENT_IMAGE_DIGEST is not sha256:<64 hex>: %r" % (path, values["AGENT_IMAGE_DIGEST"]))
    for key in ("AGENT_VERSION", "SOURCE_POSTGRES_VERSION", "TARGET_DUCKDB_VERSION"):
        if key in values and not VERSION.match(values[key]):
            problems.append("%s: %s is not a version: %r" % (path, key, values[key]))
    if "AGENT_IMAGE_TAG" in values and "AGENT_VERSION" in values \
            and values["AGENT_IMAGE_TAG"] != "v" + values["AGENT_VERSION"]:
        problems.append("%s: AGENT_IMAGE_TAG %r is not v%s" % (path, values["AGENT_IMAGE_TAG"], values["AGENT_VERSION"]))
    return values


def plugin(project, category, name, problems):
    plugins = project.get("plugins")
    entries = plugins.get(category) if isinstance(plugins, dict) else None
    found = [p for p in entries or [] if isinstance(p, dict) and p.get("plugin") == name]
    if len(found) != 1:
        problems.append("plugins.%s: expected one %s plugin, found %d" % (category, name, len(found)))
        return None
    if found[0].get("registry") != "local":
        problems.append("plugins.%s[%s].registry: must be local, is %r" % (category, name, found[0].get("registry")))
    return found[0]


def check(project, job, image):
    problems = []
    jobs = project.get("jobs") if isinstance(project, dict) else None
    if not isinstance(jobs, list) or len(jobs) != 1 or not isinstance(jobs[0], dict):
        return ["jobs: expected exactly one job"]
    got = jobs[0]
    for key in ("layer", "schemaPrefix"):
        if got.get(key) != job[key]:
            problems.append("jobs[0].%s: counts.yaml expects %r, the project has %r" % (key, job[key], got.get(key)))
    select = got.get("select")
    objects = select.get("objects") if isinstance(select, dict) else None
    if not isinstance(objects, dict):
        problems.append("jobs[0].select.objects: missing")
    else:
        for name in sorted(set(objects) | set(job["objects"])):
            want, have = job["objects"].get(name), objects.get(name)
            if want is None:
                problems.append("jobs[0].select.objects.%s: not in counts.yaml's job block" % name)
            elif have is None:
                problems.append("jobs[0].select.objects.%s: missing; counts.yaml expects %r" % (name, want))
            elif have != want:
                problems.append("jobs[0].select.objects.%s: counts.yaml expects %r, the project has %r"
                                % (name, want, have))

    if image:
        source = plugin(project, "source", "postgres", problems)
        if source is not None and source.get("version") != image.get("SOURCE_POSTGRES_VERSION"):
            problems.append("plugins.source[postgres].version: IMAGE pins %r, the project records %r"
                            % (image.get("SOURCE_POSTGRES_VERSION"), source.get("version")))
        target = plugin(project, "target", "duckdb", problems)
        if target is not None and "version" in target \
                and target["version"] != image.get("TARGET_DUCKDB_VERSION"):
            problems.append("plugins.target[duckdb].version: IMAGE pins %r, the project records %r"
                            % (image.get("TARGET_DUCKDB_VERSION"), target["version"]))
    return problems


def main(argv):
    if len(argv) > 3:
        print("usage: check_project.py [PROJECT] [COUNTS] [IMAGE]", file=sys.stderr)
        return 2
    paths = list(argv) + [DEFAULT_PROJECT, DEFAULT_COUNTS, DEFAULT_IMAGE][len(argv):]
    project_path, counts_path, image_path = paths
    counts = load_counts_module()
    try:
        expected = counts.load(counts_path)
    except counts.CountsError as err:
        print("check_project.py: %s" % err, file=sys.stderr)
        return 1
    problems = []
    image = read_image(image_path, problems)
    with open(project_path, encoding="utf-8") as handle:
        project = yaml.safe_load(handle)
    problems += check(project, expected["job"], image)
    if problems:
        print("check_project.py: %s does not match:\n  %s" % (project_path, "\n  ".join(problems)),
              file=sys.stderr)
        return 1
    print("ok: %s matches %s and %s" % (project_path, counts_path, image_path))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
