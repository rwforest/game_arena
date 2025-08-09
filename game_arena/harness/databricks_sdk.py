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

"""Model generation implementations using Databricks SDK."""

from typing import Any, Mapping, Sequence

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.serving import ChatMessage

from game_arena.harness import model_generation
from game_arena.harness import tournament_util


class DatabricksModel(model_generation.Model):
  """Wrapper for Databricks model access."""

  def __init__(
      self,
      model_name: str,
      *,
      model_options: Mapping[str, Any] | None = None,
      api_options: Mapping[str, Any] | None = None,
  ):
    super().__init__(
        model_name, model_options=model_options, api_options=api_options
    )
    self._client = WorkspaceClient()

  def _generate(
      self,
      messages: Sequence[ChatMessage],
  ) -> tournament_util.GenerateReturn:
    if self._model_options is None:
      self._model_options = {}

    config = {
        "temperature": self._model_options.get("temperature", 1.0),
        "max_tokens": self._model_options.get("max_output_tokens", 2048),
    }

    response = self._client.chat.completions.create(
        model=self._model_name,
        messages=messages,
        **config,
    )

    main_response = ""
    if response.choices:
      main_response = response.choices[0].message.content

    generation_tokens = None
    prompt_tokens = None
    if response.usage:
      generation_tokens = response.usage.completion_tokens
      prompt_tokens = response.usage.prompt_tokens

    request_for_logging = {
        "model": self._model_name,
        "messages": [msg.as_dict() for msg in messages],
        "config": config,
    }

    return tournament_util.GenerateReturn(
        main_response=main_response,
        main_response_and_thoughts=main_response,
        request_for_logging=request_for_logging,
        response_for_logging=response.as_dict(),
        generation_tokens=generation_tokens,
        prompt_tokens=prompt_tokens,
    )

  def generate_with_text_input(
      self, model_input: tournament_util.ModelTextInput
  ) -> tournament_util.GenerateReturn:
    messages = [
        ChatMessage(role="user", content=model_input.prompt_text)
    ]
    if model_input.system_instruction:
        messages.insert(0, ChatMessage(role="system", content=model_input.system_instruction))
    return self._generate(messages)
