# One-time foundation, applied from the laptop with local state (`make tf-bootstrap`):
#   - the S3 bucket that holds the prod stack's state (versioned, encrypted, private)
#   - GitHub's OIDC identity provider, so Actions can assume AWS roles without stored keys
#   - two roles for CI: plan (read-only + state lock) and apply (scoped writes, prod env only)
# Its own state is tiny and non-secret; if lost, re-import these resources.

terraform {
  required_version = ">= 1.10"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.66"
    }
  }
}

provider "aws" {
  region = var.region
  default_tags {
    tags = { project = "ghost-ledger", stack = "bootstrap", managed_by = "terraform" }
  }
}

variable "region" {
  type    = string
  default = "us-east-2"
}

# GitHub's OIDC `sub` claim for this repo uses the immutable format (owner and repo IDs), so a
# renamed or recreated repo with the same name can never assume these roles. Check the prefix:
#   gh api repos/<owner>/<repo>/actions/oidc/customization/sub   -> sub_claim_prefix
variable "github_sub_prefix" {
  description = "The repo's OIDC sub claim prefix (sub_claim_prefix)"
  type        = string
  default     = "repo:sohammandal@18245872/cta-ghost-ledger@1388304114"
}

data "aws_caller_identity" "me" {}

locals {
  state_bucket = "ghost-ledger-tfstate-${data.aws_caller_identity.me.account_id}"
  oidc_host    = "token.actions.githubusercontent.com"
}

# ---------- state bucket ----------

resource "aws_s3_bucket" "state" {
  bucket = local.state_bucket
  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "state" {
  bucket                  = aws_s3_bucket.state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    id     = "expire-old-state-versions"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration {
      noncurrent_days = 90
    }
  }
}

# ---------- GitHub OIDC ----------

resource "aws_iam_openid_connect_provider" "github" {
  url            = "https://${local.oidc_host}"
  client_id_list = ["sts.amazonaws.com"]
}

# Who may assume a role, by the `sub` claim GitHub puts in the token
data "aws_iam_policy_document" "trust_plan" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }
    condition {
      test     = "StringEquals"
      variable = "${local.oidc_host}:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "${local.oidc_host}:sub"
      values = [
        "${var.github_sub_prefix}:pull_request",
        "${var.github_sub_prefix}:ref:refs/heads/main",
      ]
    }
  }
}

# Apply: only a job running in the `prod` GitHub environment (which only `main` can use).
# AWS enforces this, so a workflow edited on a branch cannot obtain apply credentials.
data "aws_iam_policy_document" "trust_apply" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }
    condition {
      test     = "StringEquals"
      variable = "${local.oidc_host}:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "${local.oidc_host}:sub"
      values   = ["${var.github_sub_prefix}:environment:prod"]
    }
  }
}

# ---------- plan role: read everything, write only the state lock ----------
# PR runs can assume this role (a PR can edit its own workflow), so it must not be able to
# overwrite or delete the state itself: read the state, write only the lock file.

resource "aws_iam_role" "ci_plan" {
  name                 = "ghost-ledger-ci-plan"
  assume_role_policy   = data.aws_iam_policy_document.trust_plan.json
  max_session_duration = 3600
}

resource "aws_iam_role_policy_attachment" "ci_plan_readonly" {
  role       = aws_iam_role.ci_plan.name
  policy_arn = "arn:aws:iam::aws:policy/ReadOnlyAccess"
}

locals {
  state_key = "${aws_s3_bucket.state.arn}/prod/terraform.tfstate"
}

data "aws_iam_policy_document" "state_plan" {
  statement {
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.state.arn]
  }
  statement {
    actions   = ["s3:GetObject"]
    resources = [local.state_key]
  }
  statement {
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${local.state_key}.tflock"]
  }
}

resource "aws_iam_role_policy" "ci_plan_state" {
  name   = "terraform-state"
  role   = aws_iam_role.ci_plan.id
  policy = data.aws_iam_policy_document.state_plan.json
}

data "aws_iam_policy_document" "state_apply" {
  statement {
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.state.arn]
  }
  statement {
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = [local.state_key, "${local.state_key}.tflock"]
  }
}

# ---------- apply role: write only what the prod stack manages ----------

resource "aws_iam_role" "ci_apply" {
  name                 = "ghost-ledger-ci-apply"
  assume_role_policy   = data.aws_iam_policy_document.trust_apply.json
  max_session_duration = 3600
}

resource "aws_iam_role_policy_attachment" "ci_apply_readonly" {
  role       = aws_iam_role.ci_apply.name
  policy_arn = "arn:aws:iam::aws:policy/ReadOnlyAccess"
}

resource "aws_iam_role_policy" "ci_apply_state" {
  name   = "terraform-state"
  role   = aws_iam_role.ci_apply.id
  policy = data.aws_iam_policy_document.state_apply.json
}

data "aws_iam_policy_document" "apply_writes" {
  # Lightsail has little resource-level permission support; scope to the service
  statement {
    sid       = "Lightsail"
    actions   = ["lightsail:*"]
    resources = ["*"]
  }
  statement {
    sid       = "Alarms"
    actions   = ["cloudwatch:PutMetricAlarm", "cloudwatch:DeleteAlarms", "cloudwatch:TagResource", "cloudwatch:UntagResource"]
    resources = ["arn:aws:cloudwatch:${var.region}:${data.aws_caller_identity.me.account_id}:alarm:ghost-ledger-*"]
  }
  statement {
    sid = "Alerts"
    actions = [
      "sns:CreateTopic", "sns:DeleteTopic", "sns:SetTopicAttributes", "sns:Subscribe",
      "sns:Unsubscribe", "sns:TagResource", "sns:UntagResource",
    ]
    resources = ["arn:aws:sns:${var.region}:${data.aws_caller_identity.me.account_id}:ghost-ledger-*"]
  }
  statement {
    sid = "BoxIdentity"
    actions = [
      "iam:CreateUser", "iam:DeleteUser", "iam:TagUser", "iam:UntagUser",
      "iam:PutUserPolicy", "iam:DeleteUserPolicy",
    ]
    resources = ["arn:aws:iam::${data.aws_caller_identity.me.account_id}:user/ghost-ledger-*"]
  }
}

resource "aws_iam_role_policy" "ci_apply_writes" {
  name   = "ghost-ledger-writes"
  role   = aws_iam_role.ci_apply.id
  policy = data.aws_iam_policy_document.apply_writes.json
}

# ---------- outputs: copy these into GitHub repo variables ----------

output "state_bucket" {
  value = aws_s3_bucket.state.bucket
}

output "ci_plan_role_arn" {
  value = aws_iam_role.ci_plan.arn
}

output "ci_apply_role_arn" {
  value = aws_iam_role.ci_apply.arn
}
