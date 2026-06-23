# Monetize360 LinkedIn Scan Backend

This backend runs the Monetize360 LinkedIn scan workflow end to end once posts are available.

It supports:

- daily tier planning
- JSON/stdin post intake
- optional external collection through an Apify actor
- relevance scoring
- comment drafting through OpenRouter
- local fallback comments when AI is disabled or unavailable
- saved scan JSON files in `backend/data/scans`
- a FastAPI router for the React frontend or automation

## Setup

Create `backend/.env`:

```env
API_KEY=your_openrouter_key
MODEL=nvidia/nemotron-3-ultra-550b-a55b:free

# Optional collector integration
APIFY_TOKEN=your_apify_token
APIFY_LINKEDIN_ACTOR_ID=your_actor_id
COLLECTOR_RESULTS_LIMIT=50
```

Install API dependencies with `uv` from the project root:

```powershell
uv sync
```

This creates and manages the project environment in `.venv`.

PowerShell activation may be blocked by Windows execution policy. The `cmd.exe` activation path works:

```cmd
.venv\Scripts\activate.bat
```

You can also run backend commands without manual activation:

```powershell
$env:UV_CACHE_DIR="D:\linkedin\.uv-cache"
uv run python backend\linkedin_scan_bot.py plan
uv run uvicorn backend.api:app --reload --host 127.0.0.1 --port 8000
```

If `.venv` is already activated, use Python directly:

```powershell
python -m uvicorn backend.api:app --reload --host 127.0.0.1 --port 8000
```

## CLI

Print the active scan plan:

```powershell
python backend\linkedin_scan_bot.py plan
```

Scan posts from a JSON file:

```powershell
python backend\linkedin_scan_bot.py scan --posts-json posts.json
```

Scan posts from stdin without calling AI:

```powershell
Get-Content posts.json | python backend\linkedin_scan_bot.py scan --no-ai
```

Run the optional collector:

```powershell
python backend\linkedin_scan_bot.py scan --collector
```

Start the FastAPI backend:

```powershell
python -m uvicorn backend.api:app --reload --host 127.0.0.1 --port 8000
```

## API

- `GET /api/health`
- `GET /api/watchlist?date=YYYY-MM-DD`
- `GET /api/companies`
- `GET /api/reports`
- `GET /api/reports/{date}`
- `GET /api/reports/{date}/download`
- `POST /api/scan`

Example `POST /scan` body:

```json
{
  "date": "2026-06-19",
  "no_ai": true,
  "posts": [
    {
      "source_name": "CoreWeave",
      "source_kind": "company",
      "posted_at": "today",
      "text": "AI Factory demand is pushing GPU cloud inference platforms toward usage-based pricing.",
      "reactions": 120,
      "comments": 14,
      "post_url": "https://linkedin.com/posts/example"
    }
  ]
}
```

## Frontend

From the project root:

```powershell
cd frontend
npm install
npm run dev
```

The React app expects the API at `http://127.0.0.1:8000`. To change that, create `frontend/.env`:

```env
VITE_API_BASE=http://127.0.0.1:8000
```

## Free Manual Collection

If you do not want to use a paid collector, use the manual JSON box in the frontend.

One helper script is available at:

```text
frontend/public/linkedin-visible-posts-collector.js
```

Use it only on LinkedIn pages you manually open and can access in your own browser:

1. Open a LinkedIn company/person page or search page.
2. Scroll until the posts you want are visible.
3. Open browser DevTools.
4. Paste the contents of `frontend/public/linkedin-visible-posts-collector.js` into the Console.
5. The script copies visible posts as JSON.
6. Paste that JSON into the frontend `Posts JSON` box.
7. Pick the scan date and click `Run Scan`.

This helper does not log in for you, crawl pages in the background, bypass CAPTCHAs, rotate proxies, or evade LinkedIn controls.

## Post Schema

The normalizer accepts several common field aliases, but these fields are preferred:

```json
{
  "source_name": "Company or person name",
  "source_kind": "company",
  "source_url": "https://www.linkedin.com/company/example",
  "post_url": "https://www.linkedin.com/posts/example",
  "posted_at": "2026-06-19T10:00:00Z",
  "text": "Post text",
  "reactions": 0,
  "comments": 0
}
```

LinkedIn does not provide simple public unauthenticated post collection. The backend therefore treats collection as a replaceable provider. Today it can call a configured Apify actor; later we can swap that for another approved LinkedIn data source without changing the scanner, scorer, or API.
