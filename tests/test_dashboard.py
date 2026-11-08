"""Valida precisão e segurança na apresentação dos agregados."""

import unittest
from decimal import Decimal

from pipeline.dashboard import average_cents, money, percent, render_dashboard


class DashboardTests(unittest.TestCase):
    def test_money_and_average_keep_exact_values_and_missing_denominators(self):
        self.assertEqual(money("18446744073709551514"), "R$ 184.467.440.737.095.515,14")
        self.assertEqual(money(-100), "−R$ 1,00")
        self.assertEqual(money(None), "Não disponível")
        self.assertIsNone(average_cents(None, 10))
        self.assertIsNone(average_cents(0, 0))
        self.assertEqual(average_cents(1, 2), Decimal("0.5"))
        self.assertEqual(percent(1, 4), "25,00%")
        self.assertEqual(percent(0, 0), "Não disponível")

    def test_source_metadata_is_escaped_and_quality_is_not_a_price(self):
        page = render_dashboard({"source_filename": '<script>alert("x")</script>'})
        self.assertNotIn('<script>alert("x")</script>', page)
        self.assertIn("&lt;script&gt;", page)
        self.assertIn("não contém eventos de recebimento", page)
        self.assertIn("incluindo quarentena e todos os cortes", page)
