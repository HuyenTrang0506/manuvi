"""ADR-010: every model call goes through src/llm_gateway. No other module under src may import an LLM SDK or
an HTTP client, or name a model API host."""
import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
GATEWAY = SRC / "llm_gateway"

# top-level module names that reach a model (SDKs) or any network (HTTP clients)
DENIED = {"openai", "anthropic", "ollama", "litellm", "cohere", "mistralai", "groq", "langchain", "langchain_openai",
          "langchain_anthropic", "langchain_community", "google", "vertexai", "boto3", "botocore", "httpx", "requests",
          "urllib3", "aiohttp", "http", "urllib", "websockets", "pycurl", "huggingface_hub", "transformers"}
# not model calls: S3/SQS storage and queue clients of the hosted portal (checked below to name no model API)
ALLOWED = {"src/jobs/queue.py": {"boto3"}, "src/jobs/store.py": {"boto3", "botocore"}}
HOSTS = ("api.openai.com", "api.anthropic.com", "generativelanguage.googleapis.com", "bedrock-runtime",
         "11434", "/chat/completions", "api.groq.com", "api.mistral.ai")


def imported(tree: ast.AST) -> set[str]:
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            out |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.level == 0 and n.module:
            out.add(n.module.split(".")[0])
        elif isinstance(n, ast.Call) and getattr(n.func, "id", "") == "__import__" and n.args \
                and isinstance(n.args[0], ast.Constant):
            out.add(str(n.args[0].value).split(".")[0])
        elif isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "import_module" and n.args \
                and isinstance(n.args[0], ast.Constant):
            out.add(str(n.args[0].value).split(".")[0])
    return out


def violations(files, root=SRC.parent) -> list[str]:
    bad = []
    for f in files:
        rel = f.relative_to(root).as_posix()
        text = f.read_text("utf-8")
        mods = imported(ast.parse(text)) & DENIED - ALLOWED.get(rel, set())
        if mods:
            bad.append(f"{rel} imports {sorted(mods)}")
        hosts = [h for h in HOSTS if h in text]
        if hosts:
            bad.append(f"{rel} names model API endpoint(s) {hosts}")
    return bad


def test_no_module_outside_the_gateway_calls_a_model_directly():
    files = [f for f in SRC.rglob("*.py") if GATEWAY not in f.parents]
    assert len(files) > 50
    assert violations(files) == []


def test_allowed_exceptions_are_storage_clients_only():
    for rel in ALLOWED:
        text = (SRC.parent / rel).read_text("utf-8")
        assert not any(h in text for h in HOSTS) and "bedrock" not in text.lower()


def test_the_detector_catches_sdk_and_http_imports(tmp_path):
    cases = {"a.py": "import openai\n", "b.py": "from anthropic import Anthropic\n", "c.py": "import urllib.request\n",
             "d.py": "import httpx as h\n", "e.py": "x = __import__('requests')\n",
             "f.py": "U = 'https://api.openai.com/v1/chat/completions'\n", "g.py": "import json\n"}
    files = []
    for name, body in cases.items():
        (tmp_path / name).write_text(body)
        files.append(tmp_path / name)
    found = violations(files, root=tmp_path)
    assert len(found) == 6 and not any("g.py" in v for v in found)
