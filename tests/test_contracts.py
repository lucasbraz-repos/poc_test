"""Regressões de contrato físico e rejeição de arquivos inválidos."""

import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from test_cleaning import base_record
from test_pipeline import synthetic_xlsx, valid_record

from pipeline.cleaning import Cleaner
from pipeline.schema import CLEAN_SCHEMA, INT64_MAX, INT64_MIN
from pipeline.xlsx import Workbook


class NumericContractTests(unittest.TestCase):
    def test_cleaner_schema_is_explicit_and_complete(self):
        self.assertEqual(set(Cleaner(base_record()).run()[0]), set(CLEAN_SCHEMA))

    def test_int64_extremes_are_preserved(self):
        for cents, text in [
            (INT64_MAX, "92233720368547758.07"),
            (INT64_MIN, "-92233720368547758.08"),
        ]:
            raw = base_record()
            raw["AW"] = text
            data, issues = Cleaner(raw).run()
            self.assertEqual(data["valor_original_centavos"], cents)
            self.assertFalse(any(i["codigo"] == "INTEIRO_FORA_INTERVALO" for i in issues))

    def test_overflow_is_reported_before_spark_or_sqlite(self):
        for text in ["92233720368547758.08", "-92233720368547758.09"]:
            raw = base_record()
            raw["AW"] = text
            data, issues = Cleaner(raw).run()
            self.assertIsNone(data["valor_original_centavos"])
            self.assertTrue(any(i["codigo"] == "INTEIRO_FORA_INTERVALO" for i in issues))


class WorkbookContractTests(unittest.TestCase):
    def test_invalid_zip_and_missing_workbook_have_clear_errors(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "invalid.xlsx"
            path.write_text("não é uma planilha")
            with self.assertRaisesRegex(ValueError, "XLSX inválido"):
                Workbook(path)
            with ZipFile(path, "w") as archive:
                archive.writestr("other.xml", "<root/>")
            with self.assertRaisesRegex(ValueError, "XLSX inválido"):
                Workbook(path)

    def test_uncompressed_archive_size_is_bounded(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "input.xlsx"
            synthetic_xlsx(path, [valid_record()])
            with self.assertRaisesRegex(ValueError, "descompactado"):
                Workbook(path, max_uncompressed_bytes=100)


if __name__ == "__main__":
    unittest.main()
