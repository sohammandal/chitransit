# Timeliness alarms on the monitor's metrics, email
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
    # CTA's own outages (Oct 14, 21, 22: refused connections and timeouts, 20-40 min) aren't
    # ours to fix and paged twice each. Ours (key, quota, network) last hours: the call alarms
    # wait 45 minutes, and coverage pages at 85% (Oct 22's outage took it to 86.7%).
    coverage-low = {
      metric  = "SweepCoverage1h", op = "LessThanThreshold", threshold = 0.85,
      periods = 15, missing = "notBreaching",
      text    = "Under 85% of the last hour's sweeps happened (sweeps failing or running long?)"
    }
    call-success-low = {
      metric  = "CallSuccessRate15m", op = "LessThanThreshold", threshold = 0.95,
      periods = 45, missing = "notBreaching",
      text    = "Under 95% of Bus Tracker calls succeeded for 45+ minutes (key, quota, or a long CTA outage?)"
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
    # CDC. With no replication slot these metrics are 0, so the alarms stay OK.
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
    # The live map judges buses against the schedule; with none covering today it flags nothing
    # while everything else looks healthy (Oct 18-19). Missing data: a site outage, not this.
    live-unjudged = {
      metric  = "LiveUnjudgedDaytime", op = "GreaterThanOrEqualToThreshold", threshold = 1,
      periods = 20, missing = "notBreaching",
      text    = "In the day, under 25% of the live map's buses have a schedule: no long waits or bunching shown (live schedule stale?)"
    }
    # Trains (the train poller, feed 'train'): missing data is fine until it first sweeps
    train-stale = {
      metric  = "TrainMinutesSinceLastSweep", op = "GreaterThanThreshold", threshold = 5,
      periods = 2, missing = "notBreaching",
      text    = "No train sweep for 5+ minutes (train thread stopped?)"
    }
    train-call-success-low = {
      metric  = "TrainCallSuccessRate15m", op = "LessThanThreshold", threshold = 0.95,
      periods = 45, missing = "notBreaching",
      text    = "Under 95% of Train Tracker calls succeeded for 45+ minutes (key, quota, or a long CTA outage?)"
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
