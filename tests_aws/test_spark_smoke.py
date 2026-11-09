"""Smoke das expressões Spark/SQL com três linhas, sem Excel, Parquet ou AWS."""

import importlib.util
import unittest

from cloud.contracts import BRONZE, TABLES, spark_schema
from cloud.jobs.gold import build_analytics, build_gold
from cloud.jobs.silver import build_silver
from pipeline.cleaning import stable_json
from pipeline.schema import COLUMNS
from tests_aws.test_spark_jobs import contract_record, record


@unittest.skipUnless(importlib.util.find_spec("pyspark"), "Requer Spark na imagem Glue.")
class MedallionSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from pyspark.sql import SparkSession

        cls.spark = (
            SparkSession.builder.master("local[2]")
            .appName("medallion-syntax-smoke")
            .config("spark.sql.shuffle.partitions", "2")
            .config("spark.sql.session.timeZone", "UTC")
            .config("spark.sql.ansi.enabled", "true")
            .getOrCreate()
        )
        cls.spark.sparkContext.setLogLevel("ERROR")

    @classmethod
    def tearDownClass(cls):
        cls.spark.stop()

    def setUp(self):
        self.cached = []
        first = record()
        raw = [first, first.copy(), contract_record("CT002", AX="-2,00")]
        rows = [
            {
                "source_sha256": "synthetic",
                "source_bucket": "unused",
                "source_key": "memory",
                "source_version_id": "v1",
                "sheet": "memory",
                "source_row": position + 2,
                "raw_json": stable_json({column: item.get(column) for column in COLUMNS}),
                "formulas_json": "[]",
                "date_1904": False,
            }
            for position, item in enumerate(raw)
        ]
        self.bronze = self.spark.createDataFrame(rows, spark_schema(BRONZE))

    def tearDown(self):
        for frame in reversed(self.cached):
            frame.unpersist()

    def check_schemas(self, frames):
        for name, frame in frames.items():
            with self.subTest(dataset=name):
                self.assertEqual(set(frame.schema.fieldNames()), set(TABLES[name][1]))

    def check_sample(self, frame):
        sample = frame.limit(3).collect()
        self.assertTrue(sample)
        self.assertLessEqual(len(sample), 3)

    def test_bronze(self):
        # Confere o contrato em memória; não executa a leitura de Excel/S3.
        self.check_schemas({"linhas_originais": self.bronze})
        self.assertEqual(self.bronze.schema.simpleString(), spark_schema(BRONZE).simpleString())
        self.check_sample(self.bronze)

    def test_silver(self):
        silver = build_silver(self.bronze, self.cached)
        self.check_schemas(silver)
        self.check_sample(silver["registros_tratados"])

    def test_gold(self):
        silver = build_silver(self.bronze, self.cached)
        gold = build_gold(silver["registros_tratados"], self.cached)
        analytics = build_analytics(
            silver["registros_tratados"], gold["fato_carteira"], silver["ocorrencias_qualidade"]
        )
        self.check_schemas({**gold, **analytics})
        self.check_sample(gold["fato_carteira"])
        self.check_sample(analytics["mart_indicadores_carteira"])
