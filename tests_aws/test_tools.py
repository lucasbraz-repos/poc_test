"""Regressões operacionais com AWS CLI simulada, sem criar recursos."""

import argparse
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import aws_cli, deploy_aws, enviar_aws
from tools.config import positive_int


class CredentialTests(unittest.TestCase):
    def test_static_credentials_do_not_reuse_inherited_session_tokens(self):
        result = subprocess.CompletedProcess(
            [], 0, stdout='{"AccessKeyId":"test-key","SecretAccessKey":"test-secret"}'
        )
        with (
            patch.dict(os.environ, {"AWS_SESSION_TOKEN": "stale", "AWS_PROFILE": "foreign"}),
            patch.object(aws_cli.subprocess, "run", return_value=result),
        ):
            env = aws_cli.terraform_environment("own")
        self.assertNotIn("AWS_SESSION_TOKEN", env)
        self.assertNotIn("AWS_PROFILE", env)
        self.assertEqual(env["AWS_ACCESS_KEY_ID"], "test-key")


class AthenaTests(unittest.TestCase):
    def test_cli_preserves_unicode_independently_of_windows_locale(self):
        real_run = subprocess.run
        expected = {"produto": "Crédito — revisão 📊"}
        script = (
            "import json,os,sys; "
            f"value={expected!r}; "
            "sys.stdout.buffer.write(json.dumps(value,ensure_ascii=False).encode("
            "os.environ.get('AWS_CLI_OUTPUT_ENCODING','cp1252')))"
        )

        def fake_cli(command, **kwargs):
            return real_run([sys.executable, "-c", script], **kwargs)

        with patch.object(aws_cli.subprocess, "run", side_effect=fake_cli):
            actual = aws_cli.aws("own", "us-east-1", "athena", "get-query-results")
        self.assertEqual(actual, expected)

    def test_result_pagination_drops_only_first_header(self):
        page1 = {
            "ResultSet": {
                "Rows": [{"Data": [{"VarCharValue": "name"}]}, {"Data": [{"VarCharValue": "a"}]}]
            },
            "NextToken": "next",
        }
        page2 = {"ResultSet": {"Rows": [{"Data": [{"VarCharValue": "b"}]}]}}
        with (
            patch.object(aws_cli, "athena_query", return_value="query"),
            patch.object(aws_cli, "aws", side_effect=[page1, page2]) as call,
        ):
            self.assertEqual(
                aws_cli.athena_rows({"region": "us-east-1"}, "own", "select 1"), [["a"], ["b"]]
            )
            self.assertIn("--next-token", call.call_args.args)


class OperationTests(unittest.TestCase):
    def test_terminal_failures_include_all_workflow_counters(self):
        for counter in enviar_aws.FAILURE_COUNTERS:
            with self.subTest(counter=counter):
                run = {"WorkflowRunId": "run", "Status": "COMPLETED", "Statistics": {counter: 1}}
                self.assertIn("falhou", enviar_aws.workflow_failure(run))
                run["Status"] = "RUNNING"
                self.assertIsNone(enviar_aws.workflow_failure(run))

    def test_wait_accepts_recovery_after_transient_completed_failure(self):
        failed = {"WorkflowRunId": "run", "Status": "COMPLETED", "Statistics": {"FailedActions": 1}}
        running = {"WorkflowRunId": "run", "Status": "RUNNING", "Statistics": {}}
        publication = ({"load_id": "load"}, Path("report.html"))
        with (
            tempfile.TemporaryDirectory() as folder,
            patch.object(enviar_aws, "download_publication", side_effect=[None, None, publication]),
            patch.object(enviar_aws, "find_workflow_run", side_effect=[failed, running]),
            patch.object(enviar_aws.time, "sleep"),
        ):
            self.assertEqual(
                enviar_aws.wait_for_publication({}, "own", "source", Path(folder), timeout=10),
                publication,
            )

    def test_timeout_must_be_positive_before_upload(self):
        for value in ["0", "-1", "abc"]:
            with self.assertRaises(argparse.ArgumentTypeError):
                positive_int(value)

    def test_stale_release_is_rejected_before_upload(self):
        config = {"region": "us-east-1", "workflow_name": "flow", "release_id": "new"}
        with patch.object(
            enviar_aws,
            "aws",
            return_value={"Workflow": {"DefaultRunProperties": {"release_id": "old"}}},
        ):
            with self.assertRaisesRegex(ValueError, "nenhum upload"):
                enviar_aws.check_release(config, "own")

    def test_deploy_is_blocked_for_active_runs_and_inflight_inputs(self):
        config = {"region": "us-east-1", "workflow_name": "flow", "queue_url": "url"}
        with patch.object(
            deploy_aws,
            "aws",
            return_value={"Runs": [{"WorkflowRunId": "run", "Status": "RUNNING"}]},
        ):
            with self.assertRaisesRegex(RuntimeError, "execução"):
                deploy_aws.assert_deployment_idle(config, "own")
        with patch.object(
            deploy_aws,
            "aws",
            side_effect=[
                {"Runs": []},
                {"JobRuns": []},
                {"JobRuns": []},
                {"JobRuns": []},
                {"Attributes": {"ApproximateNumberOfMessagesNotVisible": "1"}},
            ],
        ):
            with self.assertRaisesRegex(RuntimeError, "fila"):
                deploy_aws.assert_deployment_idle(config, "own")

    def test_deploy_blocks_retry_even_if_workflow_reports_completed(self):
        config = {"region": "us-east-1", "workflow_name": "flow"}
        with patch.object(
            deploy_aws,
            "aws",
            side_effect=[
                {"Runs": [{"Status": "COMPLETED"}]},
                {"JobRuns": []},
                {"JobRuns": [{"JobRunState": "STARTING"}]},
            ],
        ):
            with self.assertRaisesRegex(RuntimeError, "job em execução"):
                deploy_aws.assert_deployment_idle(config, "own")

    def test_errors_preserve_cause_without_echoing_arguments(self):
        error = subprocess.CalledProcessError(
            1, ["aws", "arbitrary"], stderr="AccessDenied: role cannot read"
        )
        self.assertEqual(aws_cli.format_error(error), "AccessDenied: role cannot read")


if __name__ == "__main__":
    unittest.main()
