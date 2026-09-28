# The lake: Ingress Iceberg tables on S3, tracked in the Glue catalog. Debezium on the box
# writes them (cdc/debezium/application.prod.properties). Ingress is the full change log and
# is never rebuilt (docs/PLAN.md). How it's protected (audit 2026-09-28):
#   - Versioned: a delete only hides an object; old versions stay recoverable.
#   - Object Lock (governance, no bucket default): every Ingress data file (the rows) gets a
#     10-year retention from the nightly maintenance job (transform/lake_maintenance.py).
#     Metadata files stay unlocked so snapshot history can be trimmed; the rows live in the
#     data files, and a table can be rebuilt from them.
#   - The bucket policy denies permanent deletion of Ingress data files and any bypass of a
#     lock, for every principal: undoing either means editing this policy first.
#   - Old versions expire after 30 days, which a locked version can't: only unlocked
#     metadata and the rebuildable gtfs/ and analytics/ prefixes are ever purged.

data "aws_caller_identity" "me" {}

locals {
  lake_bucket   = "ghost-ledger-lake-${data.aws_caller_identity.me.account_id}"
  glue_database = "ghost_ledger_ingress"
}

resource "aws_s3_bucket" "lake" {
  bucket = local.lake_bucket
  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_versioning" "lake" {
  bucket = aws_s3_bucket.lake.id
  versioning_configuration {
    status = "Enabled"
  }
}

# Enables Object Lock with no default retention: retention is set per data file
# (governance mode, 10 years) by the nightly maintenance job.
resource "aws_s3_bucket_object_lock_configuration" "lake" {
  bucket              = aws_s3_bucket.lake.id
  object_lock_enabled = "Enabled"
  depends_on          = [aws_s3_bucket_versioning.lake]
}

data "aws_iam_policy_document" "lake_bucket" {
  statement {
    sid       = "NoPermanentDeleteOfIngressData"
    effect    = "Deny"
    actions   = ["s3:DeleteObjectVersion"]
    resources = ["${aws_s3_bucket.lake.arn}/warehouse/*/data/*"]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
  }
  statement {
    sid       = "NoLockBypass"
    effect    = "Deny"
    actions   = ["s3:BypassGovernanceRetention"]
    resources = ["${aws_s3_bucket.lake.arn}/*"]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
  }
  statement {
    sid       = "NoUnlockingTheBucket"
    effect    = "Deny"
    actions   = ["s3:PutBucketVersioning", "s3:PutBucketObjectLockConfiguration"]
    resources = [aws_s3_bucket.lake.arn]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
  }
}

# Applied after versioning and the lock exist: the policy then forbids changing either.
resource "aws_s3_bucket_policy" "lake" {
  bucket     = aws_s3_bucket.lake.id
  policy     = data.aws_iam_policy_document.lake_bucket.json
  depends_on = [aws_s3_bucket_object_lock_configuration.lake, aws_s3_bucket_public_access_block.lake]
}

resource "aws_s3_bucket_server_side_encryption_configuration" "lake" {
  bucket = aws_s3_bucket.lake.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "lake" {
  bucket                  = aws_s3_bucket.lake.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "lake" {
  bucket = aws_s3_bucket.lake.id
  rule {
    id     = "expire-old-versions"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration {
      noncurrent_days = 30
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

resource "aws_glue_catalog_database" "ingress" {
  name        = local.glue_database
  description = "Ingress: the CDC change log from prod Postgres, as Iceberg tables"
  lifecycle {
    prevent_destroy = true
  }
}

# What the box may do in the lake: read and write objects, and create and update (never
# delete) Iceberg tables in the Ingress database.
data "aws_iam_policy_document" "box_lake" {
  statement {
    sid       = "LakeList"
    actions   = ["s3:ListBucket", "s3:GetBucketLocation"]
    resources = [aws_s3_bucket.lake.arn]
  }
  statement {
    sid       = "LakeRead"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.lake.arn}/*"]
  }
  # Writes only where Debezium's Iceberg tables live: not analytics/ (incl. the prune gate's
  # evidence file) or gtfs/, which the nightly job owns.
  statement {
    sid       = "IngressWrite"
    actions   = ["s3:PutObject", "s3:DeleteObject", "s3:AbortMultipartUpload"]
    resources = ["${aws_s3_bucket.lake.arn}/warehouse/*"]
  }
  statement {
    sid = "IngressCatalog"
    actions = [
      "glue:GetDatabase", "glue:GetDatabases", "glue:GetTable", "glue:GetTables",
      "glue:CreateTable", "glue:UpdateTable",
    ]
    resources = [
      "arn:aws:glue:${var.region}:${data.aws_caller_identity.me.account_id}:catalog",
      aws_glue_catalog_database.ingress.arn,
      "arn:aws:glue:${var.region}:${data.aws_caller_identity.me.account_id}:table/${local.glue_database}/*",
    ]
  }
}

# Explicitly, on top of never being granted: the box can't drop the Ingress catalog.
data "aws_iam_policy_document" "box_lake_deny" {
  statement {
    sid    = "NoIngressCatalogDeletes"
    effect = "Deny"
    actions = [
      "glue:DeleteTable", "glue:BatchDeleteTable", "glue:DeleteDatabase",
      "glue:DeleteTableVersion", "glue:BatchDeleteTableVersion",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_user_policy" "box_lake_deny" {
  name   = "ghost-ledger-box-lake-deny"
  user   = aws_iam_user.box.name
  policy = data.aws_iam_policy_document.box_lake_deny.json
}

resource "aws_iam_user_policy" "box_lake" {
  name   = "ghost-ledger-box-lake"
  user   = aws_iam_user.box.name
  policy = data.aws_iam_policy_document.box_lake.json
}
