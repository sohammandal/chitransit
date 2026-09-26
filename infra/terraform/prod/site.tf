# Public dashboard: CloudFront in front of the lake's analytics/site/ prefix, which the
# nightly workflow writes. Origin access control (OAC): only this distribution can read, and
# only that prefix; the bucket stays private (public access block stays on). Served at
# chitransit.fyi (and www) with an ACM certificate in us-east-1.

locals {
  domain = "chitransit.fyi"
}

# Registered in Route 53, which created the zone; adopt it rather than recreate it.
import {
  to = aws_route53_zone.site
  id = "Z00394573RAQAY1F1P679"
}

resource "aws_route53_zone" "site" {
  name    = local.domain
  comment = "HostedZone created by Route53 Registrar"
}

resource "aws_acm_certificate" "site" {
  provider                  = aws.us_east_1
  domain_name               = local.domain
  subject_alternative_names = ["*.${local.domain}"]
  validation_method         = "DNS"
  lifecycle {
    create_before_destroy = true
  }
}

# Keyed by domain (known at plan time); the apex and the wildcard get the same validation
# record, so the second write is a no-op overwrite.
resource "aws_route53_record" "cert_validation" {
  for_each = {
    for o in aws_acm_certificate.site.domain_validation_options : o.domain_name => o
  }
  zone_id         = aws_route53_zone.site.zone_id
  name            = each.value.resource_record_name
  type            = each.value.resource_record_type
  records         = [each.value.resource_record_value]
  ttl             = 300
  allow_overwrite = true
}

resource "aws_acm_certificate_validation" "site" {
  provider                = aws.us_east_1
  certificate_arn         = aws_acm_certificate.site.arn
  validation_record_fqdns = [for r in aws_route53_record.cert_validation : r.fqdn]
}

resource "aws_route53_record" "site" {
  for_each = toset(flatten([for n in [local.domain, "www.${local.domain}"] : [
    "${n}|A", "${n}|AAAA"
  ]]))
  zone_id = aws_route53_zone.site.zone_id
  name    = split("|", each.value)[0]
  type    = split("|", each.value)[1]
  alias {
    name                   = aws_cloudfront_distribution.site.domain_name
    zone_id                = aws_cloudfront_distribution.site.hosted_zone_id
    evaluate_target_health = false
  }
}

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
  is_ipv6_enabled     = true
  aliases             = [local.domain, "www.${local.domain}"]

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
    acm_certificate_arn      = aws_acm_certificate_validation.site.certificate_arn
    ssl_support_method       = "sni-only"
    minimum_protocol_version = "TLSv1.2_2021"
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
  value = { domain = "https://${local.domain}/", cloudfront = "https://${aws_cloudfront_distribution.site.domain_name}/" }
}
