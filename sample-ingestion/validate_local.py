"""Local validation for the Azure AI Search sample ingestion lab.

This script does not call Azure. It verifies Python syntax, JSON data, local
provider behaviour, vector schema dimensions and Word document parsing.
"""
from __future__ import annotations
import json
import py_compile
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parent


def compile_python_files() -> list[str]:
    failures: list[str] = []
    for folder in [ROOT / "src", ROOT / "exercises", ROOT / "solutions", ROOT / "local_exercises", ROOT / "local_solutions"]:
        for file in folder.glob("*.py"):
            try:
                py_compile.compile(str(file), doraise=True)
            except py_compile.PyCompileError as exc:
                failures.append(f"{file.relative_to(PROJECT)}: {exc.msg}")
    return failures


def load_json(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def validate_json_and_vectors() -> list[str]:
    failures: list[str] = []
    indexes = load_json(ROOT / "data/indexes/sample-indexes.json")
    docs = load_json(ROOT / "data/documents/sample-documents.json")
    if not isinstance(indexes, list) or not indexes:
        failures.append("sample-indexes.json must be a non-empty array.")
        return failures
    index = indexes[0]
    fields = {field["name"]: field for field in index.get("fields", [])}
    for required in ["id", "title", "content", "group_ids", "titleVector", "contentVector"]:
        if required not in fields:
            failures.append(f"Index is missing required field: {required}")
    expected_dimensions = fields.get("titleVector", {}).get("dimensions")
    if expected_dimensions != fields.get("contentVector", {}).get("dimensions"):
        failures.append("titleVector and contentVector should use the same dimensions in this demo.")
    for doc in docs:
        for required in ["id", "documentCode", "title", "content", "category", "publishedDate", "group_ids"]:
            if required not in doc:
                failures.append(f"Document {doc.get('id', '<missing id>')} missing {required}")
    return failures


def validate_local_provider() -> list[str]:
    failures: list[str] = []
    sys.path.insert(0, str(ROOT))
    try:
        from src.providers import LocalSearchProvider, load_sample_index_definition
        from src.data_loader import load_sample_documents
        provider = LocalSearchProvider()
        provider.create_or_update_index(load_sample_index_definition())
        docs = load_sample_documents(with_vectors=True)
        uploaded = provider.upload_documents(docs)
        if len(uploaded) != len(docs):
            failures.append("Local provider upload count mismatch.")
        if not provider.exact_category("Policy"):
            failures.append("Local provider category filter returned no results.")
        if not provider.exact_published_date("2026-07-01"):
            failures.append("Local provider date filter returned no results.")
        if not provider.phrase_search("private endpoint"):
            failures.append("Local provider phrase search returned no results.")
        if not provider.vector_search("private endpoint", top=3):
            failures.append("Local provider vector-style search returned no results.")
        if not provider.hybrid_search("private endpoint DNS", caller_groups=["ai-platform"], top=3):
            failures.append("Local provider hybrid search returned no results.")
    except Exception as exc:
        failures.append(f"Local provider validation failed: {exc}")
    return failures


def validate_word_parser() -> list[str]:
    failures: list[str] = []
    sys.path.insert(0, str(ROOT))
    try:
        from solutions._word_parse_import_helper_solution import parse_docx
        parsed = parse_docx(ROOT / "data/word/sample-private-networking-policy.docx")
        for key in ["id", "title", "content", "titleVector", "contentVector"]:
            if key not in parsed:
                failures.append(f"Parsed Word document missing {key}")
        if len(parsed.get("titleVector", [])) != 1536:
            failures.append("Parsed titleVector should have 1536 dimensions.")
        if len(parsed.get("contentVector", [])) != 1536:
            failures.append("Parsed contentVector should have 1536 dimensions.")
    except Exception as exc:
        failures.append(f"Word parser failed: {exc}")
    return failures


def main() -> int:
    failures = []
    failures.extend(compile_python_files())
    failures.extend(validate_json_and_vectors())
    failures.extend(validate_local_provider())
    failures.extend(validate_word_parser())
    if failures:
        print("Validation failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("Local validation passed: syntax, JSON data, local provider, vector schema and Word parser are OK.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
