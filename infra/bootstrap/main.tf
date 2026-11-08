terraform {
  required_version = ">= 1.10.0"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 6.0" }
  }
}

variable "aws_account_id" { type = string }
variable "aws_region" { default = "us-east-1" }
variable "github_repository" { default = "lucasbraz-repos/poc_test" }
variable "existing_oidc_provider_arn" { default = "" }

provider "aws" {
  region              = var.aws_region
  allowed_account_ids = [var.aws_account_id]
  default_tags { tags = { Project = "poc-carteira", ManagedBy = "terraform", Purpose = "github-cicd" } }
}

locals {
  name         = "poc-carteira-dev"
  base         = "arn:aws"
  regional     = "${local.base}:%s:${var.aws_region}:${var.aws_account_id}"
  state_bucket = "poc-test-tfstate-${var.aws_account_id}-${var.aws_region}"
  state_arn    = "${local.base}:s3:::${local.state_bucket}"
  lake_arn     = "${local.base}:s3:::${local.name}-${var.aws_account_id}-${substr(sha256(var.aws_region), 0, 6)}-*"
  oidc_arn     = var.existing_oidc_provider_arn != "" ? var.existing_oidc_provider_arn : aws_iam_openid_connect_provider.github[0].arn
}

resource "aws_s3_bucket" "state" {
  bucket        = local.state_bucket
  force_destroy = false
  lifecycle { prevent_destroy = true }
}
resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id
  versioning_configuration { status = "Enabled" }
}
resource "aws_s3_bucket_public_access_block" "state" {
  bucket                  = aws_s3_bucket.state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}
resource "aws_s3_bucket_policy" "state" {
  bucket = aws_s3_bucket.state.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect    = "Deny", Principal = "*", Action = "s3:*", Resource = [local.state_arn, "${local.state_arn}/*"],
    Condition = { Bool = { "aws:SecureTransport" = "false" } }
  }] })
}

resource "aws_iam_openid_connect_provider" "github" {
  count          = var.existing_oidc_provider_arn == "" ? 1 : 0
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
}

resource "aws_iam_role" "deploy" {
  name                 = "github-poc-test-deploy"
  max_session_duration = 7200
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect = "Allow", Action = "sts:AssumeRoleWithWebIdentity", Principal = { Federated = local.oidc_arn },
    Condition = { StringEquals = {
      "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com",
      "token.actions.githubusercontent.com:sub" = "repo:${var.github_repository}:ref:refs/heads/main"
    } }
  }] })
}

resource "aws_iam_role_policy" "deploy" {
  name = "project-deployment"
  role = aws_iam_role.deploy.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Sid = "StateBucket", Effect = "Allow", Action = ["s3:ListBucket", "s3:GetBucketLocation"], Resource = local.state_arn },
    { Sid = "StateFile", Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject"], Resource = "${local.state_arn}/pipeline/terraform.tfstate" },
    { Sid = "StateLock", Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"], Resource = "${local.state_arn}/pipeline/terraform.tfstate.tflock" },
    { Sid = "ProjectBuckets", Effect = "Allow", Action = ["s3:*"], Resource = [local.lake_arn, "${local.lake_arn}/*"] },
    { Sid = "ProjectGlue", Effect = "Allow", Action = ["glue:*"], Resource = [
      "${format(local.regional, "glue")}:catalog", "${format(local.regional, "glue")}:database/poc_carteira_dev_*",
      "${format(local.regional, "glue")}:table/poc_carteira_dev_*/*", "${format(local.regional, "glue")}:job/${local.name}-*",
      "${format(local.regional, "glue")}:workflow/${local.name}", "${format(local.regional, "glue")}:trigger/${local.name}-*"
    ] },
    { Sid = "ProjectQueues", Effect = "Allow", Action = ["sqs:*"], Resource = "${format(local.regional, "sqs")}:${local.name}-*" },
    { Sid = "ProjectAthena", Effect = "Allow", Action = ["athena:*"], Resource = ["${format(local.regional, "athena")}:workgroup/${local.name}*", "${format(local.regional, "athena")}:datacatalog/AwsDataCatalog"] },
    { Sid = "ProjectLambda", Effect = "Allow", Action = ["lambda:*"], Resource = ["${format(local.regional, "lambda")}:function:${local.name}-*", "${format(local.regional, "lambda")}:event-source-mapping:*"] },
    { Sid = "CreateMapping", Effect = "Allow", Action = ["lambda:CreateEventSourceMapping"], Resource = "*", Condition = { ArnEquals = { "lambda:FunctionArn" = "${format(local.regional, "lambda")}:function:${local.name}-dispatcher" } } },
    { Sid = "ListMappings", Effect = "Allow", Action = ["lambda:ListEventSourceMappings"], Resource = "*" },
    { Sid = "ProjectRoles", Effect = "Allow", Action = ["iam:GetRole", "iam:CreateRole", "iam:DeleteRole", "iam:UpdateRole", "iam:UpdateAssumeRolePolicy", "iam:TagRole", "iam:UntagRole", "iam:ListRoleTags", "iam:ListRolePolicies", "iam:GetRolePolicy", "iam:PutRolePolicy", "iam:DeleteRolePolicy", "iam:ListAttachedRolePolicies", "iam:ListInstanceProfilesForRole"], Resource = ["${local.base}:iam::${var.aws_account_id}:role/${local.name}-glue", "${local.base}:iam::${var.aws_account_id}:role/${local.name}-dispatcher"] },
    { Sid = "ProjectPolicy", Effect = "Allow", Action = ["iam:GetPolicy", "iam:GetPolicyVersion", "iam:ListPolicyVersions", "iam:CreatePolicy", "iam:CreatePolicyVersion", "iam:DeletePolicyVersion", "iam:DeletePolicy", "iam:TagPolicy", "iam:UntagPolicy", "iam:ListPolicyTags"], Resource = "${local.base}:iam::${var.aws_account_id}:policy/${local.name}-bi-read" },
    { Sid = "PassExecutionRoles", Effect = "Allow", Action = ["iam:PassRole"], Resource = ["${local.base}:iam::${var.aws_account_id}:role/${local.name}-glue", "${local.base}:iam::${var.aws_account_id}:role/${local.name}-dispatcher"], Condition = { StringEquals = { "iam:PassedToService" = ["glue.amazonaws.com", "lambda.amazonaws.com"] } } },
    { Sid = "ReadLogGroups", Effect = "Allow", Action = ["logs:DescribeLogGroups"], Resource = "*", Condition = { StringEquals = { "aws:RequestedRegion" = var.aws_region } } },
    { Sid = "ProjectLogs", Effect = "Allow", Action = ["logs:*"], Resource = ["${format(local.regional, "logs")}:log-group:/aws/lambda/${local.name}-dispatcher", "${format(local.regional, "logs")}:log-group:/aws/lambda/${local.name}-dispatcher:*"] },
    { Sid = "ProjectAlarm", Effect = "Allow", Action = ["cloudwatch:PutMetricAlarm", "cloudwatch:DescribeAlarms", "cloudwatch:DeleteAlarms", "cloudwatch:ListTagsForResource", "cloudwatch:TagResource", "cloudwatch:UntagResource"], Resource = "${format(local.regional, "cloudwatch")}:alarm:${local.name}-*" }
  ] })
}

output "role_arn" { value = aws_iam_role.deploy.arn }
output "state_bucket" { value = aws_s3_bucket.state.id }
