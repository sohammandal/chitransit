terraform {
  required_version = ">= 1.10"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.66"
    }
  }
  # Bucket name (it contains the account id) comes from -backend-config:
  #   CI:     -backend-config="bucket=$TF_STATE_BUCKET"  (GitHub variable)
  #   laptop: -backend-config=backend.hcl               (gitignored; see backend.hcl.example)
  backend "s3" {
    key          = "prod/terraform.tfstate"
    region       = "us-east-2"
    encrypt      = true
    use_lockfile = true
  }
}

provider "aws" {
  region = var.region
  default_tags {
    tags = { project = "ghost-ledger", stack = "prod", managed_by = "terraform" }
  }
}

# CloudFront certificates must live in us-east-1.
provider "aws" {
  alias  = "us_east_1"
  region = "us-east-1"
  default_tags {
    tags = { project = "ghost-ledger", stack = "prod", managed_by = "terraform" }
  }
}
