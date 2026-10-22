import base64
from datetime import timedelta

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import Form, TransactionCase, new_test_user, tagged

from .common import _u, docx_t_nodes, b64, docx_parts, docx_text, make_docx


@tagged("post_install", "-at_install")
class TestCorrespondence(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        emp_grp = "base.group_user,correspondence.group_correspondence_employee"
        cls.dep = env["hr.department"].create({"name": "Отдел К"})
        cls.init = new_test_user(env, "c_init", groups=emp_grp, name="Исполнитель письма")
        cls.boss = new_test_user(env, "c_boss", groups=emp_grp, name="Начальник")
        cls.signer = new_test_user(env, "c_signer", groups=emp_grp, name="Утверждающий")
        cls.colleague = new_test_user(env, "c_coll", groups=emp_grp, name="Коллега")
        cls.executor = new_test_user(env, "c_exec", groups=emp_grp, name="Исполнитель задания")
        cls.secretary = new_test_user(
            env, "c_secr", name="Секретарь",
            groups="base.group_user,correspondence.group_correspondence_secretary")
        cls.director = new_test_user(
            env, "c_dir", name="Директор",
            groups="base.group_user,correspondence.group_correspondence_director")
        Emp = env["hr.employee"]
        boss_emp = Emp.create({"name": cls.boss.name, "user_id": cls.boss.id, "department_id": cls.dep.id})
        Emp.create({"name": cls.init.name, "user_id": cls.init.id, "department_id": cls.dep.id,
                    "parent_id": boss_emp.id})
        Emp.create({"name": cls.colleague.name, "user_id": cls.colleague.id, "department_id": cls.dep.id})
        for u in (cls.signer, cls.executor, cls.secretary, cls.director):
            Emp.create({"name": u.name, "user_id": u.id})
        cls.stage_approval = env.ref("correspondence.corr_outgoing_workflow_approval")

    # ------------------------------------------------------------------
    def _approve(self, rec, user, reason=""):
        wiz = _u(self.env["approval.approve.wizard"]).with_user(user).with_context(
            active_model=rec._name, active_id=rec.id).create({"reason": reason})
        return wiz.action_approve()

    def _new_outgoing(self, raw=None):
        att = _u(self.env["ir.attachment"]).with_user(self.init).create({
            "name": "letter.docx", "res_model": "corr.outgoing", "res_id": 0,
            "datas": b64(raw or make_docx(body_split=["Исх. № {{na", "me}} от {{date}}"],
                                          header="{{signer}}")),
        })
        return _u(self.env["corr.outgoing"]).with_user(self.init).create({
            "subject": "Тема", "summary": "Суть", "esp_signer_id": self.signer.id,
            "type_id": self.env.ref("correspondence.simple").id,
            "attachment_to_sign_ids": [Command.set(att.ids)],
        })

    # ------------------------------------------------------------------
    def test_01_fresh_install_signs_files(self):
        """На чистой установке этап Утверждения должен подписывать файл письма,
        а не данные записи (миграция 19.0.1.0.1 на установке не запускается)."""
        self.assertEqual(self.stage_approval.esp_type, "xml_doc")
        self.assertIn("attachment_to_sign_ids",
                      self.stage_approval.esp_document_field_ids.mapped("name"))

    def test_02_outgoing_full_route(self):
        # настройка этапа как после миграции — чтобы проверить остальной путь
        fields_ = self.env["ir.model.fields"]._get("corr.outgoing", "attachment_to_sign_ids") | \
            self.env["ir.model.fields"]._get("corr.outgoing", "attachment_additional_sign_ids")
        self.stage_approval.write({"esp_type": "xml_doc",
                                   "esp_document_field_ids": [Command.set(fields_.ids)]})
        rec = self._new_outgoing()
        _u(rec).with_user(self.init).action_send_to_approval()
        self.assertEqual(rec.state, "under_approval")
        self.assertEqual(rec.state_agreement_line_ids.user_id, self.boss)

        self._approve(rec, self.boss)
        self.assertEqual(rec.state, "approval")
        line = rec.state_agreement_line_ids.filtered(lambda l: l.user_id == self.signer)
        self.assertTrue(line.need_esp)
        self.assertEqual(line.status, "in_progress")

        # имитация /sign_esp
        srec = _u(rec).with_user(self.signer).sudo()
        names = srec._esp_document_fields(line)
        self.assertIn("attachment_to_sign_ids", names)
        draft = rec.name_draft
        self.assertTrue(draft)
        src = srec._esp_field_sources("attachment_to_sign_ids")
        text = docx_text(base64.b64decode(src[0]["datas"]))
        self.assertIn(draft, text)
        self.assertNotIn("{{", text)
        srec._esp_after_signature(b"")
        self.assertEqual(rec.name, draft)
        line.sudo().write({"signed": True})
        srec.action_approve()
        self.assertEqual(rec.state, "processing")
        sec_line = rec.state_agreement_line_ids
        self.assertEqual(sec_line.user_id, self.secretary)

        self._approve(rec, self.secretary)
        self.assertEqual(rec.state, "review")
        _u(rec).with_user(self.init).action_acknowledge()
        self.assertEqual(rec.state, "done")
        self.assertFalse(rec.message_ids.filtered(lambda m: "Документ нужно собрать" in (m.body or "")))

    def test_03_assignment_status_flags_notification(self):
        inc = _u(self.env["corr.incoming"]).with_user(self.secretary).create({
            "subject": "Вх", "summary": "Суть", "doc_arrival_date": fields.Date.today(),
        })
        line = _u(self.env["assignment.line"]).with_user(self.secretary).create({
            "incoming_id": inc.id, "user_id": self.executor.id,
            "resolution_id": self.env.ref("assignment.resolution_01").id,
            "report_needed": True,
        })
        _u(line).with_user(self.executor).write({"status": "in_progress"})
        self.assertTrue(line.needs_notification,
                        "смена статуса исполнителем не помечает задание для уведомления")

    def test_04_incoming_route(self):
        group = self.env["appstream.approval.group"].create({
            "name": "Гендир", "user_ids": [Command.set(self.director.ids)]})
        self.env.ref("correspondence.corr_incoming_workflow_review").approval_group_ids = group
        inc = _u(self.env["corr.incoming"]).with_user(self.secretary).create({
            "subject": "Вх", "summary": "Суть", "doc_arrival_date": fields.Date.today(),
        })
        _u(inc).with_user(self.secretary).action_send_to_approval()
        self.assertEqual(inc.state, "review")
        self.assertEqual(inc.state_agreement_line_ids.user_id, self.director)
        _u(self.env["assignment.line"]).with_user(self.director).create({
            "incoming_id": inc.id, "user_id": self.executor.id,
            "resolution_id": self.env.ref("assignment.resolution_01").id,
        })
        self._approve(inc, self.director)
        self.assertEqual(inc.state, "execution")
        line = inc.assignment_line_ids
        _u(line).with_user(self.executor).write({"status": "done", "report": "Сделано"})
        self.assertEqual(inc.state, "done")

    def test_05_close_document_requires_rights(self):
        """Закрыть входящее без заданий может не каждый, кто его читает."""
        inc = _u(self.env["corr.incoming"]).with_user(self.secretary).create({
            "subject": "Вх", "summary": "Суть", "doc_arrival_date": fields.Date.today(),
        })
        inc.sudo().state = "review"
        _u(self.env["assignment.line"]).with_user(self.secretary).create({
            "incoming_id": inc.id, "user_id": self.executor.id,
            "resolution_id": self.env.ref("assignment.resolution_01").id,
        })
        with self.assertRaises(AccessError):
            _u(inc).with_user(self.executor).action_close_document()

    def test_06_department_visibility(self):
        rec = self._new_outgoing()
        Out = self.env["corr.outgoing"]
        self.assertIn(rec, _u(Out).with_user(self.colleague).search([]))
        self.assertIn(rec, _u(Out).with_user(self.colleague).search([("is_my_department", "=", True)]))
        self.assertNotIn(rec, _u(Out).with_user(self.executor).search([]))

    def test_06a_confidential_hidden_from_department(self):
        """«Конфиденциально» закрывает письмо от подразделения, но не от
        участников и ролей с полным доступом."""
        rec = self._new_outgoing()
        _u(rec).with_user(self.init).write({"is_confidential": True})
        Out = self.env["corr.outgoing"]
        self.assertNotIn(rec, _u(Out).with_user(self.colleague).search([]))
        self.assertNotIn(rec, _u(Out).with_user(self.colleague).search(
            [("is_my_department", "=", True)]))
        with self.assertRaises(AccessError):
            _u(rec).with_user(self.colleague).check_access("read")
        for user in (self.init, self.secretary, self.director):
            self.assertIn(rec, _u(Out).with_user(user).search([]), user.name)
        # начальник — согласующий: видит, хотя он из того же подразделения
        _u(rec).with_user(self.init).action_send_to_approval()
        self.assertEqual(rec.state_agreement_line_ids.user_id, self.boss)
        self.assertIn(rec, _u(Out).with_user(self.boss).search([]))
        # снять отметку согласующий не может, автор — может
        self.assertFalse(_u(rec).with_user(self.boss).can_change_confidential)
        with self.assertRaises(UserError):
            _u(rec).with_user(self.boss).write({"is_confidential": False})
        self.assertTrue(_u(rec).with_user(self.init).can_change_confidential)
        _u(rec).with_user(self.init).write({"is_confidential": False})
        self.assertIn(rec, _u(Out).with_user(self.colleague).search([]))

    def test_06b_follow_up_keeps_confidential(self):
        rec = self._new_outgoing()
        _u(rec).with_user(self.init).write({"is_confidential": True})
        for method in ("action_create_follow_up_outgoing", "action_view_follow_up_outgoing"):
            action = getattr(_u(rec).with_user(self.init), method)()
            self.assertTrue(action["context"]["default_is_confidential"], method)

    def test_07_forms_open(self):
        rec = self._new_outgoing()
        _u(rec).with_user(self.init).action_send_to_approval()
        for user in (self.init, self.boss, self.colleague, self.secretary):
            r = _u(rec).with_user(user)
            r.get_views([(False, "form"), (False, "list"), (False, "search")])
            Form(r)
        inc = _u(self.env["corr.incoming"]).with_user(self.secretary).create({
            "subject": "Вх", "summary": "Суть", "doc_arrival_date": fields.Date.today(),
        })
        for user in (self.secretary, self.director):
            Form(_u(inc).with_user(user))

    def test_08_template_fill_does_not_add_headers(self):
        rec = self._new_outgoing()
        raw = make_docx(body_split=["{{subject}}"])
        filled = rec.fill_template(raw, "x.docx")
        added = set(docx_parts(filled)) - set(docx_parts(raw))
        self.assertFalse(added, "подстановка добавила в документ части: %s" % added)

    def test_09_template_fill_everywhere(self):
        rec = self._new_outgoing()
        raw = make_docx(header="{{name}}", body_split=["{{sub", "ject}}"], table="{{signer}}",
                        hyperlink="{{date}}", ins="{{executor}}", sdt="{{company}}")
        text = docx_text(rec.fill_template(raw, "x.docx"))
        self.assertNotIn("{{", text, text)

    def _configure_stage(self):
        fields_ = self.env["ir.model.fields"]._get("corr.outgoing", "attachment_to_sign_ids") | \
            self.env["ir.model.fields"]._get("corr.outgoing", "attachment_additional_sign_ids")
        self.stage_approval.write({"esp_type": "xml_doc",
                                   "esp_document_field_ids": [Command.set(fields_.ids)]})

    def test_10_return_to_intermediate_stage_rebuilds_route(self):
        rec = self._new_outgoing()
        _u(rec).with_user(self.init).action_send_to_approval()
        self._approve(rec, self.boss)
        self.assertEqual(rec.state, "approval")
        wiz_model = _u(self.env["approval.return.wizard"]).with_user(self.signer).with_context(
            active_model=rec._name, active_id=rec.id)
        wiz = wiz_model.create({"reason": "Переделать",
                                **wiz_model.default_get(["state_option_ids", "model", "record_id"])})
        wiz.state = wiz.state_option_ids.filtered(lambda o: o.technical_name == "under_approval")
        self.assertTrue(wiz.state, "в мастере возврата нет «Согласования»")
        wiz.action_return()
        self.assertEqual(rec.state, "under_approval")
        line = rec.state_agreement_line_ids.filtered(lambda l: l.status == "in_progress")
        self.assertEqual(line.user_id, self.boss, "после возврата на Согласование нет согласующих")

    def test_11_remark_replacement_is_filled(self):
        self._configure_stage()
        rec = self._new_outgoing()
        _u(rec).with_user(self.init).action_send_to_approval()
        self._approve(rec, self.boss)
        srec = rec.sudo()
        srec._refresh_name_draft()
        esp = self.env["appstream.approval.esp"].sudo().create({"model": rec._name, "res_id": rec.id})
        documents = esp._create_documents(srec._esp_field_sources("attachment_to_sign_ids"))
        new_raw = make_docx(body_split=["Исх. {{na", "me}}"])
        new_document, _lines = documents[0]._replace_file(b64(new_raw), "v2.docx")
        text = docx_text(base64.b64decode(new_document.attachment_id.datas))
        self.assertIn(rec.name_draft, text)
        self.assertNotIn("{{", text)

    def _return_to(self, rec, user, state):
        wiz_model = _u(self.env["approval.return.wizard"]).with_user(user).with_context(
            active_model=rec._name, active_id=rec.id)
        wiz = wiz_model.create({"reason": "Переделать",
                                **wiz_model.default_get(["state_option_ids", "model", "record_id"])})
        wiz.state = wiz.state_option_ids.filtered(lambda o: o.technical_name == state)
        self.assertTrue(wiz.state, "в мастере возврата нет статуса %s" % state)
        return wiz.action_return()

    def test_12_return_not_blocked_by_template_error(self):
        """Ошибка в шаблоне не должна мешать вернуть письмо на доработку."""
        rec = self._new_outgoing()
        _u(rec).with_user(self.init).action_send_to_approval()
        self._approve(rec, self.boss)
        bad = self.env["ir.attachment"].create({
            "name": "bad.docx", "res_model": rec._name, "res_id": rec.id,
            "datas": b64(make_docx(body_split=["{{opechatka}}"])),
        })
        rec.sudo().with_context(esp_document_sync=True).attachment_to_sign_ids = bad
        self._return_to(rec, self.signer, "under_approval")
        self.assertEqual(rec.state, "under_approval")
        self.assertEqual(rec.state_agreement_line_ids.filtered(
            lambda l: l.status == "in_progress").user_id, self.boss)

    def test_13_remark_replacement_with_unknown_field_rejected(self):
        self._configure_stage()
        rec = self._new_outgoing()
        _u(rec).with_user(self.init).action_send_to_approval()
        self._approve(rec, self.boss)
        srec = rec.sudo()
        esp = self.env["appstream.approval.esp"].sudo().create({"model": rec._name, "res_id": rec.id})
        documents = esp._create_documents(srec._esp_field_sources("attachment_to_sign_ids"))
        with self.assertRaises(ValidationError):
            documents[0]._replace_file(b64(make_docx(body_split=["{{nmae}}"])), "v2.docx")

    def test_20_edge_spaces_preserved(self):
        raw = make_docx(body_split=["№", "{{sub", "ject}} от", " конец"])
        rec = self._new_outgoing()
        filled = rec.fill_template(raw, "x.docx")
        bad = [t for t, preserved in docx_t_nodes(filled) if t != t.strip() and not preserved]
        self.assertFalse(bad, "узлы с пробелом по краю без xml:space=preserve: %r" % bad)

    # ------------------------------------------------------------------
    # Поручения (модуль assignment)
    # ------------------------------------------------------------------
    def _incoming_with_task(self):
        inc = _u(self.env["corr.incoming"]).with_user(self.secretary).create({
            "subject": "Вх", "summary": "Суть письма", "doc_arrival_date": fields.Date.today(),
        })
        line = _u(self.env["assignment.line"]).with_user(self.secretary).create({
            "incoming_id": inc.id, "user_id": self.executor.id,
            "resolution_id": self.env.ref("assignment.resolution_01").id,
            "deadline": fields.Date.today() + timedelta(days=400),
        })
        return inc, line

    def test_30_assignment_from_incoming(self):
        """Поручение по письму: срок не позже предела письма, задача
        исполнителю на письме, поручение видно по «Источнику»."""
        inc, line = self._incoming_with_task()
        self.assertEqual(line.document_model, "corr.incoming")
        self.assertEqual(line.deadline, inc.deadline_limit_date, "срок сокращён до предела письма")
        self.assertEqual(inc.assignment_count, 1)
        self.assertEqual(inc.assignment_open_count, 1)
        self.assertEqual(line.document_summary, "Суть письма")
        # исполнитель видит поручение и письмо
        _u(inc).with_user(self.executor).check_access("read")
        self.assertTrue(_u(self.env["assignment.line"]).with_user(self.executor).search(
            [("id", "=", line.id)]))
        # директор правит любое поручение по входящим
        _u(line).with_user(self.director).write({"task": "Уточнение"})

    def test_31_rework_wizard(self):
        """Доработка задач — общий мастер «Поручить» с причиной."""
        inc, line = self._incoming_with_task()
        inc.sudo().state = "done"
        action = _u(inc).with_user(self.secretary).action_open_rework_wizard()
        self.assertEqual(action["res_model"], "assignment.wizard")
        self.assertEqual(action["views"][0][0],
                         self.env.ref("correspondence.view_correspondence_rework_wizard_form").id)
        Wizard = _u(self.env["assignment.wizard"]).with_user(self.secretary).with_context(action["context"])
        wizard = Wizard.create({
            "line_ids": [Command.create({
                "user_id": self.executor.id,
                "resolution_id": self.env.ref("assignment.resolution_02").id,
                "deadline": fields.Date.today(),
            })],
        })
        self.assertTrue(wizard.note_required)
        with self.assertRaises(UserError):
            wizard.action_confirm()
        wizard.note = "Ответ неполный"
        wizard.action_confirm()
        self.assertEqual(inc.state, "rework")
        self.assertEqual(len(inc.assignment_line_ids), 2)
        # исполнитель не может отправлять на доработку
        with self.assertRaises(AccessError):
            _u(inc).with_user(self.executor).action_open_rework_wizard()
