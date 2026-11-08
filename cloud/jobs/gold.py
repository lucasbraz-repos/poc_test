"""Job Gold: modelo dimensional, métricas e publicação recuperável no Athena."""

from cloud.contracts import FACT, TABLES, fields, spark_schema
from cloud.reporting import render_report
from cloud.runtime import Runtime, now

MONEY_FIELDS = ("valor_original_centavos", "valor_contabil_centavos", "valor_face_centavos")


def dimension_id(*columns):
    """Calcula o mesmo SHA-256 do JSON canônico Python, usando expressões Spark."""
    from pyspark.sql import functions as F

    return F.sha2(F.to_json(F.array(*columns)), 256)


def build_gold(silver, cached=None):
    """Modela somente registros elegíveis, com uma identidade estável por dimensão."""
    from pyspark.sql import functions as F

    base = (
        silver.filter("status_qualidade <> 'QUARENTENA'")
        .withColumn("cedente_id", dimension_id(F.lit("cedente"), F.col("cnpj_cedente")))
        .withColumn(
            "devedor_id",
            dimension_id(F.lit("devedor"), F.col("tipo_pessoa"), F.col("documento_devedor")),
        )
        .withColumn(
            "produto_id", dimension_id(F.lit("produto"), F.col("produto"), F.col("subproduto"))
        )
        .persist()
    )
    if cached is not None:
        cached.append(base)
    fact = base.select(*FACT)
    cedente = base.select(
        "cedente_id",
        F.col("cnpj_cedente").alias("cnpj"),
        F.coalesce("cnpj_cedente_valido", F.lit(0)).cast("long").alias("documento_valido"),
    ).dropDuplicates()
    devedor = (
        base.groupBy("devedor_id", "documento_devedor", "tipo_pessoa")
        .agg(
            F.when(F.countDistinct("nome_devedor") == 1, F.max("nome_devedor")).alias("nome"),
            F.coalesce(F.max("documento_devedor_valido"), F.lit(0))
            .cast("long")
            .alias("documento_valido"),
        )
        .withColumnRenamed("documento_devedor", "documento")
    )
    produto = base.select("produto_id", "produto", "subproduto").dropDuplicates()
    dates = (
        base.select(
            F.explode(
                F.array(*[F.col(name) for name in fields() if name.startswith("data_")])
            ).alias("data")
        )
        .filter("data IS NOT NULL")
        .distinct()
        .withColumn("ano", F.year("data").cast("long"))
        .withColumn("mes", F.month("data").cast("long"))
        .withColumn("dia", F.dayofmonth("data").cast("long"))
        .withColumn("trimestre", F.quarter("data").cast("long"))
    )
    return {
        "fato_carteira": fact,
        "dim_cedente": cedente,
        "dim_devedor": devedor,
        "dim_produto": produto,
        "dim_data": dates,
    }


def build_analytics(records, facts, issues):
    """Executa os mesmos contratos analíticos usados pela versão local."""
    from pipeline.metrics import analytics_queries

    spark = records.sparkSession
    records.createOrReplaceTempView("_metrics_records")
    facts.createOrReplaceTempView("_metrics_facts")
    issues.createOrReplaceTempView("_metrics_issues")
    try:
        return {name: spark.sql(query) for name, query in analytics_queries(engine="spark").items()}
    finally:
        for view in ("_metrics_records", "_metrics_facts", "_metrics_issues"):
            spark.catalog.dropTempView(view)


def validate(silver, issues, lineage, gold, source_rows):
    """Consolida contagens/saldos e impede publicação com integridade quebrada."""
    from pyspark.sql import functions as F

    def sum_cents(name, condition=None):
        value = F.col(name).cast("decimal(38,0)")
        return F.sum(value if condition is None else F.when(condition, value))

    pf_fields = (
        "data_nascimento",
        "empresa_empregadora",
        "sexo",
        "estado_civil",
        "profissao",
        "nome_mae",
        "obito",
        "renda_centavos",
    )
    pj_fields = (
        "data_abertura",
        "situacao_empresa",
        "socio_1_documento",
        "socio_1_nome",
        "socio_2_documento",
        "socio_2_nome",
    )
    inapplicable = (
        "(tipo_pessoa='PF' AND ("
        + " OR ".join(f"{name} IS NOT NULL" for name in pj_fields)
        + ")) OR (tipo_pessoa='PJ' AND ("
        + " OR ".join(f"{name} IS NOT NULL" for name in pf_fields)
        + "))"
    )
    quarantine = F.col("status_qualidade") == "QUARENTENA"
    silver_stats = (
        silver.agg(
            F.count("*").alias("total"),
            F.sum("linhas_origem").alias("source_rows"),
            F.sum(F.when(F.expr(inapplicable), 1).otherwise(0)).alias("inapplicable"),
            *[
                F.sum(F.when(F.col("status_qualidade") == status, 1).otherwise(0)).alias(status)
                for status in ("APTO", "REVISAO", "QUARENTENA")
            ],
            *[sum_cents(name).alias(name) for name in MONEY_FIELDS],
            *[sum_cents(name, quarantine).alias(f"quarantine_{name}") for name in MONEY_FIELDS],
        )
        .first()
        .asDict()
    )
    fact = gold["fato_carteira"]
    fact_stats = (
        fact.agg(
            F.count("*").alias("total"), *[sum_cents(name).alias(name) for name in MONEY_FIELDS]
        )
        .first()
        .asDict()
    )
    counts = {status: silver_stats[status] or 0 for status in ("APTO", "REVISAO", "QUARENTENA")}
    severity = issues.groupBy("registro_id").agg(
        F.max(
            F.when(F.col("severidade") == "ERROR", 2)
            .when(F.col("severidade") == "WARNING", 1)
            .otherwise(0)
        ).alias("severity")
    )
    issue_stats = (
        fact.join(severity, "registro_id", "left")
        .agg(
            F.sum(F.when(F.col("severity") == 2, 1).otherwise(0)).alias("errors"),
            F.sum(
                F.when(
                    (F.col("status_qualidade") == "APTO") & (F.col("severity") > 0), 1
                ).otherwise(0)
            ).alias("apt_with_issues"),
        )
        .first()
        .asDict()
    )
    checks = {
        "linhagem_completa": lineage.count() == source_rows,
        "reconciliacao_linhas": (silver_stats["source_rows"] or 0) == source_rows,
        "reconciliacao_carteira": silver_stats["total"]
        == fact_stats["total"] + counts["QUARENTENA"],
        "status_validos": silver_stats["total"] == sum(counts.values()),
        "grao_unico": not fact.groupBy("cedente_id", "numero_contrato", "data_corte")
        .count()
        .filter("count>1")
        .limit(1)
        .count(),
        "sem_erro_na_gold": not issue_stats["errors"],
        "aptos_sem_alertas": not issue_stats["apt_with_issues"],
        "pf_pj_coerente": not silver_stats["inapplicable"],
    }
    for key, dim, foreign in (
        ("cedente", "dim_cedente", "cedente_id"),
        ("devedor", "dim_devedor", "devedor_id"),
        ("produto", "dim_produto", "produto_id"),
    ):
        checks[f"referencia_{key}"] = (
            not fact.join(gold[dim], foreign, "left_anti").limit(1).count()
        )
        checks[f"dimensao_{key}_unica"] = (
            not gold[dim].groupBy(foreign).count().filter("count>1").limit(1).count()
        )
    checks["referencia_data_corte"] = (
        not fact.select(F.col("data_corte").alias("data"))
        .join(gold["dim_data"], "data", "left_anti")
        .limit(1)
        .count()
    )
    balances = {}
    for name in MONEY_FIELDS:
        balances[name] = {
            "silver": silver_stats[name] or 0,
            "gold": fact_stats[name] or 0,
            "quarentena": silver_stats[f"quarantine_{name}"] or 0,
        }
        checks[f"saldo_{name}"] = (
            balances[name]["silver"] == balances[name]["gold"] + balances[name]["quarentena"]
        )
    if not all(checks.values()):
        raise ValueError(
            "Validações de publicação falharam: "
            + ", ".join(key for key, passed in checks.items() if not passed)
        )
    return {
        "counts": {
            "linhas_origem": source_rows,
            "registros_unicos": silver_stats["total"],
            "linhas_gold": fact_stats["total"],
            "duplicatas_removidas": source_rows - silver_stats["total"],
            **counts,
        },
        "controls": checks,
        "balances": balances,
    }


def publish(rt, summary):
    """Fixa a execução física antes de liberar as partições para consumo."""
    prepared = rt.prepare(summary)
    if prepared["prefix"] != rt.prefix:
        rt.prefix = prepared["prefix"]
        summary = rt.read_json(prepared["ready_key"])
    # A partição de controle é registrada por último e nunca muda de localização.
    for table in TABLES:
        if table != "cargas_publicadas":
            rt.partition(table)
    rt.partition("cargas_publicadas")
    rt.json(f"control/commits/{rt.props['load_id']}.json", summary)
    rt.source_receipt()


def gold():
    """Modela, valida e publica; retries reutilizam a preparação original."""
    rt = Runtime()
    cached = []
    try:
        if rt.props.get("skip_completed") == "true":
            rt.source_receipt()
            rt.finish()
            return
        if rt.props.get("resume_prefix"):
            rt.prefix = rt.props["resume_prefix"]
        ready_key = f"{rt.prefix}/meta/ready.json"
        if rt.exists(ready_key):
            publish(rt, rt.read_json(ready_key))
            rt.finish()
            return
        if rt.props.get("resume_prefix"):
            raise ValueError("A preparação de publicação não possui ready.json.")

        from pyspark.sql import functions as F

        records = rt.read("silver", "registros_tratados").persist()
        cached.append(records)
        issues = rt.read("silver", "ocorrencias_qualidade").persist()
        cached.append(issues)
        lineage = rt.read("silver", "linhagem_origem")
        meta = rt.read_json(f"{rt.prefix}/meta/bronze.json")
        frames = build_gold(records, cached)
        summary = validate(records, issues, lineage, frames, meta["source_rows"])
        frames.update(build_analytics(records, frames["fato_carteira"], issues))
        summary.update(meta)
        summary.update(
            load_id=rt.props["load_id"],
            release_id=rt.args["RELEASE_ID"],
            workflow_run_id=rt.run_id,
            published_at=now(),
            bucket=rt.bucket,
            gold_database=rt.args["GOLD_DATABASE"],
        )
        summary["quality_counts"] = [
            row.asDict()
            for row in issues.groupBy("codigo", "severidade")
            .agg(
                F.count("*").alias("ocorrencias"), F.countDistinct("registro_id").alias("registros")
            )
            .orderBy("codigo", "severidade")
            .collect()
        ]
        summary["analytics"] = {}
        summary["analytics_truncated"] = {}
        for label, table in (
            ("indicadores", "mart_indicadores_carteira"),
            ("resumo", "mart_resumo_carteira"),
            ("qualidade", "mart_qualidade"),
        ):
            frame = frames[table].persist()
            cached.append(frame)
            rows = frame.limit(1001).collect()
            summary["analytics"][label] = [row.asDict() for row in rows[:1000]]
            summary["analytics_truncated"][label] = len(rows) > 1000
        for name, frame in frames.items():
            rt.write(frame, "gold", name)
        count = summary["counts"]
        control = {
            "source_sha256": meta["source_sha256"],
            "release_id": rt.args["RELEASE_ID"],
            "workflow_run_id": rt.run_id,
            "published_at": summary["published_at"],
            "source_key": meta["source_key"],
            "source_version_id": meta["source_version_id"],
            "registros_total": count["registros_unicos"],
            "registros_gold": count["linhas_gold"],
            "aptos": count["APTO"],
            "revisao": count["REVISAO"],
            "quarentena": count["QUARENTENA"],
        }
        rt.write(
            rt.spark.createDataFrame([control], spark_schema(TABLES["cargas_publicadas"][1])),
            "control",
            "cargas_publicadas",
        )
        report_key = f"reports/{rt.props['load_id']}/relatorio.html"
        rt.s3.put_object(
            Bucket=rt.bucket,
            Key=report_key,
            Body=render_report(summary).encode("utf-8"),
            ContentType="text/html; charset=utf-8",
            ServerSideEncryption="AES256",
        )
        rt.json(f"reports/{rt.props['load_id']}/qualidade.json", summary)
        rt.json(ready_key, summary)
        publish(rt, summary)
        rt.finish()
    finally:
        for frame in reversed(cached):
            frame.unpersist()


if __name__ == "__main__":
    gold()
