# Public dashboard: CloudFront in front of the lake's analytics/site/ prefix, which the
# nightly workflow writes. Origin access control (OAC): only this distribution can read, and
# only that prefix; the bucket stays private (public access block stays on). The custom
# domain (chitransit.fyi, ACM cert in us-east-1 + Route 53 alias) is added once registered.

resource "aws_cloudfront_origin_access_control" "site" {
  name                              = "ghost-ledger-site"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

# Preview until the rules are validated (docs/PLAN.md): keep search engines out.
resource "aws_cloudfront_response_headers_policy" "site" {
  name = "ghost-ledger-site"
  custom_headers_config {
    items {
      header   = "X-Robots-Tag"
      value    = "noindex, nofollow"
      override = true
    }
  }
  security_headers_config {
    content_type_options {
      override = true
    }
    strict_transport_security {
      access_control_max_age_sec = 31536000
      override                   = true
    }
    referrer_policy {
      referrer_policy = "strict-origin-when-cross-origin"
      override        = true
    }
  }
}

data "aws_cloudfront_cache_policy" "optimized" {
  name = "Managed-CachingOptimized" # honors the Cache-Control the nightly upload sets
}

resource "aws_cloudfront_distribution" "site" {
  enabled             = true
  comment             = "ghost-ledger public dashboard"
  default_root_object = "index.html"
  price_class         = "PriceClass_100"
  http_version        = "http2and3"

  origin {
    origin_id                = "lake-site"
    domain_name              = aws_s3_bucket.lake.bucket_regional_domain_name
    origin_path              = "/analytics/site"
    origin_access_control_id = aws_cloudfront_origin_access_control.site.id
  }

  default_cache_behavior {
    target_origin_id           = "lake-site"
    viewer_protocol_policy     = "redirect-to-https"
    allowed_methods            = ["GET", "HEAD"]
    cached_methods             = ["GET", "HEAD"]
    compress                   = true
    cache_policy_id            = data.aws_cloudfront_cache_policy.optimized.id
    response_headers_policy_id = aws_cloudfront_response_headers_policy.site.id
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }
}

data "aws_iam_policy_document" "lake_site_read" {
  statement {
    sid       = "CloudFrontReadsSiteOnly"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.lake.arn}/analytics/site/*"]
    principals {
      type        = "Service"
      identifiers = ["cloudfront.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "AWS:SourceArn"
      values   = [aws_cloudfront_distribution.site.arn]
    }
  }
}

resource "aws_s3_bucket_policy" "lake" {
  bucket = aws_s3_bucket.lake.id
  policy = data.aws_iam_policy_document.lake_site_read.json
}

output "site_url" {
  value = "https://${aws_cloudfront_distribution.site.domain_name}/"
}
