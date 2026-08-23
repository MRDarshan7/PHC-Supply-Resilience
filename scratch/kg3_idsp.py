import sys
import os
import json
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

pdf_path = "data/idsp_pdfs/idsp_2025_w45.pdf"

client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

uploaded_file = client.files.upload(file=pdf_path)

schema = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {
            "outbreak_id": {"type": "STRING"},
            "state": {"type": "STRING"},
            "district": {"type": "STRING"},
            "sub_district": {"type": "STRING"},
            "disease": {"type": "STRING"},
            "cases": {"type": "INTEGER"},
            "deaths": {"type": "INTEGER"},
            "week": {"type": "INTEGER"},
            "year": {"type": "INTEGER"},
            "status": {"type": "STRING"},
        },
        "required": [
            "outbreak_id",
            "state",
            "district",
            "sub_district",
            "disease",
            "cases",
            "deaths",
            "week",
            "year",
            "status",
        ],
    },
}

prompt = """Extract every outbreak row from this IDSP weekly outbreak report PDF.

There are two tables to extract from:
1. The main weekly outbreak table.
2. The "DISEASE OUTBREAKS OF PREVIOUS WEEKS REPORTED LATE" table at the end of the document.

Combine rows from both tables into a single flat list.

For each row, extract: outbreak_id, state, district, sub_district, disease, cases, deaths, week, year, status.

sub_district is usually not a separate column in the table. Extract it from the
Comments column text, which typically reads something like "Sub-District X, District Y".
If no sub-district is mentioned in the comments, return an empty string for sub_district.
"""

response = client.models.generate_content(
    model="gemini-3.5-flash",
    contents=[uploaded_file, prompt],
    config=types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=schema,
    ),
)

print("RAW RESPONSE TEXT:")
print(response.text)

rows = json.loads(response.text)

print("\nPARSED ROWS:")
print(rows)

print("\nTOTAL COUNT:")
print(len(rows))

print("\nROWS WHERE STATE CONTAINS 'Andhra':")
andhra_rows = [row for row in rows if "Andhra" in row["state"]]
print(andhra_rows)
