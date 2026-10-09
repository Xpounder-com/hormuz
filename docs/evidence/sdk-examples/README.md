# Installed SDK loopback qualification

The [qualification harness](../../../tools/ai_work_sdk_qualification.py) runs six
sequential calls through the actual [SDK recipe](../../../examples/sdk/work_sdk.py):
OpenAI Responses, Chat Completions and Anthropic Messages, each with JSON and
streaming responses. The installed vendor SDKs send real HTTP to an authenticated
disposable Hormuz gateway. Its upstream is a declared synthetic loopback provider.
The work ledger is real SQLite; the SDK and gateway APIs are not mocked.

Each passing case requires exactly one new successful provider-response attempt,
one provider fixture call, an active job and no outcome observations. A successful
response never declares work complete. After both servers drain and close, the
harness checks the SQLite counts, integrity and foreign keys. It refuses nonlocal
DNS/socket destinations, disables ambient proxies during the run, and closes
clients, streams, sockets and temporary state. Both listeners accept at most four
handlers; SDK retries and redirects are disabled.

Terminal stream delivery can precede gateway settlement. The recipe permits one
initial metadata read and at most three status rereads with bounded backoff and
jitter while the new attempt is pending. It never replays inference. Each case
records the actual confirmation read count separately from its single provider
fixture call; terminal uncertainty or exhausted waiting retains the private work
handle and fails qualification.

Confirmation also requires exactly one new logical request group. Linked primary
and failover attempts may share that group; an unrelated concurrent attempt cannot
confirm the SDK call. Ambiguous groups fail with the owned work handle preserved,
without another status read or inference replay. Legacy attempts use their request
ID when no logical request ID exists.

Run from the reviewed candidate checkout and install it with the optional SDK
dependencies in an isolated environment. The vendor SDK packages are installed;
the gateway and recipe execute the source identified by the receipt's manifest.
Dependency installation downloads packages; the qualification itself permits only
its two loopback listeners. The optional SDKs are not core Hormuz dependencies.

```sh
python -m venv .venv-sdk-qualification
.venv-sdk-qualification/bin/python -m pip install -e . \
  openai==3.27.0 anthropic==1.12.1 httpx2==2.13.1
.venv-sdk-qualification/bin/python tools/ai_work_sdk_qualification.py \
  --output /tmp/hormuz-sdk-qualification-NEW.json
```

The output path must be new. Inspect the metadata receipt before sharing it.
It omits credentials, prompts, answers, SDK exception bodies and customer state.
Its source commit identifies the starting checkout revision; the source SHA-256
manifest identifies the actual working tree, including the SDK recipe and harness.
Installed versions identify the specific tested SDK combination.

The [recorded local run](receipt.json) on 2026-10-09 passed all six cases using OpenAI 3.27.0,
Anthropic 1.12.1 and HTTPX2 2.13.1 on Python 3.12.12. Its 32-file manifest matches
the tested working tree; `5a02b48e2e73a6ae935ece24078e579f6fcf4a72` identifies
the starting checkout commit rather than declaring the uncommitted files part of
that commit. SQLite retained six active jobs, six successful response attempts
and zero outcome observations. Chat streaming required two confirmation reads;
the other cases required one each. All six cases made one inference call each,
and both listeners, clients and streams closed before the receipt was written.
The recorded source includes the logical-group confirmation guard and the
gateway's terminal stream framing checks.

This qualifies the local SDK attachment and protocol paths only. Synthetic
responses and configured fixture costs establish no model correctness, savings,
customer speed, provider invoice, live payment, deployed customer gateway or
installed native coding-agent compatibility. Live provider/account/model/rate
qualification remains a separate requirement.
