# The public site, chitransit.fyi: a private site bucket behind CloudFront (origin access
# control: only this distribution can read it). Who writes what:
#   live/*        the poller on the box, after each sweep (the box user can write nothing else;
#                 CloudFront serves only the feed's own names there: aws_cloudfront_function.index)
#   everything    the nightly / site workflows (transform role; IAM denies it live/*)
# The lake is not an origin: nothing in it is public. The distribution is on CloudFront's
# flat-rate Free plan (no overage charges, so a flood can't run up a bill) with a per-IP rate
# limit (aws_wafv2_web_acl.site); the plan allows no custom header policy (headers below).

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

# Google Search Console: proves we own the domain (Oct 4). Keep it: Google rechecks it.
resource "aws_route53_record" "google_verification" {
  zone_id = aws_route53_zone.site.zone_id
  name    = local.domain
  type    = "TXT"
  ttl     = 300
  records = ["google-site-verification=TixlDKOOZzYLi8hzk5noYMYis2UWWoLOj_lE0odc-RE"]
}

resource "aws_s3_bucket" "site" {
  bucket = "ghost-ledger-site-${data.aws_caller_identity.me.account_id}"
}

resource "aws_s3_bucket_server_side_encryption_configuration" "site" {
  bucket = aws_s3_bucket.site.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "site" {
  bucket                  = aws_s3_bucket.site.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# S3 has no directory index for OAC origins: /route/79/ -> /route/79/index.html. Under /live/
# only the poller's own file names are served, whatever else the box key could write there.
resource "aws_cloudfront_function" "index" {
  name    = "ghost-ledger-site-index"
  runtime = "cloudfront-js-2.0"
  publish = true
  code    = <<-JS
    function handler(event) {
      var request = event.request;
      var uri = request.uri;
      if (uri.startsWith("/live/") && !/^\/live\/(vehicles|stops|patterns\/[0-9]+)\.json$/.test(uri)) {
        return { statusCode: 404, statusDescription: "Not Found" };
      }
      if (uri.endsWith("/")) {
        request.uri = uri + "index.html";
      } else if (uri.split("/").pop().indexOf(".") === -1) {
        request.uri = uri + "/index.html";
      }
      return request;
    }
  JS
}

resource "aws_cloudfront_origin_access_control" "site" {
  name                              = "ghost-ledger-site"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

# Security headers, within what CloudFront's flat-rate Free plan allows (AWS-managed header
# policies, functions; no custom header policy). AWS's SecurityHeadersPolicy puts a baseline on
# every response, errors included (HSTS, nosniff, SAMEORIGIN, Referrer-Policy). CloudFront then
# runs this function on successful responses (never on 4xx/5xx), which tightens them to the
# site's own: DENY, HSTS for www too, frame-ancestors (a <meta> CSP can't carry it; the page's
# CSP is a <meta> tag the build writes: site/astro.config.mjs) and Permissions-Policy.
data "aws_cloudfront_response_headers_policy" "security" {
  name = "Managed-SecurityHeadersPolicy"
}

resource "aws_cloudfront_function" "headers" {
  name    = "ghost-ledger-site-headers"
  runtime = "cloudfront-js-2.0"
  publish = true
  code    = <<-JS
    function handler(event) {
      var h = event.response.headers;
      h["strict-transport-security"] = { value: "max-age=31536000; includeSubDomains" };
      h["x-frame-options"] = { value: "DENY" };
      h["content-security-policy"] = { value: "frame-ancestors 'none'; base-uri 'none'; form-action 'none'; object-src 'none'" };
      h["permissions-policy"] = { value: "camera=(), microphone=(), geolocation=(self), payment=(), usb=(), interest-cohort=()" };
      delete h["x-xss-protection"]; // the managed policy's; obsolete, and the site never sent it
      return event.response;
    }
  JS
}

# A per-IP rate limit at the edge. With the distribution on CloudFront's flat-rate Free plan
# (subscribed in the console: the provider has no resource for it) the bill can't run away:
# no overage charges whatever the traffic, and requests WAF blocks don't count. Blocked
# requests get a 403, shown as the site's 404 page (custom_error_response below).
resource "aws_wafv2_web_acl" "site" {
  provider    = aws.us_east_1 # CloudFront's web ACLs live in us-east-1
  name        = "ghost-ledger-site"
  description = "chitransit.fyi: per-IP rate limit"
  scope       = "CLOUDFRONT"

  default_action {
    allow {}
  }

  rule {
    name     = "per-ip-rate"
    priority = 0
    action {
      block {}
    }
    statement {
      # Requests per IP per 5 minutes (10 a second). An open tab makes ~20-40 in that time,
      # and a page load ~20; generous, as phones and offices can share one address.
      rate_based_statement {
        limit                 = 3000
        aggregate_key_type    = "IP"
        evaluation_window_sec = 300
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "ghost-ledger-site-per-ip-rate"
      sampled_requests_enabled   = true
    }
  }

  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = "ghost-ledger-site"
    sampled_requests_enabled   = true
  }
}

data "aws_cloudfront_cache_policy" "optimized" {
  name = "Managed-CachingOptimized" # honors the Cache-Control the nightly upload sets
}

resource "aws_cloudfront_distribution" "site" {
  enabled             = true
  comment             = "chitransit.fyi"
  default_root_object = "index.html"
  price_class         = "PriceClass_All" # the flat-rate plan's (set on subscribing): every edge, same $0
  http_version        = "http2and3"
  is_ipv6_enabled     = true
  aliases             = [local.domain, "www.${local.domain}"]
  web_acl_id          = aws_wafv2_web_acl.site.arn

  origin {
    origin_id                = "site"
    domain_name              = aws_s3_bucket.site.bucket_regional_domain_name
    origin_access_control_id = aws_cloudfront_origin_access_control.site.id
  }

  default_cache_behavior {
    target_origin_id           = "site"
    viewer_protocol_policy     = "redirect-to-https"
    allowed_methods            = ["GET", "HEAD"]
    cached_methods             = ["GET", "HEAD"]
    compress                   = true
    cache_policy_id            = data.aws_cloudfront_cache_policy.optimized.id
    response_headers_policy_id = data.aws_cloudfront_response_headers_policy.security.id
    function_association {
      event_type   = "viewer-request"
      function_arn = aws_cloudfront_function.index.arn
    }
    function_association {
      event_type   = "viewer-response"
      function_arn = aws_cloudfront_function.headers.arn
    }
  }

  # A missing object is a 403 through OAC (no ListBucket): show the site's 404 page.
  custom_error_response {
    error_code            = 403
    response_code         = 404
    response_page_path    = "/404.html"
    error_caching_min_ttl = 60
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

data "aws_iam_policy_document" "site_read" {
  statement {
    sid       = "CloudFrontReads"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.site.arn}/*"]
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

resource "aws_s3_bucket_policy" "site" {
  bucket = aws_s3_bucket.site.id
  policy = data.aws_iam_policy_document.site_read.json
}

# The poller writes the live feed and nothing else in the site bucket: exact keys and JSON
# pattern files, and CloudFront serves no other name under live/ (aws_cloudfront_function.index),
# so a stolen box key can't plant pages (say live/login.html) on chitransit.fyi.
data "aws_iam_policy_document" "box_live" {
  statement {
    sid     = "LiveFeed"
    actions = ["s3:PutObject"]
    resources = [
      "${aws_s3_bucket.site.arn}/live/vehicles.json",
      "${aws_s3_bucket.site.arn}/live/stops.json",      # every stop's own wait (ghost_ledger.live)
      "${aws_s3_bucket.site.arn}/live/patterns/*.json", # every pattern's shape and stops
    ]
  }
}

resource "aws_iam_user_policy" "box_live" {
  name   = "ghost-ledger-box-live"
  user   = aws_iam_user.box.name
  policy = data.aws_iam_policy_document.box_live.json
}

output "site_bucket" {
  value = aws_s3_bucket.site.bucket
}

output "site_url" {
  value = { domain = "https://${local.domain}/", cloudfront = "https://${aws_cloudfront_distribution.site.domain_name}/" }
}
