"""CLI: python3 -m pipeline.run --input /caminho/BaseTeste.xlsx."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import os
import shutil
import sqlite3
import sys
import uuid
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

from .cleaning import Cleaner, fingerprint, quality_status, stable_json
from .modeling import build_database, dict_rows
from .parsers import clean_text
from .schema import COLUMNS, FIELD_SOURCES, RULE_VERSION, validate_headers
from .xlsx import Workbook

LOG = logging.getLogger("pipeline")
MONEY_FIELDS = ["valor_original_centavos", "valor_contabil_centavos", "valor_face_centavos"]


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def implementation_hash():
    return fingerprint({p.name: file_hash(p) for p in sorted(Path(__file__).parent.glob("*.py"))})


def write_json(path, value):
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def export_csv(path, rows, fields=None):
    rows = iter(rows)
    first = next(rows, None)
    fields = list(fields or (first or {}).keys())
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, delimiter=";", extrasaction="raise")
        writer.writeheader()

        def safe(row):
            # Evita avaliação de fórmulas ao abrir CSV no Excel. O SQLite preserva o texto exato.
            return {
                k: (
                    "'" + v
                    if isinstance(v, str) and v[:1] in {"=", "+", "-", "@", "\t", "\r"}
                    else v
                )
                for k, v in row.items()
            }

        if first is not None:
            writer.writerow(safe(first))
        for row in rows:
            writer.writerow(safe(row))


def load_and_clean(workbook, bronze, source_hash):
    column_counts = {col: set() for col in COLUMNS}
    missing = Counter()
    records_by_hash, lineage = {}, []
    raw_hashes = set()
    exact = normalized = total = 0
    with (bronze / "linhas_originais.jsonl").open("w", encoding="utf-8") as original:
        for row_number, row, formulas in workbook.rows():
            if row_number == 1:
                continue
            raw = {col: row.get(col) for col in COLUMNS}
            total += 1
            original.write(
                stable_json(
                    {
                        "arquivo_sha256": source_hash,
                        "aba": workbook.sheet,
                        "linha_excel": row_number,
                        "valores": raw,
                        "formulas": formulas,
                    }
                )
                + "\n"
            )
            for col, value in raw.items():
                if clean_text(value) is None:
                    missing[col] += 1
                else:
                    column_counts[col].add(value)
            raw_id = fingerprint(raw)
            cleaned, issues = Cleaner(raw, date_1904=workbook.date_1904).run()
            if formulas:
                issues.append(
                    {
                        "codigo": "FORMULA_NA_ORIGEM",
                        "campo": "fonte",
                        "severidade": "WARNING",
                        "detalhe": {
                            "colunas": formulas,
                            "decisao": "valor_em_cache_nao_recalculado",
                        },
                    }
                )
            record_id = fingerprint(cleaned)
            if record_id not in records_by_hash:
                records_by_hash[record_id] = {
                    "id": record_id,
                    "data": cleaned,
                    "issues": [],
                    "rows": [],
                }
            else:
                if raw_id in raw_hashes:
                    exact += 1
                else:
                    normalized += 1
            raw_hashes.add(raw_id)
            record = records_by_hash[record_id]
            record["rows"].append(row_number)
            issue_ids = {fingerprint(i) for i in record["issues"]}
            for issue in issues:
                if fingerprint(issue) not in issue_ids:
                    record["issues"].append(issue)
                    issue_ids.add(fingerprint(issue))
            lineage.append((source_hash, workbook.sheet, row_number, record_id, raw_id))
    profile = {
        "rows": total,
        "columns": {
            col: {
                "nome": name,
                "total": total,
                "ausentes": missing[col],
                "distintos": len(column_counts[col]),
            }
            for col, name in COLUMNS.items()
        },
    }
    return (
        sorted(records_by_hash.values(), key=lambda r: r["id"]),
        lineage,
        profile,
        exact,
        normalized,
    )


def detect_conflicts(records):
    business_keys, source_ids, identities = defaultdict(list), defaultdict(list), defaultdict(list)
    for record in records:
        d = record["data"]
        key = (d["cnpj_cedente"], d["numero_contrato"], d["data_corte"])
        if all(key):
            business_keys[key].append(record)
        source_ids[(d["codigo_identificador"], d["data_corte"])].append(record)
        if d["documento_devedor"]:
            identities[(d["tipo_pessoa"], d["documento_devedor"])].append(record)
    for groups, code in [
        (business_keys, "CHAVE_NEGOCIO_CONFLITANTE"),
        (source_ids, "ID_ORIGEM_CONFLITANTE"),
    ]:
        for group in groups.values():
            if len(group) > 1:
                for record in group:
                    record["issues"].append(
                        {
                            "codigo": code,
                            "campo": "chave",
                            "severidade": "ERROR",
                            "detalhe": {"registros_do_grupo": [r["id"] for r in group]},
                        }
                    )
    for group in identities.values():
        names = {r["data"]["nome_devedor"] for r in group if r["data"]["nome_devedor"]}
        if len(names) > 1:
            for record in group:
                record["issues"].append(
                    {
                        "codigo": "NOMES_DIVERGENTES_MESMO_DOCUMENTO",
                        "campo": "nome_devedor",
                        "severidade": "WARNING",
                        "detalhe": {"nomes_distintos": len(names)},
                    }
                )
    for record in records:
        record["status"] = quality_status(record["issues"])


def financial_summary(db, table, condition="1=1"):
    result = {}
    for field in MONEY_FIELDS:
        total, missing = db.execute(
            f"SELECT sum_centavos({field}),SUM({field} IS NULL) FROM {table} WHERE {condition}"
        ).fetchone()
        result[field], result[field + "_ausentes"] = (
            int(total) if total is not None else None,
            missing or 0,
        )
    return result


def validate_database(db, source_rows):
    def count(sql):
        return db.execute(sql).fetchone()[0]

    controls = {
        "integridade_sqlite": count("PRAGMA integrity_check") == "ok",
        "integridade_referencial": not db.execute("PRAGMA foreign_key_check").fetchall(),
        "todas_linhas_com_linhagem": count("SELECT COUNT(*) FROM linhagem_origem") == source_rows,
        "reconciliacao_origem_e_duplicatas": count(
            "SELECT SUM(linhas_origem) FROM registros_tratados"
        )
        == source_rows,
        "reconciliacao_silver_gold_quarentena": count("SELECT COUNT(*) FROM registros_tratados")
        == count("SELECT COUNT(*) FROM fato_carteira")
        + count("SELECT COUNT(*) FROM vw_quarentena"),
        "gold_sem_erro_bloqueante": count(
            "SELECT COUNT(*) FROM fato_carteira f JOIN ocorrencias_qualidade o USING(registro_id) WHERE o.severidade='ERROR'"
        )
        == 0,
        "gold_chave_unica": count(
            "SELECT COUNT(*) FROM (SELECT cedente_id,numero_contrato,data_corte FROM fato_carteira GROUP BY 1,2,3 HAVING COUNT(*)>1)"
        )
        == 0,
        "pf_pj_isolados": count("""SELECT COUNT(*) FROM registros_tratados WHERE
            (tipo_pessoa='PF' AND (data_abertura IS NOT NULL OR socio_1_documento IS NOT NULL OR socio_2_nome IS NOT NULL)) OR
            (tipo_pessoa='PJ' AND (data_nascimento IS NOT NULL OR sexo IS NOT NULL OR renda_centavos IS NOT NULL OR nome_mae IS NOT NULL))""")
        == 0,
        "documento_coerente_tipo": count(
            "SELECT COUNT(*) FROM fato_carteira f JOIN dim_devedor d USING(devedor_id) WHERE LENGTH(d.documento)<>CASE f.tipo_pessoa WHEN 'PF' THEN 11 ELSE 14 END"
        )
        == 0,
        "precificacao_sem_alertas": count(
            "SELECT COUNT(*) FROM vw_precificacao p JOIN ocorrencias_qualidade o USING(registro_id) WHERE o.severidade<>'INFO'"
        )
        == 0,
    }
    for field in MONEY_FIELDS:
        source = int(count(f"SELECT COALESCE(sum_centavos({field}),'0') FROM registros_tratados"))
        gold = int(count(f"SELECT COALESCE(sum_centavos({field}),'0') FROM fato_carteira"))
        quarantine = int(count(f"SELECT COALESCE(sum_centavos({field}),'0') FROM vw_quarentena"))
        controls[f"saldo_reconciliado_{field}"] = source == gold + quarantine
    if not all(controls.values()):
        raise ValueError(
            f"Validações de publicação falharam: {[k for k, v in controls.items() if not v]}"
        )
    return controls


def build_dictionary(records):
    sources = FIELD_SOURCES
    from .modeling import column_type

    fields = [
        "registro_id",
        *records[0]["data"],
        "status_qualidade",
        "linhas_origem",
        "quantidade_alertas",
    ]
    for field in fields:
        desc = "Texto normalizado; NULL representa ausência ou valor inválido (ver ocorrências)."
        if field.endswith("_centavos"):
            desc = "Centavos inteiros (BRL), sinal preservado; dividir por 100 somente na apresentação."
        elif field.startswith("data_"):
            desc = "Data ISO YYYY-MM-DD; hífen fonte MDY, barras/pontos DMY, pivot ano 30."
        elif field.endswith("_valido"):
            desc = "1=DV válido, 0=inválido, NULL=ausente; não verifica cadastro externo."
        elif field == "status_qualidade":
            desc = "APTO=sem alerta; REVISAO=alerta não bloqueante; QUARENTENA=identidade/chave irresolvida."
        elif field == "registro_id":
            desc = "SHA-256 do conteúdo tratado; chave técnica, não anonimização."
        elif field == "dias_atraso_calculado":
            desc = "MAX(data_corte-data_vencimento,0); não usa a data de execução."
        elif field == "dias_atraso_informado":
            desc = "Contagem declarada na origem, preservada independentemente do atraso derivado."
        yield {
            "campo": field,
            "tipo_sqlite": column_type(field),
            "origem_colunas": sources.get(field, "derivado_pipeline"),
            "definicao": desc,
        }


def publish_exports(db, output, records):
    queries = {
        "base_tratada.csv": "SELECT * FROM registros_tratados ORDER BY codigo_identificador,registro_id",
        "base_precificacao.csv": "SELECT * FROM registros_tratados WHERE status_qualidade='APTO' ORDER BY codigo_identificador,registro_id",
        "quarentena.csv": "SELECT * FROM vw_quarentena ORDER BY codigo_identificador,registro_id",
        "ocorrencias_qualidade.csv": "SELECT * FROM ocorrencias_qualidade ORDER BY ocorrencia_id",
        "linhagem.csv": "SELECT * FROM linhagem_origem ORDER BY linha_excel",
        "carteira_agregada.csv": "SELECT * FROM mart_resumo_carteira ORDER BY 1,2,3,4,5",
        "indicadores_carteira.csv": "SELECT * FROM mart_indicadores_carteira ORDER BY universo,data_corte",
        "qualidade_agregada.csv": "SELECT * FROM mart_qualidade ORDER BY registros_afetados DESC,codigo,campo",
        "carteira_sem_identificacao_direta.csv": "SELECT * FROM vw_carteira_mascarada ORDER BY registro_id",
    }
    for filename, sql in queries.items():
        cursor = db.execute(sql)
        names = [c[0] for c in cursor.description]
        export_csv(
            output / filename,
            (
                {
                    name: int(value) if name.endswith("_centavos") and value is not None else value
                    for name, value in zip(names, row, strict=True)
                }
                for row in cursor
            ),
            names,
        )
    export_csv(output / "dicionario_dados.csv", build_dictionary(records))
    from .metrics import metric_dictionary

    export_csv(output / "dicionario_metricas.csv", metric_dictionary())


def validate_existing(target, expected):
    manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
    if manifest["run_id"] != expected:
        raise ValueError("Diretório de carga com identificador divergente.")
    for name, checksum in manifest["artifact_sha256"].items():
        path = target / name
        if not path.is_file() or file_hash(path) != checksum:
            raise ValueError(
                f"Artefato existente alterado/ausente: {name}. Use outro --output para reconstruir."
            )
    return target


def publish_latest(output_root, target, run_id):
    pointer = output_root / f".latest.{uuid.uuid4().hex}.json"
    relative = target.relative_to(output_root).as_posix()
    write_json(
        pointer, {"run_id": run_id, "path": relative, "report": relative + "/relatorio.html"}
    )
    os.replace(pointer, output_root / "latest.json")


def run_pipeline(source: Path, output_root: Path, sheet: str | None = None):
    source, output_root = (
        Path(source).expanduser().resolve(),
        Path(output_root).expanduser().resolve(),
    )
    if not source.is_file():
        raise FileNotFoundError(f"Planilha não encontrada: {source}")
    source_hash, code_hash = file_hash(source), implementation_hash()
    run_id = fingerprint([source_hash, code_hash, RULE_VERSION, sheet])[:20]
    target = output_root / run_id
    output_root.mkdir(parents=True, exist_ok=True)
    if target.exists():
        LOG.info("Carga %s já publicada; verificando integridade dos artefatos.", run_id)
        result = validate_existing(target, run_id)
        publish_latest(output_root, target, run_id)
        return result
    staging = output_root / f".{run_id}.tmp.{uuid.uuid4().hex}"
    staging.mkdir()
    bronze = staging / "bronze"
    bronze.mkdir()
    copied_source = bronze / f"{source_hash}.xlsx"
    shutil.copyfile(source, copied_source)
    if file_hash(copied_source) != source_hash:
        raise ValueError("O arquivo de origem mudou durante a cópia; execute novamente.")
    copied_source.chmod(0o444)
    try:
        workbook = Workbook(copied_source, sheet)
        validate_headers(workbook.headers)
        write_json(
            bronze / "layout.json",
            {
                "sheet": workbook.sheet,
                "date_1904": workbook.date_1904,
                "columns": workbook.headers,
                "date_hyphen_order": "MDY",
                "date_slash_dot_order": "DMY",
            },
        )
        LOG.info("Ingestão e tratamento da aba %s.", workbook.sheet)
        records, lineage, profile, exact, normalized = load_and_clean(workbook, bronze, source_hash)
        if not records:
            raise ValueError("Planilha sem registros; nenhuma carga será publicada.")
        detect_conflicts(records)
        metadata = {
            "run_id": run_id,
            "source_sha256": source_hash,
            "implementation_sha256": code_hash,
            "source_filename": source.name,
            "rule_version": RULE_VERSION,
            "sheet": workbook.sheet,
            "executed_at_utc": datetime.now(UTC).isoformat(),
        }
        db = build_database(staging / "carteira.sqlite", records, lineage, profile, metadata)
        try:
            controls = validate_database(db, len(lineage))
            statuses = Counter(r["status"] for r in records)
            counts = {
                "linhas_origem": len(lineage),
                "registros_unicos": len(records),
                "duplicatas_removidas": len(lineage) - len(records),
                "duplicatas_exatas": exact,
                "duplicatas_normalizacao": normalized,
                "linhas_gold": len(records) - statuses["QUARENTENA"],
                **{k: statuses[k] for k in ["APTO", "REVISAO", "QUARENTENA"]},
            }
            summary = {
                **metadata,
                "counts": counts,
                "source_profile": profile,
                "controls": controls,
                "cutoff_dates": dict(
                    Counter(r["data"]["data_corte"] or "AUSENTE" for r in records)
                ),
                "quality_counts": dict_rows(
                    db,
                    "SELECT codigo,severidade,COUNT(DISTINCT registro_id) AS registros,COUNT(*) AS ocorrencias FROM ocorrencias_qualidade GROUP BY codigo,severidade ORDER BY registros DESC,codigo",
                ),
                "financial": {
                    "todos": financial_summary(db, "registros_tratados"),
                    "gold": financial_summary(db, "fato_carteira"),
                    "aptos": financial_summary(db, "fato_carteira", "status_qualidade='APTO'"),
                },
                "portfolio": dict_rows(db, "SELECT * FROM mart_resumo_carteira ORDER BY 1,2,3,4,5"),
                "analytics": {
                    "indicadores": dict_rows(
                        db, "SELECT * FROM mart_indicadores_carteira ORDER BY universo,data_corte"
                    ),
                    "resumo": dict_rows(
                        db, "SELECT * FROM mart_resumo_carteira ORDER BY 1,2,3,4,5"
                    ),
                    "qualidade": dict_rows(
                        db,
                        "SELECT * FROM mart_qualidade ORDER BY registros_afetados DESC,codigo,campo",
                    ),
                },
            }
            publish_exports(db, staging, records)
            write_json(staging / "qualidade.json", summary)
            from .reporting import write_reports

            write_reports(staging, summary)
            db.commit()
        finally:
            db.close()
        artifact_hashes = {
            p.relative_to(staging).as_posix(): file_hash(p)
            for p in sorted(staging.rglob("*"))
            if p.is_file()
        }
        write_json(
            staging / "manifest.json",
            {
                **metadata,
                "counts": counts,
                "controls": controls,
                "artifact_sha256": artifact_hashes,
            },
        )
        staging.rename(target)
        publish_latest(output_root, target, run_id)
        LOG.info("Carga publicada: %s | %s", target, counts)
        return target
    except Exception:
        LOG.exception(
            "Carga falhou; nenhum resultado parcial foi publicado. Temporários preservados em %s",
            staging,
        )
        raise


def main():
    parser = argparse.ArgumentParser(
        description="Pipeline auditável da carteira do teste técnico; sem dependências externas."
    )
    parser.add_argument("--input", type=Path, required=True, help="Arquivo XLSX de origem")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("outputs"),
        help="Pasta de cargas imutáveis (padrão: outputs)",
    )
    parser.add_argument("--sheet", help="Aba; obrigatório se houver mais de uma")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        target = run_pipeline(args.input, args.output, args.sheet)
    except (OSError, ValueError, sqlite3.Error) as error:
        LOG.error("%s", error)
        return 1
    print(target)
    return 0


if __name__ == "__main__":
    sys.exit(main())
