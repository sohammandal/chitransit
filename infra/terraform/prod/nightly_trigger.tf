# Starts the nightly from AWS's clock. GitHub's own schedule is best effort (Sep 27: 4.5 h late;
# Sep 28: never started), while its workflow_dispatch API starts a run within seconds. So an
# EventBridge rule calls that API at 06:23 CDT / 05:23 CST; GitHub's schedule stays as two
# backups, and automatic runs skip themselves once yesterday is published (nightly.yml). If
# every start fails, the box monitor's report-stale alarm emails after 10:00 CT.
#
# The GitHub token is never in Terraform (or its state). The connection is created with a
# placeholder, and the real token (fine-grained, this repo only, Actions: read and write) is
# set once by hand; see infra/README.md, "Nightly trigger token".

resource "aws_cloudwatch_event_connection" "github" {
  name               = "ghost-ledger-github"
  description        = "GitHub API, for starting the nightly workflow"
  authorization_type = "API_KEY"
  auth_parameters {
    api_key {
      key   = "Authorization"
      value = "Bearer set-by-hand"
    }
  }
  lifecycle {
    ignore_changes = [auth_parameters] # the real token is set outside Terraform
  }
}

resource "aws_cloudwatch_event_api_destination" "nightly" {
  name                             = "ghost-ledger-nightly-dispatch"
  description                      = "Start the nightly workflow (workflow_dispatch)"
  connection_arn                   = aws_cloudwatch_event_connection.github.arn
  invocation_endpoint              = "https://api.github.com/repos/sohammandal/cta-ghost-ledger/actions/workflows/nightly.yml/dispatches"
  http_method                      = "POST"
  invocation_rate_limit_per_second = 1
}

resource "aws_cloudwatch_event_rule" "nightly" {
  name                = "ghost-ledger-nightly"
  description         = "Start the nightly at 06:23 CDT / 05:23 CST (11:23 UTC)"
  schedule_expression = "cron(23 11 * * ? *)"
}

data "aws_iam_policy_document" "events_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "nightly_trigger" {
  name               = "ghost-ledger-nightly-trigger"
  assume_role_policy = data.aws_iam_policy_document.events_trust.json
}

data "aws_iam_policy_document" "nightly_trigger" {
  statement {
    actions   = ["events:InvokeApiDestination"]
    resources = [aws_cloudwatch_event_api_destination.nightly.arn]
  }
}

resource "aws_iam_role_policy" "nightly_trigger" {
  name   = "ghost-ledger-nightly-trigger"
  role   = aws_iam_role.nightly_trigger.id
  policy = data.aws_iam_policy_document.nightly_trigger.json
}

resource "aws_cloudwatch_event_target" "nightly" {
  rule     = aws_cloudwatch_event_rule.nightly.name
  arn      = aws_cloudwatch_event_api_destination.nightly.arn
  role_arn = aws_iam_role.nightly_trigger.arn
  input    = jsonencode({ ref = "main", inputs = { trigger = "aws" } })
  http_target {
    header_parameters = {
      "Accept"               = "application/vnd.github+json"
      "X-GitHub-Api-Version" = "2022-11-28"
    }
  }
  retry_policy {
    maximum_retry_attempts       = 10
    maximum_event_age_in_seconds = 3600
  }
}
