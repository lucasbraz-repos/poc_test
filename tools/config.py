"""Configuração compartilhada dos comandos locais, sem credenciais."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "build/aws/deployment.json"
DEFAULT_MAX_SOURCE_BYTES = 64 * 1024 * 1024
DEFAULT_MAX_SOURCE_ROWS = 250_000


def positive_int(value):
    """Tipo argparse para limites que precisam ser maiores que zero."""
    try:
        number = int(value)
    except (TypeError, ValueError) as error:
        raise argparse.ArgumentTypeError("Informe um número inteiro maior que zero.") from error
    if number <= 0:
        raise argparse.ArgumentTypeError("Informe um número inteiro maior que zero.")
    return number


def validate_config(config):
    """Valida os nomes usados em comandos AWS e interpolação de identificadores SQL."""
    if not isinstance(config, dict):
        raise ValueError("A configuração AWS deve ser um objeto JSON.")
    result = dict(config)
    patterns = {
        "account_id": r"[0-9]{12}",
        "region": r"[a-z]{2}(?:-[a-z]+)+-[0-9]+",
        "release_id": r"[a-f0-9]{64}",
        "data_bucket": r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]",
        "workflow_name": r"[A-Za-z0-9_-]+",
        "athena_workgroup": r"[A-Za-z0-9._-]+",
    }
    for name, pattern in patterns.items():
        value = result.get(name)
        if not isinstance(value, str) or not re.fullmatch(pattern, value):
            raise ValueError(f"Configuração AWS inválida ou ausente: {name}.")
    databases = result.get("databases")
    if not isinstance(databases, dict) or not {"bronze", "silver", "gold", "control"}.issubset(
        databases
    ):
        raise ValueError(
            "Configuração AWS deve informar os databases bronze, silver, gold e control."
        )
    if any(
        not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", value)
        for value in databases.values()
    ):
        raise ValueError("Nome de database inválido na configuração AWS.")
    for name, default in (
        ("max_source_bytes", DEFAULT_MAX_SOURCE_BYTES),
        ("max_source_rows", DEFAULT_MAX_SOURCE_ROWS),
    ):
        value = result.get(name, default)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(
                f"Configuração AWS inválida: {name} deve ser um inteiro maior que zero."
            )
        result[name] = value
    return result


def load_config(path=None):
    """Carrega outputs do Terraform; preserva compatibilidade com implantações anteriores."""
    path = Path(path) if path is not None else DEFAULT_CONFIG
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ValueError(
            f"Configuração AWS não encontrada em {path}. Execute tools/deploy_aws.py primeiro."
        ) from error
    except json.JSONDecodeError as error:
        raise ValueError(f"Configuração AWS inválida em {path}: JSON malformado.") from error
    return validate_config(config)
