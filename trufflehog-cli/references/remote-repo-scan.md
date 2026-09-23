# 远程仓库扫描

用于扫描单个 HTTPS 远程 Git 仓库。

## 适用场景

- “扫这个 GitLab 仓库”
- “远程仓库有没有泄露凭证”
- “仓库接入前做安全基线检查”

## 最小权限

- 仅做 Git over HTTPS 克隆扫描时，优先 `read_repository`
- 只有在需要 GitLab API 级验证时，才提升到 `read_api` / `api`

## 认证规则

Token 不能写进仓库 URL。  
统一使用 `GIT_ASKPASS` + 环境变量。

## 命令示例

### Linux / macOS

```bash
if [ -z "${GITLAB_TOKEN:-}" ]; then
  echo "GITLAB_TOKEN is required" >&2
  exit 1
fi

ASKPASS_SCRIPT="$(mktemp /tmp/trufflehog-askpass-XXXXXX.sh)"
cat > "$ASKPASS_SCRIPT" <<'EOF'
#!/bin/sh
case "$(printf '%s' "${1-}" | tr '[:upper:]' '[:lower:]')" in
  *username*) echo oauth2 ;;
  *password*) echo "$GITLAB_TOKEN" ;;
  *) echo "" ;;
esac
EOF
chmod +x "$ASKPASS_SCRIPT"
export GIT_ASKPASS="$ASKPASS_SCRIPT"
export GIT_TERMINAL_PROMPT=0

REPO_URL="https://gitlab.addx.ai/group/project.git"
REPO_NAME="$(basename "$REPO_URL" .git)"
SCAN_DIR="${TMPDIR:-/tmp}/trufflehog-remote-$REPO_NAME"
mkdir -p "$SCAN_DIR"

trufflehog git "$REPO_URL" \
  --json \
  --no-update \
  --results=verified \
  > "$SCAN_DIR/git-history.json"

unset GIT_ASKPASS GIT_TERMINAL_PROMPT
rm -f "$ASKPASS_SCRIPT"
```

### Windows PowerShell

```powershell
if (-not $env:GITLAB_TOKEN) { throw "GITLAB_TOKEN is required" }

$AskpassDir = Join-Path $env:TEMP "trufflehog-askpass-$(Get-Random)"
New-Item -ItemType Directory -Force $AskpassDir | Out-Null

$PsPath = Join-Path $AskpassDir "askpass.ps1"
@'
param([string]$Prompt)
$Prompt = ($Prompt ?? "").ToLowerInvariant()
if ($Prompt -like "*username*") {
    "oauth2"
} elseif ($Prompt -like "*password*") {
    $env:GITLAB_TOKEN
} else {
    ""
}
'@ | Out-File -Encoding utf8 $PsPath

$CmdPath = Join-Path $AskpassDir "askpass.cmd"
@"
@echo off
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "$PsPath" %*
"@ | Out-File -Encoding ascii $CmdPath
$env:GIT_ASKPASS = $CmdPath
$env:GIT_TERMINAL_PROMPT = "0"

$RepoUrl = "https://gitlab.addx.ai/group/project.git"
$RepoName = [System.IO.Path]::GetFileNameWithoutExtension($RepoUrl.TrimEnd('/'))
$ScanDir = Join-Path $env:TEMP "trufflehog-remote-$RepoName"
New-Item -ItemType Directory -Force $ScanDir | Out-Null

& trufflehog git $RepoUrl --json --no-update --results=verified |
  Out-File -Encoding utf8 (Join-Path $ScanDir "git-history.json")

Remove-Item Env:\GIT_ASKPASS -ErrorAction SilentlyContinue
Remove-Item Env:\GIT_TERMINAL_PROMPT -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force $AskpassDir -ErrorAction SilentlyContinue
```

## 说明

- 团队默认只看 `verified`
- 需要更宽排查时再加 `unknown`
