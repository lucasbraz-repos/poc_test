resource "aws_sqs_queue" "dead_letter" {
  name                      = "${local.name}-dlq"
  message_retention_seconds = 1209600
  sqs_managed_sse_enabled   = true
}

resource "aws_sqs_queue" "input" {
  name                       = "${local.name}-input"
  visibility_timeout_seconds = 180
  message_retention_seconds  = 86400
  receive_wait_time_seconds  = 20
  sqs_managed_sse_enabled    = true
  redrive_policy             = jsonencode({ deadLetterTargetArn = aws_sqs_queue.dead_letter.arn, maxReceiveCount = 100 })
}

resource "aws_sqs_queue_policy" "input" {
  queue_url = aws_sqs_queue.input.url
  policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect    = "Allow", Principal = { Service = "s3.amazonaws.com" }, Action = "sqs:SendMessage",
    Resource  = aws_sqs_queue.input.arn,
    Condition = { ArnEquals = { "aws:SourceArn" = aws_s3_bucket.lake["data"].arn }, StringEquals = { "aws:SourceAccount" = var.aws_account_id } }
  }] })
}

resource "aws_s3_bucket_notification" "input" {
  bucket = aws_s3_bucket.lake["data"].id
  queue {
    queue_arn     = aws_sqs_queue.input.arn
    events        = ["s3:ObjectCreated:*"]
    filter_prefix = "landing/"
    filter_suffix = ".xlsx"
  }
  depends_on = [aws_sqs_queue_policy.input, aws_s3_bucket_versioning.lake]
}

resource "aws_iam_role" "dispatcher" {
  name = "${local.name}-dispatcher"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect = "Allow", Principal = { Service = "lambda.amazonaws.com" }, Action = "sts:AssumeRole"
  }] })
}

resource "aws_cloudwatch_log_group" "dispatcher" {
  name              = "/aws/lambda/${local.name}-dispatcher"
  retention_in_days = 14
}

resource "aws_iam_role_policy" "dispatcher" {
  role = aws_iam_role.dispatcher.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"], Resource = aws_sqs_queue.input.arn },
    { Effect = "Allow", Action = ["glue:StartWorkflowRun"], Resource = "${local.glue_base}:workflow/${aws_glue_workflow.pipeline.name}" },
    { Effect = "Allow", Action = ["s3:GetObject"], Resource = "${aws_s3_bucket.lake["data"].arn}/control/sources/*" },
    # HeadObject de recibo ausente só retorna 404 quando ListBucket é permitido.
    { Effect = "Allow", Action = ["s3:ListBucket"], Resource = aws_s3_bucket.lake["data"].arn },
    { Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"], Resource = "${aws_cloudwatch_log_group.dispatcher.arn}:*" }
  ] })
}

resource "aws_lambda_function" "dispatcher" {
  function_name    = "${local.name}-dispatcher"
  role             = aws_iam_role.dispatcher.arn
  runtime          = "python3.11"
  handler          = "cloud.dispatcher.handler"
  s3_bucket        = aws_s3_object.library.bucket
  s3_key           = aws_s3_object.library.key
  source_code_hash = filebase64sha256("${local.build}/pipeline.zip")
  timeout          = 30
  memory_size      = 128
  environment {
    variables = {
      DATA_BUCKET   = aws_s3_bucket.lake["data"].id
      WORKFLOW_NAME = aws_glue_workflow.pipeline.name
      RELEASE_ID    = var.release_id
    }
  }
  depends_on = [aws_iam_role_policy.dispatcher, aws_cloudwatch_log_group.dispatcher, aws_glue_trigger.bronze, aws_glue_trigger.next]
}

resource "aws_lambda_event_source_mapping" "input" {
  event_source_arn        = aws_sqs_queue.input.arn
  function_name           = aws_lambda_function.dispatcher.arn
  batch_size              = 1
  function_response_types = ["ReportBatchItemFailures"]
  scaling_config { maximum_concurrency = 2 }
}

resource "aws_cloudwatch_metric_alarm" "dead_letters" {
  alarm_name          = "${local.name}-unprocessed-inputs"
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  dimensions          = { QueueName = aws_sqs_queue.dead_letter.name }
}
