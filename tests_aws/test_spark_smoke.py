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
    def test_silver_gold_and_sql_execute_with_three_in_memory_rows(self):
        from pyspark.sql import SparkSession

        spark = (
            SparkSession.builder.master("local[2]")
            .appName("medallion-syntax-smoke")
            .config("spark.sql.shuffle.partitions", "2")
            .config("spark.sql.session.timeZone", "UTC")
            .config("spark.sql.ansi.enabled", "true")
            .getOrCreate()
        )
        spark.sparkContext.setLogLevel("ERROR")
        cached = []
        try:
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
            bronze = spark.createDataFrame(rows, spark_schema(BRONZE))
            silver = build_silver(bronze, cached)
            gold = build_gold(silver["registros_tratados"], cached)
            analytics = build_analytics(
                silver["registros_tratados"], gold["fato_carteira"], silver["ocorrencias_qualidade"]
            )
            for name, frame in {**silver, **gold, **analytics}.items():
                with self.subTest(dataset=name):
                    # Resolver o schema força a análise das expressões e dos nomes.
                    self.assertEqual(set(frame.schema.fieldNames()), set(TABLES[name][1]))
            for frame in (gold["fato_carteira"], analytics["mart_indicadores_carteira"]):
                # Duas ações pequenas também exercitam Python, Spark e SQL em execução.
                sample = frame.limit(3).collect()
                self.assertTrue(sample)
                self.assertLessEqual(len(sample), 3)

        finally:
            for frame in reversed(cached):
                frame.unpersist()
            spark.stop()
