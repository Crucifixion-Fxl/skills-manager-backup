# TDD Platform — Non-Obvious Pitfalls

Things that wasted real time and would silently bite anyone copying the demo into a new service **or a new cluster**. Logged as engineering record so the next team doesn't pay the same cost. Each entry: symptom → root cause → fix.

- **§1–§6 + bonus** — Phase 2a wireup (2026-05, W1–W4): engineering traps building the demo itself (Kafka / Go / PVC / routing / mail). Location-independent.
- **§7–§9** — Phase 2b multi-region rollout (2026-05-27/28): traps deploying the demo (or a real service) onto a *new* staging cluster (eu / cn). These bit the eu/cn `tdd-demo` rollout directly.

---

## 1 · kafka-go GroupID consumer hangs against Redpanda

**Symptom**: A consumer-group-mode `kafka.Reader` (segmentio/kafka-go) against Redpanda v25.3.x times out on every `ReadMessage` even though `rpk topic consume` and direct partition readers see the data.

**Root cause**: The group-coordinator handshake (`FindCoordinator → JoinGroup → SyncGroup`) doesn't complete cleanly with Redpanda's coordinator implementation. The reader sits in `JoinGroup`-pending state until ctx expires.

**Fix**: Test-side probes don't need offset commits, so skip consumer groups entirely. Use `ReadPartitions(topic)` to list partitions, then one `kafka.NewReader(... Partition: i)` per partition (no `GroupID`). Round-robin poll with a short per-poll deadline (1.5s). Production-side consumers (Java spring-kafka, Go group consumers with sarama, etc.) work fine — this is a kafka-go-specific compatibility issue with Redpanda's coordinator.

**Where this lives now**: `tdd-testbase-go testbase/kafka_probe.go` PollMatching round-robin scan.

---

## 2 · Laptop → cluster Kafka needs both port-forward and `--add-host`

**Symptom**: From a docker container on your laptop, `kafka-go` connects to Redpanda's bootstrap port and gets metadata back, but every subsequent fetch hangs.

**Root cause**: Redpanda is configured with `--advertise-kafka-addr=PLAINTEXT://redpanda-0.redpanda.tdd-demo.svc.cluster.local:9092` so in-cluster clients can use the headless service DNS. From outside the cluster that hostname does not resolve, so client connection retries silently. Bootstrap (one socket) worked; partition leader connects (per-partition sockets) failed.

**Fix** (when running Go tests from laptop with docker):

```bash
kubectl -n tdd-demo port-forward pod/redpanda-0 9092:9092 &
docker run --network host \
  --add-host=redpanda-0.redpanda.tdd-demo.svc.cluster.local:127.0.0.1 \
  -e KAFKA_BROKERS=redpanda-0.redpanda.tdd-demo.svc.cluster.local:9092 \
  ...
```

The port-forward must use the same port number as the advertise-addr (`9092`, not an arbitrary local port like `19092`) because the client will reconnect using that exact port.

---

## 3 · Go workspace child modules need an explicit `replace` for Kaniko

**Symptom**: `mvn package` or `go build` inside Kaniko (`GOWORK=off`) can't resolve a sibling Go module: `unknown revision pkg/foo/v0.0.0`.

**Root cause**: `go.work` only resolves local sibling modules when workspace mode is active. Kaniko build sets `GOWORK=off` to make builds reproducible; the child module's `go.mod` has no `replace` directive so Go tries to fetch the sibling from the registry (which doesn't have it).

**Fix**: Add explicit `replace` directive in each consumer's `go.mod`, pointing at the relative path. Workspace still works in dev; Kaniko works in CI.

```go
// go.mod
require gitlab.addx.ai/engineering/tdd-platform-demo/pkg/kafkacolor v0.0.0
replace gitlab.addx.ai/engineering/tdd-platform-demo/pkg/kafkacolor => ../pkg/kafkacolor
```

---

## 4 · Non-root container + fresh EBS PVC needs `fsGroup`

**Symptom**: Redpanda (`UID/GID 101`) crashloops on first start with `mkdir failed: Permission denied [/var/lib/redpanda/data/crash_reports]`.

**Root cause**: EBS gp3 PVCs are root-owned by default on first attach. A non-root container can't create subdirectories in the mount.

**Fix**:
```yaml
spec:
  template:
    spec:
      securityContext:
        fsGroup: 101                       # match container's group
        fsGroupChangePolicy: OnRootMismatch # chown only once, not every restart
```

Applies to **any** non-root image + RWO PVC (mailpit uses in-memory store so doesn't trip this; Redpanda + ClickHouse + RabbitMQ all do).

---

## 5 · `go.mod` directive gates ServeMux method-pattern routes

**Symptom**: A Go service with `mux.HandleFunc("GET /health", ...)` returns 404 on `/health` even when compiled with Go 1.25.

**Root cause**: Go's `net/http.ServeMux` method-aware pattern syntax (the `"METHOD /path"` form) was added in 1.22. The Go language version is gated by `go 1.X` in `go.mod`, not the compiler's version. With `go 1.21` in `go.mod`, the runtime treats `"GET /health"` as a literal path that contains a space — no request will match. Liveness probe keeps killing the pod.

**Fix**: Bump `go.mod` directive to `1.22` or later. Same compiler, different runtime gate.

---

## 6 · mailpit `/api/v1/search` does NOT index custom SMTP headers

**Symptom**: An SMTP message with `X-Addx-Run-Id: foo` lands in mailpit (`/api/v1/messages` lists it; `/api/v1/message/{id}/raw` shows the header verbatim) but `/api/v1/search?query=header:X-Addx-Run-Id:foo` returns `count: 0`.

**Root cause**: mailpit's full-text search covers `subject`, `body`, `from`, `to` only. Custom headers are visible only through the separate `/api/v1/message/{id}/headers` endpoint, not searchable.

**Fix** (in `tdd-testbase-go` / `tdd-testbase-java` MailSink): list `/api/v1/messages?limit=200` then `GET /api/v1/message/{id}/headers` per message, filter `X-Addx-Run-Id` client-side. Slower but works for demo-scale inbox. Production-scale alternative: bake the run-id into the Subject line so `query=<runid>` works in one shot.

---

## 7 · cn-staging firewalls Docker Hub — every image must come from Harbor `base/`

**Symptom**: Copying the demo to cn-eks-staging, the build/test/hook pods hang on `ImagePullBackOff` or `ErrImagePull` for `docker.io/...` images (golang test image, telepresence helm-hook `curlimages/curl` + `busybox`), even though the cluster's `harbor-registry-secret` carries valid Docker Hub creds.

**Root cause**: AWS China blocks docker.io at the network layer. Auth is irrelevant — the route itself is gone. us/eu reach Docker Hub fine, so a config that "works on us" silently breaks only on cn.

**Fix**: Every image a cn workload pulls must resolve to the local Harbor `base/` project (`harbor-80144-cn-staging.addx.live/base/<img>`), and the tag must be pre-synced via `DEV/base-images` (`images.yaml` MR → fleet sync). Concrete cases hit this session:
- L2.DevLink CI region jobs: image `${HARBOR_REGISTRY}/base/golang:1.25-alpine`, NOT `docker.io/library/golang` (the plain test jobs use docker.io and only ever ran on a generic runner — they'd `ImagePullBackOff` if pinned to a cn-staging runner).
- telepresence helm hooks: override `hooks.{curl,busybox}.registry` to Harbor `base/`; added `curlimages/curl:8.1.1` + `busybox:1.36` to base-images first.

**Where**: `DEV/base-images/images.yaml` (sync source) + per-cluster telepresence values-override (`hooks.*.registry`). New base image won't exist in a freshly-bootstrapped cluster's Harbor until the per-cluster `sync-*` job runs — see [base-images sync trigger on new cluster].

---

## 8 · telepresence chart hard-depends on cert-manager (fresh cluster)

**Symptom**: Deploying the telepresence Application to a cluster that's never had it, the app sits `OutOfSync / Missing` with `one or more synchronization tasks are not valid (retried 5 times)`; no traffic-manager pod appears.

**Root cause**: The chart uses `agentInjector.certificate.method: certmanager` for the agent-injector webhook TLS, so the render needs `cert-manager.io/v1` CRDs + cainjector present. A new staging cluster (eu/cn) didn't have cert-manager installed.

**Fix**: Install cert-manager FIRST (its own values-override + ArgoCD Application, mirroring us-staging), let it go Synced/Healthy, then telepresence renders. cert-manager v1.16.2 base images are already fleet-synced, so it lands fast once the Application exists. Sequence: cert-manager → telepresence → tdd-demo services.

---

## 9 · gitlab-runner amd64+arm64 managers must NOT share a runner token

**Symptom**: A demo (or real-service) CI job tagged `<cluster>-amd64` sometimes produces an **arm64** image; the resulting pod CrashLoopBackOffs with `exec /<binary>: exec format error`. Intermittent — passes on retry maybe half the time.

**Root cause**: The `gitlab-runner` chart historically had only the amd64 instance emit one shared `gitlab-runner-token` Secret; the arm64 manager mounted the same Secret and both registered as the **same** GitLab runner identity carrying both arch tags, racing the same job queue. The arm64 manager could pick an amd64-tagged job and spawn an arm64 worker → arm64 image. Bit `tdd-platform-demo!16` directly during the eu rollout.

**Fix**: Opt in to per-arch runner identities — chart `runnerTokenExternalSecret.archSuffixed: true` so each arch emits `gitlab-runner-token-<arch>` reading a distinct vault property + registers as a separate runner with only its own arch tag (`run_untagged=false`). Fixed on us/eu/cn-staging + cn-dev (`DEV/k8s!754` / `!760`). Full detail + the per-cluster vault double-write caveat (vault-us.builder vs vault-eu.builder are separate servers): see DEV/k8s memory `gitlab-runner-per-arch-token-separation`.

---

## (Bonus, language-specific) Java record component access

**Symptom**: Maven compile error porting a Go struct literally into a Java `record`:
```
RunIdResolver.java:[65,25] branch has private access in ai.addx.testbase.RunIdResolver.Resolved
```

**Root cause**: Java records expose their components as **methods**, not fields. Coming from Go you'll write `r.branch` (field-style); the record gives you `r.branch()`.

**Fix**: change every `record.field` → `record.field()`. Caught on first build; harmless but boring.
