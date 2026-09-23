# Concise runtime guidance

Use the root `SKILL.md` as the normal prompt. Load only the reference needed for the current step:

- [Research method](research-method.md) for decision framing, VOC, questionnaire quality and inference.
- [Questionnaire design](questionnaire-design.md) when authoring a form, including stem skeletons for matched intents.
- [Project Typeform journey](typeform-research.md) for end-to-end execution or resume.
- [Host configuration](host-configuration.md) for the Personal API origin
  `https://audience-workflow-api-prod-us.addx.live` when a complete clone chooses a host;
  Hermes keeps the injected origin.
- [Platform API](platform-api.md) for exact named operations and evidence fields.
  For a native VOC report, load the publication section and deliver
  original voice text and Simplified Chinese Markdown in one
  `project_publish_report`. Do not submit `source_coverage`.
- [Errors and recovery](errors-and-recovery.md) after a safe failure or ambiguous effect.
- [API TDD loop](api-tdd-loop.md) for client or staging acceptance.

Keep interaction concise and driven by the user's goal plus authoritative API state. Confirm VOC collection, Typeform
create, audience materialization, and Brevo sync before those writes; invite optional extras such as channels and
keywords, and do not refuse because they are missing. The default provider path for new
Project Research is Typeform, followed when requested by Research materialization, Brevo sync and an unsent Campaign
Draft. Do not inject historical provider guidance into this path.
