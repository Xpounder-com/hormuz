"""Offline native tool controls through the approved connected-client workflow.

The external runtime is a deterministic stand-in. A supplied correct patch is
only a transport/client control, never model-quality or qualification evidence.
"""

import difflib
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tarfile
import tempfile


REPOSITORY = Path(__file__).resolve().parents[3]
EVIDENCE = REPOSITORY / "docs/campaigns/local-repair-20261016-v5"
OUTPUT = Path(__file__).resolve().parent
PYTHON = Path("/Users/mehrdadz/Downloads/memoWiki/hormuz/.venv/bin/python")

ENGINE = r'''#!PYTHON
import http.server,json,pathlib,signal,sys
root=pathlib.Path(__file__).parent
port=int(sys.argv[sys.argv.index('--port')+1])
count=0
class Handler(http.server.BaseHTTPRequestHandler):
    timeout=3
    def log_message(self,*_): pass
    def do_GET(self):
        data=b'{"status":"ok"}'
        self.send_response(200);self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
    def do_POST(self):
        global count
        size=int(self.headers.get('Content-Length','0'))
        if not 0<size<=65536 or count>=2:
            self.send_error(400);return
        body=json.loads(self.rfile.read(size))
        (root/('request-'+str(count)+'.json')).write_text(json.dumps(body))
        frames=json.loads((root/'frames.json').read_text())[count]
        count+=1
        chunks=[('data: '+json.dumps(frame)+'\n\n').encode() for frame in frames]
        self.send_response(200);self.send_header('Content-Type','text/event-stream')
        self.send_header('Content-Length',str(sum(map(len,chunks))));self.end_headers()
        try:
            for chunk in chunks:
                self.wfile.write(chunk);self.wfile.flush()
        except (BrokenPipeError,ConnectionResetError): pass
server=http.server.HTTPServer(('127.0.0.1',port),Handler)
server.timeout=0.2
stopping=False
def stop(*_):
    global stopping
    stopping=True
signal.signal(signal.SIGTERM,stop)
print('I srv  llama_server: listening on http://127.0.0.1:'+str(port),file=sys.stderr,flush=True)
try:
    while not stopping: server.handle_request()
finally:
    server.server_close()
'''


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def sha(value):
    return hashlib.sha256(value).hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def control(label, command):
    spec = importlib.util.spec_from_file_location("retained_runner", EVIDENCE / "development-runner.py")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    with tempfile.TemporaryDirectory(prefix="hormuz-537-routed-control-") as temporary:
        root = Path(temporary)
        root.chmod(0o700)
        runner.ROOT = root
        runner.PYTHON = PYTHON
        for part in ("home", "home/.codex", "project", "tmp", "installed", "engine"):
            (root / part).mkdir(mode=0o700)
        for path in (root / "home/.codex/config.toml", root / "original-config.toml"):
            path.write_text('personality = "none"\n')
        case = json.loads((EVIDENCE / "corpus.json").read_text())["entries"][0]
        (root / "project/solution.py").write_text(case["files"]["solution.py"])
        (root / "prompt.txt").write_text(case["goal"])
        (root / "oracle.py").write_text(case["checks"])
        (root / "oracle.py").chmod(0o400)
        engine = root / "engine/llama-server"
        engine.write_text(ENGINE.replace("#!PYTHON", "#!" + str(PYTHON), 1))
        engine.chmod(0o700)
        model = root / "synthetic.gguf"
        model.write_bytes(b"GGUF synthetic external transport fixture, not a model")
        archive = root / "runtime.tar.gz"
        with tarfile.open(archive, "w:gz") as bundle:
            bundle.add(engine, arcname="llama-server")
        pins = json.loads((EVIDENCE / "pins.json").read_text())
        for key, path in (("runtime_archive", archive), ("runtime_executable", engine), ("model", model)):
            pins[key] = {"path": str(path), "bytes": path.stat().st_size, "sha256": sha(path.read_bytes())}
        save(root / "pins.json", pins)
        profile = json.loads((EVIDENCE / "setup-profile.json").read_text())
        profile.update(qualification_id="synthetic-native-patch-control-v1",
                       runtime_sha256=pins["runtime_archive"]["sha256"], model_sha256=pins["model"]["sha256"])
        save(root / "setup-profile.json", profile)
        alias = profile["model"]
        arguments = json.dumps({"cmd": command, "max_output_tokens": 512}, separators=(",", ":"))
        call = {"id": "fc_patch_control", "type": "function_call", "status": "completed",
                "call_id": "call_patch_control", "name": "exec_command", "arguments": arguments}
        usage = {"input_tokens": 8, "output_tokens": 8, "total_tokens": 16}
        first = {"id": "resp_patch_control", "object": "response", "status": "completed", "model": alias,
                 "output": [call], "usage": usage}
        message = {"id": "msg_patch_control", "type": "message", "status": "completed", "role": "assistant",
                   "content": [{"type": "output_text", "text": "Synthetic tool control completed.", "annotations": []}]}
        second = {**first, "id": "resp_patch_control_done", "output": [message]}
        fragments = [arguments[:13], arguments[13:36], arguments[36:]]
        frames = [[{"type": "response.created", "response": {**first, "status": "in_progress", "output": []}},
                   {"type": "response.output_item.added", "output_index": 0,
                    "item": {**call, "status": "in_progress", "arguments": ""}},
                   *({"type": "response.function_call_arguments.delta", "item_id": call["id"], "output_index": 0,
                      "delta": fragment} for fragment in fragments),
                   {"type": "response.function_call_arguments.done", "item_id": call["id"], "output_index": 0,
                    "arguments": arguments},
                   {"type": "response.output_item.done", "output_index": 0, "item": call},
                   {"type": "response.completed", "response": first}],
                  [{"type": "response.output_item.done", "output_index": 0, "item": message},
                   {"type": "response.completed", "response": second}]]
        save(root / "engine/frames.json", frames)
        # Import only the frozen source distribution, independent of ambient sites.
        for path in (REPOSITORY / "hormuz").rglob("*"):
            relative = path.relative_to(REPOSITORY)
            if "__pycache__" in relative.parts or path.suffix == ".pyc":
                continue
            target = root / "installed" / relative
            if path.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            elif path.is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
        runner.preflight()
        capture = json.loads((root / "captured-request.json").read_text())
        subject = {"client_version": profile["client_version"], "system": profile["system"], "machine": profile["machine"],
                   "runtime_profile": "managed-llama.v1", "runtime_sha256": pins["runtime_archive"]["sha256"],
                   "model_revision": "sha256:" + pins["model"]["sha256"], "upstream_model": profile["model"],
                   "protocol": "openai", "capabilities": profile["capabilities"],
                   "max_request_bytes": profile["max_request_bytes"], "max_output_tokens": profile["max_output_tokens"],
                   "upstream_timeout_seconds": profile["upstream_timeout_seconds"],
                   "request_profile_sha256": sha(canonical(runner.request_profile(capture)))}
        plan = {"schema_id": "hormuz.coding-probe-plan", "schema_version": 2, "campaign_id": "synthetic-control-" + label,
                "plan_sha256": "e" * 64, "case_id": "synthetic-patch-control", "task_sha256": case["task_sha256"],
                "checks_sha256": case["checks_sha256"], "model": alias, "subject": subject,
                "first_input_sha256": sha(canonical(runner.semantic_input(capture["input"]))),
                "max_requests": 2, "max_elapsed_seconds": 30}
        save(root / "probe-plan.json", plan)
        experiment = runner.Experiment()
        report = {"label": label, "native_model_inferences": 0, "external_runtime": "deterministic synthetic stand-in",
                  "supplied_patch_is_quality_evidence": False, "baseline_exit_code": runner.independent_check(),
                  "preflight": json.loads((root / "preflight.json").read_text()), "emitted_arguments_sha256": sha(arguments.encode())}
        before = (root / "project/solution.py").read_bytes()
        try:
            report["setup"] = experiment.connect()
            report["readiness_before"] = experiment.cli("client", "status", "codex", "--client-home", str(experiment.client_home), "--routing")
            armed = experiment.cli("client", "qualify", "codex", "--client-home", str(experiment.client_home), "--plan", str(root / "probe-plan.json"))
            report["native"] = experiment.launch("controlled-native", probe=armed["probe_id"], timeout=30)
            report["probe_after"] = experiment.cli("client", "qualify", "codex", "--client-home", str(experiment.client_home))
            report["work_state"] = experiment.public("work/state")
            report["readiness_after"] = experiment.cli("client", "status", "codex", "--client-home", str(experiment.client_home), "--routing")
            report["revoked"] = experiment.cli("client", "qualify", "codex", "--client-home", str(experiment.client_home), "--revoke")
        finally:
            experiment.close(report)
            after = (root / "project/solution.py").read_bytes()
            report["implementation_changed"] = before != after
            report["check_exit_code"] = runner.independent_check()
            report["checks_unchanged"] = sha((root / "oracle.py").read_bytes()) == case["checks_sha256"]
            report["events"] = [json.loads(line) for line in (root / "controlled-native.jsonl").read_text().splitlines()]
            requests = [json.loads(path.read_text()) for path in sorted((root / "engine").glob("request-*.json"))]
            report["controlled_requests"] = len(requests)
            calls = [item for request in requests[1:] for item in request.get("input", [])
                     if item.get("type") == "function_call" and item.get("call_id") == call["call_id"]]
            report["continued_arguments_match"] = bool(calls) and all(item.get("arguments") == arguments for item in calls)
            report["implementation_patch"] = "".join(difflib.unified_diff(before.decode().splitlines(keepends=True), after.decode().splitlines(keepends=True)))
            save(OUTPUT / ("routed-" + label + ".json"), report)
        print(json.dumps({key: report.get(key) for key in ("label", "native_model_inferences", "native", "controlled_requests",
            "continued_arguments_match", "implementation_changed", "check_exit_code", "checks_unchanged", "settings_restored", "owned_listeners_closed")}))
        if not (report["controlled_requests"] == 2 and report["continued_arguments_match"]
                and report["checks_unchanged"] and report["settings_restored"] and report["owned_listeners_closed"]):
            raise RuntimeError("Synthetic native control did not complete")


if __name__ == "__main__":
    control("empty", "apply_patch")
    patch = ("*** Begin Patch\n*** Update File: solution.py\n@@\n def clamp(value, lower, upper):\n"
             "-    return max(lower, value)\n+    if lower > upper:\n+        raise ValueError('inverted bounds')\n"
             "+    return max(lower, min(value, upper))\n*** End Patch\n")
    control("populated", "apply_patch <<'PATCH'\n" + patch + "PATCH")
