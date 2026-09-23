# Physical/video security and shared IoT joint assessment

Assess these concerns together before proposing any project. Do not start by creating two repositories, and do not infer final English slugs or boundary keys from legacy directory names.

Keep two candidate hypotheses visible:

- a `stable-business-domain` candidate for durable physical/video security outcomes, vocabulary, policies, and cross-repository product workflows;
- a `shared-platform-domain` candidate for reusable IoT platform contracts, device/cloud compatibility, adoption, and platform release governance.

Compare both hypotheses against every non-archived catalog boundary and every source-repository reference. Repository overlap is expected and is not proof that the candidates should merge or split. Determine whether each hypothesis has an explicit Owner, independent roadmap/lifecycle, durable includes/excludes, distinct consumer/provider responsibility, and cross-repository governance need.

Use [references/fixtures/security-iot-joint-assess.yaml](fixtures/security-iot-joint-assess.yaml) as a positive `ASSESS` input. Its null names and boundary keys are intentional. A valid result preserves both candidate labels, reports existing VAS/Nature/Quality Engineering repository references, keeps `creation_authorized` false, and lists final Owner/name/boundary decisions as unresolved.

If evidence later shows one candidate is source-owned detail, an extension of an existing workspace, or lacks an independent Owner and roadmap, route it accordingly. Do not force two proposals merely because two candidate types were identified during discovery.
