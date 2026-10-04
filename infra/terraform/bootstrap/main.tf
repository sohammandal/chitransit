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

# GitHub's OIDC `sub` claim for this repo uses the immutable format (owner and repo IDs), so
# another repo, even one given the same name, can never assume these roles. Check the prefix:
#   gh api repos/<owner>/<repo>/actions/oidc/customization/sub   -> sub_claim_prefix
# The name is part of it: during a rename both are listed (same IDs), then the old one goes.
variable "github_sub_prefixes" {
  description = "The repo's OIDC sub claim prefixes (sub_claim_prefix)"
  type        = list(string)
  default = [
    "repo:sohammandal@18245872/cta-ghost-ledger@1388304114",
    "repo:sohammandal@18245872/chitransit@1388304114",
  ]
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
      values = flatten([
        for p in var.github_sub_prefixes : ["${p}:pull_request", "${p}:ref:refs/heads/main"]
      ])
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
      values   = [for p in var.github_sub_prefixes : "${p}:environment:prod"]
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
  statement {
    sid = "LakeBucket"
    actions = [
      "s3:CreateBucket", "s3:DeleteBucket", "s3:PutBucketVersioning", "s3:PutEncryptionConfiguration",
      "s3:PutBucketPublicAccessBlock", "s3:PutLifecycleConfiguration", "s3:PutBucketTagging",
      "s3:PutBucketPolicy", "s3:DeleteBucketPolicy",
    ]
    resources = ["arn:aws:s3:::ghost-ledger-lake-*", "arn:aws:s3:::ghost-ledger-site-*"]
  }
  # Public dashboard: CloudFront (OAC + headers policy + distribution) in front of the lake's
  # analytics/site/ prefix; ACM + Route 53 records for the custom domain.
  statement {
    sid = "SiteCdn"
    actions = [
      "cloudfront:CreateDistribution", "cloudfront:UpdateDistribution", "cloudfront:DeleteDistribution",
      "cloudfront:TagResource", "cloudfront:UntagResource",
      "cloudfront:CreateOriginAccessControl", "cloudfront:UpdateOriginAccessControl",
      "cloudfront:DeleteOriginAccessControl",
      "cloudfront:CreateResponseHeadersPolicy", "cloudfront:UpdateResponseHeadersPolicy",
      "cloudfront:DeleteResponseHeadersPolicy",
      "cloudfront:CreateFunction", "cloudfront:UpdateFunction", "cloudfront:PublishFunction",
      "cloudfront:DeleteFunction",
    ]
    resources = ["arn:aws:cloudfront::${data.aws_caller_identity.me.account_id}:*"]
  }
  statement {
    sid = "SiteDomain"
    actions = [
      "acm:RequestCertificate", "acm:DeleteCertificate", "acm:AddTagsToCertificate",
      "acm:RemoveTagsFromCertificate",
      "route53:ChangeResourceRecordSets", "route53:ChangeTagsForResource", "route53:UpdateHostedZoneComment",
    ]
    resources = [
      "arn:aws:acm:us-east-1:${data.aws_caller_identity.me.account_id}:certificate/*",
      "arn:aws:route53:::hostedzone/*",
    ]
  }
  statement {
    sid = "LakeCatalog"
    actions = [
      "glue:CreateDatabase", "glue:UpdateDatabase", "glue:DeleteDatabase",
      "glue:TagResource", "glue:UntagResource",
    ]
    resources = [
      "arn:aws:glue:${var.region}:${data.aws_caller_identity.me.account_id}:catalog",
      "arn:aws:glue:${var.region}:${data.aws_caller_identity.me.account_id}:database/ghost_ledger_*",
      "arn:aws:glue:${var.region}:${data.aws_caller_identity.me.account_id}:table/ghost_ledger_*/*",
      "arn:aws:glue:${var.region}:${data.aws_caller_identity.me.account_id}:userDefinedFunction/ghost_ledger_*/*",
    ]
  }
}

resource "aws_iam_role_policy" "ci_apply_writes" {
  name   = "ghost-ledger-writes"
  role   = aws_iam_role.ci_apply.id
  policy = data.aws_iam_policy_document.apply_writes.json
}

# ---------- transform role: the nightly dbt job ----------
# Reads Ingress (Glue + S3), writes only the lake's gtfs/ and analytics/ prefixes, and
# publishes the public site (site bucket, except live/, which only the poller writes). Only
# workflows on main (schedule, manual dispatch, pushes) can assume it.

data "aws_iam_policy_document" "trust_transform" {
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
      values   = [for p in var.github_sub_prefixes : "${p}:ref:refs/heads/main"]
    }
  }
}

resource "aws_iam_role" "transform" {
  name                 = "ghost-ledger-transform"
  assume_role_policy   = data.aws_iam_policy_document.trust_transform.json
  max_session_duration = 3600
}

locals {
  lake_arn = "arn:aws:s3:::ghost-ledger-lake-${data.aws_caller_identity.me.account_id}"
  site_arn = "arn:aws:s3:::ghost-ledger-site-${data.aws_caller_identity.me.account_id}"
  glue_arn = "arn:aws:glue:${var.region}:${data.aws_caller_identity.me.account_id}"
}

data "aws_iam_policy_document" "transform" {
  statement {
    sid       = "LakeList"
    actions   = ["s3:ListBucket", "s3:GetBucketLocation"]
    resources = [local.lake_arn]
  }
  statement {
    sid       = "LakeRead"
    actions   = ["s3:GetObject"]
    resources = ["${local.lake_arn}/*"]
  }
  statement {
    sid       = "LakeWriteDerived"
    actions   = ["s3:PutObject", "s3:DeleteObject", "s3:AbortMultipartUpload"]
    resources = ["${local.lake_arn}/gtfs/*", "${local.lake_arn}/analytics/*"]
  }
  statement {
    sid       = "SiteList"
    actions   = ["s3:ListBucket", "s3:GetBucketLocation"]
    resources = [local.site_arn]
  }
  statement {
    sid       = "SitePublish"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:AbortMultipartUpload"]
    resources = ["${local.site_arn}/*"]
  }
  statement {
    sid       = "LiveFeedIsThePollers"
    effect    = "Deny"
    actions   = ["s3:PutObject", "s3:DeleteObject"]
    resources = ["${local.site_arn}/live/*"]
  }
  statement {
    sid    = "NoIngressDeletes"
    effect = "Deny"
    actions = [
      "s3:DeleteObjectVersion", "s3:BypassGovernanceRetention",
      "glue:DeleteTable", "glue:BatchDeleteTable", "glue:DeleteDatabase",
      "glue:DeleteTableVersion", "glue:BatchDeleteTableVersion",
    ]
    resources = ["*"]
  }
  statement {
    sid = "IngressCatalogRead"
    actions = [
      "glue:GetCatalog", "glue:GetCatalogs", "glue:GetDatabase", "glue:GetDatabases",
      "glue:GetTable", "glue:GetTables",
    ]
    resources = [
      "${local.glue_arn}:catalog",
      "${local.glue_arn}:database/ghost_ledger_ingress",
      "${local.glue_arn}:table/ghost_ledger_ingress/*",
    ]
  }
}

resource "aws_iam_role_policy" "transform" {
  name   = "ghost-ledger-transform"
  role   = aws_iam_role.transform.id
  policy = data.aws_iam_policy_document.transform.json
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

output "transform_role_arn" {
  value = aws_iam_role.transform.arn
}

# ---------- lake maintenance role: the nightly's Ingress upkeep ----------
# Separate from the transform role so dbt never gains write access to Ingress. It may compact
# finished days (write new data files: transform/compact_ingress.py, verified row for row
# before any commit), lock data files (10-year governance retention), write new Iceberg
# metadata (daily tags, trimmed snapshot history) and update the Glue table pointers, and
# nothing else: every delete is denied explicitly. Same trust as the transform role:
# workflows on main only.
resource "aws_iam_role" "lake_maintenance" {
  name                 = "ghost-ledger-lake-maintenance"
  assume_role_policy   = data.aws_iam_policy_document.trust_transform.json
  max_session_duration = 3600
}

data "aws_iam_policy_document" "lake_maintenance" {
  statement {
    sid       = "LakeList"
    actions   = ["s3:ListBucket", "s3:ListBucketVersions", "s3:GetBucketLocation"]
    resources = [local.lake_arn]
  }
  statement {
    sid       = "IngressRead"
    actions   = ["s3:GetObject", "s3:GetObjectVersion", "s3:GetObjectRetention"]
    resources = ["${local.lake_arn}/warehouse/*"]
  }
  statement {
    sid       = "LockIngressData"
    actions   = ["s3:PutObjectRetention"]
    resources = ["${local.lake_arn}/warehouse/*/data/*"]
  }
  # Compaction writes each finished day as a few new files; the old ones stay on S3 (locked,
  # still in the day tags). Writing only: deletes stay denied below.
  statement {
    sid       = "CompactIngressData"
    actions   = ["s3:PutObject", "s3:AbortMultipartUpload"]
    resources = ["${local.lake_arn}/warehouse/*/data/*"]
  }
  statement {
    sid       = "WriteIngressMetadata"
    actions   = ["s3:PutObject"]
    resources = ["${local.lake_arn}/warehouse/*/metadata/*"]
  }
  # Iceberg hides metadata.json files older than the last 50 after each of its commits
  # (write.metadata.delete-after-commit.enabled). Only those: a hide, kept 30 days by
  # versioning. Every other delete stays denied below.
  statement {
    sid       = "TidyOldIcebergMetadata"
    actions   = ["s3:DeleteObject"]
    resources = ["${local.lake_arn}/warehouse/*/metadata/*.metadata.json"]
  }
  statement {
    sid = "IngressCatalog"
    actions = [
      "glue:GetDatabase", "glue:GetDatabases", "glue:GetTable", "glue:GetTables", "glue:UpdateTable",
    ]
    resources = [
      "${local.glue_arn}:catalog",
      "${local.glue_arn}:database/ghost_ledger_ingress",
      "${local.glue_arn}:table/ghost_ledger_ingress/*",
    ]
  }
  statement {
    sid    = "NeverDelete"
    effect = "Deny"
    actions = [
      "s3:DeleteObjectVersion", "s3:BypassGovernanceRetention",
      "glue:DeleteTable", "glue:BatchDeleteTable", "glue:DeleteDatabase",
      "glue:DeleteTableVersion", "glue:BatchDeleteTableVersion",
    ]
    resources = ["*"]
  }
  statement {
    sid           = "NoDeletesButOldMetadata"
    effect        = "Deny"
    actions       = ["s3:DeleteObject"]
    not_resources = ["${local.lake_arn}/warehouse/*/metadata/*.metadata.json"]
  }
}

resource "aws_iam_role_policy" "lake_maintenance" {
  name   = "ghost-ledger-lake-maintenance"
  role   = aws_iam_role.lake_maintenance.id
  policy = data.aws_iam_policy_document.lake_maintenance.json
}

output "lake_maintenance_role_arn" {
  value = aws_iam_role.lake_maintenance.arn
}
