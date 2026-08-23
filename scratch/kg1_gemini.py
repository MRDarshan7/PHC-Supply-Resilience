import os
import json
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

schema = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {
            "name": {"type": "STRING"},
            "age": {"type": "INTEGER"},
        },
        "required": ["name", "age"],
    },
}

response = client.models.generate_content(
    model="gemini-3.6-flash",
    contents="Give me two fictional people with a name and age.",
    config=types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=schema,
    ),
)

print("RAW RESPONSE TEXT:")
print(response.text)

parsed = json.loads(response.text)

print("\nPARSED PYTHON OBJECT:")
print(parsed)

print("\nTYPE:")
print(type(parsed))
