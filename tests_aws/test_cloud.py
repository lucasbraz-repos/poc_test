import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from cloud.contracts import TABLES, catalog, fields, load_id, source_id
from cloud.dispatcher import dispatch
from cloud.jobs.gold import publish
from cloud.reporting import render_report


class Missing(Exception):
    response = {"Error": {"Code": "404"}}


def message(key="landing/base+teste.xlsx", bucket="lake", version="v1"):
    return {"Records": [{"messageId": "m1", "body": json.dumps({"Records": [{"eventName": "ObjectCreated:Put",
        "s3": {"bucket": {"name": bucket}, "object": {"key": key, "versionId": version}}}]})}]}


class CloudContractsTests(unittest.TestCase):
    def test_catalog_has_explicit_types_and_all_layers(self):
        self.assertEqual({v[0] for v in TABLES.values()}, {"bronze", "silver", "gold", "control"})
        self.assertEqual(fields()["valor_contabil_centavos"], "bigint")
        self.assertEqual(fields()["documento_devedor"], "string")
        self.assertEqual(set(catalog()), set(TABLES))
        self.assertTrue(all("load_id" not in cols for _, cols in TABLES.values()))

    def test_source_identity_pins_version_and_release(self):
        one = source_id("b", "landing/a.xlsx", "v1", "r1")
        self.assertNotEqual(one, source_id("b", "landing/a.xlsx", "v2", "r1"))
        self.assertNotEqual(one, source_id("b", "landing/a.xlsx", "v1", "r2"))
        for version in [None, "", "null"]:
            with self.assertRaises(ValueError):source_id("b", "k", version, "r")

    def test_load_identity_deduplicates_content_across_object_versions(self):
        self.assertEqual(load_id("a"*64, "r", "Aba"), load_id("a"*64, "r", "Aba"))
        self.assertNotEqual(load_id("a"*64, "r", "Aba"), load_id("a"*64, "r2", "Aba"))

    def test_publish_registers_control_last_and_receipt_after_commit(self):
        calls = []
        rt = SimpleNamespace(props={"load_id": "load"}, partition=lambda table:calls.append(table),
                             json=lambda key, value:calls.append(key), source_receipt=lambda:calls.append("receipt"))
        publish(rt, {})
        self.assertEqual(calls[-3:], ["cargas_publicadas", "control/commits/load.json", "receipt"])
        self.assertEqual(set(calls[:-3]), set(TABLES)-{"cargas_publicadas"})


class DispatcherTests(unittest.TestCase):
    def setUp(self):
        self.s3 = Mock();self.s3.head_object.side_effect = Missing()
        self.glue = Mock();self.glue.start_workflow_run.return_value = {"RunId": "wr_1"}

    def run_dispatch(self, event):
        return dispatch(event, self.s3, self.glue, bucket="lake", workflow="flow", release_id="release")

    def test_s3_upload_starts_workflow_with_exact_version(self):
        self.assertEqual(self.run_dispatch(message()), {"batchItemFailures": []})
        props = self.glue.start_workflow_run.call_args.kwargs["RunProperties"]
        self.assertEqual(props["source_key"], "landing/base teste.xlsx")
        self.assertEqual(props["source_version_id"], "v1")

    def test_duplicate_completed_source_does_not_start_again(self):
        self.s3.head_object.side_effect = None
        self.assertEqual(self.run_dispatch(message()), {"batchItemFailures": []})
        self.glue.start_workflow_run.assert_not_called()

    def test_busy_workflow_keeps_message_for_retry(self):
        self.glue.start_workflow_run.side_effect = RuntimeError("ConcurrentRunsExceeded")
        self.assertEqual(self.run_dispatch(message()), {"batchItemFailures": [{"itemIdentifier": "m1"}]})

    def test_unversioned_or_wrong_prefix_is_rejected(self):
        for event in [message(version=None), message(key="reports/base.xlsx"), message(bucket="foreign")]:
            self.assertEqual(self.run_dispatch(event), {"batchItemFailures": [{"itemIdentifier": "m1"}]})
        self.glue.start_workflow_run.assert_not_called()

    def test_test_notification_is_acknowledged(self):
        event = {"Records": [{"messageId": "test", "body": json.dumps({"Event": "s3:TestEvent"})}]}
        self.assertEqual(self.run_dispatch(event), {"batchItemFailures": []})
        self.glue.start_workflow_run.assert_not_called()

    def test_permission_error_is_not_treated_as_missing_commit(self):
        self.s3.head_object.side_effect = PermissionError("denied")
        self.assertEqual(self.run_dispatch(message()), {"batchItemFailures": [{"itemIdentifier": "m1"}]})
        self.glue.start_workflow_run.assert_not_called()


class ReportTests(unittest.TestCase):
    def test_report_escapes_metadata_and_does_not_expose_individuals(self):
        summary = {"counts": {"linhas_origem": 1, "registros_unicos": 1, "APTO": 1, "REVISAO": 0, "QUARENTENA": 0},
                   "controls": {"saldo": True}, "gold_database": "gold", "load_id": "load", "workflow_run_id": "wr",
                   "source_key": "<script>alert(1)</script>", "source_sha256": "hash", "published_at": "utc", "bucket": "lake"}
        html = render_report(summary)
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("vw_carteira_atual", html)


if __name__ == "__main__":unittest.main()
