from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class InvoicePromptEntry:
    prompt_version: str
    schema_version: str
    prompt_path: Path
    schema_path: Path


class PromptRegistry:
    def __init__(self, repo_root: Path, raw_config: dict) -> None:
        self.repo_root = repo_root
        self.raw_config = raw_config

    def get_invoice_entry(self, prompt_version: str) -> InvoicePromptEntry:
        invoice_config = self.raw_config["invoices"][prompt_version]
        return InvoicePromptEntry(
            prompt_version=prompt_version,
            schema_version=invoice_config["schema_version"],
            prompt_path=self.repo_root / invoice_config["prompt"],
            schema_path=self.repo_root / invoice_config["schema"],
        )


def load_prompt_registry(repo_root: Path) -> PromptRegistry:
    registry_path = repo_root / "prompts" / "registry.yaml"
    raw_config = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    return PromptRegistry(repo_root, raw_config)
