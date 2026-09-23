# Channel Canvas excerpt: GitLab role routing

The surrounding Canvas may contain normal domain context. Keep exactly one
versioned routing block. Channel admins edit `route_id`, the complete trusted
Bridge header prefix, the code-owned `role` id, and a short reason. Agent display
names, pubkeys, prompts, skills, and SaaS scopes stay in the reviewed Agent setup
configuration, not in Canvas.

The route gate reads the raw kind `40100` envelope, recomputes the NIP-01 event
id, verifies its BIP-340 signature, and then requires the latest visible author's
pubkey to be present in the code-owned admin allowlist. A newer untrusted or
invalid Canvas fails closed; it never revives an older trusted policy.

<!-- gitlab-buzz-routing:v1 -->
| route_id | trigger_prefix | role | reason |
| --- | --- | --- | --- |
| feature-ready | `[gitlab-notify:v1][object:issue][type:feature][status:ready][state:opened][change:routing]` | `feature` | `feature+ready` |
<!-- /gitlab-buzz-routing -->
