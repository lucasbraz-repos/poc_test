"""Casos que protegem decisões de interpretação, não apenas a implementação."""

import unittest
from datetime import date
from decimal import Decimal

from pipeline.parsers import (
    clean_text,
    digits,
    document_is_valid,
    normalize_ddd,
    normalize_email,
    normalize_phone,
    normalize_text,
    parse_bool,
    parse_date,
    parse_excel_money,
    parse_integer,
    parse_money,
    parse_person_type,
    token,
)


class ParserTests(unittest.TestCase):
    def test_nulls_whitespace_and_unicode(self):
        for value in (None, "", " n/a ", "NULL", "NaN", " S/D ", "-", "sem dado"):
            self.assertIsNone(clean_text(value))
        self.assertEqual(clean_text("  João\t da\nSilva  "), "João da Silva")
        self.assertEqual(normalize_text("João"), "JOÃO")
        self.assertEqual(token("Pessoa Física"), "PESSOA FISICA")

    def test_money_preserves_exact_cents_and_sign(self):
        values = {
            "R$ 1.234,56": 123456,
            "1,234.56": 123456,
            "1234,56": 123456,
            "1234.56": 123456,
            "(8439.11)": -843911,
            "-12,50": -1250,
            "(0.00)": 0,
            "0": 0,
            "1.234.567": 123456700,
            ",50": 50,
        }
        for raw, expected in values.items():
            with self.subTest(raw=raw):
                self.assertEqual(parse_money(raw), expected)
        self.assertEqual(parse_money(Decimal("0.29")), 29)
        self.assertEqual(parse_money(0.29), 29)
        self.assertIsNone(parse_money("N/A"))

    def test_money_rejects_ambiguous_or_malformed(self):
        for raw in (
            "1.234",
            "1,234",
            "abc",
            "1.23,45",
            "1,234.567",
            "(-12)",
            "12-50",
            "1e3",
            "NaN reais",
        ):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                parse_money(raw)

    def test_excel_money_recovers_only_tiny_numeric_residue(self):
        self.assertEqual(parse_excel_money("33155.620000000003"), (3315562, True))
        self.assertEqual(parse_excel_money("9501.1299999999992"), (950113, True))
        self.assertEqual(parse_excel_money("-75752.179999999993"), (-7575218, True))
        self.assertEqual(parse_excel_money("123,45"), (12345, False))
        self.assertEqual(parse_excel_money("N/A"), (None, False))
        for raw in (
            "1.234",
            "1,234",
            "1.234567",
            "1.230001",
            "999999999999999999999999999999.234567",
        ):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                parse_excel_money(raw)

    def test_integer_is_not_money(self):
        self.assertEqual(parse_integer("1.677"), 1677)
        self.assertEqual(parse_integer("1,677"), 1677)
        self.assertEqual(parse_integer(" 12 "), 12)
        self.assertEqual(parse_integer("12.00"), 12)
        self.assertEqual(parse_integer(12.0), 12)
        for raw in ("1.5", "4,25", 1.5, "1.67.7"):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                parse_integer(raw)

    def test_date_formats_and_fixed_two_digit_year(self):
        values = {
            "2026-08-01": "2026-08-01",
            "01.08.2026": "2026-08-01",
            "1 de agosto de 2026": "2026-08-01",
            "02-Mar-2004": "2004-03-02",
            "15/08/05": "2005-08-15",
            "31/12/29": "2029-12-31",
            "31/12/30": "1930-12-31",
            "07-13-2023": "2023-07-13",
            "25866": "1970-10-25",
        }
        for raw, expected in values.items():
            with self.subTest(raw=raw):
                self.assertEqual(parse_date(raw)[0], expected)
        self.assertEqual(parse_date(date(2026, 8, 1)), ("2026-08-01", None))

    def test_date_ambiguity_and_explicit_source_convention(self):
        self.assertEqual(parse_date("01/08/26"), ("2026-08-01", "DATA_AMBIGUA_DIA_PRIMEIRO"))
        self.assertEqual(parse_date("08-01-2026"), ("2026-01-08", "DATA_AMBIGUA_DIA_PRIMEIRO"))
        self.assertEqual(parse_date("08-01-2026", hyphen_order="MDY"), ("2026-08-01", None))
        self.assertEqual(parse_date("01/08/26", flag_ambiguous=False), ("2026-08-01", None))
        self.assertEqual(parse_date("08/08/26"), ("2026-08-08", None))
        for raw in ("31/02/2026", "00/01/26", "13-07-2023"):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                parse_date(raw, hyphen_order="MDY")

    def test_excel_calendar_boundaries(self):
        self.assertEqual(parse_date(59), ("1900-02-28", None))
        with self.assertRaises(ValueError):
            parse_date(60)
        self.assertEqual(parse_date(61), ("1900-03-01", None))
        self.assertEqual(parse_date(0, date_1904=True), ("1904-01-01", None))
        self.assertEqual(parse_date(1, date_1904=True), ("1904-01-02", None))
        with self.assertRaises(ValueError):
            parse_date("45000.5")

    def test_documents_keep_leading_zero_and_validate_checksums(self):
        self.assertEqual(digits(" 033.659.924-21 "), "03365992421")
        self.assertTrue(document_is_valid("529.982.247-25"))
        self.assertTrue(document_is_valid("04.252.011/0001-10"))
        for raw in ("529.982.247-24", "11111111111", "00000000000000", "abc", None):
            with self.subTest(raw=raw):
                self.assertFalse(document_is_valid(raw))
        with self.assertRaises(ValueError):
            digits("CPF 529.982.247-25")

    def test_boolean_and_person_type(self):
        for raw in ("S", "sim", "TRUE", "Verdadeiro", 1):
            self.assertEqual(parse_bool(raw), 1)
        for raw in ("N", "não", "FALSE", "Falso", 0):
            self.assertEqual(parse_bool(raw), 0)
        self.assertEqual(parse_person_type("Pessoa Física"), "PF")
        self.assertEqual(parse_person_type("j"), "PJ")
        self.assertIsNone(parse_bool("N/A"))
        with self.assertRaises(ValueError):
            parse_bool("talvez")

    def test_email_syntax_and_normalization(self):
        self.assertEqual(
            normalize_email("  USER+teste@EXAMPLE.COM.BR  "), "user+teste@example.com.br"
        )
        self.assertEqual(normalize_email("  JOÃO.SILVA@EXEMPLO.COM  "), "joão.silva@exemplo.com")
        self.assertEqual(normalize_email("JOA\u0303O.SILVA@EXEMPLO.COM"), "joão.silva@exemplo.com")
        self.assertEqual(normalize_email("usuário@exémplo.com"), "usuário@xn--exmplo-cva.com")
        self.assertIsNone(normalize_email("NULL"))
        for raw in (
            "a.example.com",
            "a@@example.com",
            "a b@example.com",
            ".a@example.com",
            "a@-x.com",
            "a@example",
            "a\u200bb@example.com",
            "a😀@example.com",
        ):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                normalize_email(raw)

    def test_phone_requires_consistent_area_code(self):
        self.assertEqual(normalize_ddd("+55 11"), "11")
        self.assertEqual(normalize_phone(" 96373-7350 ", "85"), "+5585963737350")
        self.assertEqual(normalize_phone("+55 (11) 99999-1234", "11"), "+5511999991234")
        self.assertEqual(normalize_phone("1133334444"), "+551133334444")
        for raw, ddd in (
            ("11999991234", "21"),
            ("999991234", None),
            ("999991234", "20"),
            ("+449999999999", None),
        ):
            with self.subTest(raw=raw, ddd=ddd), self.assertRaises(ValueError):
                normalize_phone(raw, ddd)


if __name__ == "__main__":
    unittest.main()
