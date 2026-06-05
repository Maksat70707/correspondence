{
    'name': 'Correspondence',
    'summary': 'Register and track correspondence',
    'version': '16.0.0.6',
    'author': 'Caspiy Neft, Maksat',
    'category': 'Productivity',
    'depends': [
        'base',
        'mail',
        'hr',
        'mol',
        'report_xlsx',
        'appstream_approval',
        'contacts',
        'delegation',  # для работы делегирования
        'portal',  # для портального подписания
        'website',  # для портальных шаблонов
    ],
    'application': True,
    'installable': True,
    'data': [
        # Security (порядок важен)
        'security/security.xml',
        'security/ir.model.access.csv',
        'security/record_rules.xml',

        # Data
        'data/ir_sequence.xml',
        'data/resolution_options.xml',
        'data/outgoing_type.xml',
        'data/shipment_method.xml',
        'data/approval_correspondence_incoming.xml',
        'data/approval_correspondence_outgoing.xml',
        'data/state_mixin_mail_template.xml',
        'data/ir_cron_assignment_notification.xml',

        # Views
        'views/correspondence_incoming_views.xml',
        'views/correspondence_outgoing_views.xml',
        'views/correspondence_assignment_line_views.xml',
        'views/correspondence_rework_wizard_views.xml',
        'views/correspondence_resolution_option_views.xml',
        'views/correspondence_shipment_method_views.xml',
        'views/correspondence_outgoing_type_views.xml',
        'views/correspondence_correspondent_line_views.xml',
        'views/correspondence_menu.xml',
        
        # Portal
        'views/portal_correspondence.xml',

        # Reports
        'reports/correspondence_reports.xml',
    ]
}
