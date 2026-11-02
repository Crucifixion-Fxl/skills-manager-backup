# Changed Tracking Event Publication Check

Read this reference only when the diff changes tracking event definitions, tracking versions, or event call sites.

For every changed event, verify that the exact application, version, and event are published before merge. Read both YAML/configuration changes and code call sites; a code-only tracking change still triggers the check.

1. Collect changed event IDs/names from tracking-spec blocks, YAML event config, calls such as `track(...)`, `logEvent(...)`, `Analytics.log(...)`, and changed event constants.
2. Collect the latest version from config, `spec_version`/`TRACKING_VERSION`, or the call site. A missing version blocks completion.
3. Run the `tracker-publish-check.js` script from the `tracking-lifecycle` skill with `--application=<app> --version=<version> --specs=<event1,event2,...>`.
4. All changed events must return `published` for the exact version and `releaseStatus=3 (RELEASED)`. `definedUnreleased`, `undefinedOnPlatform`, or a non-released version is a deterministic red line. Report the two event states separately because their remediation differs.

Do not skip this check because only code changed, because an event exists in a YAML file, or because the MR says it will be published later. Do not treat a platform definition or an AI coverage label as proof of publication.
