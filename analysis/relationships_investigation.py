"""Inspeciona regras de relacionamento sem expor exemplos individuais."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline.run import run_pipeline


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("arquivo", type=Path)
    parser.add_argument("--output", type=Path, default=Path("build/analysis"))
    args = parser.parse_args()
    target = run_pipeline(args.arquivo, args.output)
    result = json.loads((target / "qualidade.json").read_text())
    print(json.dumps(result["analytics"]["qualidade"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
