"""Регрессия: classmethod-конструктор ошибки в AuthResult не должен затенять
поле `error`.

Раньше метод назывался `error` — как и поле `error: Optional[str]`. Из-за этого
@dataclass брал в качестве дефолта поля сам classmethod, и у НЕ-ошибочных
результатов `.error` оказывался bound-методом (truthy), а не None. Проверка
вида `if result.error:` молча ломалась бы.
"""

from __future__ import annotations

import unittest

from tg_exporter.core.auth import AuthResult, AuthStep


class TestAuthResultErrorField(unittest.TestCase):
    def test_non_error_results_have_none_error(self):
        self.assertIsNone(AuthResult.ok().error)
        self.assertIsNone(AuthResult.waiting().error)
        self.assertIsNone(AuthResult.expired().error)
        self.assertIsNone(AuthResult.code_sent().error)
        self.assertIsNone(AuthResult.password_required().error)

    def test_failure_carries_message(self):
        r = AuthResult.failure("boom")
        self.assertEqual(r.step, AuthStep.ERROR)
        self.assertEqual(r.error, "boom")


if __name__ == "__main__":
    unittest.main()
