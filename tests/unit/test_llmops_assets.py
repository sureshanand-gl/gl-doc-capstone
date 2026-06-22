from pathlib import Path

from llmops.registry import load_prompt_registry
from llmops.schema import validate_invoice_fields


def test_prompt_registry_resolves_invoice_v1():
    repo_root = Path(__file__).resolve().parents[2]

    registry = load_prompt_registry(repo_root)
    invoice_entry = registry.get_invoice_entry("v1")

    assert invoice_entry.prompt_version == "v1"
    assert invoice_entry.schema_version == "v1"
    assert invoice_entry.prompt_path == repo_root / "prompts" / "invoices" / "v1" / "system.md"
    assert invoice_entry.schema_path == repo_root / "schemas" / "invoice_v1.json"


def test_schema_accepts_invoice_fields_and_fallback_metadata():
    fields = {
        "invoice_number": "INV-1001",
        "invoice_date": "01/15/2026",
        "due_date": None,
        "total": "250.00",
        "tax": "12.50",
        "vendor_name": "Acme Supplies",
        "customer_name": None,
        "currency": "USD",
        "fallback_reason": "local_regex_fallback",
        "fallback_detail": "prompt parse failed",
    }

    errors = validate_invoice_fields(fields)

    assert errors == []
