"""Регистрационные номера без пропусков.

Последовательности работали в режиме «standard»: номер выдаёт
postgres-последовательность, а она не транзакционна. Любой откат — сбой при
подписании, закрытое окно NCALayer, исключение в after_script — съедал номер,
и в журнале регистрации появлялась дыра. Режим «no_gap» хранит счётчик строкой
в ir_sequence и берёт его под блокировку в той же транзакции, поэтому откат
возвращает номер на место.

Почему не только в data/ir_sequence.xml: ir.sequence.write() при переходе
standard -> no_gap дропает postgres-последовательность и НЕ переносит её
позицию в поле number_next (odoo/addons/base/models/ir_sequence.py). Колонка
number_next в режиме standard не обновляется и осталась единицей, так что после
наивного переключения нумерация пошла бы по второму кругу и выдала дубли
номеров. Поэтому позицию переносим руками.

Скрипт pre-migrate: отрабатывает ДО загрузки данных модуля, поэтому к моменту,
когда XML запишет implementation=no_gap, режим уже переключён и тот write
попадёт в ветку no_gap -> no_gap, то есть ничего не сделает.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

SEQUENCES = (
    "correspondence.seq_correspondence_incoming",
    "correspondence.seq_correspondence_outgoing",
)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    for xmlid in SEQUENCES:
        sequence = env.ref(xmlid, raise_if_not_found=False)
        if not sequence:
            _logger.warning("Последовательность %s не найдена — пропускаем", xmlid)
            continue
        if sequence.implementation == "no_gap":
            continue
        # Читаем, ПОКА режим ещё standard: number_next_actual в этом режиме
        # смотрит в саму postgres-последовательность, а number_next — нет.
        number_next = max(sequence.number_next_actual or 0, 1)
        sequence.write({"implementation": "no_gap", "number_next": number_next})
        _logger.info("%s переведена в no_gap, следующий номер: %s", xmlid, number_next)
