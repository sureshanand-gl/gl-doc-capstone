import json
import os
import sys
from argparse import ArgumentParser
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from llmops.pipeline import run_live_golden_eval  # noqa: E402
from llmops.reporting import write_llmops_artifacts  # noqa: E402

DEFAULT_OPENAI_API_BASE = "https://aibe.mygreatlearning.com/openai/v1"


def parse_args() -> ArgumentParser:
    parser = ArgumentParser()
    parser.add_argument(
        "--dataset",
        type=Path,
        default=REPO_ROOT / "data" / "golden" / "invoice_extraction_v2.jsonl",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPO_ROOT / "outputs" / "llmops",
    )
    parser.add_argument("--model", default="gpt-4o-mini")
    parser.add_argument("--prompt-version", default=os.getenv("LLMOPS_PROMPT_VERSION", "v2"))
    parser.add_argument("--min-field-accuracy", type=float, default=0.80)
    parser.add_argument("--no-dotenv", action="store_true")
    return parser


def _resolve_path(path: Path) -> Path:
    return path if path.is_absolute() else REPO_ROOT / path


def load_openai_config(repo_root: Path, load_dotenv_file: bool = True) -> tuple[str | None, str]:
    explicit_blank_api_key = os.environ.get("OPENAI_API_KEY") == ""
    if load_dotenv_file:
        load_dotenv(repo_root / ".env", override=False)
    api_key = "" if explicit_blank_api_key else os.getenv("OPENAI_API_KEY")
    api_base = os.getenv("OPENAI_API_BASE") or DEFAULT_OPENAI_API_BASE
    return api_key, api_base


def main() -> int:
    args = parse_args().parse_args()
    api_key, api_base = load_openai_config(REPO_ROOT, load_dotenv_file=not args.no_dotenv)
    if not api_key:
        print("OPENAI_API_KEY is required for live LLMOps pipeline", file=sys.stderr)
        return 2

    dataset_path = _resolve_path(args.dataset)
    output_dir = _resolve_path(args.output_dir)

    rows, schema_version = run_live_golden_eval(
        repo_root=REPO_ROOT,
        dataset_path=dataset_path,
        api_key=api_key,
        api_base=api_base,
        model=args.model,
        prompt_version=args.prompt_version,
    )
    report = write_llmops_artifacts(
        rows=rows,
        output_dir=output_dir,
        min_field_accuracy=args.min_field_accuracy,
        prompt_version=args.prompt_version,
        schema_version=schema_version,
    )

    print(json.dumps(report, indent=2))
    return 0 if report["meets_threshold"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
