# Argo CD Image Updater annotation-based argocd write-back

This reference defines the active A4x image automation contract. Application
teams keep their existing workload overlay and CI image build. The platform
creates the Argo CD Application with standard Image Updater annotations. Image
Updater selects an immutable SHA and updates the live Application override;
it does not write to the application Git repository.

## Data flow

```text
application CI -> environment-scoped Harbor commit-SHA tag
  -> Argo CD Image Updater reads Application annotations
  -> live Application spec.source.kustomize.images override
  -> Argo CD sync
```

The root-apps Application must ignore the controlled live image override so an
older recovery seed in Git does not overwrite a newer Image Updater value.

## Application contract

An opted-in Kustomize Application declares:

```yaml
metadata:
  annotations:
    argocd-image-updater.argoproj.io/image-list: app=<harbor>/cicd/<env>-<region>/<app>
    argocd-image-updater.argoproj.io/app.update-strategy: newest-build
    argocd-image-updater.argoproj.io/app.allow-tags: "regexp:^[a-f0-9]{7,40}$"
    argocd-image-updater.argoproj.io/write-back-method: argocd
    argocd-image-updater.argoproj.io/app.kustomize.image-name: <app>
    argocd-image-updater.argoproj.io/app.platforms: linux/amd64,linux/arm64
spec:
  source:
    kustomize:
      images:
        - <app>=<harbor>/cicd/<env>-<region>/<app>:<current-sha>
```

Use `linux/amd64` for a single-arch target. Multi-image Applications repeat the
alias-specific annotations and recovery seed for each alias. Helm Applications
use the explicit Helm repository/tag parameter annotations instead of the
Kustomize image-name field.

The image path is environment-specific and literal. Kustomize and Image Updater
do not expand `${IMAGE_BASE}`. The CI runner-selected Harbor, overlay newName,
Application image-list, and recovery seed must all resolve to the same path.

## Recovery seed

`spec.source.kustomize.images` is the recovery baseline recorded in
`argocd-apps`, not a per-release Git source of truth.

- New Application: CI first pushes a real SHA to the target Harbor. Verify its
  digest and required platforms before generating or submitting the Application
  with that SHA as its recovery seed. Until then, keep only a pending contract.
- Existing Application: read the current live override and workload image and
  preserve that SHA when editing the Git Application. The explicit incident-pin
  variant in `workflows/manage-business-image-rollback.md` instead records the
  reviewed rollback target SHA and matching exact allow-tags filter together.
- `0000000` is allowed only as an app-repository overlay placeholder before CI
  produces the first image, never in an Application file or draft MR.

The root-app ignore rule protects later live Image Updater SHAs from the older
Git recovery seed. Editing the seed alone does not prove that the live image
changed. Business rollback must first control updater selection, then verify or
explicitly perform the approved live Application override and runtime acceptance.
Keep the pin until a separately reviewed release; see
[business-image-rollback.md](../business-image-rollback.md).

## No application-repository onboarding contract

New applications do not add any of the following:

- `ImageUpdater` CR or per-application `ApplicationSet`;
- `.argocd-source-<application>.yaml`;
- Flux image automation resources;
- Git write credential, merger policy, or CI build-loop guard;
- GitLab role or protected-branch changes for Image Updater.

An active Application source path must not retain `.argocd-source-*`. Argo CD
loads that parameter file during manifest generation, so it can override the
Application source and defeat the live override owner. Preserve its history in
Git commits/MRs, then remove the active file as part of the rollback.

`Argocd-deploy` only needs the repository read permission Argo CD already uses;
Reporter is sufficient. Retired `image-writeback.addx.io/*` labels and the
`argocd-image-updater.argoproj.io/git-branch` annotation are invalid.

## force-update exception

Do not add `<alias>.force-update: "true"` by default. It bypasses the normal
check that the image is present in `Application.status.summary.images`.

It is allowed only when read-only evidence proves the accurate target workload
image is missing from that Application summary. Scope it to the exact alias,
record the evidence in the platform change, and verify a no-change reconcile
does not produce repeated updates. Remove it when the summary becomes reliable.

## Activation gate

Before enabling an Application, verify:

1. the target cluster runs the approved annotation-compatible Image Updater;
2. image-list, overlay newName, CI target Harbor, and recovery seed use the same
   environment-specific image path;
3. the recovery seed is a real SHA present in Harbor;
4. the exact Application source path plus recovery override renders the expected
   workload path and SHA;
5. no retired platform labels, git-branch, active `.argocd-source-*`,
   ImageUpdater CR, ApplicationSet, or second image owner exists;
6. a no-change reconcile remains quiet and a real new image updates the live
   Application and reaches a Healthy workload.
