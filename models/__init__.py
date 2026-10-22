# Сначала mixin (должен быть загружен до моделей, которые его наследуют)
from . import corr_incoming_approve_process_mixin
from . import corr_outgoing_approve_process_mixin
from . import portal_signing_mixin
from . import approval_activity_dedup_mixin
from . import docx_template

# Расширения моделей appstream_approval
from . import appstream_approval_workflow
from . import approval_agreement_line_delegation
from . import approval_esp

# Затем основные модели
from . import correspondence_incoming
from . import correspondence_outgoing
from . import assignment_line
from . import correspondence_shipment_method
from . import correspondence_outgoing_type_esp_signer
from . import correspondence_correspondent_line
