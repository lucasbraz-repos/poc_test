"""Integração das decisões de limpeza com identidade e valores financeiros."""

from datetime import date
import unittest

from pipeline.cleaning import Cleaner, quality_status


def base_record():
    return {
        "A": "ID001", "B": "04.252.011/0001-10", "N": "04252011000110",
        "C": "529.982.247-25", "X": "52998224725", "AQ": "529 982 247 25", "BR": "52998224725",
        "D": "ct001", "P": "CT001", "AR": " CT001 ", "BS": "CT001",
        "Y": "Pessoa Física", "E": "João Silva", "Z": "JOÃO SILVA", "BT": "João  Silva",
        "CA": "08-01-2026", "AU": "01-02-2020", "AV": "07-01-2024",
        "AS": "Crédito pessoal", "AW": "1000,00", "AX": "950,00", "AY": "1100,00",
        "BG": "12", "BL": "12", "BI": "1", "BK": "11", "AF": "SP",
    }


class CleaningTests(unittest.TestCase):
    def test_source_date_convention_and_aging_use_cutoff(self):
        data, issues = Cleaner(base_record()).run()
        self.assertEqual(data["data_corte"], "2026-08-01")
        self.assertEqual(data["data_contratacao"], "2020-01-02")
        expected = (date(2026, 8, 1) - date(2024, 7, 1)).days
        self.assertEqual(data["dias_atraso_calculado"], expected)
        self.assertEqual(data["faixa_atraso"], "721_1080")
        self.assertEqual(quality_status(issues), "APTO")

    def test_pf_never_carries_pj_attributes(self):
        raw = base_record()
        raw.update({"F": "1980-03-10", "G": "Empresa empregadora", "R": "2000-01-01",
                    "S": "Ativa", "T": "52998224725", "U": "Sócio Um", "M": "1250,50"})
        data, issues = Cleaner(raw).run()
        for field in ("data_abertura", "situacao_empresa", "socio_1_documento", "socio_1_nome", "socio_2_documento", "socio_2_nome"):
            self.assertIsNone(data[field], field)
        self.assertEqual(data["data_nascimento"], "1980-03-10")
        self.assertEqual(data["renda_centavos"], 125050)
        self.assertTrue(any(i["codigo"] == "CAMPO_INAPLICAVEL_REMOVIDO" for i in issues))

    def test_pj_never_carries_pf_attributes(self):
        raw = base_record()
        raw.update({"Y": "PJ", "O": "04252011000110", "X": "04252011000110",
                    "AQ": "04252011000110", "BR": "04252011000110", "Q": "Empresa Ltda",
                    "Z": "Empresa Ltda", "BT": "Empresa Ltda", "F": "1980-03-10", "G": "Empregadora",
                    "H": "Feminino", "I": "Casada", "J": "Professora", "K": "Nome Mãe", "L": "Sim",
                    "M": "9000,00", "R": "2010-01-01", "S": "Ativa"})
        data, issues = Cleaner(raw).run()
        for field in ("data_nascimento", "empresa_empregadora", "sexo", "estado_civil", "profissao", "nome_mae", "obito", "renda_centavos"):
            self.assertIsNone(data[field], field)
        self.assertEqual(data["nome_devedor"], "EMPRESA LTDA")
        self.assertEqual(data["data_abertura"], "2010-01-01")
        self.assertEqual(data["documento_devedor_valido"], 1)

    def test_unicode_email_and_excel_residue_are_preserved(self):
        raw = base_record()
        raw.update({"AO": "JOÃO.SILVA@EXEMPLO.COM", "AW": "33155.620000000003", "AF": "São Paulo"})
        data, issues = Cleaner(raw).run()
        self.assertEqual(data["email_1"], "joão.silva@exemplo.com")
        self.assertEqual(data["valor_original_centavos"], 3315562)
        self.assertEqual(data["uf"], "SP")
        self.assertFalse(any(i["codigo"] == "FORMATO_INVALIDO" for i in issues))
        self.assertTrue(any(i["codigo"] == "RESIDUO_NUMERICO_EXCEL_AJUSTADO" and i["severidade"] == "INFO" for i in issues))

    def test_negative_balance_is_never_abs_or_zeroed(self):
        raw = base_record()
        raw["AW"] = "(1.250,50)"
        data, issues = Cleaner(raw).run()
        self.assertEqual(data["valor_original_centavos"], -125050)
        self.assertEqual(quality_status(issues), "REVISAO")

    def test_identity_conflict_is_quarantined(self):
        raw = base_record()
        raw["AQ"] = "11144477735"
        data, issues = Cleaner(raw).run()
        self.assertEqual(data["documento_devedor"], "52998224725")
        self.assertEqual(quality_status(issues), "QUARENTENA")
        self.assertTrue(any(i["codigo"] == "COLUNAS_REDUNDANTES_DIVERGENTES" and i["severidade"] == "ERROR" for i in issues))

    def test_missing_name_recovers_from_redundant_column(self):
        raw = base_record()
        raw["E"] = "NULL"
        data, issues = Cleaner(raw).run()
        self.assertEqual(data["nome_devedor"], "JOÃO SILVA")
        self.assertTrue(any(i["codigo"] == "RECUPERADO_DE_COLUNA_REDUNDANTE" for i in issues))
        self.assertEqual(quality_status(issues), "APTO")

    def test_derived_remaining_installments_and_conflicting_flags(self):
        raw = base_record()
        raw.update({"BK": "N/A", "BM": "Não", "BN": "52998224725", "BO": "Não", "BP": "Aval"})
        data, issues = Cleaner(raw).run()
        self.assertEqual(data["parcelas_restantes"], 11)
        self.assertEqual(data["possui_avalista"], 0)
        self.assertEqual(data["documento_avalista"], "52998224725")
        self.assertEqual(data["possui_garantia"], 0)
        codes = {i["codigo"] for i in issues}
        self.assertIn("PARCELAS_RESTANTES_DERIVADAS", codes)
        self.assertIn("DOCUMENTO_AVALISTA_COM_FLAG_NAO", codes)
        self.assertIn("GARANTIA_CONTRADIZ_FLAG", codes)


if __name__ == "__main__":
    unittest.main()
