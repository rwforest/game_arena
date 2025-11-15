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

import os
from typing import Any, Mapping, Sequence
import requests

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
    self._host = os.environ.get("DATABRICKS_HOST")
    self._token = os.environ.get("DATABRICKS_TOKEN")

    if not self._host or not self._token:
      raise ValueError("DATABRICKS_HOST and DATABRICKS_TOKEN must be set in environment")

  def _generate(
      self,
      messages: list[dict[str, str]],
  ) -> tournament_util.GenerateReturn:
    if self._model_options is None:
      self._model_options = {}

    # Build the request payload
    payload = {
        "messages": messages,
        "temperature": self._model_options.get("temperature", 1.0),
        "max_tokens": self._model_options.get("max_output_tokens", 2048),
    }

    # Make HTTP request to Databricks serving endpoint
    url = f"{self._host}/serving-endpoints/{self._model_name}/invocations"
    headers = {
        "Authorization": f"Bearer {self._token}",
        "Content-Type": "application/json",
    }

    response = requests.post(url, json=payload, headers=headers)
    response.raise_for_status()

    response_json = response.json()

    main_response = ""
    generation_tokens = None
    prompt_tokens = None

    # Parse OpenAI-compatible response format
    if 'choices' in response_json and len(response_json['choices']) > 0:
      content = response_json['choices'][0]['message']['content']

      # Handle structured content (list of content blocks)
      if isinstance(content, list):
        # Extract text from content blocks
        text_parts = []
        for block in content:
          if isinstance(block, dict):
            if block.get('type') == 'text':
              text_parts.append(block.get('text', ''))
            elif block.get('type') == 'reasoning':
              # Extract summary text from reasoning blocks
              summary = block.get('summary', [])
              for summary_item in summary:
                if isinstance(summary_item, dict) and summary_item.get('type') == 'summary_text':
                  text_parts.append(summary_item.get('text', ''))
        main_response = '\n'.join(text_parts)
      else:
        # Plain string content
        main_response = content

      if 'usage' in response_json:
        generation_tokens = response_json['usage'].get('completion_tokens')
        prompt_tokens = response_json['usage'].get('prompt_tokens')

    request_for_logging = {
        "model": self._model_name,
        "messages": messages,
        "temperature": payload["temperature"],
        "max_tokens": payload["max_tokens"],
    }

    return tournament_util.GenerateReturn(
        main_response=main_response,
        main_response_and_thoughts=main_response,
        request_for_logging=request_for_logging,
        response_for_logging=response_json,
        generation_tokens=generation_tokens,
        prompt_tokens=prompt_tokens,
    )

  def generate_with_text_input(
      self, model_input: tournament_util.ModelTextInput
  ) -> tournament_util.GenerateReturn:
    messages = [
        {"role": "user", "content": model_input.prompt_text}
    ]
    if model_input.system_instruction:
        messages.insert(0, {"role": "system", "content": model_input.system_instruction})
    return self._generate(messages)
