import json
import re
from typing import Any


INVOICE_FIELD_KEYS = [
    "invoice_number",
    "invoice_date",
    "due_date",
    "total",
    "tax",
    "vendor_name",
    "customer_name",
    "currency",
]

PATTERNS = {
    "invoice_number": r"(?:invoice\s*(?:no\.?|number|#))\s*[:=]?\s*([A-Za-z0-9\-/]+)",
    "invoice_date": r"(?:invoice\s*date|date)\s*[:=]?\s*([0-9]{1,2}[/-][0-9]{1,2}[/-][0-9]{2,4})",
    "due_date": r"(?:due\s*date|due)\s*[:=]?\s*([0-9]{1,2}[/-][0-9]{1,2}[/-][0-9]{2,4})",
    "total": r"(?:grand\s*total|total)\s*[:=]?\s*[$EURINR€₹]?\s*([0-9,]+\.?[0-9]{0,2})",
    "tax": r"(?:tax|vat)\s*[:=]?\s*[$EURINR€₹]?\s*([0-9,]+\.?[0-9]{0,2})",
}


def _search_value(pattern: str, text: str) -> str | None:
    match = re.search(pattern, text, flags=re.IGNORECASE)
    if not match:
        return None
    return match.group(1).strip()


def _search_named_party(text: str, label_pattern: str) -> str | None:
    match = re.search(label_pattern, text, flags=re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return None


def _search_vendor_name(text: str) -> str | None:
    vendor = _search_named_party(text, r"(?:vendor|from|seller)\s*[:=]\s*(.+)")
    if vendor:
        return vendor

    for line in text.splitlines():
        cleaned = line.strip()
        if not cleaned:
            continue
        lowered = cleaned.lower()
        if "invoice" in lowered or "bill to" in lowered or "date" in lowered:
            continue
        if re.search(r"\d", cleaned):
            continue
        return cleaned
    return None


def _search_customer_name(text: str) -> str | None:
    return _search_named_party(text, r"(?:bill\s*to|customer|client)\s*[:=]\s*(.+)")


def _detect_currency(text: str) -> str | None:
    if "$" in text:
        return "USD"
    if "€" in text:
        return "EUR"
    if "₹" in text:
        return "INR"
    return None


def extract_invoice_fields_local(text: str) -> dict[str, Any]:
    fields: dict[str, Any] = {
        key: _search_value(pattern, text) for key, pattern in PATTERNS.items()
    }
    fields["vendor_name"] = _search_vendor_name(text)
    fields["customer_name"] = _search_customer_name(text)
    fields["currency"] = _detect_currency(text)
    fields["fallback_reason"] = "local_regex_fallback"
    return fields


def parse_model_json_or_fallback(
    content: str,
    ocr_text: str,
    fallback_reason: str,
    fallback_detail: str | None = None,
) -> dict[str, Any]:
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        parsed = None

    if parsed is None:
        start = content.find("{")
        end = content.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                parsed = json.loads(content[start : end + 1])
            except json.JSONDecodeError:
                parsed = None

    if isinstance(parsed, dict):
        normalized = {key: parsed.get(key) for key in INVOICE_FIELD_KEYS}
        if "fallback_reason" in parsed:
            normalized["fallback_reason"] = parsed["fallback_reason"]
        if "fallback_detail" in parsed:
            normalized["fallback_detail"] = parsed["fallback_detail"]
        return normalized

    fields = extract_invoice_fields_local(ocr_text)
    fields["fallback_reason"] = fallback_reason
    if fallback_detail:
        fields["fallback_detail"] = fallback_detail
    return fields
