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

The project file is read with PyYAML when it is installed, as in CI. Without it, a small
standard-library reader takes over, for readers of the tutorial whose python3 has no
PyYAML. It accepts a strict subset of block-style YAML, the subset the agent writes, and
returns for it exactly what yaml.safe_load returns; it refuses every input outside the
subset with an error. builtin_load below lists the subset. CHECK_PROJECT_YAML=builtin
forces it.
counts.yaml is read with counts.py's own parser.
"""

import datetime
import importlib.util
import os
import re
import sys

try:
    import yaml
except ImportError:  # the built-in reader below takes over
    yaml = None

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


class ProjectYamlError(Exception):
    pass


# The built-in reader. It accepts a strict subset of block-style YAML and returns for it
# exactly what yaml.safe_load returns; anything outside the subset raises ProjectYamlError.
#
#   - The text is printable ASCII and newlines: no tab, carriage return, byte-order mark,
#     control character or non-ASCII character.
#   - No document markers, flow collections, anchors, aliases, tags, block scalars or
#     multi-line scalars.
#   - Each line, after its indentation, is a comment ("# ..."), "key:", "key: value",
#     "- value" or "- key: ..." with exactly one space after the dash. Keys are plain
#     words, unique within their mapping, that YAML reads as strings.
#   - A value is a double-quoted string with no backslash or quote inside, a single-quoted
#     string with no quote inside, or a plain scalar that does not start with an indicator,
#     ends in no colon, and holds no ": " and no " #".
#
# A plain scalar is typed by PyYAML's own implicit resolvers (YAML 1.1), copied below from
# PyYAML 6.0's resolver.py: a bool, a null, a decimal integer without sign or underscores,
# or a timestamp is built as PyYAML's constructor builds it (timestamp_regexp and
# construct_yaml_timestamp, copied from constructor.py); every other int, every float,
# and the merge and value keys are refused. A scalar that matches no resolver is a string.
RESOLVE_BOOL = re.compile(r'''^(?:yes|Yes|YES|no|No|NO
                    |true|True|TRUE|false|False|FALSE
                    |on|On|ON|off|Off|OFF)$''', re.X)
RESOLVE_FLOAT = re.compile(r'''^(?:[-+]?(?:[0-9][0-9_]*)\.[0-9_]*(?:[eE][-+][0-9]+)?
                    |\.[0-9][0-9_]*(?:[eE][-+][0-9]+)?
                    |[-+]?[0-9][0-9_]*(?::[0-5]?[0-9])+\.[0-9_]*
                    |[-+]?\.(?:inf|Inf|INF)
                    |\.(?:nan|NaN|NAN))$''', re.X)
RESOLVE_INT = re.compile(r'''^(?:[-+]?0b[0-1_]+
                    |[-+]?0[0-7_]+
                    |[-+]?(?:0|[1-9][0-9_]*)
                    |[-+]?0x[0-9a-fA-F_]+
                    |[-+]?[1-9][0-9_]*(?::[0-5]?[0-9])+)$''', re.X)
RESOLVE_MERGE = re.compile(r'^(?:<<)$')
RESOLVE_NULL = re.compile(r'''^(?: ~
                    |null|Null|NULL
                    | )$''', re.X)
RESOLVE_TIMESTAMP = re.compile(r'''^(?:[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]
                    |[0-9][0-9][0-9][0-9] -[0-9][0-9]? -[0-9][0-9]?
                     (?:[Tt]|[ \t]+)[0-9][0-9]?
                     :[0-9][0-9] :[0-9][0-9] (?:\.[0-9]*)?
                     (?:[ \t]*(?:Z|[-+][0-9][0-9]?(?::[0-9][0-9])?))?)$''', re.X)
RESOLVE_VALUE = re.compile(r'^(?:=)$')
TIMESTAMP_PARTS = re.compile(
    r'''^(?P<year>[0-9][0-9][0-9][0-9])
        -(?P<month>[0-9][0-9]?)
        -(?P<day>[0-9][0-9]?)
        (?:(?:[Tt]|[ \t]+)
        (?P<hour>[0-9][0-9]?)
        :(?P<minute>[0-9][0-9])
        :(?P<second>[0-9][0-9])
        (?:\.(?P<fraction>[0-9]*))?
        (?:[ \t]*(?P<tz>Z|(?P<tz_sign>[-+])(?P<tz_hour>[0-9][0-9]?)
        (?::(?P<tz_minute>[0-9][0-9]))?))?)?$''', re.X)
BOOL_VALUES = {"yes": True, "no": False, "true": True, "false": False, "on": True, "off": False}
DECIMAL = re.compile(r"(0|[1-9][0-9]*)\Z")
PLAIN_KEY = re.compile(r"([A-Za-z_][A-Za-z0-9_.-]*):(?: (.*))?\Z")
PLAIN_FIRST_REFUSED = "-?:,[]{}#&*!|>'\"%@`<="


def build_timestamp(text, where):
    """PyYAML's construct_yaml_timestamp, refusing what it would fail on."""
    values = TIMESTAMP_PARTS.match(text).groupdict()
    try:
        year, month, day = int(values["year"]), int(values["month"]), int(values["day"])
        if not values["hour"]:
            return datetime.date(year, month, day)
        hour, minute, second = int(values["hour"]), int(values["minute"]), int(values["second"])
        fraction = 0
        tzinfo = None
        if values["fraction"]:
            fraction = values["fraction"][:6]
            while len(fraction) < 6:
                fraction += "0"
            fraction = int(fraction)
        if values["tz_sign"]:
            delta = datetime.timedelta(hours=int(values["tz_hour"]), minutes=int(values["tz_minute"] or 0))
            if values["tz_sign"] == "-":
                delta = -delta
            tzinfo = datetime.timezone(delta)
        elif values["tz"]:
            tzinfo = datetime.timezone.utc
        return datetime.datetime(year, month, day, hour, minute, second, fraction, tzinfo=tzinfo)
    except ValueError as err:
        raise ProjectYamlError("%s: %r is not a valid timestamp: %s" % (where, text, err))


def resolve_plain(text, where):
    """The value PyYAML gives a plain scalar, or a refusal."""
    if RESOLVE_BOOL.match(text):
        return BOOL_VALUES[text.lower()]
    if RESOLVE_NULL.match(text):
        return None
    if RESOLVE_TIMESTAMP.match(text):
        return build_timestamp(text, where)
    if RESOLVE_INT.match(text):
        if DECIMAL.match(text):
            return int(text)
        raise ProjectYamlError("%s: %r is a YAML 1.1 integer form this reader refuses" % (where, text))
    if RESOLVE_FLOAT.match(text) or RESOLVE_MERGE.match(text) or RESOLVE_VALUE.match(text):
        raise ProjectYamlError("%s: %r is typed as a float or a special key by YAML; quote it" % (where, text))
    return text


def builtin_scalar(text, where):
    if text.startswith('"'):
        if len(text) < 2 or not text.endswith('"') or "\\" in text or '"' in text[1:-1]:
            raise ProjectYamlError("%s: unsupported double-quoted string %s" % (where, text))
        return text[1:-1]
    if text.startswith("'"):
        if len(text) < 2 or not text.endswith("'") or "'" in text[1:-1]:
            raise ProjectYamlError("%s: unsupported single-quoted string %s" % (where, text))
        return text[1:-1]
    if text[0] in PLAIN_FIRST_REFUSED or text.endswith(":") or ": " in text or " #" in text:
        raise ProjectYamlError("%s: unsupported value %r" % (where, text))
    return resolve_plain(text, where)


def builtin_load(text, name):
    """The block-style YAML the agent writes, as yaml.safe_load would return it."""
    lines = []
    for number, raw in enumerate(text.split("\n"), 1):
        where = "%s:%d" % (name, number)
        bad = [c for c in raw if not " " <= c <= "~"]
        if bad:
            raise ProjectYamlError("%s: character %r is not printable ASCII" % (where, bad[0]))
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("---") or stripped.startswith("..."):
            raise ProjectYamlError("%s: document markers are not supported" % where)
        indent = len(raw) - len(raw.lstrip(" "))
        lines.append([where, indent, raw.rstrip()[indent:]])
    pos = [0]

    def is_item(line):
        return line[2] == "-" or line[2].startswith("- ")

    def block(indent):
        return sequence(indent) if is_item(lines[pos[0]]) else mapping(indent)

    def mapping(indent):
        out = {}
        while pos[0] < len(lines) and lines[pos[0]][1] == indent and not is_item(lines[pos[0]]):
            where, _, content = lines[pos[0]]
            m = PLAIN_KEY.match(content)
            if not m:
                raise ProjectYamlError("%s: expected 'key: value', got %r" % (where, content))
            key, rest = m.group(1), (m.group(2) or "").strip()
            if not isinstance(resolve_plain(key, where), str):
                raise ProjectYamlError("%s: key %r is not read as a string by YAML" % (where, key))
            if key in out:
                raise ProjectYamlError("%s: duplicate key %r" % (where, key))
            pos[0] += 1
            if rest:
                out[key] = builtin_scalar(rest, where)
            elif pos[0] < len(lines) and (lines[pos[0]][1] > indent
                                          or (lines[pos[0]][1] == indent and is_item(lines[pos[0]]))):
                out[key] = block(lines[pos[0]][1])
            else:
                out[key] = None
        return out

    def sequence(indent):
        out = []
        while pos[0] < len(lines) and lines[pos[0]][1] == indent and is_item(lines[pos[0]]):
            where, _, content = lines[pos[0]]
            rest = content[2:]
            if not rest or rest[0] == " ":
                raise ProjectYamlError("%s: a sequence item needs exactly one space after the dash" % where)
            if PLAIN_KEY.match(rest):
                # "- key: value" opens a mapping whose keys sit two columns in.
                lines[pos[0]] = [where, indent + 2, rest]
                out.append(mapping(indent + 2))
            else:
                out.append(builtin_scalar(rest, where))
                pos[0] += 1
        return out

    if not lines:
        return None
    if lines[0][1] != 0:
        raise ProjectYamlError("%s: the document must start at column 1" % lines[0][0])
    data = block(0)
    if pos[0] != len(lines):
        raise ProjectYamlError("%s: inconsistent indentation" % lines[pos[0]][0])
    return data


def load_project(path):
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    if yaml is not None and os.environ.get("CHECK_PROJECT_YAML") != "builtin":
        return yaml.safe_load(text)
    return builtin_load(text, os.path.basename(path))


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
    try:
        project = load_project(project_path)
    except ProjectYamlError as err:
        print("check_project.py: %s" % err, file=sys.stderr)
        return 1
    problems += check(project, expected["job"], image)
    if problems:
        print("check_project.py: %s does not match:\n  %s" % (project_path, "\n  ".join(problems)),
              file=sys.stderr)
        return 1
    print("ok: %s matches %s and %s" % (project_path, counts_path, image_path))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
