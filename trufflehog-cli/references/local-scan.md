# 本地扫描

用于开发机或本地已检出仓库的密钥自查。

## 适用场景

- “本地扫一下这个仓库”
- “检查当前工作区有没有泄露”
- “本地 Git 历史有没有旧密钥”

## 默认范围

除非用户明确要求最小扫描，否则默认执行两步：

1. `trufflehog filesystem .`
2. `trufflehog git file://<repo-name>`

这能覆盖：

- 当前工作区文件
- 本地 Git 历史对象

这不等于“显式覆盖所有分支”。

## 命令示例

### Linux / macOS

```bash
REPO_NAME="$(basename "$PWD")"
SCAN_DIR="${TMPDIR:-/tmp}/trufflehog-local-$REPO_NAME"
mkdir -p "$SCAN_DIR"

trufflehog filesystem . \
  --json \
  --no-update \
  --results=verified,unknown \
  > "$SCAN_DIR/filesystem.json"

(
  cd ..
  trufflehog git "file://$REPO_NAME" \
    --json \
    --no-update \
    --results=verified,unknown \
    > "$SCAN_DIR/git-history.json"
)
```

### Windows PowerShell

```powershell
$RepoName = Split-Path -Leaf (Get-Location)
$ScanDir = Join-Path $env:TEMP "trufflehog-local-$RepoName"
New-Item -ItemType Directory -Force $ScanDir | Out-Null

trufflehog filesystem . --json --no-update --results=verified,unknown |
  Out-File -Encoding utf8 (Join-Path $ScanDir "filesystem.json")

Push-Location ..
trufflehog git "file://$RepoName" --json --no-update --results=verified,unknown |
  Out-File -Encoding utf8 (Join-Path $ScanDir "git-history.json")
Pop-Location
```

## 结果解读

- 先处理 `verified`
- 再复核 `unknown`
- 不要把 `Verified: false` 当成“安全”
- 修复后要复扫再关单
