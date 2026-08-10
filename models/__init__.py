# Сначала mixin (должен быть загружен до моделей, которые его наследуют)
from . import corr_incoming_approve_process_mixin
from . import corr_outgoing_approve_process_mixin

# Затем основные модели
from . import correspondence_incoming
from . import correspondence_outgoing
from . import correspondence_assignment_line
from . import correspondence_resolution_option
from . import correspondence_shipment_method
from . import correspondence_outgoing_type_esp_signer
from . import correspondence_correspondent_line
