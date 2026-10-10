#!/usr/bin/env python3
"""Validate and read jaffle/expected/counts.yaml.

Usage:
  counts.py validate [FILE]     check the file's structure; exit 1 listing every problem
  counts.py get PATH [FILE]     print one scalar, by a dotted path from the top of the
                                file: runs.run2.history.customers, reveal.after.street;
                                a key that holds a dot, such as sales.customers, is the
                                last segment: runs.run1.source.sales.customers
  counts.py expect RUN [FILE]   print what db/source_counts.sql prints after RUN (run1 or
                                run2), for a diff: one line per source table, then the
                                mover's row, tab-separated

FILE defaults to counts.yaml beside this script. Every command validates the file first
and refuses to read an invalid one.

The file is read with a small parser for the YAML subset it is written in, because the
Python standard library has no YAML: block mappings indented with spaces, one-line flow
mappings of scalars ({ a: 1, b: 2 }), integers, double-quoted strings, plain words, and
comments. Anything else is rejected rather than guessed at, including plain scalars that
YAML would read as something other than a string (true, null, 1.5, 0x1F), so every
reader, this one or a real YAML parser, sees the same values.

Python standard library only.
"""

import os
import re
import sys

DEFAULT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "counts.yaml")

# The seven tables of the model, schema-qualified as the source names them, and the bare
# names the warehouse layers use.
SOURCE_TABLES = (
    "sales.customers", "sales.stores", "sales.orders", "sales.order_items",
    "catalog.products", "catalog.supplies", "marketing.tweets",
)
BARE_TABLES = tuple(t.split(".", 1)[1] for t in SOURCE_TABLES)
RUNS = ("run1", "run2")
LAYERS = ("source", "staging", "history", "bridge_act", "bridge_hst")
ADDRESS_FIELDS = ("street", "postal_code", "city", "country")

KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*\Z")
INTEGER = re.compile(r"-?(0|[1-9][0-9]*)\Z")
PLAIN_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]*\Z")
# Plain words a YAML 1.1 or 1.2 reader types as a boolean or null, not as a string.
NON_STRING_WORDS = {
    "true", "false", "yes", "no", "on", "off", "y", "n", "null",
}
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
ZIP = re.compile(r"[0-9]{5}\Z")


class CountsError(Exception):
    pass


# --- the YAML subset ------------------------------------------------------------------


def strip_comment(line, where):
    """The line without a trailing comment; a # starts one outside a quoted string."""
    in_quote = False
    escaped = False
    for i, ch in enumerate(line):
        if in_quote:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_quote = False
        elif ch == '"':
            in_quote = True
        elif ch == "#" and (i == 0 or line[i - 1] == " "):
            return line[:i].rstrip()
    if in_quote:
        raise CountsError("%s: unterminated string" % where)
    return line.rstrip()


def parse_scalar(text, where):
    if text.startswith('"'):
        if len(text) < 2 or not text.endswith('"'):
            raise CountsError("%s: malformed string %s" % (where, text))
        body = text[1:-1]
        out = []
        i = 0
        while i < len(body):
            ch = body[i]
            if ch == "\\":
                if i + 1 >= len(body) or body[i + 1] not in '"\\':
                    raise CountsError("%s: only \\\" and \\\\ escapes are allowed: %s" % (where, text))
                out.append(body[i + 1])
                i += 2
            elif ch == '"':
                raise CountsError("%s: unescaped quote in %s" % (where, text))
            else:
                out.append(ch)
                i += 1
        return "".join(out)
    if INTEGER.match(text):
        return int(text)
    if PLAIN_WORD.match(text) and text.lower() not in NON_STRING_WORDS:
        return text
    raise CountsError("%s: unsupported value %r (quote strings with double quotes)" % (where, text))


def split_flow(body, where):
    """Split a flow mapping's body on the commas outside quoted strings."""
    parts, current, in_quote, escaped = [], [], False, False
    for ch in body:
        if in_quote:
            current.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_quote = False
        elif ch == '"':
            in_quote = True
            current.append(ch)
        elif ch == ",":
            parts.append("".join(current).strip())
            current = []
        elif ch in "{}[]":
            raise CountsError("%s: nested collections are not supported" % where)
        else:
            current.append(ch)
    parts.append("".join(current).strip())
    if parts == [""]:
        return []
    if any(p == "" for p in parts):
        raise CountsError("%s: empty entry in a flow mapping" % where)
    return parts


def split_entry(text, where):
    """Split 'key: value' (or 'key:') into the key and the value text."""
    m = re.match(r"([^\s:\"]+):(?:\s+(.*))?\Z", text)
    if not m:
        raise CountsError("%s: expected 'key: value', got %r" % (where, text))
    key, value = m.group(1), (m.group(2) or "").strip()
    if not KEY.match(key):
        raise CountsError("%s: unsupported key %r" % (where, key))
    return key, value


def put(mapping, key, value, where):
    if key in mapping:
        raise CountsError("%s: duplicate key %r" % (where, key))
    mapping[key] = value


def parse_flow(text, where):
    if not text.endswith("}"):
        raise CountsError("%s: a flow mapping must close on its own line" % where)
    out = {}
    for part in split_flow(text[1:-1], where):
        key, value = split_entry(part, where)
        if value == "":
            raise CountsError("%s: %r has no value" % (where, key))
        put(out, key, parse_scalar(value, where), where)
    return out


def parse(text, name="counts.yaml"):
    """Parse the YAML subset into nested dicts."""
    root = {}
    stack = [(-1, root)]  # (indent of the mapping's keys, mapping)
    pending = None  # (indent, mapping) opened by 'key:' and awaiting its first key
    for number, raw in enumerate(text.split("\n"), 1):
        where = "%s:%d" % (name, number)
        if "\t" in raw:
            raise CountsError("%s: tabs are not allowed" % where)
        if raw.rstrip() in ("---", "..."):
            raise CountsError("%s: document markers are not supported" % where)
        line = strip_comment(raw, where)
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        content = line.strip()
        if pending is not None:
            parent_indent, mapping = pending
            if indent <= parent_indent:
                raise CountsError("%s: a key with no value must be followed by an indented mapping" % where)
            stack.append((indent, mapping))
            pending = None
        while indent < stack[-1][0]:
            stack.pop()
        if indent != stack[-1][0] and not (stack[-1][0] == -1 and indent == 0):
            raise CountsError("%s: inconsistent indentation" % where)
        if stack[-1][0] == -1:
            stack[-1] = (0, root)
        mapping = stack[-1][1]
        key, value = split_entry(content, where)
        if value == "":
            child = {}
            put(mapping, key, child, where)
            pending = (indent, child)
        elif value.startswith("{"):
            put(mapping, key, parse_flow(value, where), where)
        elif value[0] in "[&*!|>'%@`-" and not INTEGER.match(value):
            raise CountsError("%s: unsupported YAML construct %r" % (where, value))
        else:
            put(mapping, key, parse_scalar(value, where), where)
    if pending is not None:
        raise CountsError("%s: the last key has no value" % name)
    return root


# --- the structure ----------------------------------------------------------------------


def is_count(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def check_keys(problems, where, mapping, expected):
    if not isinstance(mapping, dict):
        problems.append("%s: must be a mapping" % where)
        return False
    missing = [k for k in expected if k not in mapping]
    extra = sorted(k for k in mapping if k not in expected)
    if missing:
        problems.append("%s: missing %s" % (where, ", ".join(missing)))
    if extra:
        problems.append("%s: unexpected %s" % (where, ", ".join(extra)))
    return not missing and not extra


def validate(data):
    """Every problem with the parsed file, as a list of messages; empty when it is valid."""
    problems = []
    if not check_keys(problems, "top level", data, ("schema", "dataset", "runs", "reveal")):
        return problems
    if data["schema"] != 1 or isinstance(data["schema"], bool):
        problems.append("schema: must be 1, is %r" % (data["schema"],))
    if data["dataset"] != "jaffle":
        problems.append("dataset: must be jaffle, is %r" % (data["dataset"],))

    runs = data["runs"]
    if check_keys(problems, "runs", runs, RUNS):
        for run in RUNS:
            if not check_keys(problems, "runs.%s" % run, runs[run], LAYERS):
                continue
            complete = True
            for layer in LAYERS:
                where = "runs.%s.%s" % (run, layer)
                tables = SOURCE_TABLES if layer == "source" else BARE_TABLES
                if not check_keys(problems, where, runs[run][layer], tables):
                    complete = False
                    continue
                for table in tables:
                    if not is_count(runs[run][layer][table]):
                        complete = False
                        problems.append("%s.%s: must be a non-negative integer, is %r"
                                        % (where, table, runs[run][layer][table]))
            if not complete:
                continue
            # Rules that hold for every run, whatever the data: a full load stages the source
            # snapshot, and bridge_hst is a view over the whole history table.
            layers = runs[run]
            for source_name, table in zip(SOURCE_TABLES, BARE_TABLES):
                if layers["staging"][table] != layers["source"][source_name]:
                    problems.append("runs.%s.staging.%s: a full load stages the source, %d, not %d"
                                    % (run, table, layers["source"][source_name], layers["staging"][table]))
                if layers["bridge_hst"][table] != layers["history"][table]:
                    problems.append("runs.%s.bridge_hst.%s: is the whole history table, %d, not %d"
                                    % (run, table, layers["history"][table], layers["bridge_hst"][table]))
                if run == "run1":
                    for layer in LAYERS[1:]:
                        if layers[layer][table] != layers["source"][source_name]:
                            problems.append("runs.run1.%s.%s: run 1 starts from empty layers, so it "
                                            "equals the source, %d, not %d"
                                            % (layer, table, layers["source"][source_name], layers[layer][table]))

    reveal = data["reveal"]
    if check_keys(problems, "reveal", reveal,
                  ("object", "key_column", "customer_id", "name", "before", "after", "history")):
        if reveal["object"] != "customers":
            problems.append("reveal.object: must be customers, is %r" % (reveal["object"],))
        if reveal["key_column"] != "id":
            problems.append("reveal.key_column: must be id, is %r" % (reveal["key_column"],))
        if not isinstance(reveal["customer_id"], str) or not UUID.match(reveal["customer_id"]):
            problems.append("reveal.customer_id: must be a lowercase UUID string, is %r"
                            % (reveal["customer_id"],))
        if not isinstance(reveal["name"], str) or not reveal["name"].strip():
            problems.append("reveal.name: must be a non-empty string")
        addresses_ok = True
        for version in ("before", "after"):
            where = "reveal.%s" % version
            if not check_keys(problems, where, reveal[version], ADDRESS_FIELDS):
                addresses_ok = False
                continue
            for field in ADDRESS_FIELDS:
                value = reveal[version][field]
                if not isinstance(value, str) or not value.strip() or "\t" in value or "\n" in value:
                    addresses_ok = False
                    problems.append("%s.%s: must be a non-empty one-line string, is %r" % (where, field, value))
            if isinstance(reveal[version]["postal_code"], str) and not ZIP.match(reveal[version]["postal_code"]):
                addresses_ok = False
                problems.append("%s.postal_code: must be a quoted 5-digit ZIP code" % where)
        if addresses_ok:
            before, after = reveal["before"], reveal["after"]
            for field in ("street", "postal_code", "city"):
                if before[field] == after[field]:
                    problems.append("reveal: a move changes the %s, but before and after are both %r"
                                    % (field, before[field]))
            if before["country"] != after["country"]:
                problems.append("reveal: the move stays in the country, but %r becomes %r"
                                % (before["country"], after["country"]))
        history = reveal["history"]
        if check_keys(problems, "reveal.history", history,
                      ("run", "rows", "valid_to_column", "closed", "open")):
            if history["run"] != "run2":
                problems.append("reveal.history.run: must be run2, is %r" % (history["run"],))
            if history["rows"] != 2 or isinstance(history["rows"], bool):
                problems.append("reveal.history.rows: one move makes two versions, 2, not %r"
                                % (history["rows"],))
            if not isinstance(history["valid_to_column"], str) or not KEY.match(history["valid_to_column"]):
                problems.append("reveal.history.valid_to_column: must be a column name")
            if history["closed"] != "before":
                problems.append("reveal.history.closed: the closed version holds the before address")
            if history["open"] != "after":
                problems.append("reveal.history.open: the open version holds the after address")
    return problems


def load(path):
    try:
        with open(path, encoding="utf-8", newline="") as handle:
            text = handle.read()
    except OSError as err:
        raise CountsError("cannot read %s: %s" % (path, err.strerror))
    if "\r" in text:
        raise CountsError("%s: line endings must be LF" % path)
    data = parse(text, os.path.basename(path))
    problems = validate(data)
    if problems:
        raise CountsError("%s is not valid:\n  %s" % (path, "\n  ".join(problems)))
    return data


def lookup(data, path):
    node, rest = data, path
    while rest:
        if not isinstance(node, dict):
            raise CountsError("%s: %r is not a mapping" % (path, node))
        # The longest key that matches, so a key holding a dot is found whole.
        for key in sorted(node, key=len, reverse=True):
            if rest == key or rest.startswith(key + "."):
                node, rest = node[key], rest[len(key) + 1:]
                break
        else:
            raise CountsError("no value at %s" % path)
    if isinstance(node, dict):
        raise CountsError("%s is a mapping, not a value" % path)
    return node


def expect(data, run):
    if run not in RUNS:
        raise CountsError("unknown run %r; expected one of %s" % (run, ", ".join(RUNS)))
    lines = ["source\t%s\t%d" % (table, data["runs"][run]["source"][table])
             for table in sorted(SOURCE_TABLES)]
    reveal = data["reveal"]
    address = reveal["before" if run == "run1" else "after"]
    lines.append("\t".join(["mover", reveal["customer_id"], reveal["name"]]
                           + [address[f] for f in ADDRESS_FIELDS]))
    return "\n".join(lines) + "\n"


def main(argv):
    usage = "usage: counts.py validate [FILE] | get PATH [FILE] | expect RUN [FILE]"
    if not argv:
        print(usage, file=sys.stderr)
        return 2
    command, args = argv[0], argv[1:]
    takes = {"validate": 0, "get": 1, "expect": 1}
    if command not in takes or not takes[command] <= len(args) <= takes[command] + 1:
        print(usage, file=sys.stderr)
        return 2
    path = args[takes[command]] if len(args) > takes[command] else DEFAULT_FILE
    try:
        data = load(path)
        if command == "validate":
            print("ok: %s" % path)
        elif command == "get":
            sys.stdout.write("%s\n" % lookup(data, args[0]))
        else:
            sys.stdout.write(expect(data, args[0]))
    except CountsError as err:
        print("counts.py: %s" % err, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
