# The prod box: one Lightsail instance running Postgres, the poller and the monitor (and
# Debezium from week 2), deployed by infra/box/deploy.sh pulling images from GHCR.

resource "aws_lightsail_key_pair" "admin" {
  name       = "ghost-ledger-admin"
  public_key = var.ssh_public_key
}

resource "aws_lightsail_instance" "box" {
  name              = "ghost-ledger-prod"
  availability_zone = var.availability_zone
  blueprint_id      = var.blueprint_id
  bundle_id         = var.bundle_id
  key_pair_name     = aws_lightsail_key_pair.admin.name
  # IPv4 only: the firewall below restricts IPv4 CIDRs, and Lightsail computes an IPv6
  # rule itself (possibly ::/0) when the instance is dual-stack. Nothing here needs IPv6.
  ip_address_type = "ipv4"
  user_data = templatefile("${path.module}/../../box/setup.sh.tftpl", {
    deploy_sh = file("${path.module}/../../box/deploy.sh")
  })

  # Daily snapshot of the whole disk. Postgres keeps only ~2 days once the prune gate runs;
  # Ingress on S3 holds the full log (DDIA ch. 6: replication is not backup).
  add_on {
    type          = "AutoSnapshot"
    snapshot_time = "10:00" # UTC = 05:00 Chicago (CDT), low service
    status        = "Enabled"
  }

  lifecycle {
    # user_data only runs at first boot, and changing it would REPLACE the instance
    # (and its database). deploy.sh keeps itself current from each new image instead.
    ignore_changes  = [user_data]
    prevent_destroy = true
  }
}

resource "aws_lightsail_static_ip" "box" {
  name = "ghost-ledger-prod-ip"
}

resource "aws_lightsail_static_ip_attachment" "box" {
  static_ip_name = aws_lightsail_static_ip.box.name
  instance_name  = aws_lightsail_instance.box.name
}

# Replaces Lightsail's default rules (22 and 80 open to the world): SSH from admin IPs only.
resource "aws_lightsail_instance_public_ports" "box" {
  instance_name = aws_lightsail_instance.box.name
  port_info {
    protocol  = "tcp"
    from_port = 22
    to_port   = 22
    cidrs     = var.admin_cidrs
  }
}

# The box's own AWS identity: it may only publish metrics to its namespace (lake access is
# in lake.tf). Its access key is created by `make prod-secrets`, outside Terraform, so the
# secret never lands in Terraform state.
resource "aws_iam_user" "box" {
  name = "ghost-ledger-box-prod"
}

data "aws_iam_policy_document" "box" {
  statement {
    sid       = "PublishMetrics"
    actions   = ["cloudwatch:PutMetricData"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "cloudwatch:namespace"
      values   = ["GhostLedger/prod"]
    }
  }
}

resource "aws_iam_user_policy" "box" {
  name   = "ghost-ledger-box"
  user   = aws_iam_user.box.name
  policy = data.aws_iam_policy_document.box.json
}
