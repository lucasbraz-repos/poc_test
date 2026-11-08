output "data_bucket" { value = aws_s3_bucket.lake["data"].id }
output "artifacts_bucket" { value = aws_s3_bucket.lake["artifacts"].id }
output "athena_results_bucket" { value = aws_s3_bucket.lake["athena"].id }
output "workflow_name" { value = aws_glue_workflow.pipeline.name }
output "athena_workgroup" { value = aws_athena_workgroup.pipeline.name }
output "databases" { value = { for layer, db in aws_glue_catalog_database.layer : layer => db.name } }
output "region" { value = var.aws_region }
output "account_id" { value = var.aws_account_id }
output "release_id" { value = var.release_id }
output "queue_url" { value = aws_sqs_queue.input.url }
output "dead_letter_queue_url" { value = aws_sqs_queue.dead_letter.url }
output "max_source_bytes" { value = var.max_source_bytes }
output "max_source_rows" { value = var.max_source_rows }
