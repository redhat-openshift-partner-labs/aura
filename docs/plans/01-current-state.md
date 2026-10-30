# Current State

## Operating Context

OpenShift Partner Labs currently spans several independently managed repositories:

- `fleet` holds Tekton pipelines, hub configuration, and lifecycle workflows.
- `fleet-clusters` holds cluster definitions and provisioning/deprovisioning data.
- `fleet-cluster-scaffold` generates cluster scaffolding.
- `portal` provides the operator dashboard.
- `queue`, `workers`, `scribe`, and `messenger` implement the legacy event-driven path.

The operator can currently manage the environment manually. That changes the design constraint: Aura does not need to preserve or replace the legacy message pipeline immediately.

## Protected Repositories

The following repositories are code-frozen for this effort:

| Repository | Aura interaction |
| --- | --- |
| `fleet` | Read documentation and pipeline definitions; invoke Tekton only through OpenShift APIs |
| `fleet-cluster-scaffold` | Invoke as a versioned CLI or container; do not modify |
| `fleet-clusters` | Read inventory; optionally produce a patch or PR if operational changes are allowed |
| `portal` | Read-only reporting source or no integration; never write its database |

## Existing Architectural Signals

### Fleet already identifies the workflow boundary

`fleet` explicitly separates responsibilities:

- Argo CD reconciles declarative state.
- Tekton runs ordered workflows.
- ACM/Hive controls cluster lifecycle.

Aura should not introduce a second workflow engine or turn the model into one.

### Queue state is difficult to manage manually

The legacy queue system uses topic exchanges, quorum queues, TTLs, and dead-letter routing as a distributed saga mechanism. Its documentation treats queue position as workflow state. That model supports automated delivery, but it is difficult to inspect, explain, and operate by hand.

### Legacy implementation has drifted from its invariants

The current event-driven path contains several reliability gaps:

- Portal writes directly to the database even though Scribe is documented as the sole writer.
- Workers do not use one consistent acknowledgement strategy.
- Queue documentation and manifests disagree on the number of queues.
- Failure handling is split among worker code, queue arguments, and manual DLQ inspection.

These issues support building Aura beside the legacy path; Aura does not extend it.

## Why a Sidecar Rebuild Is Justified

A sidecar rebuild is justified because:

1. Manual operation is already acceptable.
2. The protected repositories can remain unchanged.
3. The legacy event path can be bypassed without migration.
4. Aura can start with diagnosis and proposal generation, where the risk is low.
5. A new audit and command model can be designed without retrofitting Scribe, workers, and Portal.

## Why a Full Workflow Replacement Is Not Justified

Aura should not become a replacement workflow engine because:

1. Tekton already owns ordered cluster lifecycle work.
2. Git already owns desired cluster definitions.
3. OpenShift already owns actual cluster state.
4. A model cannot safely own durable workflow state.
5. Replacing RabbitMQ with an AI loop would recreate retries, idempotency, and dead-letter semantics less reliably.

## Conclusion

The chosen design is a new Aura control plane that consumes existing sources of truth, prepares safe actions, and executes only after human approval. The legacy path can remain untouched until Aura proves sufficient value.

