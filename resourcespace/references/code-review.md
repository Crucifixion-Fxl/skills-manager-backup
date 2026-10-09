# ResourceSpace Skill independent review

## 1. Scope and binding

Review mode: new-MR local pre-review; single agent, security/API-contract/quality/documentation perspectives. No source changes or live DAM writes. Final MR-related-MR verification is pending because no MR exists.

Live glab reads verified engineering/skills project1021 Issue249 is opened and assigned400; root marketing/marketing_automation project2080 Issue24 is opened and assigned400; project1021 Issue249 links server-side to2080#24 with native relates_to. Remote origin is engineering/skills. gitlab-mr Step1.25 explicitly permits new-MR pre-review on live Issues/native root-work link, with related-MR readback required after creation. This report does not claim formal MR binding, merge approval or release authorization.

## 2. Findings and reproduction

[P2][confirmed, FIXED] original client.mjs:68 rejected every application/json download, including authorized original .json assets (and JSON MIME for .gltf assets). Extension validation allows json; documented original download has no such exclusion. Local mock: get_resource_path -> same-origin /filestore/model.json; GET returns HTTP200 application/json with {"fixture":true}; perform(download,{ref:1,extension:'json',output:newPath}) throws COMMAND_EXEC instead of storing the original. Rejecting login HTML is appropriate; generic JSON content is not proof of an API error. Narrow this rejection or validate an explicit error envelope while preserving legitimate original MIME. No production format test is requested. Final correction removes application/json from the MIME rejection, keeps text/html login-page rejection, and adds a JSON glTF original download fixture. Reviewer reran current suite: JSON glTF passes together with existing HTML and partial-cleanup tests.

[P1][confirmed, FIXED] initial API failure check ignored official ajax_response_fail status:'fail'. Pure mock initially returned {added:true,collection:{ref:3}} after permission-denied fail response. Author changed failure predicate to ['error','fail'].includes(result?.status). Current fixture adds official fail; reviewer independently reran upload and add-to-collection mocks: both now throw COMMAND_EXEC before readback and suppress success flags. This finding is resolved in the reviewed disk candidate.

[refuted] acceptance reference was initially suspected absent: saas-access.md is inside references/, and its same-directory acceptance.json now exists; no broken-reference finding remains.

## 3. Security and behavior coverage

- Signed API uses exact encoded query input plus per-user key; key/sign URL retained in process. Transport/HTTP/HTML/failure errors suppress raw bodies and signed URLs; recursive known-key redaction handles returned access_key/API fields. Manual redirects prevent signed URLs from following another origin. Fixture validates named query, pagination and exact signature.
- Credentials are host injected; core client has Node built-ins only. Key-file lstat rejects symlinks and requires current uid/no group-other permissions. Independent local checks: 0600 accepted,0644 rejected,symlink rejected. No real key printed or copied into this review.
- Writes require apply=true; SKILL separately requires user task authorization, instance/ref/field checks, and prohibits treating apply as authorization. No automatic write retry; creation is archive=-2. Upload includes signed basename file_name, POST multipart, precise HTTP204 success and resource readback. Official false/null/fail/error all now fail. Collection membership needs documented separate search readback; collection() alone is not proof of member insertion.
- Download same-origin and URL-credential checks, wx creation,0600 mode,2GiB stream cap,SHA256 output and failure unlink are implemented. Fixtures verify no overwrite,cross-origin refusal,hash/mode,HTML refusal and failed-stream cleanup. Large-file throughput is explicitly unverified. JSON-original regression is resolved; application/json originals now remain supported.
- Adapter registers13 LOCAL commands and delegates to the shared client. Installer refuses differing existing adapters; only installation path/state is written, not SaaS operations. Skill/docs do not imply administrator role from status or browser-session identity.
- Source text/metadata is explicitly untrusted data. No delete/share/publication/permissions/cloud-backup commands. General Skill lives in company plugin, Marketing instance credentials/business labels remain host/business-owned.

## 4. Evidence and documentation

Reviewer executed current Node24.14.0 fixture suite:10/10 pass,exit0 (final increment). Additional ephemeral local mocks verified both failed-write paths,private-key permissions and legitimate-JSON rejection. Runtime13-command/local-synthetic acceptance is recorded by owner in references/acceptance.json,not re-executed by reviewer. Owner reports OpenCLI validate13,security validation0,catalog check and19 layout/platformscan tests; these are owner evidence,not independent executions here. Runtime/production/large-file/CDN/version boundaries are honestly stated.

Non-release pre-review. Production L3/release approval is deferred and does not follow from local fixture success. Formal MR reviewer/binding remains pending. API/authorization semantics are covered; generated catalogue/index pages are owner-generated and owner-check evidence only,not independently regenerated by this reviewer.

## 5. Decision

Review state: complete for final snapshot. Code pre-review PASSED (8.5/10); no unresolved confirmed blockers. P1 official status fail and P2 JSON-original regression are both fixed and retained as regression history. Final 10-fixture suite is independently green, including no-readback on official fail, JSON glTF download, HTML refusal and stream failure cleanup. Formal MR binding/reviewer/readback remains pending and is not implied by this code conclusion. No commit, push, merge, release or live DAM write was performed by reviewer.

Reviewed UTC: 2026-10-09T07:15:59.655096+00:00

Final candidate SHA256:

- `skills/collaboration/resourcespace/SKILL.md`: `39317160ecdcfa778922dab18c3b25cbb2898eaf69769b80db2d8d2c183c1075`
- `skills/collaboration/resourcespace/scripts/cli.mjs`: `943cbc45d195c298306232064a567c6570c71cde5bd79912f27c8a3a9d678f39`
- `skills/collaboration/resourcespace/scripts/client.mjs`: `b152929c949589a2b081c7aaac19a170ebc7e27bd48f771e7e0237999c37e35f`
- `skills/collaboration/resourcespace/scripts/install.mjs`: `8fdce18a81dbcb8f2374d5c342949809d55c7a6ddd49e9c1f1582b258e1de1cf`
- `skills/collaboration/resourcespace/references/saas-access.md`: `87961adb89cf05306d7c7779c70e9127c4c519ffce2409b9758a8e349c4cc458`
- `skills/collaboration/resourcespace/references/acceptance.json`: `483cdcdfb6f748426e01892c15a23d30fda9466f173f8623bde649a514aed0c7`
- `skills/collaboration/resourcespace/references/auth-profile.json`: `47fca5c358aea8c774ee935d02415c78d22f0d3291f279e55227b79579c8a8f4`
- `skills/collaboration/resourcespace/tests/client.test.mjs`: `f08e7b12548da66266edef1c7573933fe798ff8b2da44d90c6be7ea42bcabf43`
- `AGENTS.md`: `c398afbd926735cbcf34ccd3882a0c6f0d29977a3af3e06bb3e229195e0c9faa`
- `skills/agent-harness/platform-onboarding/references/registry.json`: `347c8bb9c78a5773fd231e51fe241298e5fc09a7c0675c49ed11d4b64a98fb67`
- `docs/collaboration/resourcespace-opencli.md`: `61e4e129dd2cc9ff2d4f6bf42b6aff7e729c97aeabed95fb707a914efb535d0a`
