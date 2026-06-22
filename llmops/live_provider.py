import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from openai import OpenAI

from llmops.local_extraction import INVOICE_FIELD_KEYS
from llmops.schema import validate_invoice_fields


@dataclass(frozen=True)
class LiveExtractionResult:
    provider: str
    model: str
    fields: dict[str, Any]
    validation_status: str
    validation_errors: list[str]
    latency_ms: float
    fallback_reason: str | None = None
    raw_response: str | None = None


class OpenAICompatibleExtractor:
    def __init__(
        self,
        client: Any,
        model: str,
        prompt: str,
        schema_path: Path,
        provider: str = "openai",
    ) -> None:
        self.client = client
        self.model = model
        self.prompt = prompt
        self.schema_path = schema_path
        self.provider = provider

    @classmethod
    def from_credentials(
        cls,
        api_key: str,
        api_base: str,
        model: str,
        prompt: str,
        schema_path: Path,
    ) -> "OpenAICompatibleExtractor":
        http_client = httpx.Client(verify=False)
        client = OpenAI(api_key=api_key, base_url=api_base, http_client=http_client)
        return cls(client=client, model=model, prompt=prompt, schema_path=schema_path)

    @staticmethod
    def _empty_fields(fallback_reason: str, fallback_detail: str) -> dict[str, Any]:
        fields = {key: None for key in INVOICE_FIELD_KEYS}
        fields["fallback_reason"] = fallback_reason
        fields["fallback_detail"] = fallback_detail
        return fields

    @staticmethod
    def empty_failure_result(
        provider: str,
        model: str,
        fallback_reason: str,
        fallback_detail: str,
        validation_errors: list[str],
        latency_ms: float = 0.0,
        raw_response: str | None = None,
    ) -> LiveExtractionResult:
        return LiveExtractionResult(
            provider=provider,
            model=model,
            fields=OpenAICompatibleExtractor._empty_fields(fallback_reason, fallback_detail),
            validation_status="invalid",
            validation_errors=validation_errors,
            latency_ms=round(latency_ms, 2),
            fallback_reason=fallback_reason,
            raw_response=raw_response,
        )

    @staticmethod
    def _parse_json_object(content: str) -> dict[str, Any] | None:
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            parsed = None

        if isinstance(parsed, dict):
            return parsed

        start = content.find("{")
        end = content.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None

        try:
            parsed = json.loads(content[start : end + 1])
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None

    def extract(self, ocr_text: str) -> LiveExtractionResult:
        started_at = time.perf_counter()
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You are a precise invoice field extraction engine."},
                    {"role": "user", "content": f"{self.prompt}\n\nOCR_TEXT:\n{ocr_text}"},
                ],
                temperature=0,
                max_tokens=500,
            )
            content = (response.choices[0].message.content or "").strip()
        except Exception as exc:
            latency_ms = (time.perf_counter() - started_at) * 1000
            detail = str(exc)
            return self.empty_failure_result(
                provider=self.provider,
                model=self.model,
                fallback_reason="provider_error",
                fallback_detail=detail,
                validation_errors=[detail],
                latency_ms=latency_ms,
            )

        latency_ms = (time.perf_counter() - started_at) * 1000
        parsed = self._parse_json_object(content)
        if parsed is None:
            return self.empty_failure_result(
                provider=self.provider,
                model=self.model,
                fallback_reason="llm_parse_error",
                fallback_detail="LLM response was not valid JSON.",
                validation_errors=["LLM response was not valid JSON."],
                latency_ms=latency_ms,
                raw_response=content,
            )

        fields = {key: parsed.get(key) for key in INVOICE_FIELD_KEYS}
        validation_errors = validate_invoice_fields(fields, self.schema_path)
        validation_status = "valid" if not validation_errors else "invalid"
        fallback_reason = None
        if validation_errors:
            fallback_reason = "schema_validation_error"
            fields["fallback_reason"] = fallback_reason
            fields["fallback_detail"] = "; ".join(validation_errors)

        return LiveExtractionResult(
            provider=self.provider,
            model=self.model,
            fields=fields,
            validation_status=validation_status,
            validation_errors=validation_errors,
            latency_ms=round(latency_ms, 2),
            fallback_reason=fallback_reason,
            raw_response=content,
        )
