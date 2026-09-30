import os
import json
import time
import logging

from dotenv import load_dotenv
from google import genai
from google.genai import types
from google.genai.errors import ClientError, ServerError

from config.settings import GEMINI_MODEL, GEMINI_MAX_RETRIES, GEMINI_RETRY_DELAY_SECONDS

load_dotenv()
log = logging.getLogger(__name__)


def get_api_keys() -> list[str]:
    """Collect all unique API keys from environment variables.
    
    Checks GEMINI_API_KEY_1, GEMINI_API_KEY_2, GEMINI_API_KEY_3... as well as
    GEMINI_API_KEY and GEMINI_API_KEYS (comma-separated).
    """
    raw_keys = []

    # 1. Numbered keys GEMINI_API_KEY_1, _2, _3...
    i = 1
    while f"GEMINI_API_KEY_{i}" in os.environ:
        val = os.environ.get(f"GEMINI_API_KEY_{i}", "").strip()
        if val:
            raw_keys.append(val)
        i += 1

    # 2. Standard GEMINI_API_KEY
    standard = os.environ.get("GEMINI_API_KEY", "").strip()
    if standard:
        raw_keys.append(standard)

    # 3. Comma-separated list in GEMINI_API_KEYS if present
    list_keys = os.environ.get("GEMINI_API_KEYS", "").strip()
    if list_keys:
        raw_keys.extend([k.strip() for k in list_keys.split(",") if k.strip()])

    # Deduplicate while preserving order
    seen = set()
    deduped = []
    for k in raw_keys:
        if k not in seen:
            seen.add(k)
            deduped.append(k)

    # Keep os.environ["GEMINI_API_KEY"] synced for callers checking existence
    if deduped and not os.environ.get("GEMINI_API_KEY"):
        os.environ["GEMINI_API_KEY"] = deduped[0]

    return deduped


_current_key_index = 0


def mask_key(key: str) -> str:
    if not key or len(key) <= 10:
        return "***"
    return f"{key[:6]}...{key[-4:]}"


def call_gemini(contents, response_schema):
    """Execute Gemini request with automatic fallback and key rotation.
    
    If the active key encounters a rate limit (429), quota issue (403), or
    server error (503), it rotates immediately to the next available key.
    """
    global _current_key_index
    keys = get_api_keys()
    if not keys:
        raise KeyError("No Gemini API keys found in environment (checked GEMINI_API_KEY, GEMINI_API_KEY_1, ...)")

    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=response_schema,
    )

    num_keys = len(keys)
    last_error = None

    for attempt in range(1, GEMINI_MAX_RETRIES + 1):
        for _ in range(num_keys):
            active_key = keys[_current_key_index]
            os.environ["GEMINI_API_KEY"] = active_key
            try:
                client = genai.Client(api_key=active_key)
                response = client.models.generate_content(
                    model=GEMINI_MODEL,
                    contents=contents,
                    config=config,
                )
                return json.loads(response.text)
            except (ClientError, ServerError) as e:
                status_code = getattr(e, "code", None)
                last_error = e
                # Rate limit (429), quota (403), or server unavailable (503)
                if status_code in (429, 403, 503):
                    prev_masked = mask_key(active_key)
                    _current_key_index = (_current_key_index + 1) % num_keys
                    next_masked = mask_key(keys[_current_key_index])
                    print(
                        f"[Gemini Key Rotation] Key {prev_masked} returned status {status_code}. "
                        f"Switching to key {next_masked}..."
                    )
                    if num_keys > 1:
                        continue
                raise

        # If all keys failed on this cycle, wait before next retry attempt
        if attempt < GEMINI_MAX_RETRIES:
            print(
                f"[Gemini Key Rotation] All {num_keys} keys exhausted on attempt {attempt}/{GEMINI_MAX_RETRIES}. "
                f"Waiting {GEMINI_RETRY_DELAY_SECONDS}s before retrying..."
            )
            time.sleep(GEMINI_RETRY_DELAY_SECONDS)

    if last_error:
        raise last_error
