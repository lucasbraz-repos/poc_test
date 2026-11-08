"""Entrada simplificada: selecionar o Excel e receber o resultado da pipeline."""

from __future__ import annotations

import argparse
import json
import logging
import platform
import sqlite3
import subprocess
import sys
import webbrowser
from pathlib import Path

from pipeline.run import run_pipeline

PROJECT = Path(__file__).resolve().parent


def select_file() -> Path | None:
    if platform.system() == "Darwin":
        script = """set selectedFile to choose file with prompt "Selecione o arquivo Excel para processar. As demais etapas são automáticas." of type {"xlsx"}
return POSIX path of selectedFile"""
        result = subprocess.run(
            ["osascript", "-e", script], capture_output=True, text=True, check=False
        )
        if result.returncode:
            if "(-128)" in result.stderr:
                return None
            raise OSError("Não foi possível abrir o seletor de arquivos. " + result.stderr.strip())
        selected = result.stdout.strip()
        return Path(selected) if selected else None
    value = input("Informe o caminho do arquivo Excel (.xlsx), ou Enter para cancelar: ").strip()
    return Path(value.strip("\"'")) if value else None


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Selecione o Excel; a pipeline resolve todas as outras etapas."
    )
    parser.add_argument(
        "arquivo", nargs="?", type=Path, help="Opcional: informar o Excel sem abrir o seletor"
    )
    parser.add_argument(
        "--sem-abrir", action="store_true", help="Gerar os resultados sem abrir o navegador"
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        selected = args.arquivo if args.arquivo is not None else select_file()
        if selected is None:
            print("Seleção cancelada. Nenhuma carga foi iniciada.")
            return 0
        selected = selected.expanduser().resolve()
        if selected.suffix.lower() != ".xlsx":
            raise ValueError("Selecione uma planilha no formato .xlsx.")
        if not selected.is_file():
            raise FileNotFoundError(f"Arquivo não encontrado: {selected}")
        print(f"Arquivo selecionado: {selected.name}", flush=True)
        print(
            "Processando: leitura → limpeza → validações → banco e bases finais → relatório.",
            flush=True,
        )
        output = run_pipeline(selected, PROJECT / "outputs")
        counts = json.loads((output / "qualidade.json").read_text(encoding="utf-8"))["counts"]
        report = output / "relatorio.html"
        print(f"\nConcluído: {counts['registros_unicos']:,} registros tratados.".replace(",", "."))
        print(
            f"Aptos: {counts['APTO']} | Revisão: {counts['REVISAO']} | Quarentena: {counts['QUARENTENA']}"
        )
        print(f"Resultados: {output}")
        print(f"Relatório: {report}")
        if not args.sem_abrir:
            try:
                opened = webbrowser.open(report.as_uri())
                if not opened:
                    print("Abra o arquivo relatorio.html acima para visualizar os resultados.")
            except (OSError, webbrowser.Error):
                print("Abra o arquivo relatorio.html acima para visualizar os resultados.")
        return 0
    except (OSError, ValueError, EOFError, sqlite3.Error, subprocess.SubprocessError) as error:
        print(f"Não foi possível concluir: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
