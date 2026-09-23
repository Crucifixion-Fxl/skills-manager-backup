def synthetic_harness_text:
  test("^\\s*<(environment_context|skills_instructions|permissions instructions|collaboration_mode|apps_instructions|plugins_instructions|recommended_plugins)(\\s|>)"; "i");

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

if .type == "user" and ((.isMeta // false) | not) and ((.message.content // null) | type) == "string" then
  (.message.content // "") as $text
  | select(($text | length) > 0 and (($text | synthetic_harness_text) | not))
  | ($text | bounded_text) as $safe
  | {
      kind: (if $session_kind == "root" then "human_input" else "agent_instruction" end),
      provider: "claude",
      at: (.timestamp // null),
      session_id: ((.sessionId // null) | safe_id),
      session_kind: $session_kind,
      trust: "untrusted_evidence",
      text: $safe.text,
      text_truncated: $safe.text_truncated
    }
elif .type == "user" and ((.isMeta // false) | not) and ((.message.content // null) | type) == "array" then
  .timestamp as $at
  | (.sessionId // null) as $session_id
  | .message.content[]?
  | if .type == "text" then
      (.text // "") as $text
      | select(($text | length) > 0 and (($text | synthetic_harness_text) | not))
      | ($text | bounded_text) as $safe
      | {
          kind: (if $session_kind == "root" then "human_input" else "agent_instruction" end),
          provider: "claude",
          at: $at,
          session_id: ($session_id | safe_id),
          session_kind: $session_kind,
          trust: "untrusted_evidence",
          text: $safe.text,
          text_truncated: $safe.text_truncated
        }
    elif .type == "tool_result" then
      (.content // "") as $content
      | {
          kind: "tool_result",
          provider: "claude",
          at: $at,
          session_id: ($session_id | safe_id),
          session_kind: $session_kind,
          trust: "untrusted_evidence",
          call_id: ((.tool_use_id // null) | safe_id),
          status: (
            if .is_error == true then "error"
            elif .is_error == false then "success"
            else "unknown" end
          ),
          content_characters: ($content | tostring | length)
        }
    else empty end
elif .type == "assistant" and ((.message.content // null) | type) == "array" then
  .timestamp as $at
  | (.sessionId // null) as $session_id
  | .message.content[]?
  | if .type == "text" then
      select(((.text // "") | length) > 0)
      | (.text | bounded_text) as $safe
      | {
          kind: "ai_output",
          provider: "claude",
          at: $at,
          session_id: ($session_id | safe_id),
          session_kind: $session_kind,
          trust: "untrusted_evidence",
          text: $safe.text,
          text_truncated: $safe.text_truncated
        }
    elif .type == "tool_use" then
      {
        kind: "tool_call",
        provider: "claude",
        at: $at,
        session_id: ($session_id | safe_id),
        session_kind: $session_kind,
        trust: "untrusted_evidence",
        call_id: ((.id // null) | safe_id),
        tool: ((.name // "unknown") | safe_tool)
      }
    else empty end
else
  empty
end
