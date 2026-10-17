# Retired Codex app startup adapter experiment

Normal-app activation is disabled. A real restart produced the reported
“Organization settings could not be loaded” recovery screen. Earlier combined
catalog and model-routing probes covered the native app-server interface;
they did not qualify the desktop cold-start policy, login or recovery paths.
The desktop's actual launch arguments were absent from those probes.

The installed desktop constructs `-c features.code_mode_host=true app-server
--analytics-default-enabled`. The adapter's positional parser rejected the
leading `-c` before starting the native engine. Replaying those exact arguments
reproduced the preflight failure and `unrecognized arguments: -c`. The parser
now preserves leading native options and their order. The isolated regression
also checks native policy/config RPCs, not just a model response. This explains
a concrete failure in the deployed launch path; it does not establish that
every organization-policy failure has the same cause.

An isolated replay of the app's initialize, configRequirements/read and
config/read sequence reproduced a related startup failure: an unavailable
local catalog made the bridge exit before the native engine initialized.
The regression now verifies native startup survives that outage. This recovery
is performed before consuming stdin or sending any protocol frames; it never
retries a submitted model request or silently moves local work to the cloud.

## Recover the normal app

```sh
koto off
```

This disables the saved bridge, restores the previous launch setting when
still owned, and disconnects its separate router owner. Verify the returned
receipt reports the router and any owned model process stopped. Current normal
settings remain intact; no app reinstall or sign-out is required by this command.
Existing files remain available for inspection and recovery.

Use [the explicit chat workflow](KOTO_CHAT.md) for local tasks. It leaves the
native application startup and selected host model intact.

## Isolated experiment only

The old public installer now returns the additive v1 error
`connected_client_app_activation_disabled` before reading a router or changing
files or launch settings. Preview is blocked too. Its compatibility fixture is
`tests/fixtures/codex-app-activation-disabled-v1.json`; existing receipts retain
their versions, and `off` continues to recover old installations.

Regression fixtures can use `--isolated-test` with a disposable client home and
a fake launch controller. The real macOS launch controller and normal Codex
home remain rejected even with this flag. No test activates the user's app.

```sh
python -m unittest tests.test_codex_startup_workflow tests.test_codex_app_bridge_workflow -v
```

A future native Koto picker entry needs supported per-selection app routing
and a complete isolated desktop startup, organization-policy, login, menu and
restoration qualification before any daily-app activation.
