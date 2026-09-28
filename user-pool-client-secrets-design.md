# Proposal: Manage Cognito User Pool Client Secrets

This proposal adds support for managing Amazon Cognito User Pool Client secrets with a dedicated `UserPoolClientSecret` CRD.

The existing `UserPoolClient` resource supports `generateSecret`, but the generated value is only returned by Cognito once and cannot be safely exposed through the ACK resource spec or status. Newer Cognito APIs also support up to two active client secrets, which enables zero-downtime rotation. A single export field on `UserPoolClient` would not model this lifecycle well.

This proposal therefore introduces one Kubernetes resource per Cognito client secret.

## Goals

- Support Cognito-generated client secrets through `AddUserPoolClientSecret`.
- Support user-provided client secrets stored in Kubernetes Secrets.
- Keep client secret values out of ACK CR specs and statuses.
- Support two active Cognito secrets for explicit, zero-downtime rotation.
- Allow managing a client by literal AWS IDs or by ACK resource references.
- Preserve user ownership of Kubernetes Secret objects.

## Non-goals

- Automatically rotate secrets on a schedule.
- Adopt or export the initial secret created by `UserPoolClient.spec.generateSecret`.
- Create, delete, or own destination Kubernetes Secrets.
- Detect drift of a generated secret value after it has been exported.
- Adopt an existing Cognito client secret. The resource must only manage a secret it created.

## Proposed API

A `UserPoolClientSecret` represents one client secret attached to one Cognito User Pool Client.

```yaml
apiVersion: cognitoidentityprovider.services.k8s.aws/v1alpha1
kind: UserPoolClientSecret
metadata:
  name: application-client-secret-v2
  namespace: application
spec:
  userPoolId: eu-west-3_example
  userPoolClientId: 1example23456789

  exportTo:
    name: application-cognito
    key: clientSecret
```

The same resource can refer to ACK-managed parent resources:

```yaml
apiVersion: cognitoidentityprovider.services.k8s.aws/v1alpha1
kind: UserPoolClientSecret
metadata:
  name: application-client-secret-v2
  namespace: application
spec:
  userPoolRef:
    from:
      name: user-pool
  userPoolClientRef:
    from:
      name: application-client

  exportTo:
    name: application-cognito
    key: clientSecret
```

A user-supplied secret is read from an existing Kubernetes Secret and passed to Cognito:

```yaml
apiVersion: cognitoidentityprovider.services.k8s.aws/v1alpha1
kind: UserPoolClientSecret
metadata:
  name: application-client-secret-v2
  namespace: application
spec:
  userPoolId: eu-west-3_example
  userPoolClientId: 1example23456789

  clientSecret:
    name: application-cognito
    key: nextClientSecret
```

The status contains only non-sensitive Cognito metadata:

```yaml
status:
  id: 8d640e8e-...
  clientSecretCreateDate: "2026-09-09T12:00:00Z"
  conditions: []
```

`status.id` is the Cognito `ClientSecretId`, not the client secret value.

## Spec Fields

| Field | Description |
| --- | --- |
| `userPoolId` | Cognito User Pool ID. Optional when `userPoolRef` is used. |
| `userPoolRef` | Optional reference to an ACK `UserPool`, resolved from `status.id`. |
| `userPoolClientId` | Cognito User Pool Client ID. Optional when `userPoolClientRef` is used. |
| `userPoolClientRef` | Optional reference to an ACK `UserPoolClient`, resolved from `status.id`. |
| `exportTo` | Destination `SecretKeyReference` for an AWS-generated secret. |
| `clientSecret` | Source `SecretKeyReference` containing a user-provided secret value. |
| `status.id` | Cognito `ClientSecretId`, used for read and delete operations. |
| `status.clientSecretCreateDate` | Cognito `ClientSecretCreateDate`. |

The `exportTo` and `clientSecret` fields use the existing ACK `SecretKeyReference` shape:

```yaml
name: application-cognito
namespace: application # optional, defaults to the CR namespace
key: clientSecret
```

## Validation Rules

- One of `userPoolId` or `userPoolRef` must be specified.
- One of `userPoolClientId` or `userPoolClientRef` must be specified.
- `userPoolId`, `userPoolRef`, `userPoolClientId`, and `userPoolClientRef` are immutable.
- Exactly one of `exportTo` and `clientSecret` must be specified.
- `exportTo` is required in generated mode.
- `clientSecret` is required in supplied mode.
- ACK's generated reference handling accepts exactly one of an ID and its corresponding reference. Supplying both is rejected.
- A generated secret must be exported to an existing Kubernetes Secret.
- A generated secret must not overwrite an existing non-empty destination key. This is terminal.
- A supplied secret must exist, contain the requested key, and contain a non-empty value.
- Supplied secrets follow the ACK secret-input convention and require an `Opaque` Kubernetes Secret.
- Cross-namespace Secret access must honor the controller's `--enable-cross-namespace` setting.

## Reconciliation Behavior

### Generated Secret Mode

1. Resolve the User Pool and User Pool Client IDs.
2. Validate that the referenced User Pool Client belongs to the resolved User Pool.
3. Atomically verify that the destination Kubernetes Secret exists and that `exportTo.key` is absent or empty.
4. Fail with a terminal condition if the target Secret is missing or `exportTo.key` is already non-empty.
5. Call `AddUserPoolClientSecret` without `ClientSecret`.
6. Receive `ClientSecretId`, `ClientSecretCreateDate`, and `ClientSecretValue`.
7. Atomically write `ClientSecretValue` only if the destination key remains empty.
8. Persist only the secret ID and creation date in the resource status.

The generated value is never persisted in the custom resource.

### Supplied Secret Mode

1. Resolve the User Pool and User Pool Client IDs.
2. Validate that the referenced User Pool Client belongs to the resolved User Pool.
3. Read the source value from `clientSecret`.
4. Call `AddUserPoolClientSecret` with `ClientSecret` set to that value.
5. Persist the returned `ClientSecretId` and creation date in the resource status.

Cognito does not return `ClientSecretValue` when the caller provided the value. This is expected because the source value remains in Kubernetes.

### Read

The resource uses a custom read implementation over `ListUserPoolClientSecrets` to retrieve secret descriptors and searches for `status.id`.

- If the ID is present, ACK refreshes non-sensitive metadata such as `clientSecretCreateDate`.
- If the ID is absent, the resource is considered not found.
- ACK never attempts to read or compare `ClientSecretValue`, because Cognito intentionally never returns it in list responses.

### Update

No AWS update operation is proposed.

All identity and secret source/destination fields are immutable. Changing any of them requires creating a new `UserPoolClientSecret`, which creates a new Cognito secret. This maps cleanly to Cognito's credential lifecycle.

### Delete

The resource uses `DeleteUserPoolClientSecret` with `status.id`.

The controller does not delete or clear the referenced Kubernetes Secret key. That Secret remains user-managed and may contain data unrelated to this resource.

Cognito does not allow deletion of the last remaining active secret. If AWS rejects deletion for that reason, ACK must keep the finalizer and set a terminal condition. The user can then:

1. Create a second `UserPoolClientSecret`.
2. Move workloads to the new credential.
3. Delete the old `UserPoolClientSecret`.

The standard ACK `services.k8s.aws/deletion-policy: retain` annotation remains available when a user intentionally wants to remove the Kubernetes resource while retaining the Cognito secret.

## Rotation Workflow

Cognito allows a maximum of two active client secrets. Rotation is intentionally explicit:

1. Create a second `UserPoolClientSecret`.
2. Wait for `ACK.ResourceSynced=True`.
3. Update workloads to consume the new Kubernetes Secret key or Secret object.
4. Delete the first `UserPoolClientSecret`.
5. ACK deletes the old Cognito client secret.

No automated `rotateAt` field or scheduled rotation is proposed initially. Application rollout and rollback semantics are workload-specific, and automatic rotation can easily conflict with Cognito's two-secret limit.

## Interaction with UserPoolClient.generateSecret

`UserPoolClient.spec.generateSecret=true` remains unchanged.

The initial secret returned by `CreateUserPoolClient` is outside the scope of this CRD because:

- Cognito returns its value only once.
- `ListUserPoolClientSecrets` does not reveal secret values.
- The current resource does not persist a stable `ClientSecretId` for that initial value.
- ACK cannot safely adopt, export, or later verify that initial secret.

`UserPoolClientSecret` manages secrets created through `AddUserPoolClientSecret`. It can therefore create the second active secret and support a migration away from the initial credential.

Supporting `CreateUserPoolClient.ClientSecret` as an input from a Kubernetes Secret may be added later as a separate enhancement to `UserPoolClient`. It should be mutually exclusive with `generateSecret: true`, but it is intentionally not part of this initial proposal.

## Proof of Concept Findings

A generator proof of concept was run against Cognito service SDK `v1.73.0`. It produced a compilable `UserPoolClientSecret` scaffold and confirmed the following API shape:

```go
type UserPoolClientSecretSpec struct {
    ClientSecret *ackv1alpha1.SecretKeyReference `json:"clientSecret,omitempty"`
    ExportTo     *ackv1alpha1.SecretKeyReference `json:"exportTo,omitempty"`
    UserPoolID   *string                          `json:"userPoolID,omitempty"`
    UserPoolClientID *string                      `json:"userPoolClientID,omitempty"`
}
```

`clientSecret` maps naturally to `AddUserPoolClientSecretInput.ClientSecret`: ACK's `is_secret: true` support resolves a `SecretKeyReference` to the value sent to Cognito. `exportTo` is necessarily synthetic: it represents a Kubernetes Secret destination and has no corresponding Cognito request field.

The earlier `generatedSecret` and `suppliedSecret` proposal was rejected by the proof of concept. Both fields become `SecretKeyReference` values, but the generator only knows that such values are input sources. It cannot infer that one reference is an output destination. Binding `generatedSecret` to Cognito's `ClientSecret` therefore incorrectly supplied a Kubernetes Secret value to Cognito, while the supplied-secret field was left disconnected from the request.

Likewise, `ClientSecretValue` must be excluded from generated API fields. It is a one-time AWS response value, not a `SecretKeyReference`, and must only be passed directly to a custom Secret export path. It must never appear in the ACK resource Spec or Status.

`ListUserPoolClientSecrets` requires a custom read implementation. Its response contains `ClientSecrets[]`, while ACK must select the descriptor whose `ClientSecretId` equals `status.id`. The default generated read path cannot select an item from this list and otherwise reports success without confirming that the secret exists.

The POC also confirmed that the generated delete payload can use the desired stable identity:

```text
UserPoolId     <- spec.userPoolId
ClientId       <- spec.userPoolClientId
ClientSecretId <- status.id
```

The resource must be configured as non-adoptable. Cognito never returns enough information to prove that an existing secret belongs to this CR, and an adopted resource could otherwise delete a secret that ACK did not create.

## ACK Generator Configuration

The generator must be invoked with a Cognito service SDK version that includes these operations. The previously pinned controller service SDK version does not expose:

- `AddUserPoolClientSecret`
- `ListUserPoolClientSecrets`
- `DeleteUserPoolClientSecret`

The implementation should use root-level ACK generator configuration to identify the operations as:

```yaml
operations:
  AddUserPoolClientSecret:
    resource_name: UserPoolClientSecret
    operation_type: CREATE
  ListUserPoolClientSecrets:
    resource_name: UserPoolClientSecret
    operation_type: READ_ONE
  DeleteUserPoolClientSecret:
    resource_name: UserPoolClientSecret
    operation_type: DELETE
```

The proof of concept was generated with:

```sh
SERVICE=cognitoidentityprovider \
ACK_GENERATE_BIN_PATH=bin/ack-generate \
RUNTIME_CRD_DIR=../runtime/config \
TEMPLATES_DIR=templates \
AWS_SERVICE_SDK_VERSION=v1.73.0 \
make build-controller
```

The SDK upgrade also exposes `UserPoolReplica`. It must remain in `ignore.resource_names` unless it is implemented and registered completely; generated CRD artifacts for an ignored resource must not be packaged in Helm.

`ClientSecret` should use the model-derived field with `is_secret: true`. `ExportTo` should be a synthetic `bytes` field with `is_secret: true`, following the type-generation pattern used by ACM's `Certificate.spec.exportTo`. Both fields must be excluded from standard delta comparison and enforced as immutable.

The resource requires custom create and read implementations because the read operation returns a list of descriptors and the secret ID is only known after create.

ACK already has an output-to-Kubernetes-Secret precedent in the ACM controller's `Certificate.spec.exportTo` support. This proposal should reuse the `SecretKeyReference` API shape but not expose the sensitive output in the ACK resource.

## Runtime Prerequisites

The proof of concept showed that the existing `runtime.WriteToSecret` helper is insufficient for generated-secret export. This design requires a runtime enhancement before the generated mode can be shipped safely.

The runtime should provide an atomic operation equivalent to:

```go
WriteToSecretIfEmpty(
    ctx context.Context,
    sourceValue string,
    ref *ackv1alpha1.SecretKeyReference,
) error
```

The operation must:

1. Resolve an omitted namespace to the source CR namespace.
2. Enforce the same cross-namespace policy and advisory condition behavior as `SecretValueFromReference`.
3. Require the destination Secret to exist.
4. Reject a non-empty destination key with a terminal error.
5. Write only when the key is absent or empty.
6. Use resource-version-aware update or patch semantics and reject a conflict or concurrent write rather than overwrite a newly populated key.

A separate read followed by `WriteToSecret` is not sufficient: another actor can populate the key between those calls, and the current writer would overwrite that value. This violates the no-overwrite contract.

The helper should not create, delete, own, or clear the destination Secret. It should preserve unrelated data keys.

## Failure and Recovery Considerations

`AddUserPoolClientSecret` has no client token or idempotency token, and an AWS-generated `ClientSecretValue` can never be retrieved again.

A failure after AWS creates the secret but before ACK persists `status.id` can leave an untracked Cognito secret. A failed export after AWS succeeds must also not cause ACK to call `AddUserPoolClientSecret` again: that would create a second credential and can exhaust Cognito's two-secret limit.

The implementation should:

- Validate the export target before the AWS request with the atomic runtime operation.
- Write the generated value only after the Cognito API returns successfully.
- When AWS creation succeeds, persist `status.id` and `status.clientSecretCreateDate` even if a subsequent export failure is being surfaced.
- Never retry `AddUserPoolClientSecret` once an ID has been recorded.
- Treat a failed generated-value export as terminal, because the value cannot be recovered. The condition must explain that the Cognito secret was created but could not be exported and require manual remediation.
- Never mark the resource `ACK.ResourceSynced=True` unless the requested generated-value export completed successfully.
- Consider recording resource ownership metadata on the destination Secret after successful export, such as the `UserPoolClientSecret` UID and `ClientSecretId`, to support diagnosis and recovery.

The process still has an unavoidable crash window after Cognito creates the secret but before the CR status is persisted. Cognito does not offer an idempotency token and `ListUserPoolClientSecrets` cannot identify a secret created by a particular Kubernetes object. The initial implementation must document manual cleanup of a stranded secret. A future Cognito or runtime capability is required to make this flow fully crash-safe.

## Test Plan

Unit tests:

- Generated mode creates a Cognito secret and exports its value.
- Generated mode refuses to overwrite a non-empty destination key.
- Generated mode rejects a target Secret that does not exist before calling Cognito.
- Atomic export rejects a concurrent write rather than overwriting it.
- Generated mode allows an absent or empty destination key.
- Supplied mode reads a Kubernetes Secret and sends the value to Cognito.
- Source Secret missing, key missing, empty source value, and invalid Secret type produce expected errors.
- Supplying both a parent ID and its ACK reference is rejected.
- Read finds the descriptor matching `status.id`.
- Read returns not found when `status.id` is absent from `ListUserPoolClientSecrets`.
- Delete invokes `DeleteUserPoolClientSecret` with the persisted ID.
- Cross-namespace source and destination behavior follows `--enable-cross-namespace`.
- Generated export failure preserves the Cognito secret ID, reports a terminal condition, and never reports the resource as synced.
- The resource cannot be adopted.

E2E tests:

- Create an AWS-generated client secret and assert that its value is written to the requested Kubernetes Secret key.
- Assert that the generated value is absent from the ACK resource spec and status.
- Create a client secret from a Kubernetes Secret value.
- Create two `UserPoolClientSecret` resources for the same User Pool Client.
- Update the consuming workload or target reference as part of a manual rotation scenario.
- Delete the old secret and verify that only its Cognito descriptor is removed.
- Verify that attempting to create a third active secret fails.
- Verify that deleting the final active secret produces the expected terminal state.
- Verify that unrelated keys in the target Kubernetes Secret are preserved.

## Addendum: the initial secret problem, and its resolution

During implementation (see PR #58), two assumptions above turned out to be wrong, and together they blocked the rotation workflow this proposal was designed to support:

1. **`AddUserPoolClientSecret` requires the client to already have secret support enabled.** Calling it against a `UserPoolClient` created with `GenerateSecret=false` (or omitted) fails with `InvalidParameterException: User pool client secret not enabled. Cannot create new secret.` In practice, a `UserPoolClientSecret` can only ever be created for a client that was already created with `spec.generateSecret: true` — meaning that client already has an AWS-generated initial secret occupying one of Cognito's two secret slots before this CRD does anything.
2. **The initial secret's value is not actually one-time.** `DescribeUserPoolClient`, `CreateUserPoolClient`, and `UpdateUserPoolClient` responses all return `ClientSecret` in plaintext on every call — this proposal's stated reason for treating the initial secret as unreadable/unexportable ("Cognito returns its value only once") does not hold. Only secrets created via `AddUserPoolClientSecret` are truly one-time-readable (`ListUserPoolClientSecrets` never reveals any value, including the initial one).

Combined, this meant the initial secret permanently and invisibly occupied one of the two available slots, and the "create a 2nd secret via this CRD, then delete the 1st" rotation flow described above could never actually complete: the CRD had no way to see or delete that first secret.

### Resolution

**Phase 1 — round-trip the initial secret through `UserPoolClient` itself**, since it is fully and repeatedly readable/writable there and does not need this CRD's one-time-secret machinery:

- `spec.clientSecret` (`SecretKeyReference`, `is_secret: true`, immutable): supply your own value at client-creation time, feeding `CreateUserPoolClientInput.ClientSecret` directly. Mutually exclusive with `spec.generateSecret: true`.
- `spec.clientSecretExportTo` (`SecretKeyReference`, `is_secret: true`, immutable, synthetic like ACM's `Certificate.spec.exportTo`): when `spec.generateSecret: true`, export the AWS-generated value (read straight from the `CreateUserPoolClient` response) to a Kubernetes Secret. Requires `generateSecret: true`.
- `status.initialClientSecretID`: captured once, right after `CreateUserPoolClient` succeeds with `generateSecret: true`, via a single `ListUserPoolClientSecrets` call — at that moment exactly one secret exists, so its ID is unambiguous. This becomes a stable, permanent pointer to "the original secret," even after a second one is later added through `UserPoolClientSecret`.
  - Pre-existing clients that already have two secrets and no captured ID are an accepted edge case; the closest available heuristic is matching a secret's `ClientSecretCreateDate` to the client's own `CreationDate`, otherwise the ID must be identified out-of-band (e.g. via the AWS CLI) — consistent with the "Failure and Recovery Considerations" section's existing acceptance of manual recovery paths.

**Phase 2 — allow `UserPoolClientSecret` to adopt that initial secret**, superseding this proposal's original "must only manage a secret it created" non-goal and its `is_adoptable: false` setting:

- `UserPoolClientSecret.is_adoptable` is now `true`. No new bespoke "bring your own ID" field was needed: the code generator already produces a correct `PopulateResourceFromAnnotation` for this resource (it requires and populates `status.id`, `spec.userPoolID`, and `spec.userPoolClientID` from the fields map), and the existing `customFind` implementation already looks up a descriptor by `status.id` via `ListUserPoolClientSecrets`. This means ACK's standard annotation-based adoption flow (`services.k8s.aws/adoption-policy: adopt` + `services.k8s.aws/adoption-fields: '{"id": "<initialClientSecretID>", "userPoolClientID": "...", "userPoolID": "..."}'`) works unmodified once the flag is flipped.
- With adoption enabled, a full rotation is now achievable: adopt the initial secret into a `UserPoolClientSecret` CR (using `status.initialClientSecretID` from `UserPoolClient`), create a second `UserPoolClientSecret` in generated or supplied mode, migrate consumers to it, then delete the adopted CR for the original secret (invoking `DeleteUserPoolClientSecret` normally).

This keeps the original CRD-per-secret model intact for anything created after this feature ships, while giving existing/initial secrets a supported path both to be used directly (via `UserPoolClient`) and, if desired, to be fully retired (via adoption + delete on `UserPoolClientSecret`).

