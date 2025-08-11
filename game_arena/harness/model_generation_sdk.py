# Copyright 2025 The game_arena Authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Model generation implementations using official SDKs."""
import base64
import dataclasses
import os  # added
from typing import Any, Mapping, Sequence
from absl import logging
import anthropic
from anthropic import _types as anthropic_internal_types
from anthropic import types as anthropic_types
from game_arena.harness import model_generation
from game_arena.harness import tournament_util
from google import genai as google_genai
from google.genai import types as google_genai_types
import openai
from openai import _types as openai_internal_types
from openai.types.chat import chat_completion as openai_chat_completion_types  
from openai.types.chat import chat_completion_chunk
# --- New: Databricks SDK WorkspaceClient helper and Serving model wrapper ---
try:
  # Import optional SDK. We keep imports optional to allow running outside Databricks.
  from databricks.sdk import WorkspaceClient  # type: ignore
  _HAS_DATABRICKS_SDK = True
except Exception:  # pragma: no cover - optional dependency
  WorkspaceClient = None  # type: ignore
  _HAS_DATABRICKS_SDK = False
_DATBR_WORKSPACE_URL_ENV_KEYS = ("DATABRICKS_WORKSPACE_URL", "DATABRICKS_HOST")
def _normalize_workspace_url(url: str | None) -> str | None:
  if not url:
    return None
  if not url.startswith("http://") and not url.startswith("https://"):
    url = "https://" + url
  return url.rstrip("/")
def _get_workspace_client(host: str | None = None, token: str | None = None):
  """Return a cached WorkspaceClient constructed from env or provided args.
  Uses the following env vars if args are not provided:
    - DATABRICKS_WORKSPACE_URL or DATABRICKS_HOST
    - DATABRICKS_TOKEN
  """
  if not _HAS_DATABRICKS_SDK:
    raise RuntimeError(
        "databricks-sdk is not installed. Please add `databricks-sdk` to requirements."
    )
  # Resolve host/token from args or environment
  env_host = next((os.getenv(k) for k in _DATBR_WORKSPACE_URL_ENV_KEYS if os.getenv(k)), None)
  host = _normalize_workspace_url(host or env_host)
  token = token or os.getenv("DATABRICKS_TOKEN")
  # Construct client with the best available information
  try:
    if host and token:
      return WorkspaceClient(host=host, token=token)  # type: ignore[arg-type]
    if host:
      return WorkspaceClient(host=host)  # type: ignore[arg-type]
    if token:
      return WorkspaceClient(token=token)  # type: ignore[arg-type]
    return WorkspaceClient()
  except Exception:
    # As a last resort, try default constructor
    return WorkspaceClient()
# --- Existing provider wrappers ---
_ANTHROPIC_MAX_TOKENS = {
    # Max tokens is a required parameter for the API. Therefore default to the
    # highest value for each model.
    # This table must be manually updated for every new model.
    # keep-sorted start
    "claude-1.0": 4096,
    "claude-1.3": 4096,
    "claude-2.0": 4096,
    "claude-2.1": 4096,
    "claude-3-5-haiku-20241022": 8192,
    "claude-3-5-sonnet-20240620": 8192,
    "claude-3-5-sonnet-20241022": 8192,
    "claude-3-5-sonnet-latest": 8192,
    # lower than 64k because streaming is required when max_tokens is greater
    # than 21,333.
    "claude-3-7-sonnet-20250219": 16384,
    "claude-3-haiku-20240307": 4096,
    "claude-3-opus-20240229": 4096,
    "claude-3-opus-latest": 4096,
    "claude-3-sonnet-20240229": 4096,
    "claude-instant-1.1": 4096,
    "claude-instant-1.2": 4096,
    "claude-opus-4-20250514": 16384,
    "claude-sonnet-4-20250514": 16384,
    # keep-sorted end
}
_ANTHROPIC_MAX_TOKENS_STREAMING = {
    # Some models allow larger values for max_tokens when streaming is enabled.
    # keep-sorted start
    "claude-3-7-sonnet-20250219": 64000,
    "claude-opus-4-20250514": 32000,
    "claude-sonnet-4-20250514": 64000,
    # keep-sorted end
}
_ANTHROPIC_MAX_TOKENS_NON_STREAMING_LIMIT = 21333
class AIStudioModel(model_generation.MultimodalModel):
  """Wrapper for AI Studio model access."""
  def __init__(
      self,
      model_name: str,
      *,
      model_options: Mapping[str, Any] | None = None,
      api_options: Mapping[str, Any] | None = None,
      api_key: str | None = None,
  ):
    super().__init__(
        model_name, model_options=model_options, api_options=api_options
    )
    # If API key is None, defaults to GOOGLE_API_KEY in environment.
    self._client = google_genai.Client(api_key=api_key)
  def _generate(
      self,
      contents: Sequence[str | google_genai_types.Part],
      system_instruction: str | None = None,
  ) -> tournament_util.GenerateReturn:
    if self._model_options is None:
      self._model_options = {}
    if self._api_options is None:
      self._api_options = {}
    # TODO(google-deepmind): Add error handling. generate_content raises
    # google.genai.errors.APIError.
    config = google_genai_types.GenerateContentConfig(
        http_options=google_genai_types.HttpOptions(
            timeout=self._api_options.get(
                "timeout", 20 * 60 * 1000  # Milliseconds.
            )
        ),
        system_instruction=system_instruction,
        temperature=self._model_options.get("temperature", None),
        top_p=self._model_options.get("top_p", None),
        top_k=self._model_options.get("top_k", None),
        max_output_tokens=self._model_options.get("max_output_tokens", None),
        thinking_config=google_genai_types.ThinkingConfig(
            include_thoughts=self._api_options.get("include_thoughts", None),
            thinking_budget=self._model_options.get("thinking_budget", None),
        ),
    )
    response = self._client.models.generate_content(
        model=self._model_name,
        contents=contents,
        config=config,
    )
    main_response = ""
    main_response_and_thoughts = ""
    for part in response.candidates[0].content.parts:
      if not part.text:
        continue
      if part.thought:
        main_response_and_thoughts += part.text
      else:
        main_response += part.text
    generation_tokens = None
    prompt_tokens = None
    reasoning_tokens = None
    if response.usage_metadata is not None:
      generation_tokens = response.usage_metadata.candidates_token_count
      prompt_tokens = response.usage_metadata.prompt_token_count
      reasoning_tokens = response.usage_metadata.thoughts_token_count
    request_for_logging = {
        "model": self._model_name,
        "contents": contents,
        "config": config.to_json_dict(),
    }
    return tournament_util.GenerateReturn(
        main_response=main_response,
        main_response_and_thoughts=main_response_and_thoughts,
        request_for_logging=request_for_logging,
        response_for_logging=response.to_json_dict(),
        generation_tokens=generation_tokens,
        prompt_tokens=prompt_tokens,
        reasoning_tokens=reasoning_tokens,
    )
  def generate_with_text_input(
      self, model_input: tournament_util.ModelTextInput
  ) -> tournament_util.GenerateReturn:
    contents = [model_input.prompt_text]
    return self._generate(contents, model_input.system_instruction)
  def generate_with_image_text_input(
      self, model_input: tournament_util.ModelImageTextInput
  ) -> tournament_util.GenerateReturn:
    contents = [
        model_input.prompt_text,
        google_genai_types.Part.from_bytes(
            data=model_input.prompt_image_bytes,
            mime_type=model_input.prompt_image_mime_type,
        ),
    ]
    return self._generate(contents, model_input.system_instruction)
@dataclasses.dataclass(frozen=True, kw_only=True)
class _OpenAIChatCompletionsStreamReturn:
  """The return value of _process_openai_chat_completions_stream."""
  main_response: str
  completion_tokens: int | None
  prompt_tokens: int | None
  reasoning_tokens: int | None
  response_chunks: Sequence[chat_completion_chunk.ChatCompletionChunk]
def _process_openai_chat_completions_stream(
    stream: openai.Stream[chat_completion_chunk.ChatCompletionChunk],
) -> _OpenAIChatCompletionsStreamReturn:
  """Processes an OpenAI Chat Completions stream."""
  main_response = ""
  completion_tokens = None
  prompt_tokens = None
  reasoning_tokens = None
  response_chunks = []
  try:
    for chunk in stream:
      response_chunks.append(chunk)
      if (
          chunk.choices
          and chunk.choices[0].delta
          and chunk.choices[0].delta.content
      ):
        main_response += chunk.choices[0].delta.content
      if chunk.usage:
        if chunk.usage.completion_tokens is not None:
          completion_tokens = (
              completion_tokens or 0
          ) + chunk.usage.completion_tokens
        if chunk.usage.prompt_tokens is not None:
          prompt_tokens = (prompt_tokens or 0) + chunk.usage.prompt_tokens
        if (
            chunk.usage.completion_tokens_details
            and chunk.usage.completion_tokens_details.reasoning_tokens
            is not None
        ):
          reasoning_tokens = (
              reasoning_tokens or 0
          ) + chunk.usage.completion_tokens_details.reasoning_tokens
  except openai.APIError as e:
    logging.exception("Error during OpenAI stream processing: %s", e)
    raise
  return _OpenAIChatCompletionsStreamReturn(
      main_response=main_response,
      completion_tokens=completion_tokens,
      prompt_tokens=prompt_tokens,
      reasoning_tokens=reasoning_tokens,
      response_chunks=response_chunks,
  )
class OpenAIChatCompletionsModel(model_generation.MultimodalModel):
  """Wrapper for OpenAI model access with Chat Completions API."""
  def __init__(
      self,
      model_name: str,
      *,
      model_options: Mapping[str, Any] | None = None,
      api_options: Mapping[str, Any] | None = None,
      api_key: str | None = None,
  ):
    super().__init__(
        model_name, model_options=model_options, api_options=api_options
    )
    # If API key is None, defaults to OPENAI_API_KEY in environment.
    base_url = None
    if api_options and "base_url" in api_options:
      base_url = api_options["base_url"]
    self._client = openai.OpenAI(api_key=api_key, base_url=base_url)
  # TODO(google-deepmind): Add error handling.
  def _generate(
      self,
      content: Sequence[Mapping[str, Any]],
      system_instruction: str | None = None,
  ) -> tournament_util.GenerateReturn:
    messages = []
    if system_instruction is not None:
      messages.append({"role": "developer", "content": system_instruction})
    messages.append({
        "role": "user",
        "content": content,
    })
    if self._model_options is None:
      self._model_options = {}
    if self._api_options is None:
      self._api_options = {}
    config = {
        "temperature": self._model_options.get(
            "temperature", openai_internal_types.NotGiven()
        ),
        "top_p": self._model_options.get(
            "top_p", openai_internal_types.NotGiven()
        ),
        "max_tokens": self._model_options.get(
            "max_output_tokens", openai_internal_types.NotGiven()
        ),
        "reasoning_effort": self._model_options.get(
            "reasoning_effort", openai_internal_types.NotGiven()
        ),
    }
    if self._api_options.get("stream", False):
      stream_options = {"include_usage": True}
      try:
        stream = self._client.chat.completions.create(
            model=self._model_name,
            messages=messages,
            timeout=self._api_options.get("timeout", 300),
            stream=True,
            stream_options=stream_options,
            **config,
        )
      except openai.NotFoundError as e:
        raise model_generation.DoNotRetryError(str(e)) from e
      except Exception as e:
        logging.exception("Error during OpenAI stream: %s", e)
        raise e
      processed_stream = _process_openai_chat_completions_stream(stream)
      request_for_logging = {
          "model": self._model_name,
          "messages": messages,
          "config": config,
          "stream_options": stream_options,
      }
      response_for_logging = {
          "response_chunks": [
              chunk.to_dict() for chunk in processed_stream.response_chunks
          ]
      }
      return tournament_util.GenerateReturn(
          main_response=processed_stream.main_response,
          main_response_and_thoughts="",
          request_for_logging=request_for_logging,
          response_for_logging=response_for_logging,
          generation_tokens=processed_stream.completion_tokens,
          prompt_tokens=processed_stream.prompt_tokens,
          reasoning_tokens=processed_stream.reasoning_tokens,
      )
    try:
      completion = self._client.chat.completions.create(
          model=self._model_name,
          messages=messages,
          timeout=self._api_options.get("timeout", 1200),
          **config,
      )
    except openai.NotFoundError as e:
      raise model_generation.DoNotRetryError(str(e)) from e
    if not isinstance(completion, openai_chat_completion_types.ChatCompletion):
      raise ValueError(
          f"Expected OpenAI Chat Completion type. Got {type(completion)}."
      )
    completion_tokens = None
    prompt_tokens = None
    reasoning_tokens = None
    if completion.usage is not None:
      completion_tokens = completion.usage.completion_tokens
      prompt_tokens = completion.usage.prompt_tokens
      if completion.usage.completion_tokens_details is not None:
        reasoning_tokens = (
            completion.usage.completion_tokens_details.reasoning_tokens
        )
    request_for_logging = {
        "model": self._model_name,
        "messages": messages,
        "config": config,
    }
    response_for_logging = completion.to_dict()
    content = completion.choices[0].message.content
    if content is None:
      logging.warning(
          "OpenAI Chat Completion return content is None. Returning empty"
          " string. Request: %s", request_for_logging
      )
      content = ""
    return tournament_util.GenerateReturn(
        main_response=content,
        main_response_and_thoughts="",
        request_for_logging=request_for_logging,
        response_for_logging=response_for_logging,
        generation_tokens=completion_tokens,
        prompt_tokens=prompt_tokens,
        reasoning_tokens=reasoning_tokens,
    )
  def generate_with_text_input(
      self, model_input: tournament_util.ModelTextInput
  ) -> tournament_util.GenerateReturn:
    content = [{"type": "text", "text": model_input.prompt_text}]
    return self._generate(content, model_input.system_instruction)
  def generate_with_image_text_input(
      self, model_input: tournament_util.ModelImageTextInput
  ) -> tournament_util.GenerateReturn:
    content = [{"type": "text", "text": model_input.prompt_text}]
    base64_image = base64.b64encode(model_input.prompt_image_bytes).decode(
        "utf-8"
    )
    content.append({
        "type": "image_url",
        "image_url": {
            "url": (
                f"data:{model_input.prompt_image_mime_type};base64,{base64_image}"
            )
        },
    })
    return self._generate(content, model_input.system_instruction)
@dataclasses.dataclass(frozen=True, kw_only=True)
class AnthropicReturn:
  main_response: str
  main_response_and_thoughts: str
  prompt_tokens: int
  generation_tokens: int
def _process_anthropic_stream(
    stream: anthropic.MessageStream,
) -> tuple[
    AnthropicReturn,
    list[
        anthropic_types.raw_message_delta_event.RawMessageDeltaEvent
        | anthropic_types.raw_content_block_delta_event.RawContentBlockDeltaEvent
        | anthropic_types.raw_message_start_event.RawMessageStartEvent
    ],
]:
  """Processes an Anthropic stream."""
  main_response = ""
  main_response_and_thoughts = ""
  prompt_tokens = 0
  generation_tokens = 0
  response_chunks = []
  for event in stream:
    response_chunks.append(event)
    if event.type == "content_block_delta":
      assert isinstance(
          event,
          anthropic_types.raw_content_block_delta_event.RawContentBlockDeltaEvent,
      )
      if event.delta.type == "thinking_delta":
        assert isinstance(
            event.delta, anthropic_types.thinking_delta.ThinkingDelta
        )
        main_response_and_thoughts += event.delta.thinking
      elif event.delta.type == "text_delta":
        assert isinstance(event.delta, anthropic_types.text_delta.TextDelta)
        main_response += event.delta.text
        main_response_and_thoughts += event.delta.text
    elif event.type == "message_delta":
      assert isinstance(
          event,
          anthropic_types.raw_message_delta_event.RawMessageDeltaEvent,
      )
      if event.usage.input_tokens is not None:
        prompt_tokens += event.usage.input_tokens
      generation_tokens += event.usage.output_tokens
    elif event.type == "message_start":
      assert isinstance(
          event,
          anthropic_types.raw_message_start_event.RawMessageStartEvent,
      )
      if event.message.usage.input_tokens is not None:
        prompt_tokens += event.message.usage.input_tokens
      generation_tokens += event.message.usage.output_tokens
  return (
      AnthropicReturn(
          main_response=main_response,
          main_response_and_thoughts=main_response_and_thoughts,
          prompt_tokens=prompt_tokens,
          generation_tokens=generation_tokens,
      ),
      response_chunks,
  )
def _process_anthropic_response(
    response: anthropic_types.Message,
) -> AnthropicReturn:
  """Processes a single Anthropic (synchronous) response."""
  main_response = ""
  main_response_and_thoughts = ""
  for block in response.content:
    if block.type == "text":
      assert isinstance(block, anthropic_types.TextBlock)
      main_response += block.text
      main_response_and_thoughts += block.text
    elif block.type == "thinking":
      assert isinstance(block, anthropic_types.ThinkingBlock)
      main_response_and_thoughts += block.thinking
  return AnthropicReturn(
      main_response=main_response,
      main_response_and_thoughts=main_response_and_thoughts,
      prompt_tokens=response.usage.input_tokens,
      generation_tokens=response.usage.output_tokens,
  )
class AnthropicModel(model_generation.Model):
  """Wrapper for Anthropic model access."""
  def __init__(
      self,
      model_name: str,
      *,
      model_options: Mapping[str, Any] | None = None,
      api_options: Mapping[str, Any] | None = None,
      api_key: str | None = None,
  ):
    super().__init__(
        model_name, model_options=model_options, api_options=api_options
    )
    # If API key is None, defaults to ANTHROPIC_API_KEY in environment.
    self._client = anthropic.Anthropic(api_key=api_key)
  def _generate(
      self,
      messages: Sequence[Mapping[str, Any]],
      system_instruction: str | None = None,
  ) -> tournament_util.GenerateReturn:
    if self._model_options is None:
      self._model_options = {}
    if self._api_options is None:
      self._api_options = {}
    if "max_tokens" in self._model_options:
      max_tokens = self._model_options["max_tokens"]
    else:
      if self._api_options.get("stream", False):
        max_tokens = _ANTHROPIC_MAX_TOKENS_STREAMING[self._model_name]
      else:
        max_tokens = _ANTHROPIC_MAX_TOKENS[self._model_name]
    # https://docs.anthropic.com/en/docs/build-with-claude/extended-thinking#performance-considerations
    if (
        max_tokens > _ANTHROPIC_MAX_TOKENS_NON_STREAMING_LIMIT
        and not self._api_options.get("stream", False)
    ):
      raise ValueError(
          "Streaming responses must be enabled for max_tokens > 21333. Max"
          f" tokens = {max_tokens}."
      )
    if "thinking" in self._model_options:
      maybe_thinking: anthropic.types.ThinkingConfigParam = self._model_options[
          "thinking"
      ]
      # https://docs.anthropic.com/en/docs/build-with-claude/extended-thinking#how-to-use-extended-thinking
      if (
          maybe_thinking["type"] == "enabled"
          and maybe_thinking["budget_tokens"] >= max_tokens
      ):
        raise ValueError(
            f"Thinking budget tokens = {maybe_thinking['budget_tokens']} must"
            f" be less than max tokens = {max_tokens}."
        )
    config = {
        "system": (
            system_instruction
            if system_instruction is not None
            else anthropic_internal_types.NotGiven()
        ),
        "max_tokens": max_tokens,
        "temperature": self._model_options.get(
            "temperature", anthropic_internal_types.NotGiven()
        ),
        "top_k": self._model_options.get(
            "top_k", anthropic_internal_types.NotGiven()
        ),
        "top_p": self._model_options.get(
            "top_p", anthropic_internal_types.NotGiven()
        ),
        "thinking": self._model_options.get(
            "thinking", anthropic_internal_types.NotGiven()
        ),
        "timeout": self._api_options.get("timeout", 1200),
    }
    if self._api_options.get("stream", False):
      with self._client.messages.stream(
          model=self._model_name,
          messages=messages,
          **config,
      ) as stream:
        anthropic_return, response_chunks = _process_anthropic_stream(stream)
      response_for_logging = {
          "response_chunks": [chunk.to_dict() for chunk in response_chunks]
      }
    else:
      response = self._client.messages.create(
          model=self._model_name,
          messages=messages,
          **config,
      )
      assert isinstance(response, anthropic_types.Message)
      anthropic_return = _process_anthropic_response(response)
      response_for_logging = response.to_dict()
    request_for_logging = {
        "model": self._model_name,
        "messages": messages,
        "config": config,
    }
    # Anthropic does not return the reasoning token count separately.
    return tournament_util.GenerateReturn(
        **dataclasses.asdict(anthropic_return),
        request_for_logging=request_for_logging,
        response_for_logging=response_for_logging,
    )
  def generate_with_text_input(
      self, model_input: tournament_util.ModelTextInput
  ) -> tournament_util.GenerateReturn:
    messages = [{"role": "user", "content": model_input.prompt_text}]
    return self._generate(messages, model_input.system_instruction)
  def generate_with_image_text_input(
      self, model_input: tournament_util.ModelImageTextInput
  ):
    base64_image = base64.b64encode(model_input.prompt_image_bytes).decode(
        "utf-8"
    )
    messages = [{
        "role": "user",
        "content": [
            {
                "type": "text",
                "text": model_input.prompt_text,
            },
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": model_input.prompt_image_mime_type,
                    "data": base64_image,
                },
            },
        ],
    }]
    return self._generate(messages, model_input.system_instruction)
# --- New: Direct Databricks Serving via SDK ---
class DatabricksServingModel(model_generation.MultimodalModel):
  """Wrapper that calls Databricks Serving Endpoints via WorkspaceClient.query.
  Notes:
    - model_name should be the serving endpoint name.
    - Uses an OpenAI-style chat payload by default: messages + parameters.
    - Requires databricks-sdk and env HOST/TOKEN or an already configured profile.
  """
  def __init__(
      self,
      model_name: str,
      *,
      model_options: Mapping[str, Any] | None = None,
      api_options: Mapping[str, Any] | None = None,
      workspace_host: str | None = None,
      workspace_token: str | None = None,
  ):
    super().__init__(model_name, model_options=model_options, api_options=api_options)
    # Lazily create a WorkspaceClient; raise a clear error if SDK missing.
    try:
      self._w = _get_workspace_client(workspace_host, workspace_token)
    except Exception as e:  # pragma: no cover - environment specific
      logging.exception("Failed to initialize Databricks WorkspaceClient: %s", e)
      raise
  def _build_messages(
      self,
      *,
      user_content: Sequence[Mapping[str, Any]] | str,
      system_instruction: str | None,
  ) -> list[Mapping[str, Any]]:
    messages: list[Mapping[str, Any]] = []
    if system_instruction:
      # Many serving endpoints support both "system" and "developer"; use system.
      messages.append({"role": "system", "content": system_instruction})
    if isinstance(user_content, str):
      messages.append({"role": "user", "content": user_content})
    else:
      messages.append({"role": "user", "content": user_content})
    return messages
  def _query_chat(self, messages: list[Mapping[str, Any]]) -> Mapping[str, Any]:
    # Parameters follow OpenAI-like fields where applicable
    mo = self._model_options or {}
    params: dict[str, Any] = {}
    # Normalize option names
    if "max_output_tokens" in mo and mo.get("max_output_tokens") is not None:
      params["max_tokens"] = mo.get("max_output_tokens")
    if mo.get("max_tokens") is not None:
      params["max_tokens"] = mo.get("max_tokens")
    if mo.get("temperature") is not None:
      params["temperature"] = mo.get("temperature")
    if mo.get("top_p") is not None:
      params["top_p"] = mo.get("top_p")
    if mo.get("top_k") is not None:
      params["top_k"] = mo.get("top_k")
    # Optional served model name for multi-model endpoints
    served_model_name = None
    if isinstance(self._api_options, dict):
      served_model_name = self._api_options.get("served_model_name")
    # Prepare payload for inputs-style call (works with dict messages and multimodal)
    payload: dict[str, Any] = {"messages": messages} | params
    # Try to convert to ChatMessage objects only if content is simple text
    use_object_messages = False
    message_objs = None
    try:
      from databricks.sdk.service.serving import ChatMessage, ChatMessageRole  # type: ignore
      tmp_objs = []
      for m in messages:
        if not isinstance(m, dict):
          use_object_messages = False
          tmp_objs = []
          break
        role = m.get("role")
        content = m.get("content")
        # Only support str content for object mode; lists (e.g., image_url) use inputs fallback
        if not isinstance(role, str) or not isinstance(content, str):
          use_object_messages = False
          tmp_objs = []
          break
        role_upper = role.upper()
        role_enum = getattr(ChatMessageRole, role_upper, None)
        if role_enum is None:
          # Map developer to system; default unknown to user
          if role_upper == "DEVELOPER":
            role_enum = ChatMessageRole.SYSTEM
          else:
            role_enum = ChatMessageRole.USER
        tmp_objs.append(ChatMessage(role=role_enum, content=content))
      if tmp_objs:
        use_object_messages = True
        message_objs = tmp_objs
    except Exception:
      use_object_messages = False
      message_objs = None
    # Call SDK
    try:
      if use_object_messages and message_objs is not None:
        kwargs: dict[str, Any] = {"name": self._model_name, "messages": message_objs}
        if served_model_name:
          kwargs["served_model_name"] = served_model_name
        kwargs.update(params)
        resp = self._w.serving_endpoints.query(**kwargs)
      else:
        # Use inputs payload to avoid ChatMessage requirement and support multimodal
        try:
          kwargs = {"name": self._model_name, "inputs": payload}
          if served_model_name:
            kwargs["served_model_name"] = served_model_name
          resp = self._w.serving_endpoints.query(**kwargs)
        except TypeError:
          # Positional fallback
          resp = self._w.serving_endpoints.query(self._model_name, payload)
    except Exception as e:  # pragma: no cover - network/SDK specific
      msg = str(e)
      if "NOT_FOUND" in msg or "Resource does not exist" in msg:
        raise model_generation.DoNotRetryError(msg) from e
      raise
    # Convert potential SDK objects to dict
    if hasattr(resp, "as_dict"):
      out = resp.as_dict()  # type: ignore[attr-defined]
    elif hasattr(resp, "to_dict"):
      out = resp.to_dict()  # type: ignore[attr-defined]
    elif isinstance(resp, dict):
      out = resp
    else:
      # Last resort
      out = {"response": str(resp)}
    return out
  def _parse_chat_like_response(self, out: Mapping[str, Any]) -> tuple[str, str, int | None, int | None, int | None]:
    # Extract both main text and combined (thoughts + text) when possible.
    def _extract_main_and_combined(content: Any) -> tuple[str, str]:
      main_parts: list[str] = []
      combined_parts: list[str] = []
      def rec(node: Any):
        if isinstance(node, str):
          main_parts.append(node)
          combined_parts.append(node)
          return
        if isinstance(node, list):
          for el in node:
            rec(el)
          return
        if isinstance(node, dict):
          t = node.get("type") if isinstance(node.get("type"), str) else None
          txt = node.get("text") if isinstance(node.get("text"), str) else None
          # Heuristic: blocks of type reasoning/thinking/thought are considered thoughts only
          if txt is not None:
            if t and t.lower() in ("reasoning", "thinking", "thought"):
              combined_parts.append(txt)
              # don't add to main_parts to keep main clean
            else:
              main_parts.append(txt)
              combined_parts.append(txt)
          else:
            # Recurse into common containers
            for k in ("content", "message", "parts", "data"):
              if k in node:
                rec(node[k])
          return
      rec(content)
      main = "".join(main_parts).strip()
      combined = "".join(combined_parts).strip()
      return main, combined
    def _extract_usage(container: Mapping[str, Any] | None) -> tuple[int | None, int | None, int | None]:
      if not isinstance(container, dict):
        return None, None, None
      ct = container.get("completion_tokens")
      pt = container.get("prompt_tokens")
      rt = None
      det = container.get("completion_tokens_details") if isinstance(container.get("completion_tokens_details"), dict) else None
      if isinstance(det, dict):
        rt = det.get("reasoning_tokens")
      return ct, pt, rt
    # Try OpenAI chat-completions-like shape
    try:
      choices = out.get("choices") or (out.get("output", {}).get("choices") if isinstance(out.get("output"), dict) else None)  # type: ignore[union-attr]
      if choices and isinstance(choices, (list, tuple)):
        first = choices[0] if isinstance(choices[0], dict) else None
        msg = first.get("message") if isinstance(first, dict) else None
        if isinstance(msg, dict) and "content" in msg:
          main, combined = _extract_main_and_combined(msg.get("content"))
          if main or combined:
            usage_container = out.get("usage") or (out.get("output", {}).get("usage") if isinstance(out.get("output"), dict) else None)  # type: ignore[union-attr]
            ct, pt, rt = _extract_usage(usage_container if isinstance(usage_container, dict) else None)
            if not combined:
              combined = main
            return main, combined, ct, pt, rt
    except Exception:
      pass
    # Databricks responses API shape: output.message.content (list of blocks)
    try:
      output_obj = out.get("output") if isinstance(out, dict) else None
      if isinstance(output_obj, dict):
        msg = output_obj.get("message")
        if isinstance(msg, dict):
          main, combined = _extract_main_and_combined(msg.get("content"))
          if main or combined:
            usage_container = out.get("usage") or output_obj.get("usage")
            ct, pt, rt = _extract_usage(usage_container if isinstance(usage_container, dict) else None)
            if not combined:
              combined = main
            return main, combined, ct, pt, rt
        # Some endpoints place text in output.text directly
        out_text = output_obj.get("text")
        if isinstance(out_text, str) and out_text:
          return out_text, out_text, None, None, None
    except Exception:
      pass
    # Generic MLflow/serving output shapes
    preds = out.get("predictions") if isinstance(out, dict) else None
    if isinstance(preds, list) and preds:
      cand = None
      first = preds[0]
      if isinstance(first, dict):
        cands = first.get("candidates")
        if isinstance(cands, list) and cands:
          cand = cands[0]
      if isinstance(cand, dict):
        text = cand.get("text") or cand.get("content") or cand.get("message")
        main, combined = _extract_main_and_combined(text)
        if main or combined:
          if not combined:
            combined = main
          return main, combined, None, None, None
    # Fallbacks: single-field
    for key in ("output_text", "text", "content", "result", "reply"):
      v = out.get(key) if isinstance(out, dict) else None
      if isinstance(v, str) and v:
        return v, v, None, None, None
      if isinstance(v, dict) or isinstance(v, list):
        main, combined = _extract_main_and_combined(v)
        if main or combined:
          if not combined:
            combined = main
          return main, combined, None, None, None
    # Last resort: stringify
    s = str(out)
    return s, s, None, None, None
  def generate_with_text_input(self, model_input: tournament_util.ModelTextInput) -> tournament_util.GenerateReturn:
    messages = self._build_messages(
        user_content=model_input.prompt_text,
        system_instruction=model_input.system_instruction,
    )
    out = self._query_chat(messages)
    main_text, combined_text, gen_toks, prompt_toks, reasoning_toks = self._parse_chat_like_response(out)
    request_for_logging = {
        "endpoint": self._model_name,
        "messages": messages,
        "model_options": self._model_options,
    }
    return tournament_util.GenerateReturn(
        main_response=main_text or "",
        main_response_and_thoughts=combined_text or main_text or "",
        request_for_logging=request_for_logging,
        response_for_logging=out,  # raw dict from SDK response
        generation_tokens=gen_toks,
        prompt_tokens=prompt_toks,
        reasoning_tokens=reasoning_toks,
    )
  def generate_with_image_text_input(self, model_input: tournament_util.ModelImageTextInput) -> tournament_util.GenerateReturn:
    base64_image = base64.b64encode(model_input.prompt_image_bytes).decode("utf-8")
    user_content = [
        {"type": "text", "text": model_input.prompt_text},
        {
            "type": "image_url",
            "image_url": {
                "url": f"data:{model_input.prompt_image_mime_type};base64,{base64_image}",
            },
        },
    ]
    messages = self._build_messages(
        user_content=user_content,
        system_instruction=model_input.system_instruction,
    )
    out = self._query_chat(messages)
    main_text, combined_text, gen_toks, prompt_toks, reasoning_toks = self._parse_chat_like_response(out)
    request_for_logging = {
        "endpoint": self._model_name,
        "messages": messages,
        "model_options": self._model_options,
    }
    return tournament_util.GenerateReturn(
        main_response=main_text or "",
        main_response_and_thoughts=combined_text or main_text or "",
        request_for_logging=request_for_logging,
        response_for_logging=out,
        generation_tokens=gen_toks,
        prompt_tokens=prompt_toks,
        reasoning_tokens=reasoning_toks,
    )
