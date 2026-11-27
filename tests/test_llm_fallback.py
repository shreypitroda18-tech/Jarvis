import pytest
from unittest.mock import MagicMock, patch
from openai import RateLimitError, APIConnectionError, InternalServerError

from jarvis.llm.base import BaseLLMProvider, ProviderResponse, ToolCall
from jarvis.llm.schema import function_to_tool_schema, generate_tools_schema
from jarvis.llm.history import NeutralHistoryManager
from jarvis.llm.fallback import FallbackManager


def sample_tool(location: str, temperature: float = 20.0) -> str:
    """Gets the current weather for a specific city.

    Args:
        location: City and state or country name.
        temperature: Optional baseline temperature.
    """
    return f"Weather in {location} is sunny."


def test_schema_generator():
    schema = function_to_tool_schema(sample_tool)
    assert schema["type"] == "function"
    fn = schema["function"]
    assert fn["name"] == "sample_tool"
    assert "Gets the current weather" in fn["description"]
    props = fn["parameters"]["properties"]
    assert "location" in props
    assert props["location"]["type"] == "string"
    assert "City and state" in props["location"]["description"]
    assert "temperature" in props
    assert props["temperature"]["type"] == "number"
    assert fn["parameters"]["required"] == ["location"]


def test_neutral_history_to_openai_format():
    hist = NeutralHistoryManager(system_instruction="You are a butler.")
    hist.add_user_message("Hello")
    hist.add_assistant_message(
        content=None,
        tool_calls=[{"id": "call_1", "name": "sample_tool", "arguments": {"location": "London"}}]
    )
    hist.add_tool_response(tool_call_id="call_1", function_name="sample_tool", result="Sunny in London.")
    hist.add_assistant_message("It is sunny in London, sir.")

    formatted = hist.to_openai_format()
    assert len(formatted) == 5
    assert formatted[0]["role"] == "system"
    assert formatted[1]["role"] == "user"
    assert formatted[2]["role"] == "assistant"
    assert len(formatted[2]["tool_calls"]) == 1
    assert formatted[3]["role"] == "tool"
    assert formatted[3]["tool_call_id"] == "call_1"
    assert formatted[4]["role"] == "assistant"
    assert formatted[4]["content"] == "It is sunny in London, sir."


class MockProvider(BaseLLMProvider):
    def __init__(self, name: str, model: str = "mock-model"):
        self._name = name
        self._model = model
        self.call_count = 0
        self.chat_side_effect = None

    @property
    def name(self) -> str:
        return self._name

    @property
    def model_name(self) -> str:
        return self._model

    def chat(self, messages, tools=None):
        self.call_count += 1
        if self.chat_side_effect:
            if callable(self.chat_side_effect):
                return self.chat_side_effect(messages, tools)
            raise self.chat_side_effect
        return ProviderResponse(content=f"Response from {self._name}")

    def list_models(self):
        return [self._model]

    def is_available(self):
        return True, "Ready"


def test_fallback_on_daily_quota_429(caplog):
    mgr = FallbackManager(system_instruction="Butler", tools=[sample_tool])

    mock_groq = MockProvider("groq")
    mock_ollama = MockProvider("ollama")

    # Groq fails with daily quota limit
    mock_groq.chat_side_effect = RateLimitError(
        message="Daily requests per day (RPD) limit exceeded",
        response=MagicMock(status_code=429),
        body={}
    )

    mgr.providers = [mock_groq, mock_ollama]

    with caplog.at_level("WARNING"):
        response = mgr.execute_turn("Good morning")

    assert response == "Response from ollama"
    assert mock_groq.call_count == 1
    assert mock_ollama.call_count == 1
    # Verify exact log output required by specification
    assert any("Groq quota hit, using Ollama" in rec.message for rec in caplog.records)


def test_cooldown_skips_blocked_provider():
    mgr = FallbackManager(system_instruction="Butler", tools=[sample_tool])

    mock_groq = MockProvider("groq")
    mock_ollama = MockProvider("ollama")
    mock_groq.chat_side_effect = RateLimitError(
        message="Daily quota exhausted",
        response=MagicMock(status_code=429),
        body={}
    )

    mgr.providers = [mock_groq, mock_ollama]

    # Turn 1 triggers fallback and sets cooldown
    mgr.execute_turn("First query")
    assert mock_groq.call_count == 1
    assert mock_ollama.call_count == 1

    # Turn 2: Groq must be skipped immediately due to active cooldown!
    mock_ollama.chat_side_effect = None
    res2 = mgr.execute_turn("Second query")
    assert res2 == "Response from ollama"
    # Groq should NOT have been called again
    assert mock_groq.call_count == 1
    assert mock_ollama.call_count == 2


def test_per_minute_rate_limit_retries_with_backoff(mocker):
    mocker.patch("time.sleep") # Don't actually sleep in tests
    mgr = FallbackManager(system_instruction="Butler", tools=[sample_tool])

    mock_groq = MockProvider("groq")
    attempts = 0

    def groq_effect(messages, tools):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RateLimitError(
                message="Rate limit 429 TPM exceeded, please wait 1 second",
                response=MagicMock(status_code=429),
                body={}
            )
        return ProviderResponse(content="Recovered after backoff")

    mock_groq.chat_side_effect = groq_effect
    mgr.providers = [mock_groq]

    response = mgr.execute_turn("Tell me a fact")
    assert response == "Recovered after backoff"
    assert attempts == 2


def test_fallback_on_network_and_5xx_error(caplog):
    mgr = FallbackManager(system_instruction="Butler", tools=[sample_tool])

    mock_groq = MockProvider("groq")
    mock_ollama = MockProvider("ollama")
    mock_groq.chat_side_effect = APIConnectionError(request=MagicMock())

    mgr.providers = [mock_groq, mock_ollama]

    with caplog.at_level("WARNING"):
        response = mgr.execute_turn("Hello")

    assert response == "Response from ollama"
    assert any("Groq connection error" in rec.message and "using Ollama" in rec.message for rec in caplog.records)


def test_tool_calling_loop_execution():
    tool_called_with = None

    def weather_test_tool(location: str) -> str:
        """Weather lookup tool."""
        nonlocal tool_called_with
        tool_called_with = location
        return f"Current temperature in {location} is 22C."

    mgr = FallbackManager(system_instruction="Butler", tools=[weather_test_tool])

    mock_groq = MockProvider("groq")
    turn = 0

    def chat_mock(messages, tools):
        nonlocal turn
        turn += 1
        if turn == 1:
            # Model asks to call weather_test_tool
            return ProviderResponse(
                content=None,
                tool_calls=[ToolCall(id="call_abc", name="weather_test_tool", arguments={"location": "Paris"})]
            )
        # Model returns final response after seeing tool output
        return ProviderResponse(content="The temperature in Paris is 22 degrees, sir.")

    mock_groq.chat_side_effect = chat_mock
    mgr.providers = [mock_groq]

    reply = mgr.execute_turn("What is the weather in Paris?")
    assert "22 degrees" in reply
    assert tool_called_with == "Paris"
    assert turn == 2


def test_all_providers_offline_graceful_message():
    mgr = FallbackManager(system_instruction="Butler", tools=[sample_tool])

    mock_groq = MockProvider("groq")
    mock_groq.chat_side_effect = APIConnectionError(request=MagicMock())

    mock_ollama = MockProvider("ollama")
    mock_ollama.chat_side_effect = APIConnectionError(request=MagicMock())

    mgr.providers = [mock_groq, mock_ollama]

    reply = mgr.execute_turn("Help")
    assert "all configured AI providers are currently unavailable" in reply
    assert "ollama serve" in reply


def test_console_output_silent_on_fallback_when_notify_false(capsys, monkeypatch):
    from jarvis.config import config, setup_logger
    monkeypatch.setattr(config.llm, "notify_on_fallback", False)
    setup_logger()

    mgr = FallbackManager(system_instruction="Butler", tools=[sample_tool])
    mock_groq = MockProvider("groq")
    mock_ollama = MockProvider("ollama")
    mock_groq.chat_side_effect = RateLimitError(
        message="Daily requests per day limit exceeded",
        response=MagicMock(status_code=429),
        body={}
    )
    mgr.providers = [mock_groq, mock_ollama]

    capsys.readouterr()  # Clear previous output
    reply = mgr.execute_turn("Good morning")
    captured = capsys.readouterr()

    assert reply == "Response from ollama"
    # Ensure nothing regarding provider switch is printed to stdout or stderr
    assert "Groq quota hit" not in captured.out
    assert "using Ollama" not in captured.out
    assert "Groq quota hit" not in captured.err
    assert "using Ollama" not in captured.err


def test_console_output_notified_when_notify_true(capsys, monkeypatch):
    from jarvis.config import config, setup_logger
    monkeypatch.setattr(config.llm, "notify_on_fallback", True)
    setup_logger()

    mgr = FallbackManager(system_instruction="Butler", tools=[sample_tool])
    mock_groq = MockProvider("groq")
    mock_ollama = MockProvider("ollama")
    mock_groq.chat_side_effect = RateLimitError(
        message="Daily requests per day limit exceeded",
        response=MagicMock(status_code=429),
        body={}
    )
    mgr.providers = [mock_groq, mock_ollama]

    capsys.readouterr()
    reply = mgr.execute_turn("Good morning")
    captured = capsys.readouterr()

    # When notify_on_fallback is True, console or reply contains the notification
    combined = captured.out + captured.err + reply
    assert "Groq quota hit, using Ollama" in combined or "[Using Ollama]" in reply


def test_unregistered_tool_fake_output_not_executed_and_no_raw_json():
    """
    Feeds fake model output naming an unregistered tool ('print_joke').
    Checks that nothing is executed and no raw JSON is shown to the user.
    """
    executed_tools = []

    def mock_registered_tool(location: str) -> str:
        executed_tools.append(location)
        return "Done"

    mgr = FallbackManager(system_instruction="Butler", tools=[mock_registered_tool])
    mock_ollama = MockProvider("ollama")

    fake_output = (
        '{"name": "print_joke", "parameters": {"joke": "Why don\'t scientists trust atoms? Because they make up everything!"}}'
    )
    mock_ollama.chat_side_effect = lambda msgs, tools: ProviderResponse(content=fake_output)
    mgr.providers = [mock_ollama]

    reply = mgr.execute_turn("Tell me a joke")

    # 1. Registered tools must NOT have been called
    assert len(executed_tools) == 0

    # 2. No raw JSON should be shown to the user
    assert '{"name":' not in reply
    assert '"print_joke"' not in reply
    assert "{" not in reply and "}" not in reply

    # 3. The extracted text (the joke) must be returned
    assert "Why don't scientists trust atoms? Because they make up everything!" in reply


def test_unregistered_native_tool_call_not_executed():
    """
    Feeds fake model output with an unregistered native ToolCall.
    Verifies it is blocked from execution and raw JSON is never shown.
    """
    executed_tools = []

    def mock_registered_tool(location: str) -> str:
        executed_tools.append(location)
        return "Done"

    mgr = FallbackManager(system_instruction="Butler", tools=[mock_registered_tool])
    mock_ollama = MockProvider("ollama")

    fake_tc = ToolCall(
        id="call_unregistered_123",
        name="delete_all_files",
        arguments={"path": "C:\\", "text": "I am not allowed to do that, sir."}
    )
    mock_ollama.chat_side_effect = lambda msgs, tools: ProviderResponse(
        content=None,
        tool_calls=[fake_tc]
    )
    mgr.providers = [mock_ollama]

    reply = mgr.execute_turn("Delete everything")

    # 1. Ensure no registered tools executed
    assert len(executed_tools) == 0

    # 2. Extracted text is returned cleanly without raw JSON
    assert "I am not allowed to do that, sir." in reply
    assert "{" not in reply and "delete_all_files" not in reply


def test_unregistered_tool_retry_when_no_text():
    """
    When an unregistered tool has no extractable text, FallbackManager retries once
    with a system note prompting conversational text.
    """
    mgr = FallbackManager(system_instruction="Butler", tools=[sample_tool])
    mock_ollama = MockProvider("ollama")
    turn = 0

    def chat_side_effect(msgs, tools):
        nonlocal turn
        turn += 1
        if turn == 1:
            # Emits empty unregistered tool call
            return ProviderResponse(content='{"name": "unknown_tool", "parameters": {}}')
        # Emits conversational response on retry
        return ProviderResponse(content="I apologize for the confusion, sir. How may I help?")

    mock_ollama.chat_side_effect = chat_side_effect
    mgr.providers = [mock_ollama]

    reply = mgr.execute_turn("Hello")
    assert reply == "I apologize for the confusion, sir. How may I help?"
    assert turn == 2


def test_pseudo_tool_for_registered_tool_is_executed_properly():
    """
    When a model emits JSON matching a registered tool, FallbackManager recognizes it,
    executes the tool safely, and proceeds with the turn.
    """
    tool_inputs = []

    def city_temperature(location: str) -> str:
        tool_inputs.append(location)
        return f"Temperature in {location} is 25C."

    mgr = FallbackManager(system_instruction="Butler", tools=[city_temperature])
    mock_ollama = MockProvider("ollama")
    turn = 0

    def chat_mock(msgs, tools):
        nonlocal turn
        turn += 1
        if turn == 1:
            # Emits pseudo-tool call in text
            return ProviderResponse(content='{"name": "city_temperature", "parameters": {"location": "Tokyo"}}')
        return ProviderResponse(content="It is 25 degrees in Tokyo, sir.")

    mock_ollama.chat_side_effect = chat_mock
    mgr.providers = [mock_ollama]

    reply = mgr.execute_turn("How hot is Tokyo?")
    assert tool_inputs == ["Tokyo"]
    assert reply == "It is 25 degrees in Tokyo, sir."


def test_unregistered_tool_in_markdown_fences_extracted_cleanly():
    """
    Ensures that when a local model returns markdown code block fences around an
    unregistered tool JSON, the content is parsed, the tool is NOT executed, and
    no raw JSON or fences are shown to the user.
    """
    executed = []

    def dummy_tool():
        executed.append(True)
        return "OK"

    mgr = FallbackManager(system_instruction="Butler", tools=[dummy_tool])
    mock_ollama = MockProvider("ollama")

    fake_fenced_output = """```json
{
  "name": "print_joke",
  "parameters": {
    "joke": "Parallel lines have so much in common. It's a shame they'll never meet."
  }
}
```"""
    mock_ollama.chat_side_effect = lambda msgs, tools: ProviderResponse(content=fake_fenced_output)
    mgr.providers = [mock_ollama]

    reply = mgr.execute_turn("Make me laugh")

    assert len(executed) == 0
    assert "```" not in reply
    assert "{" not in reply and "}" not in reply
    assert "print_joke" not in reply
    assert "Parallel lines have so much in common" in reply


