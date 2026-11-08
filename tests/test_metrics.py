"""Semântica dos indicadores com exemplos sintéticos, sem dados da fonte."""

import sqlite3
import unittest
from decimal import Decimal

from pipeline.metrics import analytics_queries, register_sqlite_aggregates, safe_ratio


class MetricsTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.addCleanup(self.db.close)
        register_sqlite_aggregates(self.db)
        self.db.execute("""CREATE TABLE records (
            registro_id TEXT, data_corte TEXT, tipo_pessoa TEXT, documento_devedor TEXT, cnpj_cedente TEXT,
            produto TEXT, faixa_atraso TEXT, status_qualidade TEXT, valor_original_centavos INTEGER,
            valor_contabil_centavos INTEGER, valor_face_centavos INTEGER, email_1 TEXT, email_2 TEXT,
            telefone_1 TEXT, telefone_2 TEXT, dias_atraso_calculado INTEGER, data_vencimento TEXT,
            possui_garantia INTEGER, ajuizado INTEGER)""")
        self.db.execute(
            "CREATE VIEW facts AS SELECT * FROM records WHERE status_qualidade <> 'QUARENTENA'"
        )
        self.db.execute(
            "CREATE TABLE issues (registro_id TEXT, codigo TEXT, campo TEXT, severidade TEXT)"
        )
        self.queries = analytics_queries(
            "sqlite", records="records", facts="facts", issues="issues"
        )

    def add(self, **changes):
        record = {
            "registro_id": "r1",
            "data_corte": "2026-08-01",
            "tipo_pessoa": "PF",
            "documento_devedor": "D1",
            "cnpj_cedente": "C1",
            "produto": "PRODUTO",
            "faixa_atraso": "091_180",
            "status_qualidade": "APTO",
            "valor_original_centavos": 100,
            "valor_contabil_centavos": 0,
            "valor_face_centavos": None,
            "email_1": "exemplo@example.invalid",
            "email_2": None,
            "telefone_1": None,
            "telefone_2": None,
            "dias_atraso_calculado": 91,
            "data_vencimento": "2026-05-02",
            "possui_garantia": None,
            "ajuizado": 0,
        }
        record.update(changes)
        fields = ",".join(record)
        self.db.execute(
            f"INSERT INTO records ({fields}) VALUES ({','.join('?' for _ in record)})",
            tuple(record.values()),
        )

    def rows(self, name):
        return [dict(row) for row in self.db.execute(self.queries[name])]

    def test_sums_preserve_null_zero_negative_and_exceed_64_bits(self):
        self.db.execute("CREATE TABLE cents (value INTEGER)")
        self.db.executemany("INSERT INTO cents VALUES (?)", [(None,), (None,)])
        self.assertIsNone(self.db.execute("SELECT sum_centavos(value) FROM cents").fetchone()[0])
        self.db.execute("INSERT INTO cents VALUES (0)")
        self.assertEqual(
            self.db.execute("SELECT sum_centavos(value) FROM cents").fetchone()[0], "0"
        )
        self.db.execute("INSERT INTO cents VALUES (-27)")
        self.assertEqual(
            self.db.execute("SELECT sum_centavos(value) FROM cents").fetchone()[0], "-27"
        )
        self.db.executemany("INSERT INTO cents VALUES (?)", [(2**63 - 1,), (2**63 - 1,)])
        self.assertEqual(
            self.db.execute("SELECT sum_centavos(value) FROM cents").fetchone()[0],
            str(2 * (2**63 - 1) - 27),
        )

    def test_marts_separate_contracts_from_people_and_preserve_financial_coverage(self):
        self.add()
        self.add(
            registro_id="r2",
            status_qualidade="REVISAO",
            valor_original_centavos=-25,
            valor_contabil_centavos=-100,
            valor_face_centavos=200,
            email_1=None,
            email_2="outro@example.invalid",
            telefone_2="11999990000",
            possui_garantia=1,
            ajuizado=1,
            dias_atraso_calculado=0,
            data_vencimento="2026-08-02",
        )
        self.add(
            registro_id="r3",
            tipo_pessoa="PJ",
            documento_devedor="D2",
            cnpj_cedente="C2",
            status_qualidade="QUARENTENA",
            valor_original_centavos=None,
            valor_contabil_centavos=None,
            valor_face_centavos=0,
            email_1=None,
            possui_garantia=0,
            ajuizado=None,
            dias_atraso_calculado=None,
            data_vencimento=None,
        )
        by_universe = {row["universo"]: row for row in self.rows("mart_indicadores_carteira")}
        base, published, eligible = (
            by_universe[name] for name in ("BASE_TRATADA", "CARTEIRA_PUBLICADA", "APTOS")
        )
        self.assertEqual(
            (base["quantidade"], published["quantidade"], eligible["quantidade"]), (3, 2, 1)
        )
        self.assertEqual((base["devedores_distintos"], base["cedentes_distintos"]), (2, 2))
        self.assertEqual(
            (published["devedores_distintos"], published["cedentes_distintos"]), (1, 1)
        )
        self.assertEqual((base["aptos"], base["revisao"], base["quarentena"]), (1, 1, 1))
        self.assertEqual((base["pessoas_fisicas"], base["pessoas_juridicas"]), (2, 1))
        self.assertEqual((base["com_email"], base["com_telefone"]), (2, 1))
        self.assertEqual(
            (
                base["atraso_calculavel"],
                base["atraso_sem_calculo"],
                base["atraso_acima_90"],
                base["vencimento_apos_corte"],
            ),
            (2, 1, 1, 1),
        )
        self.assertEqual(
            (base["garantia_sim"], base["garantia_nao"], base["garantia_sem_informacao"]), (1, 1, 1)
        )
        self.assertEqual(
            (base["ajuizado_sim"], base["ajuizado_nao"], base["ajuizado_sem_informacao"]), (1, 1, 1)
        )
        self.assertEqual(
            (
                base["saldo_contabil_centavos"],
                base["saldos_preenchidos"],
                base["saldos_ausentes"],
                base["saldos_negativos"],
            ),
            ("-100", 2, 1, 1),
        )
        self.assertEqual(
            (
                base["valor_original_centavos"],
                base["original_preenchidos"],
                base["original_negativos"],
            ),
            ("75", 2, 1),
        )
        self.assertEqual(
            (base["valor_face_centavos"], base["face_preenchidos"], base["face_ausentes"]),
            ("200", 2, 1),
        )
        self.assertIsNone(eligible["valor_face_centavos"])
        summary = self.rows("mart_resumo_carteira")
        self.assertEqual(sum(row["quantidade"] for row in summary), 2)
        self.assertEqual(
            sum(Decimal(row["saldo_contabil_centavos"]) for row in summary), Decimal(-100)
        )

    def test_empty_universe_has_zero_counts_not_one_missing_balance(self):
        self.add(
            status_qualidade="QUARENTENA",
            possui_garantia=2,
            ajuizado=2,
            documento_devedor=None,
            email_1="",
            telefone_1=" ",
        )
        rows = {row["universo"]: row for row in self.rows("mart_indicadores_carteira")}
        self.assertEqual(rows["BASE_TRATADA"]["devedores_distintos"], 0)
        self.assertEqual(rows["BASE_TRATADA"]["garantia_sem_informacao"], 1)
        self.assertEqual(rows["BASE_TRATADA"]["ajuizado_sem_informacao"], 1)
        self.assertEqual(rows["BASE_TRATADA"]["com_email"], 0)
        self.assertEqual(rows["BASE_TRATADA"]["com_telefone"], 0)
        for name in ("APTOS", "CARTEIRA_PUBLICADA"):
            empty = rows[name]
            self.assertEqual(
                (
                    empty["quantidade"],
                    empty["saldos_ausentes"],
                    empty["atraso_sem_calculo"],
                    empty["garantia_sem_informacao"],
                ),
                (0, 0, 0, 0),
            )
            self.assertIsNone(empty["saldo_contabil_centavos"])
        self.assertEqual(self.rows("mart_resumo_carteira"), [])

    def test_cuts_are_separate_and_90_days_is_not_above_90(self):
        self.add()
        self.add(registro_id="r2", data_corte="2026-09-01", dias_atraso_calculado=90)
        base = {
            row["data_corte"]: row
            for row in self.rows("mart_indicadores_carteira")
            if row["universo"] == "BASE_TRATADA"
        }
        self.assertEqual(set(base), {"2026-08-01", "2026-09-01"})
        self.assertEqual(base["2026-09-01"]["quantidade"], 1)
        self.assertEqual(base["2026-09-01"]["atraso_acima_90"], 0)

    def test_quality_counts_occurrences_and_records_with_whole_load_denominator(self):
        self.add()
        self.add(registro_id="r2", data_corte="2026-09-01")
        self.db.executemany(
            "INSERT INTO issues VALUES (?,?,?,?)",
            [
                ("r1", "REGRA", "valor_original_centavos", "WARNING"),
                ("r1", "REGRA", "valor_original_centavos", "WARNING"),
                ("r1", "REGRA", "valor_face_centavos", "WARNING"),
            ],
        )
        rows = {row["campo"]: row for row in self.rows("mart_qualidade")}
        self.assertEqual(
            (
                rows["valor_original_centavos"]["ocorrencias"],
                rows["valor_original_centavos"]["registros_afetados"],
            ),
            (2, 1),
        )
        self.assertTrue(all(row["total_registros"] == 2 for row in rows.values()))

    def test_ratio_uses_decimal_and_has_no_value_without_a_positive_denominator(self):
        self.assertEqual(safe_ratio(1, 4), Decimal("0.25"))
        self.assertIsNone(safe_ratio(1, 0))
        self.assertIsNone(safe_ratio(None, 3))
        self.assertIsNone(safe_ratio(1, -1))
        with self.assertRaises(TypeError):
            safe_ratio(0.1, 1)


if __name__ == "__main__":
    unittest.main()
