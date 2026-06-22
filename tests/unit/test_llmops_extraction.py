from llmops.local_extraction import extract_invoice_fields_local, parse_model_json_or_fallback


def test_invalid_llm_json_uses_local_fallback():
    ocr_text = "Invoice Number: INV-1001\nGrand Total: $250.00"

    fields = parse_model_json_or_fallback(
        content="not json",
        ocr_text=ocr_text,
        fallback_reason="gpt_parse_fallback_local",
    )

    assert fields["invoice_number"] == "INV-1001"
    assert fields["total"] == "250.00"
    assert fields["fallback_reason"] == "gpt_parse_fallback_local"


def test_local_baseline_extracts_invoice_fields():
    ocr_text = (
        "Vendor: Acme Supplies\n"
        "Invoice Number: INV-1001\n"
        "Invoice Date: 01/15/2026\n"
        "Due Date: 01/30/2026\n"
        "Tax: $12.50\n"
        "Grand Total: $250.00\n"
        "Bill To: Example Customer"
    )

    fields = extract_invoice_fields_local(ocr_text)

    assert fields == {
        "invoice_number": "INV-1001",
        "invoice_date": "01/15/2026",
        "due_date": "01/30/2026",
        "total": "250.00",
        "tax": "12.50",
        "vendor_name": "Acme Supplies",
        "customer_name": "Example Customer",
        "currency": "USD",
        "fallback_reason": "local_regex_fallback",
    }
