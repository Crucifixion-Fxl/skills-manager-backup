# Project key secret-scan coverage

The deterministic repository secret gate recognized legacy Audience Personal
keys but omitted the current Project Personal key prefix. Recognize both v1 and
v2 with the existing audience_personal_key rule and token-tail threshold. Keep
findings limited to file path and rule name; never include the matched value.
This is a repository-content scan, not a credential validator or environment
scanner. Environment-only key injection remains outside its file scope.

CLI regressions construct disposable valid-format examples of both versions,
write them only into temporary scan roots, and require exit 1 with exactly the
safe path/rule finding and no secret in stdout or stderr. Separate cases keep
the same values solely in the environment and require an empty-root/safe-file
scan to pass without exposing them. No real keys or full fake literals are
stored in source files.

Move the existing legacy non-ASCII audience-name rejection input to an explicit
client test fixture. The decoded input and invalid_request_schema expectation
remain identical. Client code, request/response contracts, legitimate Audience
and Brevo result URLs, Skill instructions and references are unchanged. Add no
scanner exemptions and change no unrelated detection patterns.

Run the full canonical suite, source lock and deterministic secret gate, Ruff,
and the shared repository scoped security validator against an exact temporary
distribution. Report any remaining advisory warnings separately from errors.
