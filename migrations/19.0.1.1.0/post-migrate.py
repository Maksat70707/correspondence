# -*- coding: utf-8 -*-
"""Поручения и резолюции переехали в модуль assignment.

Было: correspondence.assignment.line и correspondence.resolution.option —
модели корреспонденции. Стало: assignment.line и assignment.resolution
модуля assignment, который корреспонденция теперь требует. Модуль
assignment ставится при обновлении корреспонденции раньше неё, его
таблицы к этому моменту есть и пусты (или уже с поручениями другого
модуля), а старые таблицы корреспонденции ещё не тронуты: Odoo их сам не
удаляет, модели просто исчезают из реестра.

Скрипт post-migrate: колонка incoming_id на assignment_line появляется,
только когда корреспонденция загружена.

Что переносится:
- резолюции. Стандартные (correspondence_res_NN) ложатся на такие же
  стандартные модуля assignment (resolution_NN) — с правками
  администратора: название, срок, отчёт, архив. Свои — новыми записями;
- поручения — новыми id (у assignment.line своя последовательность, и в
  ней уже могут быть поручения другого модуля), со ссылками на
  резолюции, историей исполнителей и вложениями;
- чаттер, подписчики, активности, вложения, история изменений полей
  (mail.tracking.value ссылается на поле старой модели — без переноса её
  удалило бы вместе с полем), сохранённые фильтры.

В конце старые таблицы удаляются. Повторный запуск ничего не делает:
старой таблицы уже нет.
"""

import logging

from odoo import SUPERUSER_ID, api
from odoo.tools.sql import table_exists

_logger = logging.getLogger(__name__)

OLD_LINE = "correspondence.assignment.line"
NEW_LINE = "assignment.line"
OLD_RES = "correspondence.resolution.option"
NEW_RES = "assignment.resolution"

OLD_TABLES = (
    "correspondence_assignment_line",
    "assignment_executor_history_rel",
    "correspondence_assignment_docs_rel",
    "correspondence_resolution_option",
    "correspondence_rework_wizard_line",
    "correspondence_rework_wizard",
)

LINE_COLUMNS = (
    "incoming_id", "user_id", "original_user_id", "assigner_id", "department_id",
    "create_uid", "write_uid", "last_notified_status", "status", "deadline", "task",
    "report", "delegated", "report_needed", "needs_notification",
    "completion_datetime", "create_date", "write_date",
)


def migrate(cr, version):
    if not version or not table_exists(cr, "correspondence_assignment_line"):
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    resolutions = _migrate_resolutions(cr)
    lines = _migrate_lines(cr, resolutions)
    _migrate_relations(cr)
    _remap_tracking_resolutions(cr)
    _migrate_references(cr, env)
    for table in OLD_TABLES:
        cr.execute('DROP TABLE IF EXISTS "%s" CASCADE' % table)
    _logger.info(
        "Поручения: перенесено в модуль assignment %s поручений и %s резолюций",
        lines, resolutions,
    )


def _migrate_resolutions(cr):
    """Резолюции -> assignment.resolution. Соответствие old_id -> new_id —
    во временной таблице asg_res_map."""
    cr.execute("CREATE TEMP TABLE asg_res_map (old_id integer PRIMARY KEY, new_id integer)")
    cr.execute("""
        SELECT r.id, r.name, r.active, r.report_needed, r.days_for_completion,
               r.create_uid, r.create_date, r.write_uid, r.write_date, d.name
          FROM correspondence_resolution_option r
          LEFT JOIN ir_model_data d
                 ON d.model = %s AND d.res_id = r.id AND d.module = 'correspondence'
         ORDER BY r.id
    """, [OLD_RES])
    rows = cr.fetchall()
    for (old_id, name, active, report_needed, days, create_uid, create_date,
         write_uid, write_date, xmlid) in rows:
        new_id = None
        if xmlid and xmlid.startswith("correspondence_res_"):
            cr.execute(
                "SELECT res_id FROM ir_model_data WHERE module = 'assignment' AND name = %s",
                ["resolution_" + xmlid[len("correspondence_res_"):]],
            )
            found = cr.fetchone()
            new_id = found and found[0]
        if new_id:
            # стандартная резолюция — с правками администратора
            cr.execute("""
                UPDATE assignment_resolution
                   SET name = %s, active = %s, report_needed = %s, days_for_completion = %s
                 WHERE id = %s
            """, [name, active, report_needed, days, new_id])
        else:
            cr.execute("""
                INSERT INTO assignment_resolution
                       (name, active, report_needed, days_for_completion,
                        create_uid, create_date, write_uid, write_date)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, [name, active, report_needed, days, create_uid, create_date,
                  write_uid, write_date])
            new_id = cr.fetchone()[0]
        cr.execute("INSERT INTO asg_res_map VALUES (%s, %s)", [old_id, new_id])
    # xmlid старых записей больше не нужны: стандартные теперь у assignment
    cr.execute("DELETE FROM ir_model_data WHERE model = %s", [OLD_RES])
    return len(rows)


def _migrate_lines(cr, resolutions):
    """Поручения -> assignment.line с новыми id (asg_line_map)."""
    cr.execute("""
        CREATE TEMP TABLE asg_line_map AS
        SELECT old_id, nextval('assignment_line_id_seq') AS new_id
          FROM (SELECT id AS old_id FROM correspondence_assignment_line ORDER BY id) ordered
    """)
    cr.execute("ALTER TABLE asg_line_map ADD PRIMARY KEY (old_id)")
    columns = ", ".join(LINE_COLUMNS)
    selected = ", ".join("o.%s" % column for column in LINE_COLUMNS)
    cr.execute("""
        INSERT INTO assignment_line (id, resolution_id, document_model, %s)
        SELECT m.new_id, rm.new_id,
               CASE WHEN o.incoming_id IS NOT NULL THEN 'corr.incoming' END,
               %s
          FROM correspondence_assignment_line o
          JOIN asg_line_map m ON m.old_id = o.id
          JOIN asg_res_map rm ON rm.old_id = o.resolution_id
    """ % (columns, selected))
    return cr.rowcount


def _migrate_relations(cr):
    if table_exists(cr, "assignment_executor_history_rel"):
        cr.execute("""
            INSERT INTO assignment_line_executor_rel (line_id, user_id)
            SELECT m.new_id, r.user_id
              FROM assignment_executor_history_rel r
              JOIN asg_line_map m ON m.old_id = r.assignment_id
            ON CONFLICT DO NOTHING
        """)
    if table_exists(cr, "correspondence_assignment_docs_rel"):
        cr.execute("""
            INSERT INTO assignment_line_attachment_rel (line_id, attachment_id)
            SELECT m.new_id, r.attachment_id
              FROM correspondence_assignment_docs_rel r
              JOIN asg_line_map m ON m.old_id = r.line_id
            ON CONFLICT DO NOTHING
        """)


def _migrate_references(cr, env):
    """Чаттер, подписчики, активности, вложения и фильтры старой модели."""
    for table, model_column in (
        ("mail_message", "model"),
        ("mail_followers", "res_model"),
        ("mail_activity", "res_model"),
        ("ir_attachment", "res_model"),
    ):
        cr.execute("""
            UPDATE %(table)s t
               SET %(col)s = %%s, res_id = m.new_id
              FROM asg_line_map m
             WHERE t.%(col)s = %%s AND t.res_id = m.old_id
        """ % {"table": table, "col": model_column}, [NEW_LINE, OLD_LINE])
    # то, что осталось от удалённых когда-то поручений, — сироты
    cr.execute("DELETE FROM mail_followers WHERE res_model = %s", [OLD_LINE])
    cr.execute("DELETE FROM mail_activity WHERE res_model = %s", [OLD_LINE])
    cr.execute("DELETE FROM mail_message WHERE model = %s", [OLD_LINE])
    cr.execute("UPDATE ir_attachment SET res_model = NULL, res_id = NULL WHERE res_model = %s",
               [OLD_LINE])

    new_model_id = env["ir.model"]._get_id(NEW_LINE)
    cr.execute("UPDATE mail_activity SET res_model_id = %s WHERE res_model = %s",
               [new_model_id, NEW_LINE])

    # история изменений: поле старой модели -> поле новой с тем же именем
    cr.execute("""
        UPDATE mail_tracking_value t
           SET field_id = nf.id
          FROM ir_model_fields old_f, ir_model_fields nf
         WHERE t.field_id = old_f.id
           AND old_f.model = %s
           AND nf.model = %s
           AND nf.name = old_f.name
    """, [OLD_LINE, NEW_LINE])

    cr.execute("UPDATE ir_filters SET model_id = %s WHERE model_id = %s", [NEW_LINE, OLD_LINE])
    cr.execute("UPDATE ir_filters SET model_id = %s WHERE model_id = %s", [NEW_RES, OLD_RES])


def _remap_tracking_resolutions(cr):
    """В истории «Резолюция: было -> стало» лежат id резолюций — старые.
    До переноса истории на поля новой модели: по полю старой модели видно,
    что запись наша, а не поручения другого модуля."""
    cr.execute("""
        SELECT id FROM ir_model_fields WHERE model = %s AND name = 'resolution_id'
    """, [OLD_LINE])
    found = cr.fetchone()
    if not found:
        return
    for column in ("old_value_integer", "new_value_integer"):
        cr.execute("""
            UPDATE mail_tracking_value t
               SET %(col)s = rm.new_id
              FROM asg_res_map rm
             WHERE t.field_id = %%s AND t.%(col)s = rm.old_id
        """ % {"col": column}, [found[0]])
