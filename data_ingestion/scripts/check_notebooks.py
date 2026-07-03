# check_notebooks.py
# ===================
# Notebooks are JSON files. Editing them by hand in a text editor easily breaks
# them - the classic mistake is typing a "quoted phrase" inside a cell, which
# ends the JSON string early and corrupts the whole file (this is exactly what
# happened to notebook 04 once).
#
# This script:
#   1. VALIDATES every .ipynb in notebooks/ and reports the exact line/column
#      of any JSON error.
#   2. With --fix : repairs the common damage automatically -
#      unescaped double-quotes inside a cell's source lines get escaped.
#      A backup (<name>.ipynb.bak) is saved before anything is changed.
#   3. With --strip-outputs : clears all cell outputs (keeps the code). Outputs
#      are where interrupted saves and giant tracebacks usually live, and
#      stripping them also makes the files small and git-friendly.
#
# Run from the project root:
#   python3 data_ingestion/scripts/check_notebooks.py            # just check
#   python3 data_ingestion/scripts/check_notebooks.py --fix      # check + repair
#   python3 data_ingestion/scripts/check_notebooks.py --strip-outputs
#
# Tip: the safest way to avoid corruption is to edit notebooks in Jupyter or
# VS Code (they write valid JSON), never in a plain text editor.

import json
import os
import re
import shutil
import sys

THIS_DIR     = os.path.dirname(os.path.abspath(__file__))      # .../data_ingestion/scripts
PROJECT_ROOT = os.path.dirname(os.path.dirname(THIS_DIR))
NB_DIR       = os.path.join(PROJECT_ROOT, "notebooks")

# A JSON string line inside a notebook looks like:   "some text",
# If the text itself contains a " that is not escaped, the line is broken.
# This regex grabs everything between the first and last quote of the line.
LINE_SHAPE = re.compile(r'^(\s*")(.*)("(,?)\s*)$')

# Structural lines like  "cell_type": "markdown",  must NOT be touched -
# they are key/value pairs, not cell text. This recognises them.
KEY_VALUE_LINE = re.compile(r'^\s*"[A-Za-z0-9_]+"\s*:')


def escape_inner_quotes(line):
    """Escape any " inside the line's string body that isn't already \"."""
    if KEY_VALUE_LINE.match(line):
        return line
    m = LINE_SHAPE.match(line)
    if not m:
        return line
    head, body, tail = m.group(1), m.group(2), m.group(3) + ("" if line.rstrip().endswith('"') else "")
    # Replace " that is NOT preceded by a backslash.
    fixed_body = re.sub(r'(?<!\\)"', r'\\"', body)
    if fixed_body == body:
        return line
    # Rebuild the line exactly, keeping the trailing comma if there was one.
    trailing_comma = "," if line.rstrip().endswith('",') else ""
    return head + fixed_body + '"' + trailing_comma + "\n"


def try_fix_file(path):
    """Escape bad quotes line by line, then re-check. Returns True if fixed."""
    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    fixed_lines = [escape_inner_quotes(line) for line in lines]
    fixed_text = "".join(fixed_lines)
    try:
        json.loads(fixed_text)
    except json.JSONDecodeError:
        return False  # damage is something else - needs a human
    shutil.copy2(path, path + ".bak")
    with open(path, "w", encoding="utf-8") as f:
        f.write(fixed_text)
    return True


def strip_outputs(path):
    """Clear every cell's outputs and execution counts (keeps all code)."""
    with open(path, "r", encoding="utf-8") as f:
        nb = json.load(f)
    changed = False
    for cell in nb.get("cells", []):
        if cell.get("cell_type") == "code":
            if cell.get("outputs") or cell.get("execution_count") is not None:
                cell["outputs"] = []
                cell["execution_count"] = None
                changed = True
    if changed:
        shutil.copy2(path, path + ".bak")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(nb, f, indent=1)
    return changed


def main():
    do_fix = "--fix" in sys.argv
    do_strip = "--strip-outputs" in sys.argv

    notebooks = sorted(f for f in os.listdir(NB_DIR) if f.endswith(".ipynb"))
    print("Checking", len(notebooks), "notebook(s) in", NB_DIR)
    broken = 0

    for name in notebooks:
        path = os.path.join(NB_DIR, name)
        try:
            with open(path, "r", encoding="utf-8") as f:
                json.load(f)
            status = "OK"
            if do_strip:
                if strip_outputs(path):
                    status = "OK (outputs stripped, backup saved as .bak)"
        except json.JSONDecodeError as e:
            if do_fix and try_fix_file(path):
                status = "FIXED (bad quotes escaped, backup saved as .bak)"
            else:
                status = "BROKEN - %s (line %d, column %d)%s" % (
                    e.msg, e.lineno, e.colno,
                    "" if do_fix else "  -> try --fix",
                )
                broken += 1
        print("  %-38s %s" % (name, status))

    if broken:
        print("\n%d notebook(s) still broken - open in Jupyter or fix by hand." % broken)
        sys.exit(1)
    print("\nAll notebooks are valid.")


if __name__ == "__main__":
    main()
