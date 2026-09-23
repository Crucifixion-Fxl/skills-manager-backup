# Profile-root user aggregate Skill compatibility

The standalone client and Chinese semantic guide consume Audience !317
`d40108fe69eb5eeaa6879ca4e9aee7f513aa6e2e`, paired with DATA !3878.
This pins a candidate contract, not deployed or merged main. Package version
remains 0.8.1 pending separately authorized SkillHub release/version work.

Profile is the membership root with Platform-owned tenant/bundle scope. Reviewed
children correlate by global user_id; facts can be cross-business. Only advertised
one-level edges and named metrics are accepted. No raw SQL or scope parameters.
COUNT empty=0; other empty/all-null aggregates=NULL. No implicit coalesce.

The fixed client validates aggregate shape, advertised IDs/operators/types,
same-Project binding and nested-path rejection. Historical criteria readback
preserves aggregate meaning. The bundled OpenAPI and source lock match the exact
candidate above; existing operation count and authority do not change.

Verification: full offline unit suite, targeted aggregate request/capability/readback
and negative cases, secret scan, operation/source lock checks, lint and compile.
Fixtures do not prove Athena behavior or live deployment. Release sequence:
compatible DATA, Audience, tested canonical Skill MR, then separately approved
engineering/skills SkillHub version update. No runtime reload or publication here.

Main-session cross-repository verification: the canonical client validated all
64 actual Audience compiler fixtures against its emitted capabilities, followed
by the complete DATA sensor validator and local SQLite semantic execution:
64 positive cases passed and 384 malicious mutations were rejected. This uses
synthetic rows and is not live warehouse acceptance.
