"""Regressões do deploy com credenciais temporárias e janela de execução."""

import os
import subprocess
import unittest
from unittest.mock import patch

from tools import aws_cli, deploy_aws, release_aws


class CICDTests(unittest.TestCase):
    def test_environment_credentials_do_not_require_a_local_profile(self):
        with patch.object(
            aws_cli.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "{}")
        ) as run:
            aws_cli.aws(None, "us-east-1", "sts", "get-caller-identity")
        self.assertNotIn("--profile", run.call_args.args[0])
        with (
            patch.dict(os.environ, {"AWS_ACCESS_KEY_ID": "temporary"}),
            patch.object(aws_cli.subprocess, "run") as run,
        ):
            environment = aws_cli.terraform_environment(None)
        self.assertEqual(environment["AWS_ACCESS_KEY_ID"], "temporary")
        run.assert_not_called()

    def test_busy_pipeline_is_retried_but_permission_errors_are_not(self):
        with (
            patch.object(
                deploy_aws, "assert_deployment_idle", side_effect=[RuntimeError("busy"), None]
            ) as check,
            patch.object(deploy_aws.time, "sleep"),
        ):
            deploy_aws.wait_for_idle({}, None, minutes=1)
        self.assertEqual(check.call_count, 2)
        with (
            patch.object(
                deploy_aws,
                "assert_deployment_idle",
                side_effect=subprocess.CalledProcessError(1, "aws"),
            ),
            patch.object(deploy_aws.time, "sleep") as sleep,
        ):
            with self.assertRaises(subprocess.CalledProcessError):
                deploy_aws.wait_for_idle({}, None, minutes=1)
        sleep.assert_not_called()

    def test_deploy_does_not_wait_without_an_explicit_timeout(self):
        with (
            patch.object(deploy_aws, "assert_deployment_idle", side_effect=RuntimeError("busy")),
            patch.object(deploy_aws.time, "sleep") as sleep,
        ):
            with self.assertRaisesRegex(RuntimeError, "busy"):
                deploy_aws.wait_for_idle({}, None)
        sleep.assert_not_called()

    def test_smoke_rejects_a_workflow_with_an_old_release(self):
        config = {"region": "us-east-1", "release_id": "expected", "workflow_name": "project"}
        with patch.object(
            release_aws,
            "aws",
            return_value={"Workflow": {"DefaultRunProperties": {"release_id": "old"}}},
        ):
            with self.assertRaisesRegex(ValueError, "Workflow"):
                release_aws.verify_deployed(config, None)

    def test_smoke_rejects_a_job_using_another_script(self):
        config = {"region": "us-east-1", "release_id": "expected", "workflow_name": "project"}
        with patch.object(
            release_aws,
            "aws",
            side_effect=[
                {"Workflow": {"DefaultRunProperties": {"release_id": "expected"}}},
                {
                    "Job": {
                        "DefaultArguments": {"--RELEASE_ID": "expected"},
                        "Command": {"ScriptLocation": "s3://other/old.py"},
                    }
                },
            ],
        ):
            with self.assertRaisesRegex(ValueError, "bronze"):
                release_aws.verify_deployed(config, None)
