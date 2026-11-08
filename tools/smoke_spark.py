"""Executar na imagem oficial Glue: compara regras distribuídas com a base real."""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from cloud.contracts import BRONZE, spark_schema
from cloud.jobs.gold import build_analytics, build_gold, validate
from cloud.jobs.silver import build_silver
from pipeline.cleaning import stable_json
from pipeline.schema import COLUMNS, validate_headers


def main():
    from pyspark.sql import SparkSession
    from test_pipeline import valid_record

    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path)
    args = parser.parse_args()
    spark = (
        SparkSession.builder.master("local[2]")
        .appName("carteira-glue-smoke")
        .config("spark.sql.shuffle.partitions", "2")
        .getOrCreate()
    )
    spark.sparkContext.setLogLevel("ERROR")
    if args.input:
        from pipeline.xlsx import Workbook

        book = Workbook(args.input)
        validate_headers(book.headers)
        rows = [
            (
                "hash",
                "lake",
                "landing/source.xlsx",
                "v1",
                book.sheet,
                n,
                stable_json({c: raw.get(c) for c in COLUMNS}),
                stable_json(formulas),
                book.date_1904,
            )
            for n, raw, formulas in book.rows()
            if n != 1
        ]
    else:
        raw = [
            valid_record(),
            valid_record(AO="pessoa.teste@example.com"),
            valid_record(A="ID002", D="CT002", P="CT002", AR="CT002", BS="CT002", AW="(10,00)"),
            valid_record(A="ID003", AX="800.00"),
            valid_record(
                A="ID004",
                D="CT004",
                P="CT004",
                AR="CT004",
                BS="CT004",
                Y="PJ",
                C=None,
                E=None,
                O="11222333000181",
                X="11222333000181",
                AQ="11222333000181",
                BR="11222333000181",
                Q="EMPRESA TESTE",
                Z="EMPRESA TESTE",
                BT="EMPRESA TESTE",
            ),
        ]
        rows = [
            (
                "hash",
                "lake",
                "landing/source.xlsx",
                "v1",
                "Carteira",
                n + 2,
                stable_json({c: r.get(c) for c in COLUMNS}),
                "[]",
                False,
            )
            for n, r in enumerate(raw)
        ]
    bronze = spark.createDataFrame(rows, spark_schema(BRONZE)).persist()
    silver = build_silver(bronze)
    gold = build_gold(silver["registros_tratados"])
    result = validate(
        silver["registros_tratados"],
        silver["ocorrencias_qualidade"],
        silver["linhagem_origem"],
        gold,
        len(rows),
    )
    gold.update(
        build_analytics(
            silver["registros_tratados"], gold["fato_carteira"], silver["ocorrencias_qualidade"]
        )
    )
    if args.input:
        expected = {
            "linhas_origem": 10740,
            "registros_unicos": 10500,
            "linhas_gold": 10481,
            "duplicatas_removidas": 240,
            "APTO": 3067,
            "REVISAO": 7414,
            "QUARENTENA": 19,
        }
    else:
        expected = {
            "linhas_origem": 5,
            "registros_unicos": 4,
            "linhas_gold": 2,
            "duplicatas_removidas": 1,
            "APTO": 1,
            "REVISAO": 1,
            "QUARENTENA": 2,
        }
    assert result["counts"] == expected, (result["counts"], expected)
    for name, frame in {**silver, **gold}.items():
        frame.write.mode("overwrite").parquet("/tmp/carteira-test-parquet/" + name)
        assert spark.read.parquet("/tmp/carteira-test-parquet/" + name).count() == frame.count()
    print("SPARK_PARQUET_VALIDATED " + json.dumps(result, sort_keys=True, default=str), flush=True)
    spark.stop()


if __name__ == "__main__":
    main()
