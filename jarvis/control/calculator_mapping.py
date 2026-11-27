"""Windows Calculator element name mapping and display reader."""

import re
from typing import Optional, Dict, Tuple, List


CALCULATOR_BUTTON_MAP: Dict[str, Dict[str, str]] = {
    # Digits
    "0": {"name": "Zero", "automation_id": "num0Button"},
    "zero": {"name": "Zero", "automation_id": "num0Button"},
    "1": {"name": "One", "automation_id": "num1Button"},
    "one": {"name": "One", "automation_id": "num1Button"},
    "2": {"name": "Two", "automation_id": "num2Button"},
    "two": {"name": "Two", "automation_id": "num2Button"},
    "3": {"name": "Three", "automation_id": "num3Button"},
    "three": {"name": "Three", "automation_id": "num3Button"},
    "4": {"name": "Four", "automation_id": "num4Button"},
    "four": {"name": "Four", "automation_id": "num4Button"},
    "5": {"name": "Five", "automation_id": "num5Button"},
    "five": {"name": "Five", "automation_id": "num5Button"},
    "6": {"name": "Six", "automation_id": "num6Button"},
    "six": {"name": "Six", "automation_id": "num6Button"},
    "7": {"name": "Seven", "automation_id": "num7Button"},
    "seven": {"name": "Seven", "automation_id": "num7Button"},
    "8": {"name": "Eight", "automation_id": "num8Button"},
    "eight": {"name": "Eight", "automation_id": "num8Button"},
    "9": {"name": "Nine", "automation_id": "num9Button"},
    "nine": {"name": "Nine", "automation_id": "num9Button"},

    # Operators
    "+": {"name": "Plus", "automation_id": "plusButton"},
    "plus": {"name": "Plus", "automation_id": "plusButton"},
    "add": {"name": "Plus", "automation_id": "plusButton"},
    "-": {"name": "Minus", "automation_id": "minusButton"},
    "minus": {"name": "Minus", "automation_id": "minusButton"},
    "subtract": {"name": "Minus", "automation_id": "minusButton"},
    "*": {"name": "Multiply", "automation_id": "multiplyButton"},
    "x": {"name": "Multiply", "automation_id": "multiplyButton"},
    "times": {"name": "Multiply", "automation_id": "multiplyButton"},
    "multiply": {"name": "Multiply", "automation_id": "multiplyButton"},
    "multiplied by": {"name": "Multiply", "automation_id": "multiplyButton"},
    "/": {"name": "Divide", "automation_id": "divideButton"},
    "divide": {"name": "Divide", "automation_id": "divideButton"},
    "divided by": {"name": "Divide", "automation_id": "divideButton"},
    "=": {"name": "Equals", "automation_id": "equalButton"},
    "equals": {"name": "Equals", "automation_id": "equalButton"},
    "equal": {"name": "Equals", "automation_id": "equalButton"},

    # Other common controls
    ".": {"name": "Decimal separator", "automation_id": "decimalSeparatorButton"},
    "point": {"name": "Decimal separator", "automation_id": "decimalSeparatorButton"},
    "dot": {"name": "Decimal separator", "automation_id": "decimalSeparatorButton"},
    "c": {"name": "Clear", "automation_id": "clearButton"},
    "clear": {"name": "Clear", "automation_id": "clearButton"},
    "ce": {"name": "Clear entry", "automation_id": "clearEntryButton"},
    "backspace": {"name": "Backspace", "automation_id": "backSpaceButton"},
}


def get_calculator_button(key_or_name: str) -> Optional[Dict[str, str]]:
    """
    Returns mapped Calculator name and automation_id for a given digit, symbol, or word.
    Example: '5' -> {'name': 'Five', 'automation_id': 'num5Button'}
    """
    cleaned = key_or_name.strip().lower()
    return CALCULATOR_BUTTON_MAP.get(cleaned)


def match_calculator_control(target: str, control_info: dict) -> bool:
    """
    Determines if a UIA control matches a Calculator action target.
    Checks:
    1. Direct automation_id match (case-insensitive)
    2. Direct name match (case-insensitive)
    3. Mapped name/automation_id match
    """
    target_clean = target.strip().lower()
    ctrl_name = str(control_info.get("name", "")).strip().lower()
    ctrl_auto_id = str(control_info.get("automation_id", "")).strip().lower()

    if target_clean in (ctrl_name, ctrl_auto_id):
        return True

    mapping = get_calculator_button(target_clean)
    if mapping:
        map_name = mapping["name"].lower()
        map_auto_id = mapping["automation_id"].lower()
        if ctrl_name == map_name or ctrl_auto_id == map_auto_id:
            return True

    return False


def extract_calculator_display(raw_text: str) -> str:
    """
    Parses Windows Calculator display text.
    Examples:
    'Display is 8' -> '8'
    'Display is  8 ' -> '8'
    '8' -> '8'
    'CalculatorResults: Display is 1,234' -> '1,234'
    """
    if not raw_text:
        return ""
    text = raw_text.strip()
    match = re.search(r"Display is\s+([-\d.,]+)", text, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    match_num = re.search(r"^([-\d.,]+)$", text)
    if match_num:
        return match_num.group(1).strip()
    return text
