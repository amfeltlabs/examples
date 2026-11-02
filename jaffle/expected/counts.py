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
                                mover's row, then, after run1 only, the deleted
                                customer's row; tab-separated

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
# The job block: the job's settings and, per object, the settings select.objects holds.
JOB_KEYS = ("layer", "schemaPrefix", "objects")
OBJECT_KEYS = ("tableType", "historyMode")
OPTIONAL_OBJECT_KEYS = ("eventTimeCol",)
TABLE_TYPES = ("master", "transaction", "reference")
# The history modes the layer rules below are written for. SCD2 closes a version on an
# update or a delete and opens one on an insert or an update; append inserts new keys and
# closes nothing.
HISTORY_MODES = ("scd2", "append")

KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*\Z")
INTEGER = re.compile(r"-?(0|[1-9][0-9]*)\Z")
PLAIN_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]*\Z")
# Plain words a YAML 1.1 or 1.2 reader types as a boolean or null, not as a string.
NON_STRING_WORDS = {
    "true", "false", "yes", "no", "on", "off", "y", "n", "null",
}
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
# A Dutch postal code: four digits with no leading zero, a space, two letters.
POSTAL_CODE = re.compile(r"[1-9][0-9]{3} [A-Z]{2}\Z")
MOVE_CITY = "Rotterdam"
MOVE_POSTAL_CODE = re.compile(r"30[0-9]{2} [A-Z]{2}\Z")


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


def check_address(problems, where, address):
    """True when address holds the four fields, each a one-line string, a Dutch postal code."""
    if not check_keys(problems, where, address, ADDRESS_FIELDS):
        return False
    ok = True
    for field in ADDRESS_FIELDS:
        value = address[field]
        if not isinstance(value, str) or not value.strip() or "\t" in value or "\n" in value:
            ok = False
            problems.append("%s.%s: must be a non-empty one-line string, is %r" % (where, field, value))
    if isinstance(address["postal_code"], str) and not POSTAL_CODE.match(address["postal_code"]):
        ok = False
        problems.append("%s.postal_code: must be a Dutch postal code, as 3521 HN" % where)
    return ok


def check_customer(problems, where, block):
    if not isinstance(block["customer_id"], str) or not UUID.match(block["customer_id"]):
        problems.append("%s.customer_id: must be a lowercase UUID string, is %r" % (where, block["customer_id"]))
    if not isinstance(block["name"], str) or not block["name"].strip():
        problems.append("%s.name: must be a non-empty string" % where)


def check_history(problems, where, history, rows, closed, open_):
    """A history expectation: rows versions after run 2, the closed and the open one named
    by the address they hold (none: there is no such version)."""
    if not check_keys(problems, where, history, ("run", "rows", "valid_to_column", "closed", "open")):
        return
    if history["run"] != "run2":
        problems.append("%s.run: must be run2, is %r" % (where, history["run"]))
    if not is_count(history["rows"]) or history["rows"] != rows:
        problems.append("%s.rows: must be %d, is %r" % (where, rows, history["rows"]))
    if not isinstance(history["valid_to_column"], str) or not KEY.match(history["valid_to_column"]):
        problems.append("%s.valid_to_column: must be a column name" % where)
    if history["closed"] != closed:
        problems.append("%s.closed: must be %s, is %r" % (where, closed, history["closed"]))
    if history["open"] != open_:
        problems.append("%s.open: must be %s, is %r" % (where, open_, history["open"]))


def check_bridge_act(problems, where, bridge, rows, holds):
    """A bridge_act expectation: rows active rows after run 2, holding the named address."""
    if not check_keys(problems, where, bridge, ("run", "rows", "holds")):
        return
    if bridge["run"] != "run2":
        problems.append("%s.run: must be run2, is %r" % (where, bridge["run"]))
    if not is_count(bridge["rows"]) or bridge["rows"] != rows:
        problems.append("%s.rows: must be %d, is %r" % (where, rows, bridge["rows"]))
    if bridge["holds"] != holds:
        problems.append("%s.holds: must be %s, is %r" % (where, holds, bridge["holds"]))


def check_job(problems, job):
    """The job block; returns each object's history mode when every object states a valid
    one, else None."""
    if not check_keys(problems, "job", job, JOB_KEYS):
        return None
    if job["layer"] != "bridge":
        problems.append("job.layer: the counts cover staging, history and bridge, so it must be bridge, is %r"
                        % (job["layer"],))
    if not isinstance(job["schemaPrefix"], str) or not KEY.match(job["schemaPrefix"]):
        problems.append("job.schemaPrefix: must be a schema name, is %r" % (job["schemaPrefix"],))
    if not check_keys(problems, "job.objects", job["objects"], BARE_TABLES):
        return None
    modes = {}
    for table in BARE_TABLES:
        where = "job.objects.%s" % table
        obj = job["objects"][table]
        if not isinstance(obj, dict):
            problems.append("%s: must be a mapping" % where)
            continue
        missing = [k for k in OBJECT_KEYS if k not in obj]
        extra = sorted(k for k in obj if k not in OBJECT_KEYS + OPTIONAL_OBJECT_KEYS)
        if missing:
            problems.append("%s: missing %s" % (where, ", ".join(missing)))
        if extra:
            problems.append("%s: unexpected %s" % (where, ", ".join(extra)))
        if obj.get("tableType") not in TABLE_TYPES:
            problems.append("%s.tableType: must be one of %s, is %r"
                            % (where, ", ".join(TABLE_TYPES), obj.get("tableType")))
        if obj.get("historyMode") not in HISTORY_MODES:
            problems.append("%s.historyMode: must be one of %s, is %r"
                            % (where, ", ".join(HISTORY_MODES), obj.get("historyMode")))
        else:
            modes[table] = obj["historyMode"]
        if "eventTimeCol" in obj and (not isinstance(obj["eventTimeCol"], str)
                                      or not KEY.match(obj["eventTimeCol"])):
            problems.append("%s.eventTimeCol: must be a column name, is %r" % (where, obj["eventTimeCol"]))
    return modes if len(modes) == len(BARE_TABLES) else None


def check_runs(problems, runs, history_mode):
    complete = []
    for run in RUNS:
        if not check_keys(problems, "runs.%s" % run, runs[run], LAYERS):
            continue
        ok = True
        for layer in LAYERS:
            where = "runs.%s.%s" % (run, layer)
            tables = SOURCE_TABLES if layer == "source" else BARE_TABLES
            if not check_keys(problems, where, runs[run][layer], tables):
                ok = False
                continue
            for table in tables:
                if not is_count(runs[run][layer][table]):
                    ok = False
                    problems.append("%s.%s: must be a non-negative integer, is %r"
                                    % (where, table, runs[run][layer][table]))
        if ok:
            complete.append(run)

    # Rules that hold for every run, whatever the data, by history mode. They cannot catch a
    # history count that is wrong together with the views over it; that needs the run's
    # inserts, updates and deletes, and the engine-capture scenario asserts it.
    for run in complete:
        layers = runs[run]

        def mismatch(layer, table, expected, rule):
            if layers[layer][table] != expected:
                problems.append("runs.%s.%s.%s: %s, %d, not %d"
                                % (run, layer, table, rule, expected, layers[layer][table]))

        for source_name, table in zip(SOURCE_TABLES, BARE_TABLES):
            source = layers["source"][source_name]
            history = layers["history"][table]
            mismatch("staging", table, source, "a full load stages the source")
            mismatch("bridge_hst", table, history, "is the whole history table")
            # The bridge_act rule follows the job block's history mode; with no valid mode
            # there, check_job has reported it and the rule is skipped.
            if history_mode is not None and history_mode[table] == "scd2":
                mismatch("bridge_act", table, layers["staging"][table],
                         "scd2 keeps one open version per source row, as staging holds")
            elif history_mode is not None:
                mismatch("bridge_act", table, history, "append closes nothing, so it is all of history")
            if run == "run1":
                for layer in LAYERS[1:]:
                    mismatch(layer, table, source, "run 1 starts from empty layers, so it equals the source")
    if complete == list(RUNS):
        for table in BARE_TABLES:
            if runs["run2"]["history"][table] < runs["run1"]["history"][table]:
                problems.append("runs.run2.history.%s: history never loses a row, %d is less than run 1's %d"
                                % (table, runs["run2"]["history"][table], runs["run1"]["history"][table]))


def check_reveal(problems, reveal):
    if not check_keys(problems, "reveal", reveal,
                      ("object", "key_column", "customer_id", "name", "before", "after",
                       "history", "bridge_act", "deleted")):
        return
    if reveal["object"] != "customers":
        problems.append("reveal.object: must be customers, is %r" % (reveal["object"],))
    if reveal["key_column"] != "id":
        problems.append("reveal.key_column: must be id, is %r" % (reveal["key_column"],))

    # The customer who moves.
    check_customer(problems, "reveal", reveal)
    before_ok = check_address(problems, "reveal.before", reveal["before"])
    after_ok = check_address(problems, "reveal.after", reveal["after"])
    if after_ok:
        after = reveal["after"]
        if after["city"] != MOVE_CITY:
            problems.append("reveal.after.city: the move is to %s, not %r" % (MOVE_CITY, after["city"]))
        if not MOVE_POSTAL_CODE.match(after["postal_code"]):
            problems.append("reveal.after.postal_code: a Rotterdam postal code here is 30xx, not %r"
                            % (after["postal_code"],))
    if before_ok and after_ok:
        before, after = reveal["before"], reveal["after"]
        for field in ("street", "postal_code", "city"):
            if before[field] == after[field]:
                problems.append("reveal: a move changes the %s, but before and after are both %r"
                                % (field, before[field]))
        if before["country"] != after["country"]:
            problems.append("reveal: the move stays in the country, but %r becomes %r"
                            % (before["country"], after["country"]))
    check_history(problems, "reveal.history", reveal["history"], 2, "before", "after")
    check_bridge_act(problems, "reveal.bridge_act", reveal["bridge_act"], 1, "after")

    # The customer run 2 deletes.
    deleted = reveal["deleted"]
    if not check_keys(problems, "reveal.deleted", deleted,
                      ("customer_id", "name", "before", "history", "bridge_act")):
        return
    check_customer(problems, "reveal.deleted", deleted)
    if deleted["customer_id"] == reveal["customer_id"]:
        problems.append("reveal.deleted.customer_id: must not be the customer who moves")
    check_address(problems, "reveal.deleted.before", deleted["before"])
    check_history(problems, "reveal.deleted.history", deleted["history"], 1, "before", "none")
    check_bridge_act(problems, "reveal.deleted.bridge_act", deleted["bridge_act"], 0, "none")
    if (isinstance(reveal["history"], dict) and isinstance(deleted["history"], dict)
            and reveal["history"].get("valid_to_column") != deleted["history"].get("valid_to_column")):
        problems.append("reveal.deleted.history.valid_to_column: must equal reveal.history.valid_to_column")


def validate(data):
    """Every problem with the parsed file, as a list of messages; empty when it is valid."""
    problems = []
    if not check_keys(problems, "top level", data, ("schema", "dataset", "job", "runs", "reveal")):
        return problems
    if not is_count(data["schema"]) or data["schema"] != 1:
        problems.append("schema: must be 1, is %r" % (data["schema"],))
    if data["dataset"] != "jaffle":
        problems.append("dataset: must be jaffle, is %r" % (data["dataset"],))
    history_mode = check_job(problems, data["job"])
    if check_keys(problems, "runs", data["runs"], RUNS):
        check_runs(problems, data["runs"], history_mode)
    check_reveal(problems, data["reveal"])
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
    # The deleted customer is in the source after run 1 and gone after run 2.
    deleted = reveal["deleted"]
    if run == "run1":
        lines.append("\t".join(["deleted", deleted["customer_id"], deleted["name"]]
                               + [deleted["before"][f] for f in ADDRESS_FIELDS]))
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
