# Timeliness alarms on the monitor's metrics (docs/PLAN.md, "Monitoring and alerts"), email
# via SNS on ALARM and OK. Integrity checks live in Dagster instead. Cost: the account's
# own AWS Budget ("My Monthly Cost Budget", $25, alerts at 85%/100%) covers this project.

resource "aws_sns_topic" "alerts" {
  name = "ghost-ledger-prod-alerts"
}

# AWS emails a confirmation link; nothing is delivered until it's clicked.
resource "aws_sns_topic_subscription" "email" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

locals {
  namespace = "GhostLedger/prod"
  # One-minute metrics. `periods` consecutive breaching minutes raise the alarm.
  # missing = "breaching" only for staleness: if the monitor or the box dies, no data
  # arrives, and that must page.
  alarms = {
    poller-stale = {
      metric  = "MinutesSinceLastSweep", op = "GreaterThanThreshold", threshold = 5,
      periods = 2, missing = "breaching",
      text    = "No sweep for 5+ minutes, or the monitor stopped reporting (box down?)"
    }
    coverage-low = {
      metric  = "SweepCoverage1h", op = "LessThanThreshold", threshold = 0.95,
      periods = 15, missing = "notBreaching",
      text    = "Under 95% of the last hour's sweeps happened (a patch reboot costs ~3 sweeps)"
    }
    call-success-low = {
      metric  = "CallSuccessRate15m", op = "LessThanThreshold", threshold = 0.95,
      periods = 15, missing = "notBreaching",
      text    = "Under 95% of Bus Tracker calls succeeded over 15 minutes (outage or quota?)"
    }
    zero-buses-daytime = {
      metric  = "ZeroBusesDaytime", op = "GreaterThanOrEqualToThreshold", threshold = 1,
      periods = 10, missing = "notBreaching",
      text    = "Calls succeed but CTA reports zero buses in daytime"
    }
    disk-high = {
      metric  = "DiskUsedPct", op = "GreaterThanThreshold", threshold = 80,
      periods = 5, missing = "notBreaching",
      text    = "Box disk over 80% (WAL and positions share it)"
    }
    # CDC (week 2). With no replication slot these metrics are 0, so the alarms stay OK.
    cdc-lag-warn = {
      metric  = "ReplicationSlotLagMB", op = "GreaterThanThreshold", threshold = 512,
      periods = 5, missing = "notBreaching",
      text    = "CDC is 512+ MB behind the WAL"
    }
    cdc-lag-critical = {
      metric  = "ReplicationSlotLagMB", op = "GreaterThanThreshold", threshold = 1536,
      periods = 2, missing = "notBreaching",
      text    = "CDC is 1.5+ GB behind; at 2 GB max_slot_wal_keep_size invalidates the slot"
    }
    cdc-slot-lost = {
      metric  = "ReplicationSlotsLost", op = "GreaterThanOrEqualToThreshold", threshold = 1,
      periods = 1, missing = "notBreaching",
      text    = "A replication slot is lost or unreserved: CDC needs a re-snapshot"
    }
    ingress-stale = {
      metric  = "IngressFreshnessMinutes", op = "GreaterThanThreshold", threshold = 15,
      periods = 3, missing = "breaching",
      text    = "No Iceberg commit to Ingress for 15+ minutes (Debezium up but not landing?)"
    }
    report-stale = {
      metric  = "SiteReportStale", op = "GreaterThanOrEqualToThreshold", threshold = 1,
      periods = 15, missing = "notBreaching",
      text    = "After 10:00 CT the public report still lacks yesterday: the nightly didn't run or failed"
    }
    cdc-slot-inactive = {
      metric  = "ReplicationSlotsInactive", op = "GreaterThanOrEqualToThreshold", threshold = 1,
      periods = 10, missing = "notBreaching",
      text    = "A replication slot has had no consumer for 10 minutes (Debezium down?)"
    }
  }
}

resource "aws_cloudwatch_metric_alarm" "this" {
  for_each = local.alarms

  alarm_name          = "ghost-ledger-prod-${each.key}"
  alarm_description   = each.value.text
  namespace           = local.namespace
  metric_name         = each.value.metric
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = each.value.periods
  datapoints_to_alarm = each.value.periods
  comparison_operator = each.value.op
  threshold           = each.value.threshold
  treat_missing_data  = each.value.missing
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]
}
