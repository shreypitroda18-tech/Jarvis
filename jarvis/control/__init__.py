"""Computer and browser control module for JARVIS."""

from jarvis.control.safety import (
    ActionBudget,
    action_budget,
    is_dangerous_foreground,
    validate_browser_domain,
    is_password_element,
    sanitize_typed_text,
    validate_key_combo,
    classify_action_risk,
    validate_app_name,
    validate_typed_text,
)
from jarvis.control.killswitch import kill_switch
from jarvis.control.operator import operator, operate, ComputerOperator
from jarvis.control.uia_observer import UIAObserver, WindowObservation, ObservedElement
