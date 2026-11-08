"""Verifica headers assinados no SDK real, sem acesso a serviços externos."""

import importlib.util
import unittest
from unittest.mock import Mock

from cloud.runtime import put_if_absent


@unittest.skipUnless(importlib.util.find_spec("boto3"), "Requer o SDK da imagem Glue.")
class SdkCompatibilityTests(unittest.TestCase):
    def test_conditional_header_is_signed_and_removed_after_the_request(self):
        import boto3
        from botocore.awsrequest import AWSResponse
        from botocore.exceptions import ClientError

        client = boto3.client(
            "s3", region_name="us-east-1", aws_access_key_id="test", aws_secret_access_key="test"
        )
        members = client.meta.service_model.operation_model("PutObject").input_shape.members
        saved = members.pop("IfNoneMatch", None)
        requests = []

        def respond(request, **kwargs):
            requests.append(request)
            failed = len(requests) == 2
            body = b"<Error><Code>PreconditionFailed</Code></Error>" if failed else b""
            raw = Mock()
            raw.stream.return_value = [body]
            return AWSResponse(request.url, 412 if failed else 200, {}, raw)

        client.meta.events.register("before-send.s3.PutObject", respond)
        try:
            parameters = dict(Bucket="example-bucket", Key="prepared", Body=b"{}")
            put_if_absent(client, **parameters)
            with self.assertRaises(ClientError) as caught:
                put_if_absent(client, **parameters)
            self.assertEqual(caught.exception.response["Error"]["Code"], "PreconditionFailed")
            client.put_object(**parameters)
            self.assertEqual(requests[0].headers["If-None-Match"], b"*")
            self.assertIn(b"if-none-match", requests[0].headers["Authorization"])
            self.assertNotIn("If-None-Match", requests[2].headers)
        finally:
            if saved is not None:
                members["IfNoneMatch"] = saved
            client.close()
