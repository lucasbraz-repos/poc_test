"""AWS CLI como provedor de credenciais e cliente local; sem boto3 instalado."""

import json
import os
import re
import subprocess
import time


def aws(profile, region, *args):
    result = subprocess.run(
        [
            "aws",
            *args,
            *(["--profile", profile] if profile else []),
            "--region",
            region,
            "--output",
            "json",
            "--no-cli-pager",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=90,
    )
    return json.loads(result.stdout) if result.stdout.strip() else {}


def identity(profile, region, account):
    if not re.fullmatch(r"[0-9]{12}", account):
        raise ValueError("Informe o ID de 12 dígitos da conta autorizada.")
    result = aws(profile, region, "sts", "get-caller-identity")
    if result["Account"] != account:
        raise ValueError(
            f"Perfil aponta para conta {result['Account']}; esperada {account}. Nenhum recurso será criado."
        )
    return result


def terraform_environment(profile):
    if not profile:
        # GitHub Actions fornece credenciais temporárias por OIDC no ambiente.
        return os.environ.copy()
    # Captura em memória, nunca imprime ou persiste credenciais. Compatível com aws login.
    result = subprocess.run(
        ["aws", "configure", "export-credentials", "--profile", profile, "--format", "process"],
        check=True,
        capture_output=True,
        text=True,
    )
    credentials = json.loads(result.stdout)
    env = os.environ.copy()
    for name in [
        "AWS_PROFILE",
        "AWS_DEFAULT_PROFILE",
        "AWS_WEB_IDENTITY_TOKEN_FILE",
        "AWS_ROLE_ARN",
        "AWS_ROLE_SESSION_NAME",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AWS_SECURITY_TOKEN",
        "AWS_CREDENTIAL_EXPIRATION",
    ]:
        env.pop(name, None)
    env.update(
        AWS_ACCESS_KEY_ID=credentials["AccessKeyId"],
        AWS_SECRET_ACCESS_KEY=credentials["SecretAccessKey"],
    )
    if credentials.get("SessionToken"):
        env["AWS_SESSION_TOKEN"] = credentials["SessionToken"]
    return env


def athena_query(config, profile, query, *, timeout=180):
    if timeout <= 0:
        raise ValueError("O timeout da consulta Athena deve ser maior que zero.")
    result = aws(
        profile,
        config["region"],
        "athena",
        "start-query-execution",
        "--work-group",
        config["athena_workgroup"],
        "--query-string",
        query,
        "--query-execution-context",
        json.dumps({"Database": config["databases"]["gold"]}),
    )
    identifier = result["QueryExecutionId"]
    limit = time.monotonic() + timeout
    while time.monotonic() < limit:
        status = aws(
            profile,
            config["region"],
            "athena",
            "get-query-execution",
            "--query-execution-id",
            identifier,
        )["QueryExecution"]["Status"]
        if status["State"] == "SUCCEEDED":
            return identifier
        if status["State"] in {"FAILED", "CANCELLED"}:
            raise RuntimeError(f"Athena: {status.get('StateChangeReason', status['State'])}")
        time.sleep(2)
    try:
        aws(
            profile,
            config["region"],
            "athena",
            "stop-query-execution",
            "--query-execution-id",
            identifier,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise TimeoutError(
            f"Consulta Athena excedeu {timeout}s; não foi possível cancelar a consulta "
            f"{identifier}: {format_error(error)}"
        ) from error
    raise TimeoutError(
        f"Consulta Athena excedeu {timeout}s; cancelamento solicitado. QueryExecutionId={identifier}"
    )


def athena_rows(config, profile, query, *, timeout=180):
    """Executa SELECT e lê todas as páginas, removendo o cabeçalho apenas uma vez."""
    identifier = athena_query(config, profile, query, timeout=timeout)
    rows, token, first_page = [], None, True
    while True:
        arguments = [
            "athena",
            "get-query-results",
            "--query-execution-id",
            identifier,
            "--no-paginate",
        ]
        if token:
            arguments.extend(["--next-token", token])
        page = aws(profile, config["region"], *arguments)
        result_rows = page.get("ResultSet", {}).get("Rows", [])
        for row in result_rows[1:] if first_page else result_rows:
            rows.append([cell.get("VarCharValue") for cell in row["Data"]])
        first_page = False
        token = page.get("NextToken")
        if not token:
            return rows


def format_error(error):
    """Mostra a causa de falhas de subprocesso sem reproduzir argumentos completos."""
    if isinstance(error, subprocess.TimeoutExpired):
        return "O serviço externo excedeu o tempo de resposta. Verifique conexão e autenticação."
    if isinstance(error, subprocess.CalledProcessError):
        detail = (error.stderr or "").strip()
        command = error.cmd[0] if isinstance(error.cmd, (list, tuple)) else "comando externo"
        return detail or f"Falha em {command}, código de saída {error.returncode}."
    return str(error)


def missing_object(error):
    """Distingue ausência de objeto de falhas de permissão ou autenticação."""
    return isinstance(error, subprocess.CalledProcessError) and any(
        code in (error.stderr or "") for code in ("NoSuchKey", "(404)", "Not Found")
    )
