# Shared Database PushSecret blocked by an unmanaged Vault path

## Symptom

- `platform.addx.io Database` and its `XDatabase` report `Ready=True` and
  `Synced=True`.
- ArgoCD may also report the application `Synced/Healthy`.
- The expected Vault connection is absent or still contains a manually seeded
  legacy connection.
- The generated PushSecret reports:

  ```text
  secret not managed by external-secrets
  ```

## Why the top-level health is insufficient

The shared Database Composition wraps its generated PushSecret in a
`kubernetes.crossplane.io Object`. The Object can be Ready because the observed
manifest exists even when the observed PushSecret has `Ready=False`. Do not use
the claim, XR, Object, or ArgoCD health alone as proof that credentials reached
Vault.

Vault PushSecret ownership is intentional. On first creation ESO writes
`custom_metadata.managed-by=external-secrets`. When a current remote secret
already exists without that metadata, ESO refuses to overwrite it.

## Diagnose

1. Resolve the claim and XR with fully qualified resource names:

   ```bash
   kubectl --context <ctx> -n <app-ns> get databases.platform.addx.io <claim> -o json
   kubectl --context <ctx> get xdatabases.platform.addx.io <xr> -o json
   ```

2. From `XDatabase.spec.resourceRefs`, find the
   `kubernetes.crossplane.io/v1alpha2 Object`, then inspect both health layers:

   ```bash
   kubectl --context <ctx> get objects.kubernetes.crossplane.io <object> -o json
   kubectl --context <ctx> -n crossplane-system get pushsecret <app>-db-push -o json
   ```

   Required evidence:

   - Object `.status.conditions[type=Ready].status == True`
   - observed manifest
     `.status.atProvider.manifest.status.conditions[type=Ready].status == True`
   - direct PushSecret `.status.conditions[type=Ready].status == True`

3. Check the remote Vault path without printing values:

   - report only data key names and KV version;
   - report only custom metadata key names and whether
     `managed-by=external-secrets`;
   - compare values in-process against the generated Kubernetes connection
     Secret and print only boolean equality results.

## Repair a manually seeded migration path

Do not add the ownership metadata by hand. Preserve ownership as a controller
contract.

1. Pin the legacy source credential in a separate Kubernetes Secret.
2. Copy the complete legacy connection to a dedicated app-owned Vault path
   using KV v2 `cas=0`; verify an exact match without printing values.
3. In Git, repoint every legacy consumer to the backup path and remove unused
   references to the standard platform path. Merge and verify those
   ExternalSecrets first.
4. Soft-delete only the current version of the standard platform path. Do not
   delete the metadata tree or the dedicated backup.
5. Trigger one generated PushSecret reconcile by changing its resource version
   under an approved staging operation, for example:

   ```bash
   kubectl --context <ctx> -n crossplane-system annotate pushsecret <app>-db-push \
     external-secrets.io/force-sync="$(date +%s)" --overwrite
   ```

6. Require the three health checks above, ownership metadata, expected field
   names, and exact equality with the generated connection Secret before adding
   the application target ExternalSecret.

## Stop conditions

- Any current consumer still reads the standard path.
- The backup does not match the complete source connection.
- The pinned source Kubernetes Secret is absent or not Ready.
- The target is prod, or the path may contain prod-capable credentials.
- PushSecret remains `Ready=False` after one controlled reconcile.

Never delete or overwrite the legacy source path merely to make top-level
ArgoCD health green.
