# The lake: Ingress Iceberg tables on S3, tracked in the Glue catalog. Debezium on the box
# writes them (cdc/debezium/application.prod.properties). Ingress is the full change log and
# is never rebuilt (docs/PLAN.md), so the bucket is versioned: an overwritten or deleted
# object stays recoverable for 30 days.

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
    sid       = "LakeObjects"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:AbortMultipartUpload"]
    resources = ["${aws_s3_bucket.lake.arn}/*"]
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

resource "aws_iam_user_policy" "box_lake" {
  name   = "ghost-ledger-box-lake"
  user   = aws_iam_user.box.name
  policy = data.aws_iam_policy_document.box_lake.json
}
