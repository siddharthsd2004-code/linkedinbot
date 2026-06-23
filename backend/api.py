from __future__ import annotations

import datetime as dt
import html
import json
import os
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

try:
    from .linkedin_scan_bot import (
        SCAN_DIR,
        WATCHLIST,
        active_tiers,
        load_env,
        normalize_post,
        run_scan,
        scan_date,
        targets_for,
    )
except ImportError:
    from linkedin_scan_bot import (
        SCAN_DIR,
        WATCHLIST,
        active_tiers,
        load_env,
        normalize_post,
        run_scan,
        scan_date,
        targets_for,
    )


router = APIRouter(prefix="/api", tags=["linkedin-scan"])


class ScanRequest(BaseModel):
    date: str | None = None
    posts: list[dict[str, Any]] | None = None
    collector: bool = False
    no_ai: bool = False
    no_save: bool = False
    max_tokens: int = Field(default=3500, ge=1, le=12000)


@router.get("/health")
def health() -> dict[str, bool]:
    return {"ok": True}


@router.get("/config")
def config() -> dict[str, bool]:
    return {
        "collector_configured": bool(os.getenv("APIFY_TOKEN") and os.getenv("APIFY_LINKEDIN_ACTOR_ID")),
        "openrouter_configured": bool(os.getenv("API_KEY") or os.getenv("OPENROUTER_API_KEY")),
    }


@router.get("/watchlist")
def watchlist(date: str | None = None) -> dict[str, Any]:
    day = scan_date(date)
    tiers = active_tiers(day)
    return {
        "date": day.isoformat(),
        "active_tiers": tiers,
        "targets": [asdict(target) for target in targets_for(tiers)],
        "all_targets": [asdict(target) for target in WATCHLIST],
    }


@router.get("/companies")
def companies() -> dict[str, Any]:
    return {
        "companies": [
            asdict(target)
            for target in WATCHLIST
            if target.kind == "company"
        ]
    }


@router.get("/reports")
def reports() -> dict[str, Any]:
    SCAN_DIR.mkdir(parents=True, exist_ok=True)
    items = []
    for path in sorted(SCAN_DIR.glob("*.json"), reverse=True):
        report = read_report_file(path)
        items.append(
            {
                "date": report.get("scan_date") or path.stem,
                "active_tiers": report.get("active_tiers", []),
                "posts_reviewed": report.get("posts_reviewed", 0),
                "high_relevance_posts_found": report.get("high_relevance_posts_found", 0),
                "download_url": f"/api/reports/{path.stem}/download",
            }
        )
    return {"reports": items}


@router.get("/reports/{report_date}")
def report_detail(report_date: str) -> dict[str, Any]:
    path = report_path(report_date)
    return read_report_file(path)


@router.get("/reports/{report_date}/download")
def download_report(report_date: str) -> Response:
    path = report_path(report_date)
    report = read_report_file(path)
    filename = f"linkedin-scan-{path.stem}.doc"
    return Response(
        content=report_to_word_html(report),
        media_type="application/msword",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/scan")
def scan(payload: ScanRequest) -> dict[str, Any]:
    try:
        if payload.collector and (not os.getenv("APIFY_TOKEN") or not os.getenv("APIFY_LINKEDIN_ACTOR_ID")):
            raise HTTPException(
                status_code=400,
                detail="LinkedIn collector is not configured. Add APIFY_TOKEN and APIFY_LINKEDIN_ACTOR_ID to backend/.env, or submit posts manually.",
            )
        day = scan_date(payload.date)
        posts = [normalize_post(item) for item in payload.posts] if payload.posts is not None else None
        result = run_scan(
            day=day,
            posts=posts,
            use_collector=payload.collector,
            use_ai=not payload.no_ai,
            max_tokens=payload.max_tokens,
            save=not payload.no_save,
        )
        data = asdict(result)
        data["download_url"] = f"/api/reports/{result.scan_date}/download" if not payload.no_save else None
        return data
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def create_app() -> FastAPI:
    load_env()
    app = FastAPI(title="Monetize360 LinkedIn Scan API")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_cors_origins(),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)
    frontend_dist = BASE_FRONTEND_DIST()
    if frontend_dist.exists():
        app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")
    return app


def BASE_FRONTEND_DIST() -> Path:
    return Path(__file__).resolve().parent.parent / "frontend" / "dist"


def allowed_cors_origins() -> list[str]:
    configured = os.getenv("CORS_ORIGINS", "")
    origins = [
        origin.strip().rstrip("/")
        for origin in configured.split(",")
        if origin.strip()
    ]
    return [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "null",
        *origins,
    ]


def report_path(report_date: str) -> Path:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", report_date):
        raise HTTPException(status_code=400, detail="Report date must be YYYY-MM-DD.")
    path = SCAN_DIR / f"{report_date}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Report not found.")
    return path


def read_report_file(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail=f"Invalid report JSON: {path.name}") from exc


def report_to_word_html(report: dict[str, Any]) -> str:
    title = f"LinkedIn Scan - {report.get('scan_date', dt.date.today().isoformat())}"
    digest = html.escape(str(report.get("digest") or "No digest available.")).replace("\n", "<br>")
    accounts = html.escape(", ".join(report.get("accounts_scanned", [])))
    active_tiers = html.escape("+".join(str(tier) for tier in report.get("active_tiers", [])) or "-")
    posts_reviewed = html.escape(str(report.get("posts_reviewed", 0)))
    high_count = html.escape(str(report.get("high_relevance_posts_found", 0)))
    collection_status = html.escape(str(report.get("collection_status") or "No collection status available."))
    scored_posts = scored_posts_to_html(report)

    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>{html.escape(title)}</title>
  <style>
    body {{ font-family: Arial, sans-serif; color: #111827; line-height: 1.45; }}
    h1 {{ font-size: 24px; margin-bottom: 8px; }}
    h2 {{ border-bottom: 1px solid #d1d5db; font-size: 18px; margin-top: 28px; padding-bottom: 4px; }}
    h3 {{ font-size: 15px; margin-bottom: 8px; }}
    .meta {{ margin-bottom: 18px; color: #374151; }}
    .summary-table {{ border-collapse: collapse; margin-bottom: 18px; width: 100%; }}
    .summary-table td {{ border: 1px solid #d1d5db; padding: 8px; vertical-align: top; }}
    .summary-table td:first-child {{ background: #f3f4f6; font-weight: 700; width: 170px; }}
    .digest {{ white-space: normal; word-break: break-word; }}
    .note {{ background: #f9fafb; border: 1px solid #d1d5db; margin: 10px 0 16px; padding: 10px; }}
    .post {{ border: 1px solid #d1d5db; margin: 12px 0 18px; padding: 12px; }}
    .post-title {{ font-size: 15px; font-weight: 700; margin-bottom: 6px; }}
    .score {{ font-weight: 700; color: #0f766e; }}
    .label {{ color: #374151; font-weight: 700; }}
    .comment {{ background: #f9fafb; border-left: 3px solid #9ca3af; margin: 8px 0; padding: 8px; }}
    .muted {{ color: #4b5563; }}
  </style>
</head>
<body>
  <h1>{html.escape(title)}</h1>
  <h2>Summary</h2>
  <table class="summary-table">
    <tr><td>Scan date</td><td>{html.escape(str(report.get("scan_date") or ""))}</td></tr>
    <tr><td>Active tiers</td><td>{active_tiers}</td></tr>
    <tr><td>Posts reviewed</td><td>{posts_reviewed}</td></tr>
    <tr><td>High relevance posts</td><td>{high_count}</td></tr>
    <tr><td>Collection status</td><td>{collection_status}</td></tr>
    <tr><td>Accounts scanned</td><td>{accounts}</td></tr>
  </table>
  <h2>How To Read Scores</h2>
  <div class="note">
    Scores are weighted relevance points, not a percentage. A higher score means the post matched more Monetize360-relevant themes such as AI factories, usage-based pricing, consumption billing, inference, monetization, billing, margin, or ROI.
    <br><br>
    <strong>7 or higher:</strong> High priority for comment drafting.<br>
    <strong>3 to 6:</strong> Relevant enough to watch, but usually not drafted.<br>
    <strong>Below 3:</strong> Low relevance for brand engagement.
  </div>
  <h2>Review Digest</h2>
  <div class="digest">{digest}</div>
  {scored_posts}
</body>
</html>"""


def scored_posts_to_html(report: dict[str, Any]) -> str:
    sections = [
        ("High Posts", report.get("high", [])),
        ("Medium Posts", report.get("medium", [])),
        ("Low Posts", report.get("low", [])),
    ]
    parts = ["<h2>Post Scores</h2>"]
    found = False
    for title, posts in sections:
        if not posts:
            continue
        found = True
        parts.append(f"<h3>{html.escape(title)}</h3>")
        for item in posts:
            post = item.get("post", {}) if isinstance(item, dict) else {}
            source_name = html.escape(str(post.get("source_name") or "Unknown"))
            source_kind = html.escape(str(post.get("source_kind") or ""))
            posted_at = html.escape(str(post.get("posted_at") or "time not provided"))
            score = html.escape(str(item.get("score", 0) if isinstance(item, dict) else 0))
            relevance = html.escape(str(item.get("relevance", "") if isinstance(item, dict) else ""))
            reason = html.escape(str(item.get("reason", "") if isinstance(item, dict) else ""))
            matched_terms = ", ".join(item.get("matched_terms", []) if isinstance(item, dict) else [])
            matched_terms = html.escape(matched_terms or "None")
            reactions = html.escape(str(post.get("reactions", 0)))
            comments = html.escape(str(post.get("comments", 0)))
            post_as = html.escape(str(item.get("post_as", "") if isinstance(item, dict) else ""))
            post_text = html.escape(str(post.get("text") or "No post text provided.")).replace("\n", "<br>")
            post_url = html.escape(str(post.get("post_url") or ""))
            source_url = html.escape(str(post.get("source_url") or ""))
            draft_comments = item.get("draft_comments", []) if isinstance(item, dict) else []
            url_html = f'<div><span class="label">Post URL:</span> {post_url}</div>' if post_url else ""
            source_url_html = f'<div><span class="label">Source URL:</span> {source_url}</div>' if source_url else ""
            comments_html = ""
            if draft_comments:
                rendered_comments = []
                for index, comment in enumerate(draft_comments, start=1):
                    rendered = html.escape(str(comment)).replace("\n", "<br>")
                    rendered_comments.append(f'<div class="comment"><span class="label">Draft comment {index}:</span><br>{rendered}</div>')
                comments_html = "".join(rendered_comments)
            parts.append(
                "<div class=\"post\">"
                f"<div class=\"post-title\">{source_name} - {posted_at}</div>"
                f"<div class=\"score\">Score: {score}</div>"
                f"<div><span class=\"label\">Relevance:</span> {relevance}</div>"
                f"<div><span class=\"label\">Source type:</span> {source_kind}</div>"
                f"<div><span class=\"label\">Reason:</span> {reason}</div>"
                f"<div><span class=\"label\">Matched terms:</span> {matched_terms}</div>"
                f"<div><span class=\"label\">Engagement:</span> {reactions} reactions, {comments} comments</div>"
                f"<div><span class=\"label\">Post as:</span> {post_as}</div>"
                f"{url_html}"
                f"{source_url_html}"
                f"<p><span class=\"label\">Post text:</span><br>{post_text}</p>"
                f"{comments_html}"
                "</div>"
            )
    if not found:
        parts.append("<p>No scored posts in this report.</p>")
    return "\n".join(parts)


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("api:app", host="127.0.0.1", port=8000, reload=True)
