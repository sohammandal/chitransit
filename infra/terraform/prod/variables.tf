# Values that differ per person live outside the repo: GitHub repo variables in CI
# (TF_VAR_*), a gitignored terraform.tfvars on the laptop (see terraform.tfvars.example).

variable "region" {
  type    = string
  default = "us-east-2"
}

variable "availability_zone" {
  type    = string
  default = "us-east-2a"
}

variable "bundle_id" {
  description = "Lightsail plan: small_3_0 = 2 GB RAM, 2 vCPU, 60 GB SSD, public IPv4, $12/month"
  type        = string
  default     = "small_3_0"
}

variable "blueprint_id" {
  type    = string
  default = "ubuntu_24_04"
}

variable "admin_cidrs" {
  description = "Only these may SSH to the box, e.g. [\"203.0.113.7/32\"]. CI never needs SSH."
  type        = list(string)
  validation {
    condition     = length(var.admin_cidrs) > 0 && !contains(var.admin_cidrs, "0.0.0.0/0")
    error_message = "Give at least one admin CIDR, and never 0.0.0.0/0."
  }
}

variable "alert_email" {
  description = "Receives alarm (ALARM and OK) and budget emails. Confirm the SNS email once."
  type        = string
}

variable "ssh_public_key" {
  description = "Contents of ~/.ssh/ghost_ledger.pub"
  type        = string
}
