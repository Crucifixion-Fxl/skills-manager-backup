---
name: terraform-audit
description: Audit Terraform codebases for security, cost, code quality, and architecture issues. Use when reviewing Terraform projects, checking infrastructure-as-code compliance, or assessing AWS resource configurations for best practices.
---

# Terraform Audit Skill

Perform a comprehensive audit of a Terraform codebase across four dimensions: security & compliance, cost optimization, code quality, and architecture design. The audit produces a structured Markdown report with findings classified by severity. Rules are grounded in the CIS AWS Foundations Benchmark and the AWS Well-Architected Framework, with a primary focus on AWS resources.

## Description

对 Terraform 代码库进行全量审计，覆盖安全合规、成本优化、代码质量、架构设计四个维度。基于 CIS AWS Foundations Benchmark 和 AWS Well-Architected Framework，针对 AWS 资源输出结构化 Markdown 审计报告，按 Critical / Important / Minor 三级严重度分类。

---

## 执行要点

- 审计前先扫描项目结构，根据 `.tf` 文件数量判断规模（Small / Medium / Large），选择对应策略
- 四个维度按顺序执行：安全 → 成本 → 质量 → 架构，每个维度读取对应子模块 checklist
- 每条发现必须标注严重等级、涉及文件与行号、具体修复建议（含 HCL 代码）
- 报告使用 [report-template.md](report-template.md) 模板，保存到项目根目录
- 跨维度存在互补规则时（如 Multi-AZ 在架构和成本中），需标注交叉引用上下文

---

## 示例

### ❌ Bad

```hcl
# S3 bucket 无加密、无版本控制、公开访问
resource "aws_s3_bucket" "data" {
  bucket = "my-data-bucket"
}

resource "aws_s3_bucket_public_access_block" "data" {
  bucket                  = aws_s3_bucket.data.id
  block_public_acls       = false
  block_public_policy     = false
  ignore_public_acls      = false
  restrict_public_buckets = false
}
```

### ✅ Good

```hcl
resource "aws_s3_bucket" "data" {
  bucket = "${var.project}-${var.environment}-data"
}

resource "aws_s3_bucket_versioning" "data" {
  bucket = aws_s3_bucket.data.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "data" {
  bucket = aws_s3_bucket.data.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.main.arn
    }
  }
}

resource "aws_s3_bucket_public_access_block" "data" {
  bucket                  = aws_s3_bucket.data.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
```

---

## Audit Workflow

Copy this checklist and update it as you progress:

```
Audit Progress:
- [ ] Step 1: Scan project structure
- [ ] Step 2: Security & compliance audit
- [ ] Step 3: Cost optimization audit
- [ ] Step 4: Code quality audit
- [ ] Step 5: Architecture design audit
- [ ] Step 6: Generate report
```

---

## Step 1: Scan Project Structure

Use Glob to find all `**/*.tf` files in the target project. Then determine:

- **Module structure**: Identify root module, nested modules, and shared/reusable modules.
- **Backend configuration**: Check for remote state backend (S3, GCS, Terraform Cloud, etc.) and state locking.
- **Provider usage**: List providers and their version constraints.
- **Terraform version**: Check `required_version` in `terraform {}` blocks.
- **Project scale**:
  - **Small**: < 10 `.tf` files — audit all files in a single pass.
  - **Medium**: 10-30 `.tf` files — group by module, audit each module.
  - **Large**: 30+ `.tf` files — prioritize root module and shared modules first, then environment-specific configs.

Record the scale; it determines the audit strategy in later steps.

---

## Step 2: Security & Compliance Audit

Read [security-checklist.md](security-checklist.md) and apply each rule to the scanned codebase.

**Categories covered**: IAM policies & roles, S3 bucket configuration, networking (security groups, NACLs, public access), encryption at rest and in transit, logging & monitoring, secrets management.

Classify every finding as **Critical**, **Important**, or **Minor** per the severity table below.

---

## Step 3: Cost Optimization Audit

Read [cost-optimization.md](cost-optimization.md) and apply each rule to the scanned codebase.

**Categories covered**: Compute right-sizing, storage tiering & lifecycle, database instance sizing & reserved capacity, networking costs (NAT gateways, data transfer), tagging & cost governance.

Classify every finding as **Critical**, **Important**, or **Minor**.

---

## Step 4: Code Quality Audit

Read [code-quality.md](code-quality.md) and apply each rule to the scanned codebase.

**Categories covered**: Modularity & reuse, naming conventions, variable & output hygiene, state management, provider & module version pinning, DRY principle adherence, file & directory structure.

Classify every finding as **Critical**, **Important**, or **Minor**.

---

## Step 5: Architecture Design Audit

Read [architecture-review.md](architecture-review.md) and apply each rule to the scanned codebase.

**Categories covered**: High availability, disaster recovery, network design (VPC layout, subnet strategy, connectivity), environment isolation, scalability & auto-scaling readiness.

Classify every finding as **Critical**, **Important**, or **Minor**.

---

## Issue Severity Classification

| Level | Definition | Examples |
|-------|-----------|----------|
| **Critical** | Immediate security risk or data loss potential | Hardcoded secrets, publicly accessible S3 buckets, wildcard IAM permissions |
| **Important** | Best practice violation with significant impact | Missing state locking, no version pins, oversized instances |
| **Minor** | Style or optimization suggestion | Naming inconsistencies, missing variable descriptions |

---

## Step 6: Generate Report

Read [report-template.md](report-template.md) for the exact output format.

- Save the report to `{project_root}/terraform-audit-report.md`.
- Within each dimension, sort findings by severity: Critical first, then Important, then Minor.
- The executive summary must include total finding counts per severity level and an overall assessment (PASS / NEEDS ATTENTION / CRITICAL ISSUES).

---

## Project Scale Adaptation

| Scale | Strategy |
|-------|----------|
| **Small** (< 10 `.tf` files) | Audit every file directly in one pass. |
| **Medium** (10-30 files) | Group files by module. Audit each module as a unit. |
| **Large** (30+ files) | Audit root module and shared modules first. Then audit environment-specific configurations. Summarize cross-cutting concerns at the end. |
