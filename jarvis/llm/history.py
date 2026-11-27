"""Provider-neutral conversation history management."""

import json
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field


@dataclass
class NeutralMessage:
    """Standard message representation across providers."""
    role: str # "system", "user", "assistant", "tool"
    content: Optional[str] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None
    tool_call_id: Optional[str] = None
    name: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = {"role": self.role}
        if self.content is not None:
            d["content"] = self.content
        if self.tool_calls:
            d["tool_calls"] = self.tool_calls
        if self.tool_call_id:
            d["tool_call_id"] = self.tool_call_id
        if self.name:
            d["name"] = self.name
        return d


class NeutralHistoryManager:
    """Manages multi-turn conversation history in a format neutral to all LLMs."""

    def __init__(self, system_instruction: str, max_messages: int = 40):
        self.system_instruction = system_instruction
        self.max_messages = max_messages
        self.messages: List[NeutralMessage] = []
        self.reset()

    def reset(self) -> None:
        """Clears conversation and re-inserts system prompt."""
        self.messages = [
            NeutralMessage(role="system", content=self.system_instruction)
        ]

    def add_user_message(self, content: str) -> None:
        """Appends a user message."""
        self.messages.append(NeutralMessage(role="user", content=content))
        self._trim_if_needed()

    def add_assistant_message(
        self,
        content: Optional[str] = None,
        tool_calls: Optional[List[Dict[str, Any]]] = None
    ) -> None:
        """Appends an assistant message with optional tool calls."""
        self.messages.append(
            NeutralMessage(role="assistant", content=content, tool_calls=tool_calls)
        )
        self._trim_if_needed()

    def add_tool_response(self, tool_call_id: str, function_name: str, result: str) -> None:
        """Appends a tool execution response."""
        self.messages.append(
            NeutralMessage(
                role="tool",
                tool_call_id=tool_call_id,
                name=function_name,
                content=str(result)
            )
        )
        self._trim_if_needed()

    def _trim_if_needed(self) -> None:
        """Prunes oldest messages if history exceeds max_messages, preserving system instruction and turn integrity."""
        if len(self.messages) > self.max_messages:
            system_msg = self.messages[0]
            start_idx = len(self.messages) - (self.max_messages - 1)
            # Find the nearest subsequent 'user' message to avoid severing assistant/tool pairs
            while start_idx < len(self.messages) and self.messages[start_idx].role != "user":
                start_idx += 1
            if start_idx >= len(self.messages):
                # Fallback to keeping last 2 turns if user message search ran to end
                start_idx = max(1, len(self.messages) - 4)
            self.messages = [system_msg] + self.messages[start_idx:]

    def to_openai_format(self) -> List[Dict[str, Any]]:
        """Converts neutral history to standard OpenAI/Groq/Ollama messages list."""
        formatted = []
        for msg in self.messages:
            entry: Dict[str, Any] = {"role": msg.role}
            if msg.content is not None:
                entry["content"] = msg.content
            else:
                entry["content"] = ""

            if msg.role == "assistant" and msg.tool_calls:
                formatted_calls = []
                for tc in msg.tool_calls:
                    args_str = json.dumps(tc["arguments"]) if isinstance(tc["arguments"], dict) else str(tc["arguments"])
                    formatted_calls.append({
                        "id": tc["id"],
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": args_str,
                        }
                    })
                entry["tool_calls"] = formatted_calls
                if not entry.get("content"):
                    entry["content"] = None

            elif msg.role == "tool":
                entry["tool_call_id"] = msg.tool_call_id or "call_unknown"
                entry["name"] = msg.name or "tool"

            formatted.append(entry)
        return formatted
