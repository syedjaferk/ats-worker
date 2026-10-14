"""Talk to the local model: Ollama's own API, or any OpenAI-compatible server (vLLM, llama.cpp, LM Studio).
Both are asked for structured output, so the answer is JSON that follows prompt.SCHEMA."""
import json
import re

import requests


class LLMError(Exception):
    pass


class LLM:
    def __init__(self, cfg):
        self.cfg = cfg
        self.http = requests.Session()
        if cfg.llm_api_key:
            self.http.headers["Authorization"] = f"Bearer {cfg.llm_api_key}"

    def health(self):
        """None if the model is ready, else a reason."""
        try:
            if self.cfg.llm_backend == "ollama":
                resp = self.http.get(f"{self.cfg.llm_url}/api/tags", timeout=10)
                names = {m["name"] for m in resp.json().get("models", [])}
                wanted = self.cfg.llm_model if ":" in self.cfg.llm_model else f"{self.cfg.llm_model}:latest"
                return None if wanted in names else f"model {self.cfg.llm_model} not pulled (ollama pull {self.cfg.llm_model})"
            resp = self.http.get(f"{self.cfg.llm_url}/models", timeout=10)
            return None if resp.ok else f"HTTP {resp.status_code}"
        except (requests.RequestException, ValueError) as e:
            return f"unreachable: {e}"

    def chat_json(self, messages, schema):
        try:
            if self.cfg.llm_backend == "ollama":
                resp = self.http.post(f"{self.cfg.llm_url}/api/chat", timeout=self.cfg.llm_timeout, json={
                    "model": self.cfg.llm_model, "messages": messages, "stream": False, "format": schema,
                    "options": {"temperature": 0, "num_ctx": self.cfg.llm_context},
                })
                resp.raise_for_status()
                content = resp.json()["message"]["content"]
            else:
                resp = self.http.post(f"{self.cfg.llm_url}/chat/completions", timeout=self.cfg.llm_timeout, json={
                    "model": self.cfg.llm_model, "messages": messages, "temperature": 0,
                    "response_format": {"type": "json_schema",
                                        "json_schema": {"name": "resume_review", "schema": schema, "strict": True}},
                })
                resp.raise_for_status()
                content = resp.json()["choices"][0]["message"]["content"]
        except (requests.RequestException, KeyError, IndexError, ValueError) as e:
            raise LLMError(f"model request failed: {e}") from e
        return parse_json(content)


def parse_json(content):
    # Reasoning models may still wrap the answer in <think>…</think> or a ```json fence.
    content = re.sub(r"(?s)<think>.*?</think>", "", content or "").strip()
    content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content)
    try:
        data = json.loads(content)
    except ValueError as e:
        raise LLMError(f"model did not return JSON: {content[:200]!r}") from e
    if not isinstance(data, dict):
        raise LLMError("model returned JSON that is not an object")
    return data
