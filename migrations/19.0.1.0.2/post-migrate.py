"""Счётчик подписей ЭЦП убран.

Поле number_of_esp_signs увеличивалось при каждой подписи и обнулялось при
возврате в черновик, но не читалось нигде: ни в коде, ни в представлениях,
ни в отчётах, ни в шаблонах DOCX. Оно обслуживало прежнее брендирование
файлов, которого больше нет.

Odoo сам колонку не удаляет — поле просто исчезает из реестра, а столбец
остаётся в таблице. Убираем его здесь.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    cr.execute("""
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'corr_outgoing' AND column_name = 'number_of_esp_signs'
    """)
    if not cr.fetchone():
        return
    cr.execute('ALTER TABLE corr_outgoing DROP COLUMN number_of_esp_signs')
    _logger.info("Столбец corr_outgoing.number_of_esp_signs удалён")
