"""Run from the project folder: python check_api.py [--list-models]."""
import json
import os
from pathlib import Path
import sys
import tomllib
import urllib.request
import urllib.error

from ai import AIError, DEFAULT_MODEL, DEFAULT_GROQ_MODEL, generate
from core import Overview, parse_transcript


def main():
    path = Path(__file__).parent / ".streamlit" / "secrets.toml"
    try:
        config = tomllib.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else {}
    except (OSError, tomllib.TOMLDecodeError):
        print("Cannot read secrets.toml. Use UTF-8 encoding and double-quoted TOML values.")
        return 1
    provider = (os.environ.get("AI_PROVIDER") or config.get("AI_PROVIDER", "gemini")).lower()
    key_name = "GROQ_API_KEY" if provider == "groq" else "GEMINI_API_KEY"
    model_name = "GROQ_MODEL" if provider == "groq" else "GEMINI_MODEL"
    key = os.environ.get(key_name) or config.get(key_name, "")
    model = os.environ.get(model_name) or config.get(model_name, DEFAULT_GROQ_MODEL if provider == "groq" else DEFAULT_MODEL)
    if not key or key.startswith("REPLACE_"):
        print(f"Set {key_name} in .streamlit/secrets.toml first. Never paste the key into chat.")
        return 1
    if "--list-models" in sys.argv:
        print("Models available to this key (availability is NOT proof of free-tier eligibility):")
        page = ""
        try:
            while True:
                url = "https://api.groq.com/openai/v1/models" if provider == "groq" else "https://generativelanguage.googleapis.com/v1beta/models?pageSize=1000" + page
                headers = {"Authorization": "Bearer " + key, "User-Agent": "THWENK/1.0"} if provider == "groq" else {"x-goog-api-key": key}
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=30) as response:
                    data = json.load(response)
                for item in data.get("data", []) if provider == "groq" else data.get("models", []):
                    if provider == "groq":
                        print(item["id"])
                    elif "generateContent" in item.get("supportedGenerationMethods", []):
                        print(item["name"].removeprefix("models/"))
                token = data.get("nextPageToken")
                if not token:
                    break
                from urllib.parse import quote
                page = "&pageToken=" + quote(token, safe="")
        except (urllib.error.URLError, TimeoutError, ValueError):
            print("Could not list models. Check the API key, connection and project access.")
            return 1
        return 0
    try:
        segments = parse_transcript((Path(__file__).parent / "samples/learning.txt").read_text(encoding="utf-8"))
        result = generate(Overview, "Create a concise overview with themes and takeaways.", segments, "Bangla", key, model, provider=provider)
        print("SUCCESS: real API response passed schema and source-ID checks.")
        print("Provider:", provider)
        print("Model:", model)
        print(json.dumps(result.model_dump(), ensure_ascii=not sys.stdout.encoding.lower().startswith("utf"), indent=2))
        print("Now assess the Bangla wording and factual faithfulness yourself.")
        return 0
    except AIError as exc:
        print(str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
