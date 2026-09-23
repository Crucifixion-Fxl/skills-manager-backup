# Telepresence Intercept Onboarding — TDD Platform

This is the "from zero to a working L2.DevLink intercept" walk-through for the A4x TDD demo (`tdd-demo` namespace). As of Phase 2b (2026-05-28+) the demo runs on **three staging clusters** — us-eks-staging, eu-eks-staging, cn-eks-staging — and the first real pilot service (personalization-engine) is live on us-staging. Pick whichever cluster you're targeting; the flow is identical, only the `--context` and Harbor registry change. The examples below default to **us-eks-staging**; the eu / cn context ARNs are listed under [Pick your cluster](#pick-your-cluster).

> Heads-up: this doc is the **interactive** intercept path (tunnel cluster traffic to your laptop). The **automated** L2.DevLink regression path (testbase baggage through the real chain, in CI) is a different thing keyed on `addx.run-id` / `X-Addx-Run-Id` — see the table in [README.md](README.md). Don't mix the two header models.

## What this gets you

> A request hitting the cluster-side `entitlement-service` Service whose
> `X-Addx-Dev-Id` header equals your dev token gets reverse-tunneled to a
> handler running on your laptop. All other requests keep flowing to the
> in-cluster pod. Multi-tenant safe (multiple devs concurrent intercept
> on the same target with no cross-routing — Phase 2a W1 spike proved).

## Prerequisites

| Item | Why | How |
|---|---|---|
| kubectl access to the target staging cluster | telepresence rides on K8s API server port-forward to reach traffic-manager | `aws eks update-kubeconfig --name us-eks-staging --region us-east-1` (eu: `--name eu-eks-staging --region eu-central-1`; cn: `--name cn-eks-staging --region cn-north-1 --profile <cn-profile>`) then `kubectl auth can-i create pods/portforward --namespace tdd-demo` should be `yes`. If not, ops needs to add your IAM identity to `aws-auth` ConfigMap |
| telepresence client v2.27.4 | The OSS CLI | `curl -fL https://github.com/telepresenceio/telepresence/releases/download/v2.27.4/telepresence-linux-amd64 -o ~/.local/bin/telepresence && chmod +x $_` (mac/windows variants on releases page) |
| `ADDX_DEV_ID` env on your machine | The triple-key contract (impl notes D-1 v0.13): `addx.run-id` traces a test run; `addx.dev-id` routes intercepts; `user_id` is Kafka partition. Header value is exact-match in OSS (no regex) so dev-id must be stable per dev | `echo 'export ADDX_DEV_ID="$(git config user.name | tr A-Z a-z | tr -c a-z0-9- -)"' >> ~/.bashrc` |
| sudo on your laptop | The telepresence root daemon needs a TUN device for cluster DNS / VIF. WSL: `sudo` works as long as you have a sudo password configured | n/a |

## Pick your cluster

`tdd-demo` (traffic-manager + agent-injector webhook + cert-manager) is deployed via ArgoCD on all three staging clusters. Set `CTX` to your target and reuse it below:

```bash
# us-eks-staging (default — first pilot lives here)
CTX=arn:aws:eks:us-east-1:390709477306:cluster/us-eks-staging
# eu-eks-staging
CTX=arn:aws:eks:eu-central-1:390709477306:cluster/eu-eks-staging
# cn-eks-staging (AWS China, account 801447536674)
CTX=arn:aws-cn:eks:cn-north-1:801447536674:cluster/cn-eks-staging
```

## One-time setup (cluster)

The traffic-manager + agent-injector webhook are already deployed in `tdd-demo` (Phase 2b W7 GitOps-ified the chart; cert-manager is a hard prereq — see [pitfalls.md](pitfalls.md)). Scoped strictly to that namespace via `telepresence-oss.namespaces: [tdd-demo]`; no impact on other tenants. You do **not** need to install anything.

```bash
# Confirm it's there on your chosen cluster
kubectl --context "$CTX" \
  -n tdd-demo get deploy traffic-manager
kubectl --context "$CTX" \
  get mutatingwebhookconfiguration agent-injector-webhook-tdd-demo
```

## Connect + intercept

```bash
# 1. Bring up the daemons (one-time per laptop session)
sudo -E KUBECONFIG="$HOME/.kube/config" \
  telepresence connect \
  --manager-namespace tdd-demo \
  --context "$CTX" \
  --namespace tdd-demo

# 2. Run your local handler on the port the cluster Service points at
#    (entitlement-service Service: 80 -> 8081 on the pod). The intercept
#    --port flag is local-port:service-port.
python3 -m http.server 8081 &     # or whatever local impl you wrote

# 3. Open a header-filtered intercept. ADDX_DEV_ID is your stable token.
sudo -E KUBECONFIG="$HOME/.kube/config" \
  telepresence intercept entitlement-service \
  --http-header "X-Addx-Dev-Id=$ADDX_DEV_ID" \
  --port 8081:80

# 4. Test from inside the cluster (or from your laptop via cluster DNS,
#    which telepresence-connect already set up).
curl -X POST \
  -H "X-Addx-Dev-Id: $ADDX_DEV_ID" \
  -H 'X-Addx-Run-Id: my-local-test-001' \
  -d '{"user_id":"u-test-1","plan":"vip"}' \
  http://entitlement-service.tdd-demo:80/grant
# ↑ This request is now flowing to YOUR LAPTOP. A request without the header
#   still goes to the cluster pod.

# 5. Release the intercept when done.
sudo -E KUBECONFIG="$HOME/.kube/config" \
  telepresence leave entitlement-service
```

## Multi-tenant guarantees (W1 spike result)

- Two simultaneous intercepts on the same Argo Rollout with different `X-Addx-Dev-Id` values do not cross-route. Concurrent traffic stress (15 mixed requests) showed zero leakage.
- traffic-manager is the **control plane only** — actual request fork happens at the per-pod `traffic-agent` sidecar. If traffic-manager crashes, existing intercepts keep working briefly (agents cache rules); new intercepts can't be set up until it recovers. Business traffic never flows through traffic-manager so a manager outage doesn't break anyone.
- `intercept.allowGlobalIntercepts: false` is set on the chart values — any intercept must carry an HTTP header / path filter, preventing a dev from accidentally catching everyone's traffic.

## Common traps

See [pitfalls.md](pitfalls.md). The two that bite first:

- **No regex** — `--http-header X-Addx-Dev-Id=~qlv-.*` looks like it should filter on a prefix but OSS exact-matches the literal string. Use stable `ADDX_DEV_ID` not nonce-based run-ids.
- **First intercept on a Rollout is slow** — agent-injector injects the sidecar via mutating webhook on the next pod create. Argo Rollout has to spin a new pod with the sidecar in. Subsequent intercepts (~few seconds) are fast because the sidecar persists.

## When this isn't enough (fall-back)

If kubectl access isn't possible (regulated environment, lost VPN), fall back to port-forward + local httptest:

```bash
kubectl --context "$CTX" \
  -n tdd-demo port-forward svc/entitlement-service 18081:80
# In your Go/Java test, set ENTITLEMENT_SERVICE_URL=http://127.0.0.1:18081
```

This loses the multi-tenant property (port-forward is a 1:1 tunnel) but it works without telepresence. (For the *automated* CI path you don't port-forward at all — the `l2-devlink-staging-{us,eu,cn}` jobs run inside the cluster and hit `entitlement-service.tdd-demo.svc.cluster.local` directly; see [README.md](README.md).)

## Cross-links

- spike result: `DEV/k8s/docs/plans/2026-05-18-telepresence-multi-tenant-spike-result.md`
- triple-key decision (`addx.dev-id` routing vs `addx.run-id` tracing vs `user_id` partition): `DEV/k8s/docs/plans/2026-05-09-tdd-platform-impl-notes.md` §7 D-1 (v0.13)
- staging-us migration playbook (copy demo → real service; PE first + 7 pitfalls): `DEV/k8s/docs/plans/2026-05-24-staging-us-migration-playbook.md`
- automated per-region L2.DevLink CI: `engineering/tdd-platform-demo/.gitlab-ci.yml` (`l2-devlink-staging-{us,eu,cn}`), `services/personalization-engine/.gitlab-ci.yml` (`test-l2-devlink`)
- Phase 4 OIDC prereq (when this onboarding flow scales to 80+ devs): `DEV/k8s/docs/plans/2026-05-18-dev-oidc-kubeconfig-prereq.md`
