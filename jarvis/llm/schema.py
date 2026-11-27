"""Automatic JSON Schema and OpenAI function tool generator from Python functions."""

import inspect
import re
from typing import Any, Callable, Dict, List, Optional, Tuple, get_type_hints


def python_type_to_json_schema(py_type: Any) -> str:
    """Maps Python types to JSON Schema primitive types."""
    if py_type in (int,):
        return "integer"
    elif py_type in (float,):
        return "number"
    elif py_type in (bool,):
        return "boolean"
    elif py_type in (list, List):
        return "array"
    elif py_type in (dict, Dict):
        return "object"
    return "string"


def parse_docstring_descriptions(docstring: Optional[str]) -> Tuple[str, Dict[str, str]]:
    """
    Parses function docstrings to extract top-level description and argument descriptions.
    Supports Google-style and Sphinx-style docstrings.
    """
    if not docstring:
        return "No description provided.", {}

    lines = docstring.strip().splitlines()
    main_desc_lines = []
    param_descs = {}

    in_args_section = False
    current_param = None
    current_param_lines = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        if re.match(r"^(Args|Arguments|Parameters):", stripped, re.IGNORECASE):
            in_args_section = True
            continue

        if re.match(r"^(Returns|Raises|Example|Examples):", stripped, re.IGNORECASE):
            in_args_section = False
            if current_param and current_param_lines:
                param_descs[current_param] = " ".join(current_param_lines).strip()
            current_param = None
            break

        if in_args_section:
            # Check for param: description pattern
            param_match = re.match(r"^([a-zA-Z0-9_]+)\s*(?:\([^)]+\))?\s*:\s*(.*)$", stripped)
            if param_match:
                if current_param and current_param_lines:
                    param_descs[current_param] = " ".join(current_param_lines).strip()
                current_param = param_match.group(1)
                current_param_lines = [param_match.group(2)]
            elif current_param:
                current_param_lines.append(stripped)
        else:
            main_desc_lines.append(stripped)

    if current_param and current_param_lines:
        param_descs[current_param] = " ".join(current_param_lines).strip()

    main_desc = " ".join(main_desc_lines).strip() if main_desc_lines else "No description provided."
    return main_desc, param_descs


def function_to_tool_schema(func: Callable) -> Dict[str, Any]:
    """
    Automatically converts a Python callable into an OpenAI-compatible function tool specification.
    """
    sig = inspect.signature(func)
    hints = get_type_hints(func) if hasattr(func, "__annotations__") else {}
    main_desc, param_descs = parse_docstring_descriptions(func.__doc__)

    properties: Dict[str, Any] = {}
    required: List[str] = []

    for name, param in sig.parameters.items():
        if name in ("self", "cls"):
            continue

        py_type = hints.get(name, str)
        json_type = python_type_to_json_schema(py_type)

        param_info: Dict[str, Any] = {"type": json_type}
        if name in param_descs:
            param_info["description"] = param_descs[name]

        properties[name] = param_info

        # If parameter has no default value, it is required
        if param.default == inspect.Parameter.empty:
            required.append(name)

    return {
        "type": "function",
        "function": {
            "name": func.__name__,
            "description": main_desc,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


def generate_tools_schema(tools: List[Callable]) -> List[Dict[str, Any]]:
    """Converts a list of Python functions into tool schemas."""
    return [function_to_tool_schema(t) for t in tools]
