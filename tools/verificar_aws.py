"""Confere os dados publicados consultando Parquet pelo Athena real."""
import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.aws_cli import aws, identity, athena_query


def rows(config, profile, query):
    query_id = athena_query(config, profile, query)
    result = aws(profile, config["region"], "athena", "get-query-results", "--query-execution-id", query_id)
    return [[c.get("VarCharValue") for c in row["Data"]] for row in result["ResultSet"]["Rows"][1:]]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", default="poc-aws")
    args = parser.parse_args()
    config = json.loads((ROOT / "build/aws/deployment.json").read_text())
    identity(args.profile, config["region"], config["account_id"])
    db = config["databases"]
    if any(not re.fullmatch(r"[a-z][a-z0-9_]*", value) for value in db.values()):
        raise ValueError("Nome de database inválido.")
    latest = rows(config, args.profile, f"SELECT load_id FROM {db['control']}.cargas_publicadas ORDER BY published_at DESC,load_id DESC LIMIT 1")
    if not latest:
        raise ValueError("Nenhuma carga foi publicada ainda.")
    load = latest[0][0]
    if not re.fullmatch(r"[a-f0-9]{32}", load):
        raise ValueError("Load ID inválido.")
    output = ROOT / "outputs_aws/verificacao"
    output.mkdir(parents=True, exist_ok=True)
    aws(args.profile, config["region"], "s3api", "get-object", "--bucket", config["data_bucket"],
        "--key", f"control/commits/{load}.json", str(output / "manifest.json"))
    expected = json.loads((output / "manifest.json").read_text())["counts"]
    queries = []
    checks = [("linhas_origem", db["bronze"]+".linhas_originais", ""),
              ("registros_unicos", db["silver"]+".registros_tratados", ""),
              ("linhas_gold", db["gold"]+".vw_carteira", ""),
              ("APTO", db["gold"]+".vw_precificacao", ""),
              ("REVISAO", db["gold"]+".vw_carteira", " AND status_qualidade='REVISAO'"),
              ("QUARENTENA", db["silver"]+".vw_quarentena", "")]
    for label, table, extra in checks:
        queries.append(f"SELECT '{label}' AS metric, COUNT(*) AS value FROM {table} WHERE load_id='{load}'{extra}")
    actual = {name:int(value) for name, value in rows(config, args.profile, " UNION ALL ".join(queries))}
    if any(actual[k] != expected[k] for k in actual):
        raise ValueError(f"Athena diverge do manifesto: {actual}; esperado: {expected}")
    for field in ["valor_original_centavos", "valor_contabil_centavos", "valor_face_centavos"]:
        query = f"""SELECT
            (SELECT COALESCE(SUM({field}),0) FROM {db['silver']}.registros_tratados WHERE load_id='{load}') -
            (SELECT COALESCE(SUM({field}),0) FROM {db['gold']}.vw_carteira WHERE load_id='{load}') -
            (SELECT COALESCE(SUM({field}),0) FROM {db['silver']}.vw_quarentena WHERE load_id='{load}') AS delta"""
        if int(rows(config, args.profile, query)[0][0]) != 0:
            raise ValueError("Saldo não reconciliado no Athena: " + field)
    result = {"load_id": load, "athena_counts": actual, "financial_reconciliation": "OK"}
    (output / "athena.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":sys.exit(main())
