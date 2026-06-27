"""Tests covering prompt, schema, and dataset asset alignment."""

from pathlib import Path

from llmops.registry import load_prompt_registry
from llmops.schema import validate_invoice_fields


def test_prompt_registry_resolves_invoice_v2():
    repo_root = Path(__file__).resolve().parents[2]

    registry = load_prompt_registry(repo_root)
    invoice_entry = registry.get_invoice_entry("v2")

    assert invoice_entry.prompt_version == "v2"
    assert invoice_entry.schema_version == "v2"
    assert invoice_entry.prompt_path == repo_root / "prompts" / "invoices" / "v2" / "system.md"
    assert invoice_entry.schema_path == repo_root / "schemas" / "invoice_v2.json"


def test_invoice_prompt_requires_order_items_and_schema_compatible_string_values():
    repo_root = Path(__file__).resolve().parents[2]
    prompt = (repo_root / "prompts" / "invoices" / "v2" / "system.md").read_text(
        encoding="utf-8"
    )

    assert "Return every non-null value as a JSON string" in prompt
    assert "order_items" in prompt
    assert "line_no" in prompt
    assert "Infer currency from symbols" in prompt


def test_schema_accepts_invoice_v2_fields_order_items_and_fallback_metadata():
    fields = {
        "invoice_number": "INV-1001",
        "invoice_date": "01/15/2026",
        "due_date": None,
        "po_number": "PO-77",
        "payment_terms": "Net 15",
        "vendor_name": "Acme Supplies",
        "vendor_tax_id": "GSTIN-123",
        "customer_name": None,
        "customer_tax_id": None,
        "subtotal": "237.50",
        "total": "250.00",
        "tax": "12.50",
        "currency": "USD",
        "order_items": [
            {
                "line_no": "1",
                "description": "Blue widgets",
                "qty": "5",
                "unit": "pcs",
                "unit_price": "47.50",
                "net_amount": "237.50",
                "tax_rate": "5%",
                "gross_amount": "250.00",
            }
        ],
        "fallback_reason": "local_regex_fallback",
        "fallback_detail": "prompt parse failed",
    }

    repo_root = Path(__file__).resolve().parents[2]
    errors = validate_invoice_fields(fields, repo_root / "schemas" / "invoice_v2.json")

    assert errors == []


def test_schema_default_path_uses_invoice_v2_contract():
    fields = {
        "invoice_number": "INV-1001",
        "invoice_date": "01/15/2026",
        "due_date": None,
        "po_number": "PO-77",
        "payment_terms": "Net 15",
        "vendor_name": "Acme Supplies",
        "vendor_tax_id": "GSTIN-123",
        "customer_name": None,
        "customer_tax_id": None,
        "subtotal": "237.50",
        "total": "250.00",
        "tax": "12.50",
        "currency": "USD",
        "order_items": [],
    }

    assert validate_invoice_fields(fields) == []


def test_ci_workflow_uses_v2_dataset_and_compiles_new_pipeline_files():
    repo_root = Path(__file__).resolve().parents[2]
    workflow = (repo_root / ".github" / "workflows" / "llmops-ci.yml").read_text(encoding="utf-8")

    assert "data/golden/invoice_extraction_v2.jsonl" in workflow
    assert "app_frontend_helpers.py" in workflow
    assert "scripts/layout_worker.py" in workflow
