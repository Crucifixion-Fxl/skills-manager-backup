#!/usr/bin/env bash
# 通用微服务接入验证脚本
# 用法: verify.sh <platform> [project_root]
# 从 references/<platform>.md 的 checklist 表格中提取 auto 类型的验证规则并执行
#
# checklist 表格格式要求:
#   | # | 检查项 | verify（自动验证方法） | 类型 |
#   第3列包含 grep pattern，第4列为 auto/manual/optional

set -euo pipefail

PLATFORM="${1:?用法: verify.sh <platform> [project_root]}"
PROJECT_ROOT="${2:-.}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SPEC_FILE="$SCRIPT_DIR/${PLATFORM}.md"

if [[ ! -f "$SPEC_FILE" ]]; then
  echo "❌ 未找到平台规格: $SPEC_FILE"
  echo "可用平台:"
  ls "$SCRIPT_DIR"/*.md 2>/dev/null | xargs -I{} basename {} .md | grep -v '^$'
  exit 2
fi

PASS=0
FAIL=0
SKIP=0
MANUAL=0
REPORT=""

# grep 排除非代码目录和文件（数组形式，避免 glob 展开）
GREP_EXCLUDE=(
  --exclude-dir=vendor --exclude-dir=node_modules --exclude-dir=.git
  --exclude-dir=docs --exclude='*.md' --exclude='*.log'
)

# 检查单个 pattern 是否在代码中存在，返回匹配文件
# || true 防止 grep 无匹配时 exit 1 被 set -e/pipefail 捕获
grep_code() {
  grep -Erl "${GREP_EXCLUDE[@]}" "$1" "$PROJECT_ROOT" 2>/dev/null | head -1 || true
}

check() {
  local id="$1" desc="$2" type="$3"
  shift 3
  local patterns=("$@")  # 剩余参数都是 pattern（支持多个 AND 条件）

  case "$type" in
    manual)
      REPORT+="| $id | $desc | ⚠️ 待确认 | 需人工验证 |"$'\n'
      MANUAL=$((MANUAL + 1))
      return
      ;;
    optional)
      local opt_pass=true opt_file=""
      for p in "${patterns[@]}"; do
        local file
        file=$(grep_code "$p")
        if [[ -z "$file" ]]; then
          opt_pass=false
          break
        fi
        [[ -z "$opt_file" ]] && opt_file="$file"
      done
      if $opt_pass; then
        REPORT+="| $id | $desc | ✅ PASS | $opt_file |"$'\n'
        PASS=$((PASS + 1))
      else
        REPORT+="| $id | $desc | ⏭️ SKIP | 可选项，未实现 |"$'\n'
        SKIP=$((SKIP + 1))
      fi
      return
      ;;
  esac

  # auto 类型：所有 pattern 都必须通过（AND 关系）
  local all_pass=true
  local first_file=""
  local failed_pattern=""
  for p in "${patterns[@]}"; do
    local file
    file=$(grep_code "$p")
    if [[ -z "$file" ]]; then
      all_pass=false
      failed_pattern="$p"
      break
    fi
    [[ -z "$first_file" ]] && first_file="$file"
  done

  if $all_pass; then
    REPORT+="| $id | $desc | ✅ PASS | $first_file |"$'\n'
    PASS=$((PASS + 1))
  else
    REPORT+="| $id | $desc | ❌ FAIL | pattern: $failed_pattern |"$'\n'
    FAIL=$((FAIL + 1))
  fi
}

# 从 .md 的 checklist 表格中提取验证规则
# 格式: | # | 检查项 | verify | 类型 |
parse_and_run() {
  local in_checklist=false
  local header_skipped=false

  while IFS= read -r line; do
    # 检测 checklist 表格开始
    if [[ "$line" =~ ^\|.*#.*检查项.*verify ]]; then
      in_checklist=true
      header_skipped=false
      continue
    fi

    # 跳过分隔行 |---|---|---|---|
    local sep_regex='^\|[-| ]+\|$'
    if $in_checklist && [[ "$line" =~ $sep_regex ]]; then
      header_skipped=true
      continue
    fi

    # 表格结束
    if $in_checklist && $header_skipped && [[ ! "$line" =~ ^\| ]]; then
      in_checklist=false
      continue
    fi

    # 解析数据行
    if $in_checklist && $header_skipped && [[ "$line" =~ ^\| ]]; then
      # id 和 desc 用 awk 提取（前两列不含 | 歧义）
      # type 从行尾提取（避免 awk 被反引号内的 | 干扰）
      local id desc type verify_col
      id=$(echo "$line" | awk -F'|' '{gsub(/^ +| +$/,"",$2); print $2}')
      desc=$(echo "$line" | awk -F'|' '{gsub(/^ +| +$/,"",$3); print $3}')
      type=$(echo "$line" | sed 's/.*| *\([a-z]*\) *| *$/\1/')

      # 校验 type 合法性
      case "$type" in auto|manual|optional) ;;
        *)
          REPORT+="| $id | $desc | ⚠️ 未知类型 | type='$type'，请检查表格格式 |"$'\n'
          MANUAL=$((MANUAL + 1))
          continue
          ;;
      esac

      # 提取 verify 列：去掉 | id | desc | 前缀和 | type | 后缀
      verify_col=$(echo "$line" | sed 's/^|[^|]*|[^|]*|//; s/| *[a-z]* *| *$//')

      # 从 verify 列提取所有反引号中的 pattern（多个 = AND 关系）
      local patterns=()
      while IFS= read -r p; do
        [[ -n "$p" ]] && patterns+=("$p")
      done < <(echo "$verify_col" | grep -o '`[^`]*`' | tr -d '`')

      if [[ ${#patterns[@]} -eq 0 ]]; then
        # 无反引号 pattern，标记为需人工确认
        REPORT+="| $id | $desc | ⚠️ 无 pattern | verify 列缺少反引号包裹的 grep pattern，请补充 |"$'\n'
        MANUAL=$((MANUAL + 1))
      else
        # 预检：pattern 长度 < 3 的大概率是误提取
        local valid=true
        for p in "${patterns[@]}"; do
          if [[ ${#p} -lt 3 ]]; then
            REPORT+="| $id | $desc | ⚠️ 弱 pattern | pattern '$p' 过短（<3字符），可能误判 |"$'\n'
            MANUAL=$((MANUAL + 1))
            valid=false
            break
          fi
        done
        $valid && check "$id" "$desc" "$type" "${patterns[@]}"
      fi
    fi
  done < "$SPEC_FILE"
}

echo "=== ${PLATFORM} 接入验证 ==="
echo "规格文件: $SPEC_FILE"
echo "项目目录: $PROJECT_ROOT"
echo ""

parse_and_run

echo "| # | 检查项 | 结果 | 位置/说明 |"
echo "|---|--------|------|----------|"
echo -n "$REPORT"
echo ""
echo "结果: $PASS 通过, $FAIL 失败, $SKIP 跳过, $MANUAL 待确认"

if [[ $FAIL -gt 0 ]]; then
  echo ""
  echo "❌ 验证未通过，请修复上述 FAIL 项后重新运行:"
  echo "  bash $0 $PLATFORM $PROJECT_ROOT"
  exit 1
else
  echo ""
  echo "✅ 所有自动验证项通过"
  exit 0
fi
