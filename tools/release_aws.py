"""Confere a release implantada e arquiva o conteúdo completo do commit no S3."""

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cloud.contracts import TABLES
from tools.aws_cli import aws, format_error, identity
from tools.config import load_config


def verify_deployed(config, profile):
    region, release, name = config["region"], config["release_id"], config["workflow_name"]
    workflow = aws(profile, region, "glue", "get-workflow", "--name", name)["Workflow"]
    if workflow.get("DefaultRunProperties", {}).get("release_id") != release:
        raise ValueError("Workflow não está usando a release esperada.")
    for stage in ("bronze", "silver", "gold"):
        job = aws(profile, region, "glue", "get-job", "--job-name", name + "-" + stage)["Job"]
        arguments = job.get("DefaultArguments", {})
        if arguments.get("--RELEASE_ID") != release or not job["Command"][
            "ScriptLocation"
        ].endswith(f"/releases/{release}/{stage}.py"):
            raise ValueError("Job não está usando a release esperada: " + stage)
    function = aws(
        profile,
        region,
        "lambda",
        "get-function-configuration",
        "--function-name",
        name + "-dispatcher",
    )
    if function.get("Environment", {}).get("Variables", {}).get("RELEASE_ID") != release:
        raise ValueError("Dispatcher não está usando a release esperada.")
    for layer in ("bronze", "silver", "gold", "control"):
        tables = aws(
            profile, region, "glue", "get-tables", "--database-name", config["databases"][layer]
        )["TableList"]
        missing = {table for table, (table_layer, _) in TABLES.items() if table_layer == layer} - {
            table["Name"] for table in tables
        }
        if missing:
            raise ValueError("Tabelas ausentes no catálogo: " + ", ".join(sorted(missing)))
    return {"workflow": "OK", "jobs": "OK", "dispatcher": "OK", "catalog": "OK"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--profile")
    args = parser.parse_args()
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()
    if not re.fullmatch(r"[a-f0-9]{40}", args.commit) or args.commit != head:
        raise ValueError("O commit precisa corresponder ao checkout que foi implantado.")
    subprocess.run(
        ["git", "diff", "--exit-code", "HEAD"], cwd=ROOT, check=True, capture_output=True
    )
    config = load_config()
    identity(args.profile, config["region"], config["account_id"])
    checks = verify_deployed(config, args.profile)
    archive = ROOT / "build/aws" / (args.commit + ".tar.gz")
    subprocess.run(
        ["git", "archive", "--format=tar.gz", "--output=" + str(archive), args.commit],
        cwd=ROOT,
        check=True,
    )
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    key = f"commits/{args.commit}/source.tar.gz"
    aws(
        args.profile,
        config["region"],
        "s3api",
        "put-object",
        "--bucket",
        config["artifacts_bucket"],
        "--key",
        key,
        "--body",
        str(archive),
        "--server-side-encryption",
        "AES256",
        "--metadata",
        json.dumps({"commit": args.commit, "release_id": config["release_id"], "sha256": checksum}),
    )
    result = {
        "commit": args.commit,
        "release_id": config["release_id"],
        "source_archive": f"s3://{config['artifacts_bucket']}/{key}",
        "source_sha256": checksum,
        "checks": checks,
    }
    (ROOT / "build/aws/deploy-result.json").write_text(json.dumps(result, indent=2) + "\n")
    current = ROOT / "build/aws/current-deployment.json"
    current.write_text(json.dumps({**config, "deployed_commit": args.commit}, indent=2) + "\n")
    aws(
        args.profile,
        config["region"],
        "s3api",
        "put-object",
        "--bucket",
        config["artifacts_bucket"],
        "--key",
        "deployments/current.json",
        "--body",
        str(current),
        "--server-side-encryption",
        "AES256",
        "--content-type",
        "application/json",
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(format_error(error), file=sys.stderr)
        sys.exit(1)
