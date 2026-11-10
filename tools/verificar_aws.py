"""Confere os dados publicados consultando Parquet pelo Athena real."""

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.aws_cli import athena_rows, aws, format_error, identity
from tools.config import DEFAULT_CONFIG, load_config


def rows(config, profile, query):
    return athena_rows(config, profile, query)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", default="poc-aws")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--load-id")
    args = parser.parse_args()
    config = load_config(args.config)
    identity(args.profile, config["region"], config["account_id"])
    db = config["databases"]
    if any(not re.fullmatch(r"[a-z][a-z0-9_]*", value) for value in db.values()):
        raise ValueError("Nome de database inválido.")
    latest = rows(
        config,
        args.profile,
        f"SELECT load_id FROM {db['control']}.cargas_publicadas ORDER BY published_at DESC,load_id DESC LIMIT 1",
    )
    if not latest:
        raise ValueError("Nenhuma carga foi publicada ainda.")
    load = args.load_id or latest[0][0]
    if not re.fullmatch(r"[a-f0-9]{32}", load):
        raise ValueError("Load ID inválido.")
    output = ROOT / "outputs_aws/verificacao" / load
    output.mkdir(parents=True, exist_ok=True)
    aws(
        args.profile,
        config["region"],
        "s3api",
        "get-object",
        "--bucket",
        config["data_bucket"],
        "--key",
        f"control/commits/{load}.json",
        str(output / "manifest.json"),
    )
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    if manifest["release_id"] != config["release_id"]:
        raise ValueError(
            "A carga não pertence à release implantada; execute a nova pipeline antes de verificar."
        )
    expected = manifest["counts"]
    queries = []
    checks = [
        ("linhas_origem", db["bronze"] + ".linhas_originais", ""),
        ("registros_unicos", db["silver"] + ".registros_tratados", ""),
        ("linhas_gold", db["gold"] + ".vw_carteira", ""),
        ("APTO", db["gold"] + ".vw_precificacao", ""),
        ("REVISAO", db["gold"] + ".vw_carteira", " AND status_qualidade='REVISAO'"),
        ("QUARENTENA", db["silver"] + ".vw_quarentena", ""),
    ]
    for label, table, extra in checks:
        queries.append(
            f"SELECT '{label}' AS metric, COUNT(*) AS value FROM {table} WHERE load_id='{load}'{extra}"
        )
    actual = {
        name: int(value) for name, value in rows(config, args.profile, " UNION ALL ".join(queries))
    }
    if any(actual[k] != expected[k] for k in actual):
        raise ValueError(f"Athena diverge do manifesto: {actual}; esperado: {expected}")
    for field in ["valor_original_centavos", "valor_contabil_centavos", "valor_face_centavos"]:
        query = f"""SELECT
            (SELECT COALESCE(SUM(CAST({field} AS DECIMAL(38,0))),0) FROM {db["silver"]}.registros_tratados WHERE load_id='{load}') -
            (SELECT COALESCE(SUM(CAST({field} AS DECIMAL(38,0))),0) FROM {db["gold"]}.vw_carteira WHERE load_id='{load}') -
            (SELECT COALESCE(SUM(CAST({field} AS DECIMAL(38,0))),0) FROM {db["silver"]}.vw_quarentena WHERE load_id='{load}') AS delta"""
        if int(rows(config, args.profile, query)[0][0]) != 0:
            raise ValueError("Saldo não reconciliado no Athena: " + field)
    indicators = rows(
        config,
        args.profile,
        f"SELECT universo,SUM(quantidade) FROM {db['gold']}.mart_indicadores_carteira WHERE load_id='{load}' GROUP BY universo",
    )
    universes = {name: int(value) for name, value in indicators}
    if universes != {
        "BASE_TRATADA": expected["registros_unicos"],
        "CARTEIRA_PUBLICADA": expected["linhas_gold"],
        "APTOS": expected["APTO"],
    }:
        raise ValueError(f"Indicadores divergem da carteira: {universes}")
    total_summary = int(
        rows(
            config,
            args.profile,
            f"SELECT COALESCE(SUM(quantidade),0) FROM {db['gold']}.mart_resumo_carteira WHERE load_id='{load}'",
        )[0][0]
    )
    if total_summary != expected["linhas_gold"]:
        raise ValueError("Resumo segmentado diverge da carteira publicada.")
    for source, measure in (
        ("valor_original_centavos", "valor_original_centavos"),
        ("valor_contabil_centavos", "saldo_contabil_centavos"),
        ("valor_face_centavos", "valor_face_centavos"),
    ):
        sums = rows(
            config,
            args.profile,
            f"SELECT (SELECT SUM(CAST({source} AS DECIMAL(38,0))) FROM {db['gold']}.fato_carteira WHERE load_id='{load}'),(SELECT SUM({measure}) FROM {db['gold']}.mart_resumo_carteira WHERE load_id='{load}'),(SELECT SUM({measure}) FROM {db['gold']}.mart_indicadores_carteira WHERE load_id='{load}' AND universo='CARTEIRA_PUBLICADA')",
        )[0]
        if len(set(sums)) != 1:
            raise ValueError("Soma dos marts diverge da carteira: " + measure)
    result = {
        "load_id": load,
        "athena_counts": actual,
        "financial_reconciliation": "OK",
        "analytics_universes": universes,
        "analytics_reconciliation": "OK",
    }
    (output / "athena.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        print(format_error(error), file=sys.stderr)
        sys.exit(1)
