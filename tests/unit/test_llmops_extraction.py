"""Tests for offline invoice extraction behavior and schema-valid payload shaping."""

from llmops.local_extraction import extract_invoice_fields_local, parse_model_json_or_fallback


def test_invalid_llm_json_uses_local_fallback():
    ocr_text = (
        "Vendor: Acme Supplies\n"
        "Invoice Number: INV-1001\n"
        "PO Number: PO-77\n"
        "Grand Total: $250.00"
    )

    fields = parse_model_json_or_fallback(
        content="not json",
        ocr_text=ocr_text,
        fallback_reason="gpt_parse_fallback_local",
    )

    assert fields["invoice_number"] == "INV-1001"
    assert fields["total"] == "250.00"
    assert fields["po_number"] == "PO-77"
    assert fields["fallback_reason"] == "gpt_parse_fallback_local"


def test_model_json_normalizes_numeric_order_item_values_to_strings():
    ocr_text = "Invoice Number: INV-1001"

    fields = parse_model_json_or_fallback(
        content=(
            '{"invoice_number":"INV-1001","invoice_date":"2026-01-15","due_date":"2026-01-30",'
            '"po_number":"PO-77","payment_terms":"Net 15","vendor_name":"Acme Supplies",'
            '"vendor_tax_id":"GSTIN-123","customer_name":"Example Customer","customer_tax_id":"GSTIN-999",'
            '"subtotal":"237.50","tax":"12.50","total":"250.00","currency":"USD",'
            '"order_items":[{"line_no":1,"description":"Blue Widgets","qty":5,"unit":"pcs",'
            '"unit_price":"47.50","net_amount":"237.50","tax_rate":"5%","gross_amount":"250.00"}]}'
        ),
        ocr_text=ocr_text,
        fallback_reason="gpt_parse_fallback_local",
    )

    assert fields["order_items"] == [
        {
            "line_no": "1",
            "description": "Blue Widgets",
            "qty": "5",
            "unit": "pcs",
            "unit_price": "47.50",
            "net_amount": "237.50",
            "tax_rate": "5%",
            "gross_amount": "250.00",
        }
    ]


def test_local_baseline_extracts_invoice_v2_fields_and_order_items():
    ocr_text = (
        "Vendor: Acme Supplies\n"
        "Invoice Number: INV-1001\n"
        "Invoice Date: 01/15/2026\n"
        "Due Date: 01/30/2026\n"
        "PO Number: PO-77\n"
        "Payment Terms: Net 15\n"
        "Vendor GSTIN: GSTIN-123\n"
        "Customer GSTIN: GSTIN-999\n"
        "Subtotal: $237.50\n"
        "Tax: $12.50\n"
        "Grand Total: $250.00\n"
        "Bill To: Example Customer\n"
        "1 Blue Widgets 5 pcs 47.50 237.50 5% 250.00"
    )

    fields = extract_invoice_fields_local(ocr_text)

    assert fields["invoice_number"] == "INV-1001"
    assert fields["invoice_date"] == "01/15/2026"
    assert fields["due_date"] == "01/30/2026"
    assert fields["po_number"] == "PO-77"
    assert fields["payment_terms"] == "Net 15"
    assert fields["vendor_tax_id"] == "GSTIN-123"
    assert fields["customer_tax_id"] == "GSTIN-999"
    assert fields["subtotal"] == "237.50"
    assert fields["tax"] == "12.50"
    assert fields["total"] == "250.00"
    assert fields["vendor_name"] == "Acme Supplies"
    assert fields["customer_name"] == "Example Customer"
    assert fields["currency"] == "USD"
    assert fields["fallback_reason"] == "local_regex_fallback"
    assert fields["order_items"] == [
        {
            "line_no": "1",
            "description": "Blue Widgets",
            "qty": "5",
            "unit": "pcs",
            "unit_price": "47.50",
            "net_amount": "237.50",
            "tax_rate": "5%",
            "gross_amount": "250.00",
        }
    ]
