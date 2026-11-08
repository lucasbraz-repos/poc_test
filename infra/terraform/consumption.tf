resource "aws_athena_workgroup" "bi" {
  name = "${local.name}-bi"
  configuration {
    enforce_workgroup_configuration    = true
    publish_cloudwatch_metrics_enabled = true
    bytes_scanned_cutoff_per_query     = 1073741824
    engine_version { selected_engine_version = "Athena engine version 3" }
    result_configuration {
      output_location = "s3://${aws_s3_bucket.lake["athena"].id}/results/bi/"
      encryption_configuration { encryption_option = "SSE_S3" }
    }
  }
}

# Política disponível para atribuição administrativa; nenhum usuário é criado.
resource "aws_iam_policy" "bi_read" {
  name        = "${local.name}-bi-read"
  description = "Consulta de marts agregados e controle no workgroup BI do projeto."
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["athena:StartQueryExecution", "athena:GetQueryExecution", "athena:GetQueryResults", "athena:StopQueryExecution", "athena:GetWorkGroup"], Resource = aws_athena_workgroup.bi.arn },
    { Effect = "Allow", Action = ["athena:GetDataCatalog"], Resource = "arn:${local.partition}:athena:${var.aws_region}:${var.aws_account_id}:datacatalog/AwsDataCatalog" },
    { Effect = "Allow", Action = ["glue:GetDatabase", "glue:GetDatabases", "glue:GetTable", "glue:GetTables", "glue:GetPartition", "glue:GetPartitions", "glue:BatchGetPartition"], Resource = concat(["${local.glue_base}:catalog"], [for layer in ["gold", "control"] : "${local.glue_base}:database/${aws_glue_catalog_database.layer[layer].name}"], [for layer in ["gold", "control"] : "${local.glue_base}:table/${aws_glue_catalog_database.layer[layer].name}/*"]) },
    { Effect = "Allow", Action = ["s3:GetBucketLocation"], Resource = [aws_s3_bucket.lake["data"].arn, aws_s3_bucket.lake["athena"].arn] },
    { Effect = "Allow", Action = ["s3:ListBucket"], Resource = aws_s3_bucket.lake["data"].arn, Condition = { StringLike = { "s3:prefix" = ["runs/*/gold/mart_*/*", "runs/*/control/cargas_publicadas/*"] } } },
    { Effect = "Allow", Action = ["s3:GetObject", "s3:GetObjectVersion"], Resource = ["${aws_s3_bucket.lake["data"].arn}/runs/*/gold/mart_*/*", "${aws_s3_bucket.lake["data"].arn}/runs/*/control/cargas_publicadas/*"] },
    { Effect = "Allow", Action = ["s3:ListBucket"], Resource = aws_s3_bucket.lake["athena"].arn, Condition = { StringLike = { "s3:prefix" = ["results/bi/*"] } } },
    { Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject", "s3:AbortMultipartUpload"], Resource = "${aws_s3_bucket.lake["athena"].arn}/results/bi/*" }
  ] })
}

output "bi_workgroup" { value = aws_athena_workgroup.bi.name }
output "bi_read_policy_arn" { value = aws_iam_policy.bi_read.arn }
