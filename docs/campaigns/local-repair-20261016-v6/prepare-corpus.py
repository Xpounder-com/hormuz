"""Prepare a fresh held-out inventory before the single v6 development episode."""

import argparse
import hashlib
import json
from pathlib import Path


DIRECTORY = Path(__file__).resolve().parent


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference-output", type=Path, required=True)
    arguments = parser.parse_args()
    if (DIRECTORY / "corpus.json").exists() or (DIRECTORY / "frozen-manifest.json").exists() or arguments.reference_output.exists():
        raise RuntimeError("Preparation is append-only; do not rewrite a prepared campaign")
    previous = json.loads((DIRECTORY.parent / "local-repair-20261016-v5/corpus.json").read_text())
    entries = [entry for entry in previous["entries"] if entry["partition"] == "development"]
    references = {
        "dev-clamp": "def clamp(value, lower, upper):\n    if lower > upper:\n        raise ValueError('inverted bounds')\n    return max(lower, min(value, upper))\n",
        "dev-strip-prefix": "def strip_prefix(text, prefix):\n    return text[len(prefix):] if prefix and text.startswith(prefix) else text\n",
        "dev-rotate-left": "def rotate_left(items, steps):\n    if not items:\n        return []\n    steps %= len(items)\n    return items[steps:] + items[:steps]\n",
        "dev-positive-counter": "class Counter:\n    def __init__(self):\n        self.value = 0\n    def increment(self, step=1):\n        if type(step) is not int or step <= 0:\n            raise ValueError('invalid step')\n        self.value += step\n        return self.value\n",
    }

    def add(case_id, family, goal, broken, assertions, reference):
        full_goal = "Fix the Python implementation in solution.py. " + goal + " Make the edit and check the behavior. /no_think"
        checks = "import runpy\nimport sys\n\ns = runpy.run_path(sys.argv[1])\n" + assertions
        entry = {"case_id": case_id, "partition": "held_out", "family": family, "goal": full_goal,
                 "files": {"solution.py": broken}, "checks": checks,
                 "task_sha256": hashlib.sha256(canonical({"goal": full_goal, "files": {"solution.py": broken}})).hexdigest(),
                 "checks_sha256": hashlib.sha256(checks.encode()).hexdigest()}
        entries.append(entry)
        references[case_id] = reference

    add("held-signum", "arithmetic",
        "Repair signum(number): for any integer return -1 when negative, 0 when zero, and 1 when positive.",
        "def signum(number):\n    return -1 if number < 0 else 1\n",
        "f = s['signum']\nassert f(0) == 0\nassert f(-1) == -1\nassert f(1) == 1\nassert f(-1000000) == -1\nassert f(1000000) == 1\n",
        "def signum(number):\n    return (number > 0) - (number < 0)\n")
    add("held-distance-to-interval", "arithmetic",
        "Repair distance_to_interval(value, lower, upper): return zero inside an inclusive integer interval and distance to the nearest endpoint outside. Raise ValueError for inverted bounds.",
        "def distance_to_interval(value, lower, upper):\n    return min(abs(value - lower), abs(value - upper))\n",
        "f = s['distance_to_interval']\nassert f(5, 2, 8) == 0\nassert f(-3, 2, 8) == 5\nassert f(11, 2, 8) == 3\nassert f(2, 2, 8) == 0\nassert f(-1, -1, -1) == 0\ntry:\n    f(1, 3, 2)\nexcept ValueError:\n    pass\nelse:\n    raise AssertionError('inverted interval accepted')\n",
        "def distance_to_interval(value, lower, upper):\n    if lower > upper:\n        raise ValueError('inverted interval')\n    return max(lower - value, value - upper, 0)\n")
    add("held-sum-odd", "arithmetic",
        "Repair sum_odd(values): sum only odd integer elements, including negatives; empty input returns zero. Do not mutate the input list.",
        "def sum_odd(values):\n    return sum(values)\n",
        "f = s['sum_odd']\na = [2, 3, 4, -1]\nassert f(a) == 2\nassert a == [2, 3, 4, -1]\nassert f([]) == 0\nassert f([0, -2, 8]) == 0\nassert f([-5, -3, 7]) == -1\nassert f([1, 1, 1]) == 3\n",
        "def sum_odd(values):\n    return sum(value for value in values if value % 2)\n")
    add("held-remove-suffix-once", "text",
        "Repair remove_suffix_once(text, suffix): remove exactly one exact suffix only at the end. Empty suffix leaves the original string unchanged. Preserve Unicode and earlier occurrences.",
        "def remove_suffix_once(text, suffix):\n    return text.replace(suffix, '')\n",
        "f = s['remove_suffix_once']\nassert f('abcabc', 'abc') == 'abc'\nassert f('abcx', 'abc') == 'abcx'\nassert f('abc', '') == 'abc'\nassert f('', 'x') == ''\nassert f('αβα', 'α') == 'αβ'\nassert f('same', 'same') == ''\n",
        "def remove_suffix_once(text, suffix):\n    return text[:-len(suffix)] if suffix and text.endswith(suffix) else text\n")
    add("held-count-nonoverlap", "text",
        "Repair count_nonoverlap(text, needle): count nonoverlapping occurrences of a nonempty exact substring. Raise ValueError for empty needle. Preserve Unicode; return zero if no occurrence.",
        "def count_nonoverlap(text, needle):\n    return len(text.split(needle))\n",
        "f = s['count_nonoverlap']\nassert f('aaaaa', 'aa') == 2\nassert f('abc', 'x') == 0\nassert f('', 'x') == 0\nassert f('αβαβ', 'αβ') == 2\nassert f('aaaa', 'a') == 4\ntry:\n    f('abc', '')\nexcept ValueError:\n    pass\nelse:\n    raise AssertionError('empty needle accepted')\n",
        "def count_nonoverlap(text, needle):\n    if not needle:\n        raise ValueError('empty needle')\n    return text.count(needle)\n")
    add("held-indent-lines", "text",
        "Repair indent_lines(text, prefix): prepend prefix to every segment separated by ASCII newline, including empty segments and a trailing empty segment. Preserve newline bytes and Unicode. Empty text is one empty segment.",
        "def indent_lines(text, prefix):\n    return prefix + text\n",
        "f = s['indent_lines']\nassert f('a\\nb', '>') == '>a\\n>b'\nassert f('a\\n', '>') == '>a\\n>'\nassert f('', '>') == '>'\nassert f('\\n', '>') == '>\\n>'\nassert f('α\\nβ', '·') == '·α\\n·β'\nassert f('x\\ny', '') == 'x\\ny'\n",
        "def indent_lines(text, prefix):\n    return '\\n'.join(prefix + segment for segment in text.split('\\n'))\n")
    add("held-stable-unique", "collections",
        "Repair stable_unique(items): return a new list keeping the first equality-matching occurrence of each item in original order. Support unhashable values and preserve input.",
        "def stable_unique(items):\n    return list(set(items))\n",
        "f = s['stable_unique']\na = [2, 1, 2, 3, 1]\nassert f(a) == [2, 1, 3]\nassert a == [2, 1, 2, 3, 1]\nassert f([[1], [2], [1]]) == [[1], [2]]\nassert f([]) == []\nassert f([False, 0, True, 1]) == [False, True]\nassert f(a) is not a\n",
        "def stable_unique(items):\n    result = []\n    for item in items:\n        if item not in result:\n            result.append(item)\n    return result\n")
    add("held-zip-strict", "collections",
        "Repair zip_strict(left, right): return a new list of corresponding pairs. Unequal list lengths raise ValueError. Empty equal lists are valid. Preserve both lists and support unhashable entries.",
        "def zip_strict(left, right):\n    return list(zip(left, right))\n",
        "f = s['zip_strict']\nfor a, b in [([1], []), ([], [1]), ([1, 2], [3])]:\n    try:\n        f(a, b)\n    except ValueError:\n        pass\n    else:\n        raise AssertionError('unequal lengths accepted')\na, b = [1, 2], ['a', 'b']\nassert f(a, b) == [(1, 'a'), (2, 'b')]\nassert a == [1, 2] and b == ['a', 'b']\nassert f([], []) == []\nassert f([[1]], [[2]]) == [([1], [2])]\n",
        "def zip_strict(left, right):\n    if len(left) != len(right):\n        raise ValueError('unequal lengths')\n    return list(zip(left, right))\n")
    add("held-take-last", "collections",
        "Repair take_last(items, count): return a new list containing at most count last elements in original order. Count must be a strict nonnegative integer; bool is invalid. Zero returns an empty list. Preserve input.",
        "def take_last(items, count):\n    return items[-count:]\n",
        "f = s['take_last']\na = [1, 2, 3]\nassert f(a, 0) == []\nassert f(a, 2) == [2, 3]\nassert f(a, 8) == a and f(a, 8) is not a\nassert a == [1, 2, 3]\nassert f([], 3) == []\nfor bad in [-1, True, False, 1.5, '2', None]:\n    try:\n        f(a, bad)\n    except ValueError:\n        pass\n    else:\n        raise AssertionError('invalid count accepted')\n",
        "def take_last(items, count):\n    if type(count) is not int or count < 0:\n        raise ValueError('invalid count')\n    return items[-count:] if count else []\n")
    add("held-volume-level", "validation_state",
        "Repair Volume.set_level(level): accept only strict integers from zero through ten inclusive, excluding bool. Invalid values raise ValueError before mutation. Return the accepted level; a new instance starts at five.",
        "class Volume:\n    def __init__(self):\n        self.level = 5\n    def set_level(self, level):\n        self.level = level\n        return self.level\n",
        "v = s['Volume']()\nassert v.level == 5\nfor bad in [-1, 11, True, False, 2.5, '3', None]:\n    try:\n        v.set_level(bad)\n    except ValueError:\n        pass\n    else:\n        raise AssertionError('invalid level accepted')\n    assert v.level == 5\nassert v.set_level(0) == 0 and v.level == 0\nassert v.set_level(10) == 10 and v.level == 10\n",
        "class Volume:\n    def __init__(self):\n        self.level = 5\n    def set_level(self, level):\n        if type(level) is not int or not 0 <= level <= 10:\n            raise ValueError('invalid level')\n        self.level = level\n        return self.level\n")
    add("held-toggle-strict-bool", "validation_state",
        "Repair Toggle.set_enabled(value): accept only bool, set enabled and return the new bool. Reject every other type with ValueError before mutation. New instances start disabled.",
        "class Toggle:\n    def __init__(self):\n        self.enabled = False\n    def set_enabled(self, value):\n        self.enabled = bool(value)\n        return self.enabled\n",
        "v = s['Toggle']()\nassert v.enabled is False\nfor bad in [0, 1, '', 'yes', [], None]:\n    try:\n        v.set_enabled(bad)\n    except ValueError:\n        pass\n    else:\n        raise AssertionError('non-bool accepted')\n    assert v.enabled is False\nassert v.set_enabled(True) is True and v.enabled is True\nassert v.set_enabled(False) is False and v.enabled is False\n",
        "class Toggle:\n    def __init__(self):\n        self.enabled = False\n    def set_enabled(self, value):\n        if type(value) is not bool:\n            raise ValueError('non-bool')\n        self.enabled = value\n        return self.enabled\n")
    add("held-bounded-queue", "validation_state",
        "Repair BoundedQueue.push(item): append and return the new length only if capacity is available. A full queue raises OverflowError before mutation. Capacity is a nonnegative constructor integer. Preserve falsy items and existing order.",
        "class BoundedQueue:\n    def __init__(self, capacity):\n        self.capacity = capacity\n        self.items = []\n    def push(self, item):\n        self.items.append(item)\n        if len(self.items) > self.capacity:\n            raise OverflowError('full')\n        return len(self.items)\n",
        "q = s['BoundedQueue'](1)\nassert q.push(False) == 1 and q.items == [False]\ntry:\n    q.push('extra')\nexcept OverflowError:\n    pass\nelse:\n    raise AssertionError('full queue accepted')\nassert q.items == [False]\nzero = s['BoundedQueue'](0)\ntry:\n    zero.push(None)\nexcept OverflowError:\n    pass\nelse:\n    raise AssertionError('zero capacity accepted')\nassert zero.items == []\nq = s['BoundedQueue'](2)\nassert q.push(None) == 1\nassert q.push(0) == 2\nassert q.items == [None, 0]\n",
        "class BoundedQueue:\n    def __init__(self, capacity):\n        self.capacity = capacity\n        self.items = []\n    def push(self, item):\n        if len(self.items) >= self.capacity:\n            raise OverflowError('full')\n        self.items.append(item)\n        return len(self.items)\n")
    prior = {entry["task_sha256"] for entry in previous["entries"]}
    held = [entry for entry in entries if entry["partition"] == "held_out"]
    if len(entries) != 16 or len(held) != 12 or any(entry["task_sha256"] in prior for entry in held):
        raise RuntimeError("Fresh sixteen-case inventory invariant failed")
    corpus = {"document_version": 4, "campaign_id": "local-repair-20261016-v6", "criteria_version": "local-repair.v1",
              "purpose": "explicit shell-patch instruction development treatment", "native_episodes_performed": 0,
              "entries": entries}
    (DIRECTORY / "corpus.json").write_text(json.dumps(corpus, indent=2, ensure_ascii=False) + "\n")
    arguments.reference_output.write_text(json.dumps(references, indent=2, ensure_ascii=False) + "\n")
    arguments.reference_output.chmod(0o600)
    print(json.dumps({"cases": len(entries), "fresh_held_out": len(held), "native_model_inferences": 0}))


if __name__ == "__main__":
    main()
