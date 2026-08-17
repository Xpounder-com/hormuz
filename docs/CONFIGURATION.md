# Hormuz configuration contract

Hormuz treats configuration as an enforcement input, not a best-effort preference file. A configuration typo must stop startup before the process opens its listener, databases, identity metadata, or provider path.

## Fail-closed parsing

`GatewayConfig.load` accepts one JSON object and rejects unknown fields at every Hormuz-owned configuration object boundary, including:

- the root, listener, provider, authentication, OIDC issuer/login/subject, session, context-service, DLP, and policy objects;
- each static identity and model route;
- lifecycle promotion paths and context-injection policy;
- organization, team, and actor policy bodies.

Model aliases and DLP/team/actor maps intentionally have dynamic keys. Their values still use strict schemas. Model and fallback references must resolve to a configured route. Team and actor policy scopes must resolve to configured identities, and a team policy is rejected when the same team ID appears in more than one configured organization. These checks prevent a misspelled restriction from being silently ignored or shared across tenants.

Unknown-field errors report only the fixed schema path, for example `Unknown listen fields`. Hormuz does not reflect the rejected key into CLI diagnostics because an attacker-controlled JSON key can itself contain sensitive text.

## Secret boundary

The JSON file contains environment-variable names, not provider keys, bootstrap tokens, OIDC client secrets, session master keys, approval fingerprint keys, or organization dictionary values. Loading resolves those values from the process environment and stores secret-bearing dataclass fields with representation disabled. Do not place literal secrets in the JSON file.

`hormuz doctor` loads the same complete configuration used by `serve`. It verifies required secret presence, policy references, bounds, provider transport rules, and—when OIDC is configured—live discovery/JWKS metadata. It does not send a generation request to OpenAI or Anthropic. Its output is intended for an authorized operator terminal and includes configured file/database paths and non-secret environment-variable names.

## Change and rollback procedure

The configuration is immutable for the lifetime of a Hormuz process. `SIGHUP` reload is not implemented. Authentication objects, policy engines, redactors, rate limiters, session state, and database handles are constructed from one accepted snapshot at startup, so replacing the file under a running process does not change that process.

Use a replacement rollout:

1. Materialize the candidate JSON and secret references without changing the active file or process.
2. Run the candidate through the exact target Hormuz package with `hormuz --config CANDIDATE doctor` in the target secret/network environment.
3. Start a replacement process or deployment revision from that exact candidate.
4. Require `GET /health/live` and `GET /health/ready` to pass before shifting traffic.
5. Drain the old revision with `SIGTERM` only after the replacement is ready.
6. Retain the previous image digest and configuration artifact so rollback creates a fresh process from the last accepted pair.

Do not overwrite the only copy of the active configuration and call that rollback. A syntax-valid configuration may still encode an unwanted business policy; change approval, signing, deployment orchestration, and cross-replica rollout coordination remain external release controls.

## Current boundary

This checkpoint proves strict application parsing and reference integrity. It does not provide a published JSON Schema, configuration signing, live reload, multi-replica rollout coordination, secret rotation without replacement, or a configuration change-approval workflow. Those remain production-release work under issues #11 and #17.
