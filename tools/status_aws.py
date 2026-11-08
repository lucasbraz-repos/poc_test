"""Progresso das cargas recebidas; não imprime conteúdo da carteira."""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.aws_cli import aws, format_error, identity
from tools.config import DEFAULT_CONFIG, load_config


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", default="poc-aws")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args()
    config = load_config(args.config)
    identity(args.profile, config["region"], config["account_id"])
    response = aws(
        args.profile,
        config["region"],
        "glue",
        "get-workflow-runs",
        "--name",
        config["workflow_name"],
        "--max-results",
        "20",
        "--include-graph",
    )
    runs = []
    for run in response.get("Runs", []):
        if not run.get("WorkflowRunProperties", {}).get("source_id"):
            continue
        jobs = []
        for node in run.get("Graph", {}).get("Nodes", []):
            if node["Type"] != "JOB":
                continue
            attempts = node.get("JobDetails", {}).get("JobRuns", [])
            current = max(attempts, key=lambda r: r.get("StartedOn", "")) if attempts else {}
            jobs.append(
                {
                    "name": node["Name"],
                    "state": current.get("JobRunState", "AGUARDANDO"),
                    "error": current.get("ErrorMessage"),
                }
            )
        runs.append(
            {
                "run_id": run["WorkflowRunId"],
                "status": run["Status"],
                "statistics": run["Statistics"],
                "jobs": jobs,
            }
        )
    print(json.dumps(runs, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(format_error(error), file=sys.stderr)
        sys.exit(1)
