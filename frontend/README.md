# CityLens Frontend

Standalone frontend module for the **Citizen Mobility Intelligence (CMI)** platform.

## Files

| File | Purpose |
|---|---|
| `citylens.html` | Complete single-file frontend app. Open in any browser — no build step needed. |
| `supabase_adapter.js` | Drop-in Supabase adapter to replace the `localStorage Store` once you send your database credentials. |

---

## Running Locally

```bash
# Option 1 — just open it
start E:\cmi\frontend\citylens.html

# Option 2 — serve via Python (required for Geolocation API on http)
python -m http.server 8080 --directory E:\cmi\frontend
# Then open http://localhost:8080/citylens.html
```

> **Demo admin login:**  
> Email: `admin@citylens.demo` · Employee ID: `EMP-0001` · Password: `CityLens-Demo-2026`

---

## Connecting Supabase (Next Step)

1. Send your **Supabase Project URL** and **anon public key**.
2. We will run the `CREATE TABLE` SQL from `supabase_adapter.js` in your Supabase SQL editor.
3. We replace the `Store` object in `citylens.html` with `SupabaseStore` from `supabase_adapter.js`.
4. Auth, incident storage, credit ledger, and real-time incident updates will all be live.

---

## Combining with the CMI Map

The CMI corridor intelligence map (`/static/index.html`) uses the same Chennai bounds and OSRM routing.
The integration plan is:

```
citylens.html  ──► Smart Route tab calls POST /api/route/analyze
                   to get AI-enriched hazard markers from the CMI backend.
```

When you are ready, we will:
- Replace the placeholder OSRM-only route in `citylens.html`'s `fetchRoutes()` with a call to `https://citizen-mobilty-intelligence.vercel.app/api/route/analyze`
- Overlay the CMI hazard markers (SVG icons) on the CityLens live map
- Merge both map views into one unified UI

---

## Pages & Features

| Page | Access | Description |
|---|---|---|
| Gateway | Public | Choose Citizen or Municipal Admin role |
| Citizen login / signup | Public | PBKDF2 hashed passwords (browser demo), Supabase Auth in production |
| Admin login | Admin | Employee ID + email + password |
| Home | Logged in | Live stats: active, verified, resolved incidents |
| Live Map | Logged in | Leaflet map with incident markers, severity legend, TomTom traffic overlay |
| Report Incident | Citizen | Pin-based location picker, duplicate detection, photo upload, +10 credits |
| Smart Route | Logged in | OSRM road routing with hazard scoring (connects to CMI API next) |
| Admin Center | Admin | Approve access requests, manage incident lifecycle, full audit history |
