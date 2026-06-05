import json
import logging
import httpx
import config

log = logging.getLogger(__name__)


class GeminiError(Exception):
    pass


# Alias for backward compatibility
OllamaError = GeminiError


def generate(prompt: str, model: str | None = None) -> str:
    key = config.GEMINI_API_KEY
    if not key or key.startswith("YOUR_") or len(key) < 10:
        raise GeminiError("GEMINI_API_KEY is not configured or invalid.")

    gemini_model = "gemini-1.5-flash"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{gemini_model}:generateContent?key={key}"

    payload = {
        "contents": [
            {
                "parts": [
                    {"text": prompt}
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0.1,
            "responseMimeType": "application/json"
        }
    }

    log.info("Sending request to Gemini model %s...", gemini_model)

    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.post(url, json=payload)
    except httpx.TimeoutException as e:
        raise GeminiError(f"Google Gemini API timeout (30s): {e}") from e
    except httpx.ConnectError as e:
        raise GeminiError(f"Google Gemini API is unreachable (network error): {e}") from e
    except httpx.RequestError as e:
        raise GeminiError(f"HTTP request error for Gemini: {e}") from e

    if response.status_code != 200:
        raise GeminiError(
            f"Gemini API returned HTTP {response.status_code}: {response.text[:200]}"
        )

    try:
        data = response.json()
    except json.JSONDecodeError as e:
        raise GeminiError(f"Gemini API returned invalid JSON: {e}") from e

    try:
        text = data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError) as e:
        raise GeminiError(f"Unexpected Gemini API response structure: {e}")

    if not text:
        raise GeminiError("Gemini API returned empty text")

    log.info("Response received from Gemini: %d characters", len(text))
    return text


def check_availability() -> bool:
    return bool(config.GEMINI_API_KEY and len(config.GEMINI_API_KEY) > 10)


def is_model_available(model: str | None = None) -> bool:
    return True
