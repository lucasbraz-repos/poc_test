"""Integração real XLSX→SQLite/CSV e publicação, sem usar os dados pessoais da fonte."""

from copy import deepcopy
import csv
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from xml.etree.ElementTree import Element, SubElement, tostring
from zipfile import ZipFile

from pipeline.run import run_pipeline
from pipeline.schema import COLUMNS

MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


def synthetic_xlsx(path, records, headers=None):
    """Cria o XML OOXML mínimo lido pelo importador, com strings em linha."""
    headers = COLUMNS if headers is None else headers
    workbook = Element("workbook", {"xmlns": MAIN_NS, "xmlns:r": REL_NS})
    sheets = SubElement(workbook, "sheets")
    SubElement(sheets, "sheet", {"name": "Carteira", "sheetId": "1", "r:id": "rId1"})
    relationships = Element("Relationships", {"xmlns": PKG_REL_NS})
    SubElement(relationships, "Relationship", {
        "Id": "rId1", "Type": REL_NS + "/worksheet", "Target": "worksheets/sheet1.xml",
    })
    worksheet = Element("worksheet", {"xmlns": MAIN_NS})
    data = SubElement(worksheet, "sheetData")
    for row_number, values in enumerate([headers, *records], 1):
        row = SubElement(data, "row", {"r": str(row_number)})
        for column, value in values.items():
            if value is None:
                continue
            cell = SubElement(row, "c", {"r": column + str(row_number), "t": "inlineStr"})
            inline = SubElement(cell, "is")
            SubElement(inline, "t").text = str(value)
    with ZipFile(path, "w") as archive:
        for name, root in [
            ("xl/workbook.xml", workbook),
            ("xl/_rels/workbook.xml.rels", relationships),
            ("xl/worksheets/sheet1.xml", worksheet),
        ]:
            archive.writestr(name, tostring(root, encoding="utf-8", xml_declaration=True))


def valid_record(**changes):
    raw = {
        "A": "ID001", "B": "11.222.333/0001-81", "N": "11222333000181",
        "C": "529.982.247-25", "X": "52998224725", "AQ": "52998224725", "BR": "52998224725",
        "D": "ct001", "P": "CT001", "AR": "CT001", "BS": "CT001",
        "Y": "PF", "E": "Pessoa Teste", "Z": "PESSOA TESTE", "BT": "Pessoa Teste",
        "AO": "PESSOA.TESTE@EXAMPLE.COM", "CA": "08-01-2026",
        "AU": "2020-01-01", "AV": "2026-07-01", "BF": "31", "AS": "Crédito pessoal",
        "AW": "1.000,00", "AX": "950,00", "AY": "1100.00", "AF": "SP",
        "BG": "12", "BL": "12", "BI": "1", "BK": "11",
    }
    raw.update(changes)
    return raw


class PipelineIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.source = self.root / "entrada.xlsx"
        self.outputs = self.root / "publicacoes"

    def execute(self, records, headers=None):
        synthetic_xlsx(self.source, records, headers)
        return run_pipeline(self.source, self.outputs)

    def db(self, target):
        db = sqlite3.connect(target / "carteira.sqlite")
        self.addCleanup(db.close)
        return db

    def test_exact_and_email_case_duplicates_preserve_all_lineage(self):
        original = valid_record()
        normalized = valid_record(AO="pessoa.teste@example.com")
        target = self.execute([original, deepcopy(original), normalized])
        summary = json.loads((target / "qualidade.json").read_text())
        counts = summary["counts"]
        self.assertEqual(counts["linhas_origem"], 3)
        self.assertEqual(counts["registros_unicos"], 1)
        self.assertEqual(counts["duplicatas_exatas"], 1)
        self.assertEqual(counts["duplicatas_normalizacao"], 1)
        self.assertEqual(counts["duplicatas_removidas"], 2)
        self.assertTrue(all(summary["controls"].values()))
        db = self.db(target)
        self.assertEqual(db.execute("SELECT COUNT(*) FROM linhagem_origem").fetchone()[0], 3)
        self.assertEqual(db.execute("SELECT linhas_origem FROM registros_tratados").fetchone()[0], 3)
        self.assertEqual(db.execute("SELECT COUNT(*) FROM fato_carteira").fetchone()[0], 1)
        self.assertEqual(db.execute("SELECT SUM(valor_contabil_centavos) FROM fato_carteira").fetchone()[0], 95000)
        with (target / "base_tratada.csv").open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream, delimiter=";"))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["email_1"], "pessoa.teste@example.com")
        self.assertEqual(rows[0]["documento_devedor"], "52998224725")

    def test_negative_values_keep_sign_and_delay_keeps_declared_and_derived(self):
        target = self.execute([valid_record(AW="(1.250,50)", BF="999")])
        db = self.db(target)
        row = db.execute("""SELECT valor_original_centavos,dias_atraso_informado,
            dias_atraso_calculado,status_qualidade FROM registros_tratados""").fetchone()
        self.assertEqual(row, (-125050, 999, 31, "REVISAO"))
        self.assertEqual(db.execute("SELECT COUNT(*) FROM vw_precificacao").fetchone()[0], 0)
        codes = {r[0] for r in db.execute("SELECT codigo FROM ocorrencias_qualidade")}
        self.assertIn("VALOR_MONETARIO_NEGATIVO", codes)
        self.assertIn("ATRASO_RECALCULADO", codes)
        self.assertEqual(db.execute("SELECT SUM(valor_original_centavos) FROM fato_carteira").fetchone()[0], -125050)

    def test_conflicting_business_key_quarantines_every_candidate(self):
        target = self.execute([valid_record(), valid_record(A="ID002", AX="900,00")])
        db = self.db(target)
        self.assertEqual(db.execute("SELECT COUNT(*) FROM registros_tratados").fetchone()[0], 2)
        self.assertEqual(db.execute("SELECT COUNT(*) FROM vw_quarentena").fetchone()[0], 2)
        self.assertEqual(db.execute("SELECT COUNT(*) FROM fato_carteira").fetchone()[0], 0)
        self.assertEqual(db.execute("SELECT COUNT(*) FROM dim_devedor").fetchone()[0], 0)
        self.assertEqual(db.execute("SELECT SUM(valor_contabil_centavos) FROM vw_quarentena").fetchone()[0], 185000)
        self.assertEqual(db.execute("""SELECT COUNT(DISTINCT registro_id) FROM ocorrencias_qualidade
            WHERE codigo='CHAVE_NEGOCIO_CONFLITANTE' AND severidade='ERROR'""").fetchone()[0], 2)
        self.assertTrue((target / "relatorio.html").is_file())

    def test_wrong_layout_cannot_replace_an_existing_published_run(self):
        published = self.execute([valid_record()])
        before = (self.outputs / "latest.json").read_bytes()
        changed_headers = dict(COLUMNS)
        changed_headers["CA"] = "Outro campo"
        synthetic_xlsx(self.source, [valid_record()], changed_headers)
        with self.assertLogs("pipeline", level="ERROR"), self.assertRaisesRegex(ValueError, "Layout"):
            run_pipeline(self.source, self.outputs)
        self.assertEqual((self.outputs / "latest.json").read_bytes(), before)
        self.assertTrue((published / "manifest.json").is_file())
        visible_runs = [p for p in self.outputs.iterdir() if p.is_dir() and not p.name.startswith(".")]
        self.assertEqual(visible_runs, [published])

    def test_missing_input_and_empty_workbook_do_not_publish(self):
        with self.assertRaises(FileNotFoundError):
            run_pipeline(self.source, self.outputs)
        self.assertFalse(self.outputs.exists())
        synthetic_xlsx(self.source, [])
        with self.assertLogs("pipeline", level="ERROR"), self.assertRaisesRegex(ValueError, "sem registros"):
            run_pipeline(self.source, self.outputs)
        self.assertFalse((self.outputs / "latest.json").exists())
        self.assertFalse(any(p.is_dir() and not p.name.startswith(".") for p in self.outputs.iterdir()))

    def test_identical_run_reuses_verified_artifacts_and_rejects_modification(self):
        target = self.execute([valid_record()])
        manifest = json.loads((target / "manifest.json").read_text())
        self.assertTrue(manifest["artifact_sha256"])
        for name, expected in manifest["artifact_sha256"].items():
            self.assertEqual(hashlib.sha256((target / name).read_bytes()).hexdigest(), expected)
        before_manifest = (target / "manifest.json").read_bytes()
        before_latest = (self.outputs / "latest.json").read_bytes()
        pointer = json.loads(before_latest)
        self.assertEqual(pointer["path"], target.name)
        self.assertEqual(pointer["report"], target.name + "/relatorio.html")
        self.assertFalse(Path(pointer["path"]).is_absolute())
        self.assertEqual(run_pipeline(self.source, self.outputs), target)
        self.assertEqual((target / "manifest.json").read_bytes(), before_manifest)
        self.assertEqual((self.outputs / "latest.json").read_bytes(), before_latest)
        self.assertEqual(len([p for p in self.outputs.iterdir() if p.is_dir()]), 1)
        (self.outputs / "latest.json").write_text(json.dumps({"run_id": "interrompido"}))
        self.assertEqual(run_pipeline(self.source, self.outputs), target)
        self.assertEqual((self.outputs / "latest.json").read_bytes(), before_latest)
        with (target / "base_tratada.csv").open("a", encoding="utf-8") as stream:
            stream.write("alteracao indevida\n")
        with self.assertRaisesRegex(ValueError, "alterado/ausente"):
            run_pipeline(self.source, self.outputs)
        self.assertEqual((self.outputs / "latest.json").read_bytes(), before_latest)


if __name__ == "__main__":
    unittest.main()
