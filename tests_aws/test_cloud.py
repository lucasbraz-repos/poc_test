import io
import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from cloud.contracts import TABLES, catalog, fields, load_id, source_id
from cloud.dispatcher import dispatch
from cloud.jobs.bronze import write_bronze_jsonl
from cloud.jobs.gold import gold, publish
from cloud.reporting import render_report
from cloud.runtime import Runtime, json_bytes, positive_option


class Missing(Exception):
    response = {"Error": {"Code": "404"}}


def message(key="landing/base+teste.xlsx", bucket="lake", version="v1"):
    return {
        "Records": [
            {
                "messageId": "m1",
                "body": json.dumps(
                    {
                        "Records": [
                            {
                                "eventName": "ObjectCreated:Put",
                                "s3": {
                                    "bucket": {"name": bucket},
                                    "object": {"key": key, "versionId": version},
                                },
                            }
                        ]
                    }
                ),
            }
        ]
    }


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
            with self.assertRaises(ValueError):
                source_id("b", "k", version, "r")

    def test_load_identity_deduplicates_content_across_object_versions(self):
        self.assertEqual(load_id("a" * 64, "r", "Aba"), load_id("a" * 64, "r", "Aba"))
        self.assertNotEqual(load_id("a" * 64, "r", "Aba"), load_id("a" * 64, "r2", "Aba"))

    def test_publish_registers_control_last_and_receipt_after_commit(self):
        calls = []

        def prepare(summary):
            calls.append("prepared")
            return {"prefix": "runs/original"}

        rt = SimpleNamespace(
            props={"load_id": "load"},
            prefix="runs/original",
            prepare=prepare,
            partition=lambda table: calls.append(table),
            json=lambda key, value: calls.append(key),
            source_receipt=lambda: calls.append("receipt"),
        )
        publish(rt, {})
        self.assertEqual(calls[0], "prepared")
        self.assertEqual(calls[-3:], ["cargas_publicadas", "control/commits/load.json", "receipt"])
        self.assertEqual(set(calls[1:-3]), set(TABLES) - {"cargas_publicadas"})


class ObjectError(Exception):
    def __init__(self, code):
        self.response = {"Error": {"Code": code}}
        super().__init__(code)


class MemoryS3:
    def __init__(self):
        self.objects = {}
        operation = SimpleNamespace(input_shape=SimpleNamespace(members={"IfNoneMatch": None}))
        self.meta = SimpleNamespace(
            service_model=SimpleNamespace(operation_model=lambda name: operation)
        )
        self.fail_commit = False

    def head_object(self, *, Bucket, Key):
        if Key not in self.objects:
            raise Missing()

    def get_object(self, *, Bucket, Key):
        if Key not in self.objects:
            raise Missing()
        return {"Body": io.BytesIO(self.objects[Key])}

    def put_object(self, *, Bucket, Key, Body, **options):
        if options.get("IfNoneMatch") == "*" and Key in self.objects:
            raise ObjectError("PreconditionFailed")
        if self.fail_commit and Key.startswith("control/commits/"):
            raise RuntimeError("falha entre publicação e commit")
        self.objects[Key] = Body


class MemoryGlue:
    class AlreadyExists(Exception):
        pass

    class NotFound(Exception):
        pass

    def __init__(self):
        self.partitions = {}
        self.exceptions = SimpleNamespace(
            AlreadyExistsException=self.AlreadyExists, EntityNotFoundException=self.NotFound
        )

    def get_table(self, **request):
        return {"Table": {"StorageDescriptor": {"Columns": [], "Location": "s3://lake/tables/"}}}

    def create_partition(self, *, DatabaseName, TableName, PartitionInput):
        key = (DatabaseName, TableName, tuple(PartitionInput["Values"]))
        if key in self.partitions:
            raise self.AlreadyExists()
        self.partitions[key] = PartitionInput

    def get_partition(self, *, DatabaseName, TableName, PartitionValues):
        key = (DatabaseName, TableName, tuple(PartitionValues))
        if key not in self.partitions:
            raise self.NotFound()
        return {"Partition": self.partitions[key]}


def runtime_fixture(run="original", s3=None, glue=None):
    rt = Runtime.__new__(Runtime)
    rt.bucket, rt.run_id, rt.prefix = "lake", run, f"runs/{run}"
    rt.props = {"load_id": "load", "source_id": f"source-{run}"}
    rt.args = {
        "RELEASE_ID": "release",
        **{f"{layer.upper()}_DATABASE": layer for layer in ("bronze", "silver", "gold", "control")},
    }
    rt.s3, rt.glue = s3 or MemoryS3(), glue or MemoryGlue()
    return rt


class PublicationRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.rt = runtime_fixture()
        self.summary = {"load_id": "load", "release_id": "release", "workflow_run_id": "original"}
        self.rt.json("runs/original/meta/ready.json", self.summary)

    def test_existing_partition_accepts_only_the_original_location(self):
        self.rt.partition("fato_carteira")
        self.rt.partition("fato_carteira")
        self.rt.prefix = "runs/other"
        with self.assertRaisesRegex(ValueError, "imutável"):
            self.rt.partition("fato_carteira")

    def test_resume_after_control_registration_preserves_every_location(self):
        self.rt.s3.fail_commit = True
        with self.assertRaisesRegex(RuntimeError, "commit"):
            publish(self.rt, self.summary)
        original_locations = {
            key: value["StorageDescriptor"]["Location"]
            for key, value in self.rt.glue.partitions.items()
        }
        self.assertIn(("control", "cargas_publicadas", ("load",)), original_locations)
        resumed = runtime_fixture("resumed", self.rt.s3, self.rt.glue)
        resumed.props.update(resumed.publication_state("load"))
        self.assertEqual(resumed.props["resume_prefix"], "runs/original")
        resumed.s3.fail_commit = False
        resumed.finish = Mock()
        resumed.write = Mock(side_effect=AssertionError("retomada não pode regravar dados"))
        with patch("cloud.jobs.gold.Runtime", return_value=resumed):
            gold()
        self.assertEqual(
            original_locations,
            {
                key: value["StorageDescriptor"]["Location"]
                for key, value in resumed.glue.partitions.items()
            },
        )
        self.assertEqual(resumed.read_json("control/commits/load.json"), self.summary)
        resumed.write.assert_not_called()

    def test_legacy_published_partition_recovers_original_ready(self):
        self.rt.partition("cargas_publicadas")
        resumed = runtime_fixture("resumed", self.rt.s3, self.rt.glue)
        self.assertEqual(resumed.publication_state("load"), {"resume_prefix": "runs/original"})
        self.assertEqual(resumed.read_json("control/prepared/load.json")["prefix"], "runs/original")

    def test_new_run_cannot_replace_an_existing_preparation(self):
        self.rt.prepare(self.summary)
        other = runtime_fixture("other", self.rt.s3, self.rt.glue)
        self.assertEqual(other.prepare(self.summary)["prefix"], "runs/original")

    def test_existing_commit_uses_compatible_skip_protocol(self):
        self.rt.json("control/commits/load.json", self.summary)
        self.assertEqual(self.rt.publication_state("load"), {"skip_completed": "true"})

    def test_invalid_manifest_is_rejected(self):
        manifest = {
            "load_id": "load",
            "release_id": "release",
            "prefix": "../outside",
            "ready_key": "../outside/meta/ready.json",
        }
        self.rt.json("control/prepared/load.json", manifest)
        with self.assertRaisesRegex(ValueError, "incompatível"):
            self.rt.publication_state("load")


class RuntimeTests(unittest.TestCase):
    def test_decimal_metadata_preserves_precision(self):
        result = json.loads(json_bytes({"saldo": Decimal("18446744073709551614")}))
        self.assertEqual(result["saldo"], "18446744073709551614")
        with self.assertRaises(TypeError):
            json_bytes({"invalid": object()})

    def test_parallelism_options_have_compatible_positive_defaults(self):
        self.assertEqual(positive_option([], "OUTPUT_PARTITIONS", 2), 2)
        self.assertEqual(positive_option(["--OUTPUT_PARTITIONS", "8"], "OUTPUT_PARTITIONS", 2), 8)
        for arguments in (
            ["--OUTPUT_PARTITIONS"],
            ["--OUTPUT_PARTITIONS", "0"],
            ["--OUTPUT_PARTITIONS", "bad"],
        ):
            with self.assertRaises(ValueError):
                positive_option(arguments, "OUTPUT_PARTITIONS", 2)

    def test_bronze_jsonl_preserves_positions_unicode_and_formulas(self):
        book = SimpleNamespace(
            date_1904=True, rows=lambda: iter([(1, {}, []), (4, {"A": "João"}, ["A"])])
        )
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "rows.jsonl"
            self.assertEqual(write_bronze_jsonl(book, destination, {"sheet": "Carteira"}, 10), 1)
            record = json.loads(destination.read_text(encoding="utf-8"))
            self.assertEqual(record["source_row"], 4)
            self.assertEqual(json.loads(record["raw_json"])["A"], "João")
            self.assertEqual(json.loads(record["formulas_json"]), ["A"])
            self.assertTrue(record["date_1904"])
            with self.assertRaisesRegex(ValueError, "limite"):
                write_bronze_jsonl(book, destination, {}, 0)


class DispatcherTests(unittest.TestCase):
    def setUp(self):
        self.s3 = Mock()
        self.s3.head_object.side_effect = Missing()
        self.glue = Mock()
        self.glue.start_workflow_run.return_value = {"RunId": "wr_1"}

    def run_dispatch(self, event):
        return dispatch(
            event, self.s3, self.glue, bucket="lake", workflow="flow", release_id="release"
        )

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
        self.assertEqual(
            self.run_dispatch(message()), {"batchItemFailures": [{"itemIdentifier": "m1"}]}
        )

    def test_unversioned_or_wrong_prefix_is_rejected(self):
        for event in [
            message(version=None),
            message(key="reports/base.xlsx"),
            message(bucket="foreign"),
        ]:
            self.assertEqual(
                self.run_dispatch(event), {"batchItemFailures": [{"itemIdentifier": "m1"}]}
            )
        self.glue.start_workflow_run.assert_not_called()

    def test_test_notification_is_acknowledged(self):
        event = {"Records": [{"messageId": "test", "body": json.dumps({"Event": "s3:TestEvent"})}]}
        self.assertEqual(self.run_dispatch(event), {"batchItemFailures": []})
        self.glue.start_workflow_run.assert_not_called()

    def test_permission_error_is_not_treated_as_missing_commit(self):
        self.s3.head_object.side_effect = PermissionError("denied")
        self.assertEqual(
            self.run_dispatch(message()), {"batchItemFailures": [{"itemIdentifier": "m1"}]}
        )
        self.glue.start_workflow_run.assert_not_called()


class ReportTests(unittest.TestCase):
    def test_report_escapes_metadata_and_does_not_expose_individuals(self):
        summary = {
            "counts": {
                "linhas_origem": 1,
                "registros_unicos": 1,
                "APTO": 1,
                "REVISAO": 0,
                "QUARENTENA": 0,
            },
            "controls": {"saldo": True},
            "gold_database": "gold",
            "load_id": "load",
            "workflow_run_id": "wr",
            "source_key": "<script>alert(1)</script>",
            "source_sha256": "hash",
            "published_at": "utc",
            "bucket": "lake",
        }
        html = render_report(summary)
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("vw_carteira_atual", html)


if __name__ == "__main__":
    unittest.main()
