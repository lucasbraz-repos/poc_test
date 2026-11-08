"""Empacota somente código; não inclui credenciais, planilha nem resultados."""
import hashlib
import json
import sys
from pathlib import Path
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def package():
    from cloud.contracts import catalog
    output = ROOT / "build/aws"
    output.mkdir(parents=True, exist_ok=True)
    with ZipFile(output / "pipeline.zip", "w", compression=ZIP_DEFLATED) as archive:
        for folder in ["pipeline", "cloud"]:
            for path in sorted((ROOT / folder).rglob("*.py")):
                if "__pycache__" in path.parts:
                    continue
                info = ZipInfo(path.relative_to(ROOT).as_posix(), (2026, 1, 1, 0, 0, 0))
                info.compress_type = ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                archive.writestr(info, path.read_bytes())
    release = hashlib.sha256((output / "pipeline.zip").read_bytes()).hexdigest()
    (output / "release.json").write_text(json.dumps({"release_id": release}, indent=2) + "\n")
    (ROOT / "infra/terraform/catalog.json").write_text(json.dumps(catalog(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return release


if __name__ == "__main__":
    print(package())
