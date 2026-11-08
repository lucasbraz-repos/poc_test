"""AWS CLI como provedor de credenciais e cliente local; sem boto3 instalado."""
import json
import os
import re
import subprocess
import time


def aws(profile, region, *args):
    result = subprocess.run(["aws", *args, "--profile", profile, "--region", region, "--output", "json", "--no-cli-pager"],
                            check=True, capture_output=True, text=True)
    return json.loads(result.stdout) if result.stdout.strip() else {}


def identity(profile, region, account):
    if not re.fullmatch(r"[0-9]{12}", account):
        raise ValueError("Informe o ID de 12 dígitos da conta autorizada.")
    result = aws(profile, region, "sts", "get-caller-identity")
    if result["Account"] != account:
        raise ValueError(f"Perfil aponta para conta {result['Account']}; esperada {account}. Nenhum recurso será criado.")
    return result


def terraform_environment(profile):
    # Captura em memória, nunca imprime ou persiste credenciais. Compatível com aws login.
    result = subprocess.run(["aws", "configure", "export-credentials", "--profile", profile, "--format", "process"],
                            check=True, capture_output=True, text=True)
    credentials = json.loads(result.stdout)
    env = os.environ.copy()
    for name in ["AWS_PROFILE", "AWS_DEFAULT_PROFILE", "AWS_WEB_IDENTITY_TOKEN_FILE", "AWS_ROLE_ARN"]:
        env.pop(name, None)
    env.update(AWS_ACCESS_KEY_ID=credentials["AccessKeyId"], AWS_SECRET_ACCESS_KEY=credentials["SecretAccessKey"],
               AWS_SESSION_TOKEN=credentials["SessionToken"])
    return env


def athena_query(config, profile, query, *, timeout=180):
    result = aws(profile, config["region"], "athena", "start-query-execution", "--work-group", config["athena_workgroup"],
                 "--query-string", query, "--query-execution-context", json.dumps({"Database": config["databases"]["gold"]}))
    identifier = result["QueryExecutionId"]
    limit = time.monotonic() + timeout
    while time.monotonic() < limit:
        status = aws(profile, config["region"], "athena", "get-query-execution", "--query-execution-id", identifier)["QueryExecution"]["Status"]
        if status["State"] == "SUCCEEDED":
            return identifier
        if status["State"] in {"FAILED", "CANCELLED"}:
            raise RuntimeError(f"Athena: {status.get('StateChangeReason', status['State'])}")
        time.sleep(2)
    raise TimeoutError(f"Consulta Athena excedeu {timeout}s; QueryExecutionId={identifier}")
