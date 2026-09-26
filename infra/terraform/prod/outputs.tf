output "box_ip" {
  value = aws_lightsail_static_ip.box.ip_address
}

output "ssh" {
  value = "ssh -i ~/.ssh/ghost_ledger ubuntu@${aws_lightsail_static_ip.box.ip_address}"
}

output "box_iam_user" {
  description = "make prod-secrets creates this user's access key and writes it to the box"
  value       = aws_iam_user.box.name
}

output "alerts_topic_arn" {
  value = aws_sns_topic.alerts.arn
}

output "lake" {
  description = "compose.prod.yml (debezium) must name the same bucket and Glue database"
  value       = { bucket = aws_s3_bucket.lake.bucket, glue_database = aws_glue_catalog_database.ingress.name }
}
