# Disposable disaster-recovery conformance fixtures

These fixtures support issue #105's account-free Kubernetes enterprise-profile
rehearsal. They are verification infrastructure, not resources installed by the
Hormuz Helm chart.

- `state_probe.py` seeds and fingerprints every PostgreSQL-backed recovery
  class through its restricted service or repository role.
- `source-backup.yaml` runs the pinned PostgreSQL backup and continuous-WAL
  clients inside the disposable source cluster. Its control plane alone mounts
  the fresh recovery-input directory read/write; the gateway runtime has no
  access to that mount or the backup credential.
- `recovered-postgres.yaml` restores a verified physical backup and continuous
  WAL into an isolated PostgreSQL target on the disposable recovery cluster.
- `kind-recovery.yaml.tmpl` mounts the operator-owned recovery inputs read-only
  into that cluster.
- `hormuz.json` and `helm-values.yaml` exercise the generic existing-Secret DSN
  contract with managed policy and custody state.
- `probe.py` and `probe-pod.yaml` perform the first governed request only after
  recovery admission.

The source side uses the exact pinned CloudNativePG 1.30.0 topology already
verified by issue #104. The recovery target is intentionally isolated and is
not itself an HA claim. The rehearsal validates state and application behavior;
it does not certify Kind, Cilium, CloudNativePG, OpenBao, or a customer's backup
platform.

See `docs/DISASTER_RECOVERY.md` for the reviewed operating procedure, authority
separation, retention policy, clocks, failure behavior, and nonclaims.
