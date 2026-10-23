{
    'name': 'Correspondence',
    'summary': 'Register and track correspondence',
    'version': '19.0.1.1.0',
    'author': 'Caspiy Neft, Maksat',
    'category': 'Productivity',
    'license': 'LGPL-3',
    'depends': [
        'base',
        'mail',
        'hr',
        'appstream_approval',
        'docx_report_pro',  # render_docx: печатная форма письма на подпись
        'contacts',
        'delegation',  # для работы делегирования
        'portal',  # для портального подписания
        'website',  # для портальных шаблонов
        'hr_employee_extended',  # для получения должности сотрудника
        'assignment',  # поручения по входящим: модель, резолюции, мастер «Поручить»
    ],
    # python-docx: подстановка значений в загруженные письма (docx_template.py)
    'external_dependencies': {'python': ['python-docx']},
    # «Предпросмотр»: PDF в окне тем же просмотрщиком, что окно подписи
    'assets': {
        'web.assets_backend': [
            'correspondence/static/src/js/corr_preview_action.js',
            'correspondence/static/src/xml/corr_preview_action.xml',
        ],
    },
    'application': True,
    'installable': True,
    # Начальные настройки ЭЦП этапов: на чистой установке миграции не
    # выполняются, см. hooks.py
    'post_init_hook': 'post_init_hook',
    'data': [
        # Security (порядок важен)
        'security/security.xml',
        'security/ir.model.access.csv',
        'security/record_rules.xml',

        # Data
        'data/ir_sequence.xml',
        'data/outgoing_type.xml',
        'data/shipment_method.xml',
        'data/approval_correspondence_incoming.xml',
        'data/approval_correspondence_outgoing.xml',
        'data/state_mixin_mail_template.xml',

        # Views
        'views/correspondence_incoming_views.xml',
        'views/correspondence_outgoing_views.xml',
        'views/correspondence_assignment_line_views.xml',
        'views/correspondence_shipment_method_views.xml',
        'views/correspondence_outgoing_type_views.xml',
        'views/correspondence_correspondent_line_views.xml',
        'views/correspondence_menu.xml',
        
        # Portal
        'views/portal_correspondence.xml',

        # Reports
        'reports/correspondence_reports.xml',
    ],
}
