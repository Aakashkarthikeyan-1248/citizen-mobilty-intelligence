# 🛣️ CMI — Citizen Mobility Intelligence

AI-powered route corridor analysis for Chennai, India. Overlays real-time traffic, construction, and municipal hazard intelligence on an interactive map.

## Architecture

```
E:\cmi\
├── main.py                    # FastAPI application entry point
├── config.py                  # Centralized configuration (pydantic-settings)
├── .env                       # Secrets (gitignored)
├── requirements.txt           # Python dependencies
│
├── engines/                   # Core processing pipeline
│   ├── text_isolation.py      # HTML/media → plaintext converter
│   ├── llm_router.py          # Multi-provider LLM fallback gateway
│   └── route_harvester.py     # Geocode → route → corridor → alerts → AI
│
├── api/                       # FastAPI route definitions
│   └── routes.py              # HTTP endpoints (thin adapters)
│
├── static/                    # Frontend assets
│   └── index.html             # Landing page (Leaflet + OSM)
│
├── data/                      # Runtime data directories
│   ├── raw/                   # Raw scraped media
│   ├── processed/             # Cleaned plaintext
│   └── cache/                 # LLM response cache
│
├── scripts/                   # DevOps automation
│   ├── deploy.py              # Python deployment manager
│   └── deploy.sh              # Bash deployment script
│
├── tests/                     # Test suite
└── .vercel/
    └── project.json           # Vercel deployment config
```

## Quick Start

```bash
cd E:\cmi
pip install -r requirements.txt
python main.py
```

Visit `http://localhost:8000` for the map interface, or `http://localhost:8000/docs` for the interactive API docs.

## LLM Providers (Fallback Chain)

1. **NVIDIA NIM** — Primary (Llama 3.1 70B)
2. **Groq Cloud** — Fallback (Llama 3.1 70B Versatile)

If Provider 1 times out or returns an error, the router automatically cascades to Provider 2.

## Deployment

```bash
python scripts/deploy.py
```

Or manually:
```bash
bash scripts/deploy.sh
```
