data "aws_partition" "current" {}

locals {
  name          = "${var.project_name}-${var.environment}"
  bucket_prefix = "${local.name}-${var.aws_account_id}-${substr(sha256(var.aws_region), 0, 6)}"
  catalog       = jsondecode(file("${path.module}/catalog.json"))
  databases     = toset(["bronze", "silver", "gold", "control"])
  build         = abspath("${path.module}/../../build/aws")
  partition     = data.aws_partition.current.partition
  glue_base     = "arn:${local.partition}:glue:${var.aws_region}:${var.aws_account_id}"
}

resource "aws_s3_bucket" "lake" {
  for_each      = toset(["data", "artifacts", "athena"])
  bucket        = "${local.bucket_prefix}-${each.key}"
  force_destroy = false
}

resource "aws_s3_bucket_public_access_block" "lake" {
  for_each                = aws_s3_bucket.lake
  bucket                  = each.value.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "lake" {
  for_each = aws_s3_bucket.lake
  bucket   = each.value.id
  rule { object_ownership = "BucketOwnerEnforced" }
}

resource "aws_s3_bucket_versioning" "lake" {
  for_each = aws_s3_bucket.lake
  bucket   = each.value.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "lake" {
  for_each = aws_s3_bucket.lake
  bucket   = each.value.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

resource "aws_s3_bucket_policy" "tls" {
  for_each = aws_s3_bucket.lake
  bucket   = each.value.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect    = "Deny", Principal = "*", Action = "s3:*",
    Resource  = [each.value.arn, "${each.value.arn}/*"],
    Condition = { Bool = { "aws:SecureTransport" = "false" } }
  }] })
}

resource "aws_s3_bucket_lifecycle_configuration" "athena" {
  bucket = aws_s3_bucket.lake["athena"].id
  rule {
    id     = "query-results-retention"
    status = "Enabled"
    filter { prefix = "results/" }
    expiration { days = 30 }
    noncurrent_version_expiration { noncurrent_days = 30 }
    abort_incomplete_multipart_upload { days_after_initiation = 7 }
  }
}

resource "aws_s3_object" "library" {
  bucket                 = aws_s3_bucket.lake["artifacts"].id
  key                    = "releases/${var.release_id}/pipeline.zip"
  source                 = "${local.build}/pipeline.zip"
  source_hash            = filebase64sha256("${local.build}/pipeline.zip")
  server_side_encryption = "AES256"
}

resource "aws_s3_object" "job" {
  for_each               = toset(["bronze", "silver", "gold"])
  bucket                 = aws_s3_bucket.lake["artifacts"].id
  key                    = "releases/${var.release_id}/${each.key}.py"
  source                 = "${path.module}/../../cloud/jobs/${each.key}.py"
  source_hash            = filebase64sha256("${path.module}/../../cloud/jobs/${each.key}.py")
  server_side_encryption = "AES256"
}

resource "aws_glue_catalog_database" "layer" {
  for_each = local.databases
  name     = "${replace(local.name, "-", "_")}_${each.key}"
  create_table_default_permission {
    permissions = ["ALL"]
    principal { data_lake_principal_identifier = "IAM_ALLOWED_PRINCIPALS" }
  }
}

resource "aws_glue_catalog_table" "dataset" {
  for_each      = local.catalog
  name          = each.key
  database_name = aws_glue_catalog_database.layer[each.value.layer].name
  table_type    = "EXTERNAL_TABLE"
  parameters    = { EXTERNAL = "TRUE", classification = "parquet", "parquet.compression" = "SNAPPY" }
  partition_keys {
    name = "load_id"
    type = "string"
  }
  storage_descriptor {
    location      = "s3://${aws_s3_bucket.lake["data"].id}/catalog/${each.value.layer}/${each.key}/"
    input_format  = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat"
    ser_de_info {
      serialization_library = "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"
      parameters            = { "serialization.format" = "1" }
    }
    dynamic "columns" {
      for_each = each.value.columns
      content {
        name = columns.value.name
        type = columns.value.type
      }
    }
  }
}

resource "aws_iam_role" "glue" {
  name = "${local.name}-glue"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect = "Allow", Principal = { Service = "glue.amazonaws.com" }, Action = "sts:AssumeRole"
  }] })
}

resource "aws_iam_role_policy" "glue" {
  role = aws_iam_role.glue.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["s3:ListBucket", "s3:GetBucketLocation"], Resource = [for b in aws_s3_bucket.lake : b.arn] },
    { Effect = "Allow", Action = ["s3:GetObject", "s3:GetObjectVersion"], Resource = ["${aws_s3_bucket.lake["data"].arn}/*", "${aws_s3_bucket.lake["artifacts"].arn}/releases/*"] },
    { Effect = "Allow", Action = ["s3:PutObject", "s3:DeleteObject", "s3:AbortMultipartUpload"], Resource = [for prefix in ["runs", "bronze/originals", "control", "reports", "temporary"] : "${aws_s3_bucket.lake["data"].arn}/${prefix}/*"] },
    { Effect = "Allow", Action = ["glue:GetWorkflowRunProperties", "glue:PutWorkflowRunProperties"], Resource = "${local.glue_base}:workflow/${local.name}" },
    { Effect = "Allow", Action = ["glue:GetJob", "glue:GetJobRun", "glue:GetJobBookmark", "glue:UpdateJobBookmark"], Resource = "${local.glue_base}:job/${local.name}-*" },
    { Effect = "Allow", Action = ["glue:GetDatabase", "glue:GetTable", "glue:GetPartitions", "glue:GetPartition", "glue:CreatePartition", "glue:UpdatePartition", "glue:BatchCreatePartition"], Resource = concat(["${local.glue_base}:catalog"], [for d in aws_glue_catalog_database.layer : "${local.glue_base}:database/${d.name}"], [for d in aws_glue_catalog_database.layer : "${local.glue_base}:table/${d.name}/*"]) },
    { Effect = "Allow", Action = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents", "logs:AssociateKmsKey"], Resource = "arn:${local.partition}:logs:${var.aws_region}:${var.aws_account_id}:log-group:/aws-glue/*" },
    { Effect = "Allow", Action = ["cloudwatch:PutMetricData"], Resource = "*", Condition = { StringEquals = { "cloudwatch:namespace" = "Glue" } } }
  ] })
}

resource "aws_glue_job" "stage" {
  for_each          = toset(["bronze", "silver", "gold"])
  name              = "${local.name}-${each.key}"
  role_arn          = aws_iam_role.glue.arn
  glue_version      = "5.0"
  worker_type       = "G.1X"
  number_of_workers = var.glue_workers
  timeout           = 30
  max_retries       = 1
  execution_property { max_concurrent_runs = 1 }
  command {
    name            = "glueetl"
    python_version  = "3"
    script_location = "s3://${aws_s3_object.job[each.key].bucket}/${aws_s3_object.job[each.key].key}"
  }
  default_arguments = {
    "--job-language"                 = "python"
    "--job-bookmark-option"          = "job-bookmark-disable"
    "--enable-metrics"               = "true"
    "--enable-observability-metrics" = "true"
    "--extra-py-files"               = "s3://${aws_s3_object.library.bucket}/${aws_s3_object.library.key}"
    "--TempDir"                      = "s3://${aws_s3_bucket.lake["data"].id}/temporary/"
    "--DATA_BUCKET"                  = aws_s3_bucket.lake["data"].id
    "--RELEASE_ID"                   = var.release_id
    "--BRONZE_DATABASE"              = aws_glue_catalog_database.layer["bronze"].name
    "--SILVER_DATABASE"              = aws_glue_catalog_database.layer["silver"].name
    "--GOLD_DATABASE"                = aws_glue_catalog_database.layer["gold"].name
    "--CONTROL_DATABASE"             = aws_glue_catalog_database.layer["control"].name
    "--MAX_SOURCE_BYTES"             = tostring(var.max_source_bytes)
    "--MAX_SOURCE_ROWS"              = tostring(var.max_source_rows)
  }
  depends_on = [aws_iam_role_policy.glue]
}

resource "aws_glue_workflow" "pipeline" {
  name                   = local.name
  max_concurrent_runs    = 1
  default_run_properties = { release_id = var.release_id }
}

resource "aws_glue_trigger" "bronze" {
  name = "${local.name}-start"
  type = "ON_DEMAND"
  # O provider inicia triggers ON_DEMAND na criação quando enabled=true.
  # StartWorkflowRun é responsável pelo início, com a versão da fonte informada.
  enabled       = false
  workflow_name = aws_glue_workflow.pipeline.name
  actions { job_name = aws_glue_job.stage["bronze"].name }
}

resource "aws_glue_trigger" "next" {
  for_each          = { silver = "bronze", gold = "silver" }
  name              = "${local.name}-${each.key}"
  type              = "CONDITIONAL"
  workflow_name     = aws_glue_workflow.pipeline.name
  start_on_creation = true
  actions { job_name = aws_glue_job.stage[each.key].name }
  predicate {
    conditions {
      job_name = aws_glue_job.stage[each.value].name
      state    = "SUCCEEDED"
    }
  }
}

resource "aws_athena_workgroup" "pipeline" {
  name = local.name
  configuration {
    enforce_workgroup_configuration    = true
    publish_cloudwatch_metrics_enabled = true
    bytes_scanned_cutoff_per_query     = 1073741824
    engine_version { selected_engine_version = "Athena engine version 3" }
    result_configuration {
      output_location = "s3://${aws_s3_bucket.lake["athena"].id}/results/"
      encryption_configuration { encryption_option = "SSE_S3" }
    }
  }
}
