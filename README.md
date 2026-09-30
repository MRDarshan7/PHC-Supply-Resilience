# PHC Supply Resilience Layer
### Intelligent Medicine Redistribution & Outbreak Surge Forecasting for India's Primary Health Centres

[![Live Console](https://img.shields.io/badge/Live_Console-Firebase_Hosting-FFCA28?style=flat-square&logo=firebase&logoColor=black)](https://phc-supply-resilience.web.app)
[![Backend API](https://img.shields.io/badge/Backend_API-Render_FastAPI-46E3B7?style=flat-square&logo=render&logoColor=black)](https://phc-supply-resilience.onrender.com/health)
[![Google Gemini](https://img.shields.io/badge/AI_Engine-Gemini_3.5_Flash-4285F4?style=flat-square&logo=google&logoColor=white)](https://ai.google.dev/)
[![Track](https://img.shields.io/badge/Hackathon-Build_with_AI:_Code_for_Communities-EA4335?style=flat-square)](https://cloud.google.com)

> *"We used AI where nothing else works, and arithmetic where arithmetic is correct."*

---

## 📌 Executive Summary

India's network of over 158,000 **Primary Health Centres (PHCs)** and sub-centres is the frontline defense for rural healthcare. When sudden seasonal outbreaks strike—such as acute diarrhoeal disease during monsoon floods or dengue spikes—local medicine consumption accelerates rapidly. Central district replenishment often takes 7 to 14 days, creating critical stockout windows where lives are at risk.

**PHC Supply Resilience** is a decision-support system for District Medical Officers (DMOs). It monitors essential medicine runway, parses external government disease surveillance PDFs to forecast outbreak-driven surge consumption, identifies neighbouring facilities that can safely spare stock, and generates bilingual clinical transfer orders for official approval.

---

## 🔗 Live Deployments

- **Web Console (Frontend):** [https://phc-supply-resilience.web.app](https://phc-supply-resilience.web.app)
- **REST API (Backend):** [https://phc-supply-resilience.onrender.com](https://phc-supply-resilience.onrender.com)
- **API Documentation (Swagger):** [https://phc-supply-resilience.onrender.com/docs](https://phc-supply-resilience.onrender.com/docs)

---

## ⚙️ Core Mechanics: Days of Cover & The Outbreak Surge

The entire system revolves around arithmetic ground truth:

$$\text{Days of Cover} = \frac{\text{Current Stock on Shelf}}{\text{Daily Consumption Rate}}$$

```
               stock on the shelf   (changes only upon approved transfer)
days of cover = ──────────────────
             daily consumption rate (rises when an outbreak occurs)
```

### Risk Bands
- **Critical (Red):** $< 8$ days of cover (stock will exhaust before routine lorry restock)
- **Warning (Amber):** $8 - 14$ days of cover
- **Safe (Green):** $> 14$ days of cover

> **Key Architectural Principle:** An outbreak does **not** change how much stock a facility possesses. It accelerates the **burn rate** (the denominator) *before* days of cover is computed. Risk is tracked per `(facility, medicine)` pair, never as an arbitrary aggregated facility score.

---

## 🤖 The Two Gemini Jobs (Google AI Studio)

### 1. Multimodal IDSP Outbreak PDF Extraction
- **Input:** Official weekly disease surveillance PDFs published by the Ministry of Health and Family Welfare (MoHFW) Integrated Disease Surveillance Programme (IDSP).
- **The Challenge:** Format variations, scanned table layouts, late-reported outbreak addenda, and unstructured free-text comments (e.g. *"outbreak localized to university hostel in Thullur village"*). Traditional regex or OCR pipelines fail against government document format shifts.
- **Gemini's Role:** Ingests raw PDF bytes and extracts structured JSON containing:
  `outbreak_id, state, district, sub_district, disease, cases, deaths, week, year, status`.
- **Reliability:** Extraction responses are cached to disk keyed by file SHA-256 for deterministic, instant playback.

### 2. Grounded Bilingual Transfer Memo
- **Input:** Recipient shortage state + donor facilities certified as safe by the deterministic redistribution engine.
- **The Safety Boundary:** **Gemini has zero authority over quantities or clinical rules.** All numbers, safety floors, distances, and transfer counts are computed strictly in Python. Gemini drafts the administrative justification in **English and Telugu** for DMO sign-off.
- **Zero Hallucination Guardrail:** Every number written by Gemini is validated against backend input values; any discrepancy triggers an automatic fallback to an offline deterministic template.

---

## 🛡️ Deterministic 4-Stage Redistribution Engine

Finding stock in a database is easy; determining if a neighbouring clinic can **safely afford** to spare inventory under the same regional outbreak is the hard problem.

1. **Candidate Filter:** Facility holds the required medicine, is located within `MAX_TRANSFER_RADIUS_KM` (75 km), and stock is not near expiration during transit.
2. **Donor Safety Floor Check (Harm Prevention):**
   $$\text{Spare Stock} = \text{Current Stock} - (\text{Donor Burn Rate} \times \text{Safety Floor Days})$$
   If $\text{Spare} \le 0$, the facility is **ineligible**. The donor burn rate strictly incorporates the donor's own outbreak surge.
3. **Transfer Sizing:**
   $$\text{Need} = (\text{Recipient Burn Rate} \times \text{Target Days}) - \text{Recipient Stock}$$
   $$\text{Transfer Quantity} = \min(\text{Need}, \text{Spare})$$
4. **Multi-Factor Scoring:**
   - **Sufficiency (30%):** Fraction of recipient shortage resolved.
   - **Proximity (25%):** Haversine distance decay.
   - **Expiry Mitigation (30%):** Prioritizes batches nearing expiry that the recipient will consume before expiration.
   - **Donor Comfort (15%):** Remaining runway margin above the safety floor.

---

## 📊 Data Provenance & Transparency

India does not publish facility-level pharmacy stock (systems like e-Aushadhi are access-controlled). Rather than using fake data, the inventory ledger is back-calculated from verified government caseloads.

| Element | Class | Source / Authority | Details |
|---|---|---|---|
| **Facility Directory** | **REAL** | [data.gov.in](https://data.gov.in) | 1,448 health facilities in Guntur district, AP with validated coordinates |
| **Patient Caseloads** | **REAL** | MoHFW HMIS (2019–20) | Monthly diarrhoea caseloads (S.No. 10.11, Guntur Total: 2,931 cases) |
| **Outbreak Reports** | **REAL** | MoHFW IDSP | Week 45 (2025) PDF: Acute Diarrhoeal Disease in Thullur (457 cases) |
| **Essential Medicines** | **REAL** | National List of Essential Medicines (NLEM 2022) | ORS, Zinc Sulphate 20mg, IV Fluids (RL), Paracetamol, Ciprofloxacin |
| **Distances** | **DERIVED** | Coordinates | Haversine distance from verified latitude/longitude |
| **Clinical Regimens** | **CURATED** | WHO / IAP Clinical Protocols | E.g., 4 ORS packets/case, 14 zinc tablets/course (`config/rules.yaml`) |
| **Inventory Ledger** | **SIMULATED** | Derived via Caseload | Seeded generator back-calculated from HMIS consumption rates |

---

## 🏗️ System Architecture

```
                    ┌────────────────────────────────────────┐
                    │          District Medical Officer      │
                    │               Web Browser              │
                    └───────────────────┬────────────────────┘
                                        │
                         HTTPS / REST   │ (CORS Enabled)
                                        ▼
                    ┌────────────────────────────────────────┐
                    │            Firebase Hosting            │
                    │   React 19 + Vite + Leaflet OpenStreet │
                    │    Swiss Typographic Monochrome UI     │
                    └───────────────────┬────────────────────┘
                                        │
                         Probing / API  │
                                        ▼
                    ┌────────────────────────────────────────┐
                    │             FastAPI Backend            │
                    │           (Render Web Service)         │
                    ├────────────────────────────────────────┤
                    │ • Deterministic Redistribution Engine  │
                    │ • 4-Stage Donor Safety Verifier        │
                    │ • Days-of-Cover Arithmetic Evaluator   │
                    └──────┬───────────────┬─────────────────┘
                           │               │
            Read/Write DB  │               │ Part-from-Bytes / Schema
                           ▼               ▼
          ┌──────────────────────┐   ┌───────────────────────────┐
          │   SQLite Database    │   │  Google Gemini 3.5 Flash  │
          │   (data/ledger.db)   │   │  • Multimodal PDF Ingest  │
          │ • Double-entry stock │   │  • Bilingual Memo Gen     │
          │ • Real HMIS caseloads│   │  • Multi-Key Auto-Failover│
          └──────────────────────┘   └───────────────────────────┘
```

---

## 🚀 Getting Started Locally

### Prerequisites
- Python 3.11+
- Node.js 18+ & npm

### 1. Clone the Repository
```bash
git clone https://github.com/MRDarshan7/PHC-Supply-Resilience.git
cd PHC-Supply-Resilience
```

### 2. Backend Setup
```bash
# Install Python dependencies
py -m pip install -r requirements.txt

# Create .env in root with your Gemini API key(s)
# Multi-key rotation is supported out of the box:
echo "GEMINI_API_KEY_1=your_gemini_api_key_here" >> .env
echo "GEMINI_API_KEY_2=optional_backup_key_here" >> .env

# Start FastAPI server on port 8000
py -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```
- API will be accessible at: `http://127.0.0.1:8000`
- Interactive Swagger documentation: `http://127.0.0.1:8000/docs`

### 3. Frontend Setup
```bash
cd frontend

# Install Node dependencies
npm install

# Run Vite dev server
npm run dev
```
- Frontend will open at: `http://localhost:5173`

---

## 🧪 Testing & Verification

Run the automated validation suites:
```bash
# Verify database loaders, caseload allocation, and ledger consistency
py backend/run_loaders.py

# Test deterministic redistribution engine and donor safety floors
py backend/redistribute.py

# Test end-to-end demo flow (baseline -> ingest -> surge -> recommend -> approve)
py backend/test_demo.py
```

---

## 📜 Compliance & Safety Disclaimers

- **Human-in-the-Loop:** Decision-support only. Transfer orders require explicit authentication and authorization by the appointed District Medical Officer. The system never executes autonomous physical transfers.
- **Data Protection:** No personally identifiable health information (PII) is processed or retained. All caseloads and outbreak reports operate at aggregated, public facility-level statistics in accordance with India's Digital Personal Data Protection (DPDP) Act.
