# 0026: Explicit acquisition of verified local assets

Status: implemented for the existing experimental row, October 15, 2026.

The owner selected verified runtime/model acquisition with preview and explicit
downloads while coding qualification (#538) and plugin workflows (PR #545) were
being implemented separately. Existing shared setup already owns configuration,
credentials and runtime lifetime, but requires pre-existing files.

Add one shared CLI operation returning verified paths and arguments for that
setup owner. Default to offline, read-only preview. Download only on an explicit
`--download`, using a versioned reviewed catalog, immutable byte/digest pins,
license/provenance links and bounded sequential transport. Keep partial files
inside a private, exclusively locked catalog generation; resume only with
validated ranges and publish readiness only after the existing runtime verifier.

Acquisition never grants coding capability, changes route policy, runs downloaded
code, starts the model or changes Codex settings. The bundled Darwin arm64 row
remains experimental. A production support decision and the rest of #542 are
not closed by this implementation. Automatic updates, removal, asset discovery,
new model selection and cloud authentication remain separate.

The three new contracts start at version 1 and do not migrate existing stores.
Updates create another generation, while rollback preserves existing assets
and disconnects through the current connection owner before downgrade.

See [the workflow, contracts and recovery limits](../LOCAL_ASSET_ACQUISITION.md).
