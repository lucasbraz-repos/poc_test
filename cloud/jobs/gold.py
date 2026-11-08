"""Job Gold: modelo dimensional, validações e publicação para o Athena."""
from cloud.contracts import FACT, TABLES, fields, spark_schema
from cloud.reporting import render_report
from cloud.runtime import Runtime, now
from pipeline.cleaning import fingerprint


def build_gold(silver):
    """Modela os registros elegíveis em fatos, dimensões e agregados."""
    from pyspark.sql import functions as F
    from pyspark.sql.types import StringType
    identity = F.udf(lambda role, a: fingerprint([role, a]), StringType())
    debtor = F.udf(lambda person, doc: fingerprint(["devedor", person, doc]), StringType())
    product = F.udf(lambda a, b: fingerprint(["produto", a, b]), StringType())
    base = silver.filter("status_qualidade <> 'QUARENTENA'").withColumn("cedente_id", identity(F.lit("cedente"), F.col("cnpj_cedente"))).withColumn("devedor_id", debtor("tipo_pessoa", "documento_devedor")).withColumn("produto_id", product("produto", "subproduto")).persist()
    fact = base.select(*FACT).persist()
    cedente = base.select("cedente_id", F.col("cnpj_cedente").alias("cnpj"), F.coalesce("cnpj_cedente_valido", F.lit(0)).cast("long").alias("documento_valido")).dropDuplicates()
    devedor = base.groupBy("devedor_id", "documento_devedor", "tipo_pessoa").agg(
        F.when(F.countDistinct("nome_devedor") == 1, F.max("nome_devedor")).alias("nome"),
        F.coalesce(F.max("documento_devedor_valido"), F.lit(0)).cast("long").alias("documento_valido")).withColumnRenamed("documento_devedor", "documento")
    produto = base.select("produto_id", "produto", "subproduto").dropDuplicates()
    dates = None
    for name in fields():
        if name.startswith("data_"):
            part = base.select(F.col(name).alias("data")).filter("data IS NOT NULL")
            dates = part if dates is None else dates.unionByName(part)
    dates = dates.distinct().withColumn("ano", F.year("data").cast("long")).withColumn("mes", F.month("data").cast("long")).withColumn("dia", F.dayofmonth("data").cast("long")).withColumn("trimestre", F.quarter("data").cast("long"))
    mart = fact.groupBy("data_corte", "tipo_pessoa", "produto", "faixa_atraso", "status_qualidade").agg(
        F.count("*").alias("quantidade"), F.sum("valor_contabil_centavos").alias("saldo_contabil_centavos"),
        F.sum(F.col("valor_contabil_centavos").isNull().cast("long")).alias("saldos_ausentes"),
        F.sum(F.when(F.col("valor_contabil_centavos") < 0, 1).otherwise(0)).alias("saldos_negativos"))
    return {"fato_carteira": fact, "dim_cedente": cedente, "dim_devedor": devedor, "dim_produto": produto,
            "dim_data": dates, "mart_resumo_carteira": mart}


def validate(silver, issues, lineage, gold, source_rows):
    """Impede a publicação quando contagens, chaves ou saldos divergem."""
    from pyspark.sql import functions as F
    counts = {r.status_qualidade: r["count"] for r in silver.groupBy("status_qualidade").count().collect()}
    total = sum(counts.values())
    fact = gold["fato_carteira"]
    fact_count = fact.count()
    quarantine = silver.filter("status_qualidade='QUARENTENA'")
    checks = {
        "linhagem_completa": lineage.count() == source_rows,
        "reconciliacao_linhas": (silver.agg(F.sum("linhas_origem")).first()[0] or 0) == source_rows,
        "reconciliacao_carteira": total == fact_count + counts.get("QUARENTENA", 0),
        "grao_unico": fact.groupBy("cedente_id", "numero_contrato", "data_corte").count().filter("count>1").count() == 0,
        "sem_erro_na_gold": fact.join(issues.filter("severidade='ERROR'"), "registro_id").count() == 0,
        "aptos_sem_alertas": fact.filter("status_qualidade='APTO'").join(issues.filter("severidade<>'INFO'"), "registro_id").count() == 0,
        "pf_pj_coerente": silver.filter("(tipo_pessoa='PF' AND (data_abertura IS NOT NULL OR socio_1_documento IS NOT NULL OR socio_2_nome IS NOT NULL)) OR (tipo_pessoa='PJ' AND (data_nascimento IS NOT NULL OR sexo IS NOT NULL OR renda_centavos IS NOT NULL OR nome_mae IS NOT NULL))").count() == 0,
    }
    for key, dim, foreign in [("cedente", "dim_cedente", "cedente_id"), ("devedor", "dim_devedor", "devedor_id"), ("produto", "dim_produto", "produto_id")]:
        checks[f"referencia_{key}"] = fact.join(gold[dim], foreign, "left_anti").count() == 0
    for name in ["valor_original_centavos", "valor_contabil_centavos", "valor_face_centavos"]:
        summed = lambda df: df.agg(F.sum(name)).first()[0] or 0
        checks[f"saldo_{name}"] = summed(silver) == summed(fact) + summed(quarantine)
    if not all(checks.values()):
        raise ValueError("Validações de publicação falharam: " + ", ".join(k for k, v in checks.items() if not v))
    return {"counts": {"linhas_origem": source_rows, "registros_unicos": total, "linhas_gold": fact_count,
                       "duplicatas_removidas": source_rows-total, **{s: counts.get(s, 0) for s in ["APTO", "REVISAO", "QUARENTENA"]}}, "controls": checks}


def publish(rt, summary):
    """Retomável: depois de ready.json, os dados publicados não são reescritos."""
    # Todas as partições são registradas antes do controle que libera as views de consumo.
    for table in TABLES:
        if table != "cargas_publicadas":
            rt.partition(table)
    rt.partition("cargas_publicadas")
    rt.json(f"control/commits/{rt.props['load_id']}.json", summary)
    rt.source_receipt()


def gold():
    """Lê a Silver, modela, valida e publica a carga para o Athena."""
    rt = Runtime()
    if rt.props.get("skip_completed") == "true":
        rt.source_receipt()
        rt.finish()
        return
    ready_key = f"{rt.prefix}/meta/ready.json"
    if rt.exists(ready_key):
        publish(rt, rt.read_json(ready_key))
        rt.finish()
        return
    records = rt.read("silver", "registros_tratados").persist()
    issues = rt.read("silver", "ocorrencias_qualidade").persist()
    lineage = rt.read("silver", "linhagem_origem").persist()
    meta = rt.read_json(f"{rt.prefix}/meta/bronze.json")
    frames = build_gold(records)
    summary = validate(records, issues, lineage, frames, meta["source_rows"])
    summary.update(meta)
    summary.update(load_id=rt.props["load_id"], release_id=rt.args["RELEASE_ID"], workflow_run_id=rt.run_id,
                   published_at=now(), bucket=rt.bucket, gold_database=rt.args["GOLD_DATABASE"])
    summary["quality_counts"] = [row.asDict() for row in issues.groupBy("codigo", "severidade").count().collect()]
    for name, frame in frames.items():
        rt.write(frame, "gold", name)
    count = summary["counts"]
    control = {"source_sha256": meta["source_sha256"], "release_id": rt.args["RELEASE_ID"], "workflow_run_id": rt.run_id,
               "published_at": summary["published_at"], "source_key": meta["source_key"], "source_version_id": meta["source_version_id"],
               "registros_total": count["registros_unicos"], "registros_gold": count["linhas_gold"], "aptos": count["APTO"],
               "revisao": count["REVISAO"], "quarentena": count["QUARENTENA"]}
    rt.write(rt.spark.createDataFrame([control], spark_schema(TABLES["cargas_publicadas"][1])), "control", "cargas_publicadas")
    report_key = f"reports/{rt.props['load_id']}/relatorio.html"
    rt.s3.put_object(Bucket=rt.bucket, Key=report_key, Body=render_report(summary).encode(),
                     ContentType="text/html; charset=utf-8", ServerSideEncryption="AES256")
    rt.json(f"reports/{rt.props['load_id']}/qualidade.json", summary)
    rt.json(ready_key, summary)
    publish(rt, summary)
    rt.finish()


if __name__ == "__main__":
    gold()
