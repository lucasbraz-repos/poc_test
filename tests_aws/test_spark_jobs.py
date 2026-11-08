"""Integração real das transformações, executada na imagem oficial do Glue 5."""

import importlib.util
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from cloud.contracts import BRONZE, ISSUES, SILVER, TABLES, spark_schema
from cloud.jobs.gold import build_analytics, build_gold, dimension_id, validate
from cloud.jobs.silver import build_silver
from pipeline.cleaning import fingerprint, stable_json
from pipeline.metrics import analytics_queries, register_sqlite_aggregates
from pipeline.schema import COLUMNS


def record(**changes):
    source = {
        "A": "ID001",
        "B": "04252011000110",
        "N": "04252011000110",
        "C": "52998224725",
        "X": "52998224725",
        "AQ": "52998224725",
        "BR": "52998224725",
        "D": "CT001",
        "P": "CT001",
        "AR": "CT001",
        "BS": "CT001",
        "Y": "PF",
        "E": "João Silva",
        "Z": "João Silva",
        "BT": "João Silva",
        "CA": "08-01-2026",
        "AU": "01-02-2020",
        "AV": "07-01-2024",
        "AS": "Crédito pessoal",
        "AT": None,
        "AW": "1000,00",
        "AX": "950,00",
        "AY": "1100,00",
        "AF": "SP",
    }
    source.update(changes)
    return source


def contract_record(identifier, **changes):
    return record(A=identifier, D=identifier, P=identifier, AR=identifier, BS=identifier, **changes)


@unittest.skipUnless(
    importlib.util.find_spec("pyspark"), "Requer a imagem oficial AWS Glue ou PySpark compatível."
)
class SparkJobsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from pyspark.sql import SparkSession

        cls.spark = (
            SparkSession.builder.master("local[2]")
            .appName("carteira-job-tests")
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

    def tearDown(self):
        for frame in reversed(self.cached):
            frame.unpersist()

    def stages(self, raw):
        rows = [
            (
                "hash",
                "lake",
                "landing/base.xlsx",
                "v1",
                "Carteira",
                position + 2,
                stable_json({column: item.get(column) for column in COLUMNS}),
                "[]",
                False,
            )
            for position, item in enumerate(raw)
        ]
        bronze = self.spark.createDataFrame(rows, spark_schema(BRONZE))
        silver = build_silver(bronze, self.cached)
        gold = build_gold(silver["registros_tratados"], self.cached)
        return silver, gold

    def test_native_hashes_match_python_for_null_unicode_and_escaping(self):
        from pyspark.sql import functions as F

        values = [
            ("Crédito", "São Paulo"),
            (None, None),
            ("", None),
            ('aspas"barra\\/\n\t\r\b\f\x01', "João 😀\u2028"),
            ("A", "B|C"),
            ("A|B", "C"),
        ]
        frame = self.spark.createDataFrame(values, "a string, b string")
        actual = frame.select(
            "a", "b", dimension_id(F.lit("produto"), F.col("a"), F.col("b")).alias("id")
        ).collect()
        for row in actual:
            self.assertEqual(row.id, fingerprint(["produto", row.a, row.b]))
        self.assertNotIn(
            "PythonUDF",
            frame.select(dimension_id(F.lit("produto"), F.col("a"), F.col("b")))
            ._jdf.queryExecution()
            .executedPlan()
            .toString(),
        )

    def test_financial_totals_above_bigint_and_marts_match_sqlite(self):
        maximum = "92233720368547758.07"
        first = record(AW=maximum, AX=maximum, AY=maximum)
        raw = [
            first,
            first.copy(),
            contract_record("CT002", AW=maximum, AX=maximum, AY=maximum),
            contract_record("CT003", AW="-1,00", AX="-1,00", AY="-1,00"),
            contract_record("CT004", B=None, N=None),
        ]
        silver, gold = self.stages(raw)
        result = validate(
            silver["registros_tratados"],
            silver["ocorrencias_qualidade"],
            silver["linhagem_origem"],
            gold,
            len(raw),
        )
        self.assertEqual(
            result["counts"],
            {
                "linhas_origem": 5,
                "registros_unicos": 4,
                "linhas_gold": 3,
                "duplicatas_removidas": 1,
                "APTO": 2,
                "REVISAO": 1,
                "QUARENTENA": 1,
            },
        )
        self.assertEqual(
            result["balances"]["valor_contabil_centavos"]["gold"], 2 * (2**63 - 1) - 100
        )
        marts = build_analytics(
            silver["registros_tratados"], gold["fato_carteira"], silver["ocorrencias_qualidade"]
        )
        self.compare_sqlite_marts(
            silver["registros_tratados"],
            gold["fato_carteira"],
            silver["ocorrencias_qualidade"],
            marts,
        )
        with tempfile.TemporaryDirectory() as directory:
            for name, frame in {**silver, **gold, **marts}.items():
                location = str(Path(directory) / name)
                frame.select(*TABLES[name][1]).write.parquet(location)
                restored = self.spark.read.schema(spark_schema(TABLES[name][1])).parquet(location)
                self.assertEqual(restored.count(), frame.count(), name)
            indicators = self.spark.read.parquet(str(Path(directory) / "mart_indicadores_carteira"))
            published = indicators.filter("universo='CARTEIRA_PUBLICADA'").first()
            self.assertEqual(published.saldo_contabil_centavos, 2 * (2**63 - 1) - 100)

    def test_all_quarantined_preserves_empty_gold_and_zero_universes(self):
        silver, gold = self.stages([record(B=None, N=None)])
        result = validate(
            silver["registros_tratados"],
            silver["ocorrencias_qualidade"],
            silver["linhagem_origem"],
            gold,
            1,
        )
        self.assertEqual(result["counts"]["linhas_gold"], 0)
        marts = build_analytics(
            silver["registros_tratados"], gold["fato_carteira"], silver["ocorrencias_qualidade"]
        )
        scopes = {
            row.universo: row.asDict() for row in marts["mart_indicadores_carteira"].collect()
        }
        self.assertEqual(
            {key: row["quantidade"] for key, row in scopes.items()},
            {"BASE_TRATADA": 1, "CARTEIRA_PUBLICADA": 0, "APTOS": 0},
        )
        self.assertIsNone(scopes["APTOS"]["saldo_contabil_centavos"])

    def test_conflict_details_are_bounded_and_deterministic(self):
        source = [record(A=f"ID{number:03d}") for number in range(40)]
        silver, _ = self.stages(source)
        conflicts = (
            silver["ocorrencias_qualidade"].filter("codigo='CHAVE_NEGOCIO_CONFLITANTE'").collect()
        )
        self.assertEqual(len(conflicts), 40)
        details = {row.detalhe_json for row in conflicts}
        self.assertEqual(len(details), 1)
        detail = json.loads(details.pop())
        self.assertEqual(detail["quantidade_registros"], 40)
        self.assertNotIn("registros_do_grupo", detail)
        self.assertLess(len(stable_json(detail)), 600)
        self.assertEqual(
            silver["registros_tratados"].filter("status_qualidade='QUARENTENA'").count(), 40
        )

    def test_out_of_range_values_produce_quality_occurrences(self):
        silver, _ = self.stages([record(AW="92233720368547758.08")])
        row = silver["registros_tratados"].first()
        self.assertIsNone(row.valor_original_centavos)
        self.assertEqual(row.status_qualidade, "REVISAO")
        self.assertGreater(
            silver["ocorrencias_qualidade"]
            .filter("campo='valor_original_centavos' AND severidade='WARNING'")
            .count(),
            0,
        )

    def compare_sqlite_marts(self, records, facts, issues, marts):
        def normalized(rows):
            return sorted(
                stable_json(
                    {key: None if value is None else str(value) for key, value in row.items()}
                )
                for row in rows
            )

        with sqlite3.connect(":memory:") as connection:
            connection.row_factory = sqlite3.Row
            register_sqlite_aggregates(connection)
            for name, frame, schema in (
                ("source_records", records, SILVER),
                ("source_facts", facts, TABLES["fato_carteira"][1]),
                ("source_issues", issues, ISSUES),
            ):
                columns = list(schema)
                definitions = ",".join(
                    f'"{column}" {"INTEGER" if schema[column] == "bigint" else "TEXT"}'
                    for column in columns
                )
                connection.execute(f"CREATE TABLE {name} ({definitions})")
                placeholders = ",".join("?" for _ in columns)
                connection.executemany(
                    f"INSERT INTO {name} VALUES ({placeholders})",
                    [tuple(row[column] for column in columns) for row in frame.collect()],
                )
            for name, query in analytics_queries(
                "sqlite", "source_records", "source_facts", "source_issues"
            ).items():
                expected = [dict(row) for row in connection.execute(query)]
                actual = [row.asDict() for row in marts[name].collect()]
                self.assertEqual(normalized(actual), normalized(expected), name)


if __name__ == "__main__":
    unittest.main()
