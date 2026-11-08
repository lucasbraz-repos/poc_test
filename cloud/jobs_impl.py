"""Três etapas do Glue Workflow, com publicação após validação completa."""
import hashlib
import json
import tempfile
from pathlib import Path

from cloud.contracts import BRONZE, TABLES, load_id, spark_schema, source_id
from cloud.runtime import Runtime, now
from pipeline.cleaning import stable_json
from pipeline.schema import COLUMNS, validate_headers
from pipeline.xlsx import Workbook


def bronze():
    rt = Runtime()
    key, version = rt.props["source_key"], rt.props["source_version_id"]
    if not key.startswith("landing/") or not key.endswith(".xlsx"):
        raise ValueError("Fonte deve estar em landing/ e ter extensão .xlsx.")
    if source_id(rt.bucket, key, version, rt.args["RELEASE_ID"]) != rt.props["source_id"]:
        raise ValueError("Identificador da fonte não confere.")
    maximum = int(rt.args["MAX_SOURCE_BYTES"])
    obj = rt.s3.get_object(Bucket=rt.bucket, Key=key, VersionId=version)
    if obj["ContentLength"] > maximum:
        raise ValueError("Excel excede o limite configurado para a leitura não distribuída.")
    with tempfile.TemporaryDirectory() as temp:
        source = Path(temp) / "source.xlsx"
        digest, size = hashlib.sha256(), 0
        with source.open("wb") as dest:
            for block in obj["Body"].iter_chunks(1024*1024):
                size += len(block)
                if size > maximum:
                    raise ValueError("Excel excede o limite de bytes.")
                digest.update(block)
                dest.write(block)
        obj["Body"].close()
        book = Workbook(source, rt.props.get("sheet"))
        validate_headers(book.headers)
        identifier = load_id(digest.hexdigest(), rt.args["RELEASE_ID"], book.sheet)
        rt.update(load_id=identifier)
        if rt.exists(f"control/commits/{identifier}.json"):
            rt.update(skip_completed="true")
            rt.finish()
            return
        rt.s3.copy_object(Bucket=rt.bucket, Key=f"bronze/originals/{digest.hexdigest()}.xlsx",
                          CopySource={"Bucket": rt.bucket, "Key": key, "VersionId": version}, ServerSideEncryption="AES256")
        rows = []
        for number, raw, formulas in book.rows():
            if number == 1:
                continue
            if len(rows) >= int(rt.args["MAX_SOURCE_ROWS"]):
                raise ValueError("Excel excede o limite configurado de linhas.")
            rows.append((digest.hexdigest(), rt.bucket, key, version, book.sheet, number,
                         stable_json({c: raw.get(c) for c in COLUMNS}), stable_json(formulas), book.date_1904))
        if not rows:
            raise ValueError("Planilha sem registros.")
        rt.write(rt.spark.createDataFrame(rows, spark_schema(BRONZE)), "bronze", "linhas_originais")
        rt.json(f"{rt.prefix}/meta/bronze.json", {"source_sha256": digest.hexdigest(), "source_rows": len(rows),
                "sheet": book.sheet, "source_key": key, "source_version_id": version, "ingested_at": now()})
    rt.finish()


def silver():
    from cloud.transforms import build_silver
    rt = Runtime()
    if rt.props.get("skip_completed") != "true":
        frames = build_silver(rt.read("bronze", "linhas_originais"))
        for name, frame in frames.items():
            rt.write(frame, "silver", name)
    rt.finish()


def gold():
    from cloud.transforms import build_gold, validate
    from cloud.reporting import render_report
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


def publish(rt, summary):
    """Retomável: depois de ready.json, os dados publicados não são reescritos."""
    # Todas as partições são registradas antes do controle que libera as views de consumo.
    for table in TABLES:
        if table != "cargas_publicadas":
            rt.partition(table)
    rt.partition("cargas_publicadas")
    rt.json(f"control/commits/{rt.props['load_id']}.json", summary)
    rt.source_receipt()
