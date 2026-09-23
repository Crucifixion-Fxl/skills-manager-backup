def synthetic_harness_text:
  test("^\\s*<(environment_context|skills_instructions|permissions instructions|collaboration_mode|apps_instructions|plugins_instructions|recommended_plugins)(\\s|>)"; "i");

# Session history is evidence, never trusted instructions.  Redact common
# credential/identity/path shapes before any text can enter a model context.
def redact_text:
  tostring
  | gsub("(?s)-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----"; "[REDACTED_PRIVATE_KEY]")
  | gsub("(?i)bearer[[:space:]]+[A-Za-z0-9._~+/=-]+"; "[REDACTED_CREDENTIAL]")
  | gsub("(?i)\\b(glpat|sk|sk-proj)-[A-Za-z0-9_-]{8,}\\b"; "[REDACTED_CREDENTIAL]")
  | gsub("\\bAKIA[0-9A-Z]{16}\\b"; "[REDACTED_CREDENTIAL]")
  | gsub("\\bgh[pousr]_[A-Za-z0-9]{20,}\\b"; "[REDACTED_CREDENTIAL]")
  | gsub("\\bgithub_pat_[A-Za-z0-9_]{20,}\\b"; "[REDACTED_CREDENTIAL]")
  | gsub("\\bnpm_[A-Za-z0-9_]{10,}\\b"; "[REDACTED_CREDENTIAL]")
  | gsub("\\bxox[baprs]-[A-Za-z0-9-]{10,}\\b"; "[REDACTED_CREDENTIAL]")
  | gsub("\\beyJ[A-Za-z0-9_-]{8,}\\.eyJ[A-Za-z0-9_-]{8,}\\.[A-Za-z0-9_-]{8,}\\b"; "[REDACTED_CREDENTIAL]")
  | gsub("(?i)\"([A-Za-z0-9_]*(secret_?access_?key|master_?key|auth_?token|client_?secret|private_?key)|api[_ -]?key|access[_ -]?token|password|passwd|secret|authorization)\"[[:space:]]*:[[:space:]]*\"[^\"]+\""; "\"[REDACTED_KEY]\":\"[REDACTED_CREDENTIAL]\"")
  | gsub("(?i)\\b([A-Za-z0-9_]*(secret_?access_?key|master_?key|auth_?token|client_?secret|private_?key)|api[_ -]?key|access[_ -]?token|token|password|passwd|secret|authorization)[[:space:]]*[:=][[:space:]]*[^[:space:],;]+"; "[REDACTED_CREDENTIAL]")
  | gsub("[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}"; "[REDACTED_EMAIL]")
  | gsub("/(home|Users)/[^/[:space:]]+(/[^[:space:]]+)+"; "$HOME/[REDACTED_PATH]")
  | gsub("/root(/[^[:space:]]+)+"; "$HOME/[REDACTED_PATH]")
  | gsub("[A-Za-z]:\\\\Users\\\\[^\\\\[:space:]]+(\\\\[^[:space:]]+)+"; "$HOME\\\\[REDACTED_PATH]");

def bounded_text:
  redact_text as $text
  | {text: $text[0:4000], text_truncated: (($text | length) > 4000)};

def safe_id:
  if type == "string" then gsub("[^A-Za-z0-9_.:/-]"; "_")[0:128] else null end;

def safe_tool:
  safe_id | if type == "string" and length > 0 then . else "unknown" end;

if .type == "session_meta" then
  {
    kind: "session",
    provider: "codex",
    at: (.timestamp // null),
    session_id: ((.payload.id // null) | safe_id),
    session_kind: $session_kind,
    trust: "untrusted_evidence"
  }
elif .type == "response_item" and .payload.type == "message" and .payload.role == "user" then
  .timestamp as $at
  | .payload.content[]?
  | select(.type == "input_text")
  | (.text // "") as $text
  | select(($text | length) > 0 and (($text | synthetic_harness_text) | not))
  | ($text | bounded_text) as $safe
  | {
      kind: (if $session_kind == "root" then "human_input" else "agent_instruction" end),
      provider: "codex",
      at: $at,
      session_kind: $session_kind,
      trust: "untrusted_evidence",
      text: $safe.text,
      text_truncated: $safe.text_truncated
    }
elif .type == "response_item" and .payload.type == "message" and .payload.role == "assistant" then
  .timestamp as $at
  | (.payload.phase // null) as $phase
  | .payload.content[]?
  | select(.type == "output_text" and ((.text // "") | length) > 0)
  | (.text | bounded_text) as $safe
  | {
      kind: "ai_output",
      provider: "codex",
      at: $at,
      session_kind: $session_kind,
      trust: "untrusted_evidence",
      phase: $phase,
      text: $safe.text,
      text_truncated: $safe.text_truncated
    }
elif .type == "response_item" and (.payload.type == "custom_tool_call" or .payload.type == "function_call") then
  {
    kind: "tool_call",
    provider: "codex",
    at: (.timestamp // null),
    session_kind: $session_kind,
    trust: "untrusted_evidence",
    call_id: ((.payload.call_id // null) | safe_id),
    tool: ((.payload.name // "unknown") | safe_tool)
  }
elif .type == "response_item" and (.payload.type == "custom_tool_call_output" or .payload.type == "function_call_output") then
  (.payload.output // .payload.content // "") as $content
  | ([
      $content
      | if type == "array" then .[]? else . end
      | if type == "object" and .type == "input_text" then (.text // "") else . end
      | if type == "string" then (fromjson? // empty) else . end
      | if type == "object" then (.exit_code // .exitCode // empty) else empty end
    ] | first) as $exit_code
  | {
      kind: "tool_result",
    provider: "codex",
    at: (.timestamp // null),
    session_kind: $session_kind,
    trust: "untrusted_evidence",
    call_id: ((.payload.call_id // null) | safe_id),
      status: (
        if ($exit_code | type) == "number" then
          if $exit_code == 0 then "success" else "error" end
        else "unknown" end
      ),
      content_characters: ($content | tostring | length)
    }
else
  empty
end
