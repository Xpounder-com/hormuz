"""Validate independent checker sensitivity without native model inference."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile


DIRECTORY = Path(__file__).resolve().parent
WRAPPER = ("import runpy,sys\noracle=sys.argv.pop(1)\nsys.argv[0]=oracle\ntry:\n"
           " runpy.run_path(oracle,run_name='__main__')\nexcept AssertionError:\n sys.exit(1)\n"
           "except BaseException:\n sys.exit(2)\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--references", type=Path, required=True)
    arguments = parser.parse_args()
    target = DIRECTORY / "preparation-evidence.json"
    if target.exists() or (DIRECTORY / "frozen-manifest.json").exists():
        raise RuntimeError("Preserve existing preparation evidence")
    corpus = json.loads((DIRECTORY / "corpus.json").read_text())
    references = json.loads(arguments.references.read_text())
    outcomes = []
    with tempfile.TemporaryDirectory(prefix="hormuz-537-v6-checkers-") as temporary:
        root = Path(temporary)
        for case in corpus["entries"]:
            oracle = root / "oracle.py"
            oracle.chmod(0o600) if oracle.exists() else None
            oracle.write_text(case["checks"])
            oracle.chmod(0o400)
            codes = {}
            for label, code in (("baseline", case["files"]["solution.py"]), ("reference", references[case["case_id"]])):
                implementation = root / "implementation.py"
                implementation.write_text(code)
                result = subprocess.run(["/usr/bin/sandbox-exec", "-p",
                    "(version 1)(allow default)(deny network*)(deny file-write*)", sys.executable,
                    "-I", "-B", "-c", WRAPPER, str(oracle), str(implementation)],
                    cwd=root, env={"PATH": "/usr/bin:/bin"}, stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2)
                codes[label + "_exit_code"] = result.returncode
            outcomes.append({"case_id": case["case_id"], **codes,
                "checks_unchanged": hashlib.sha256(oracle.read_bytes()).hexdigest() == case["checks_sha256"]})
    passed = len(outcomes) == 16 and all(row["baseline_exit_code"] == 1 and row["reference_exit_code"] == 0
                                        and row["checks_unchanged"] for row in outcomes)
    evidence = {"document_version": 1, "campaign_id": corpus["campaign_id"], "native_model_inferences": 0,
                "reference_implementations_are_model_evidence": False, "cases": outcomes, "passed": passed}
    target.write_text(json.dumps(evidence, indent=2) + "\n")
    print(json.dumps({"cases": len(outcomes), "native_model_inferences": 0, "passed": passed}))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
