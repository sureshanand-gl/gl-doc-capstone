import json
import re
from typing import Any


SCALAR_FIELD_KEYS = [
    "invoice_number",
    "invoice_date",
    "due_date",
    "po_number",
    "payment_terms",
    "vendor_name",
    "vendor_tax_id",
    "customer_name",
    "customer_tax_id",
    "subtotal",
    "tax",
    "total",
    "currency",
]
ORDER_ITEM_FIELD_KEYS = [
    "line_no",
    "description",
    "qty",
    "unit",
    "unit_price",
    "net_amount",
    "tax_rate",
    "gross_amount",
]
INVOICE_FIELD_KEYS = [*SCALAR_FIELD_KEYS, "order_items"]

PATTERNS = {
    "invoice_number": r"(?:invoice\s*(?:no\.?|number|#))\s*[:=]?\s*([A-Za-z0-9\-/]+)",
    "invoice_date": r"(?:invoice\s*date|date)\s*[:=]?\s*([0-9]{1,2}[/-][0-9]{1,2}[/-][0-9]{2,4})",
    "due_date": r"(?:due\s*date|due)\s*[:=]?\s*([0-9]{1,2}[/-][0-9]{1,2}[/-][0-9]{2,4})",
    "po_number": r"(?:po\s*(?:number|no\.?|#)|purchase\s*order\s*(?:number|no\.?|#)?)\s*[:=]?\s*([A-Za-z0-9\-/]+)",
    "payment_terms": r"(?:payment\s*terms?|terms)\s*[:=]?\s*([^\r\n]{2,})",
    "vendor_tax_id": r"(?:vendor\s*(?:tax\s*id|gstin|vat\s*id|tin)|supplier\s*(?:tax\s*id|gstin|vat\s*id|tin))\s*[:=]?\s*([A-Za-z0-9\-/]+)",
    "customer_tax_id": r"(?:customer\s*(?:tax\s*id|gstin|vat\s*id|tin)|buyer\s*(?:tax\s*id|gstin|vat\s*id|tin))\s*[:=]?\s*([A-Za-z0-9\-/]+)",
    "subtotal": r"(?:sub\s*total|subtotal|taxable\s*amount)\s*[:=]?\s*[$EURINRâ‚¬â‚¹]?\s*([0-9,]+\.?[0-9]{0,2})",
    "tax": r"(?:tax|vat)\s*[:=]?\s*[$EURINRâ‚¬â‚¹]?\s*([0-9,]+\.?[0-9]{0,2})",
    "total": r"(?:grand\s*total|(?<!sub)total)\s*[:=]?\s*[$EURINRâ‚¬â‚¹]?\s*([0-9,]+\.?[0-9]{0,2})",
}
ORDER_ITEM_PATTERN = re.compile(
    r"^\s*(?P<line_no>\d+)\s+"
    r"(?P<description>.+?)\s+"
    r"(?P<qty>\d+(?:\.\d+)?)\s+"
    r"(?P<unit>[A-Za-z]+)\s+"
    r"(?P<unit_price>\d+(?:,\d+)?(?:\.\d{1,2})?)\s+"
    r"(?P<net_amount>\d+(?:,\d+)?(?:\.\d{1,2})?)\s+"
    r"(?P<tax_rate>\d+(?:\.\d+)?%)\s+"
    r"(?P<gross_amount>\d+(?:,\d+)?(?:\.\d{1,2})?)\s*$",
    flags=re.IGNORECASE,
)


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
    if "â‚¬" in text:
        return "EUR"
    if "â‚¹" in text:
        return "INR"
    return None


def empty_invoice_fields() -> dict[str, Any]:
    fields = {key: None for key in SCALAR_FIELD_KEYS}
    fields["order_items"] = []
    return fields


def _extract_order_items(text: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for line in text.splitlines():
        match = ORDER_ITEM_PATTERN.match(line.strip())
        if not match:
            continue
        items.append({key: match.group(key).strip() for key in ORDER_ITEM_FIELD_KEYS})
    return items


def normalize_invoice_fields(parsed: dict[str, Any] | None) -> dict[str, Any]:
    fields = empty_invoice_fields()
    if not isinstance(parsed, dict):
        return fields

    for key in SCALAR_FIELD_KEYS:
        fields[key] = parsed.get(key)

    normalized_items: list[dict[str, Any]] = []
    raw_items = parsed.get("order_items")
    if isinstance(raw_items, list):
        for raw_item in raw_items:
            if not isinstance(raw_item, dict):
                continue
            normalized_items.append({key: raw_item.get(key) for key in ORDER_ITEM_FIELD_KEYS})
    fields["order_items"] = normalized_items

    if "fallback_reason" in parsed:
        fields["fallback_reason"] = parsed["fallback_reason"]
    if "fallback_detail" in parsed:
        fields["fallback_detail"] = parsed["fallback_detail"]
    return fields


def extract_invoice_fields_local(text: str) -> dict[str, Any]:
    fields = empty_invoice_fields()
    for key, pattern in PATTERNS.items():
        fields[key] = _search_value(pattern, text)
    fields["vendor_name"] = _search_vendor_name(text)
    fields["customer_name"] = _search_customer_name(text)
    fields["currency"] = _detect_currency(text)
    fields["order_items"] = _extract_order_items(text)
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
        return normalize_invoice_fields(parsed)

    fields = extract_invoice_fields_local(ocr_text)
    fields["fallback_reason"] = fallback_reason
    if fallback_detail:
        fields["fallback_detail"] = fallback_detail
    return fields
