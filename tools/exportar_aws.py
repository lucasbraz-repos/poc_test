"""Exporta apenas produtos agregados de uma carga, com dicionário e contexto."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pipeline.metrics import MART_SCHEMAS, metric_dictionary
from pipeline.run import export_csv
from tools.aws_cli import athena_rows, format_error, identity
from tools.config import DEFAULT_CONFIG, load_config

DATASETS = {
    "indicadores": "mart_indicadores_carteira",
    "resumo": "mart_resumo_carteira",
    "qualidade": "mart_qualidade",
}


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Exportar métricas agregadas da carga atual para CSV e JSON."
    )
    parser.add_argument("--profile", default="poc-aws")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--dataset", choices=["todos", *DATASETS], default="todos")
    args = parser.parse_args(argv)
    config = load_config(args.config)
    identity(args.profile, config["region"], config["account_id"])
    db = config["databases"]
    selected = athena_rows(
        config,
        args.profile,
        f"SELECT load_id,release_id,published_at FROM {db['control']}.cargas_publicadas ORDER BY published_at DESC,load_id DESC LIMIT 1",
    )
    if not selected:
        raise ValueError("Nenhuma carga publicada para exportar.")
    load, release, published = selected[0]
    if not re.fullmatch(r"[a-f0-9]{32}", load) or release != config["release_id"]:
        raise ValueError(
            "A carga atual não pertence à release implantada; execute a nova pipeline antes de exportar."
        )
    output = ROOT / "outputs_aws/exportacoes" / load
    output.mkdir(parents=True, exist_ok=True)
    for alias, table in DATASETS.items():
        if args.dataset not in {"todos", alias}:
            continue
        columns = MART_SCHEMAS[table]
        rows = athena_rows(
            config,
            args.profile,
            f"SELECT load_id,{','.join(columns)} FROM {db['gold']}.{table} WHERE load_id='{load}'",
        )
        typed = []
        for values in rows:
            row = {"load_id": values[0]}
            for (name, kind), value in zip(columns.items(), values[1:], strict=True):
                row[name] = int(value) if value is not None and kind == "bigint" else value
            typed.append(row)
        metadata = {
            "load_id": load,
            "release_id": release,
            "published_at": published,
            "dataset": table,
            "quantidade_linhas": len(typed),
            "unidade_monetaria": "centavos BRL; somas decimais como texto exato em JSON",
            "dados": typed,
        }
        (output / (alias + ".json")).write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        csv_rows = (
            {
                name: (
                    int(value)
                    if value is not None and columns.get(name) == "decimal(38,0)"
                    else value
                )
                for name, value in row.items()
            }
            for row in typed
        )
        export_csv(output / (alias + ".csv"), csv_rows, ["load_id", *columns])
    export_csv(output / "dicionario_metricas.csv", metric_dictionary())
    print(output)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        print(format_error(error), file=sys.stderr)
        sys.exit(1)
