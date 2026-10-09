# Use your familiar SDK through Hormuz

These three runnable examples use the same AI Work gateway as the provider tour.
Each invocation creates one job (or reuses your explicit `--work-id`), applies
your chosen priority and job allowance, and makes one small inference request.
Add `--stream` to consume streaming usage.
The work stays active until your real workflow supplies an outcome observation.

Use the [current candidate setup](../../docs/TRY_HORMUZ.md), Python 3.11+, an
authorized AI Work gateway and a unique Hormuz identity. Published v1.8.0 artifacts
do not include the candidate AI Work commands. Qualify your installed vendor SDK
version, selected model and gateway rate card before using a real workload.

Install the optional SDKs in your candidate environment:

```sh
python -m pip install openai anthropic
```

Load `HORMUZ_GATEWAY_URL` and `HORMUZ_TOKEN` through your private environment.
Use the gateway URL, without `/v1`; it must be HTTPS unless your operator explicitly
enables loopback HTTP. These SDKs receive the Hormuz credential. OpenAI and
Anthropic provider keys remain on the gateway. Never commit or paste either key.

Replace the model aliases below with routes approved by your gateway operator:

```sh
python examples/sdk/work_sdk.py --api openai-responses \
  --model YOUR_OPENAI_ALIAS --objective cost --budget-usd 0.10 --execute

python examples/sdk/work_sdk.py --api openai-chat \
  --model YOUR_OPENAI_ALIAS --objective speed --budget-usd 0.10 --execute

python examples/sdk/work_sdk.py --api anthropic-messages \
  --model YOUR_ANTHROPIC_ALIAS --objective quality --budget-usd 0.10 --stream --execute
```

Each command can incur provider charges. The job allowance uses configured
gateway estimates; it is not a provider billing cap or an invoice. Higher-level
workspace and repository limits still apply. Missing usage or unqualified pricing
dimensions can retain an unknown hold. SDK retries and redirects are disabled.
The recipe uses the transport class exported by each installed vendor SDK:
`DefaultHttpx2Client` where available, otherwise `DefaultHttpxClient`. This keeps
the transport compatible with SDKs that require HTTPX2 while supporting older
exports. The client and transport use a 30-second timeout and are closed after
the request; a stream is also closed if iteration fails.

The fixed public arithmetic prompt demonstrates a connection. It does not train
a global model leaderboard, grade provider quality or establish customer savings.
The script consumes the answer without printing its body and returns a small
metadata receipt. Read the actual job in your private gateway for route decisions,
captured estimates, attempts and uncertainty.

If anything fails after job creation, the failure receipt includes `work_id`
without the SDK error body. Inspect that job before retrying: an uncertain
request may still have incurred a charge. To make one intentional retry on the
same job, pass its ID and the same API, alias, priority and allowance:

```sh
python examples/sdk/work_sdk.py --api openai-responses \
  --model YOUR_OPENAI_ALIAS --objective cost --budget-usd 0.10 \
  --work-id YOUR_EXISTING_WORK_ID --execute
```

This preserves accumulated attempts and spending. Repository and context flags
are only used when creating a new job. The gateway verifies access to the reused
job and still enforces its state and all applicable allowances.

Use [delivery examples](../../docs/AI_WORK_DELIVERY_EXAMPLES.md) to join real
workflow observations to work. The [work attachment guide](../../docs/AI_WORK_AGENT_INTEGRATION.md)
also shows how to attach an existing coding agent and preserve a work ID across
iterations. Generic API examples do not qualify every native agent feature.

Vendor references: [OpenAI Python SDK](https://github.com/openai/openai-python),
[Anthropic Python SDK](https://github.com/anthropics/anthropic-sdk-python).
The official SDK documentation and transport sources were checked on October 9,
2026, including [OpenAI's HTTPX2 migration guide](https://github.com/openai/openai-python/blob/main/httpx2.md)
and [Anthropic's exported transport implementation](https://github.com/anthropics/anthropic-sdk-python/blob/main/src/anthropic/_base_client.py).
Mocked tests cover transport selection and cleanup. A live qualification with
your installed SDK, gateway, selected model and account remains pending.
