import os
import json
import time

from dotenv import load_dotenv
from google import genai
from google.genai import types
from google.genai.errors import ClientError, ServerError

from config.settings import GEMINI_MODEL, GEMINI_MAX_RETRIES, GEMINI_RETRY_DELAY_SECONDS

load_dotenv()


def call_gemini(contents, response_schema):
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=response_schema,
    )

    for attempt in range(1, GEMINI_MAX_RETRIES + 1):
        try:
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=contents,
                config=config,
            )
            return json.loads(response.text)
        except (ClientError, ServerError) as e:
            status_code = getattr(e, "code", None)
            if status_code in (503, 429) and attempt < GEMINI_MAX_RETRIES:
                print(f"Gemini call failed with {status_code}, retrying (attempt {attempt + 1}/{GEMINI_MAX_RETRIES})...")
                time.sleep(GEMINI_RETRY_DELAY_SECONDS)
                continue
            raise
