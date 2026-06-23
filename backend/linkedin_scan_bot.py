#!/usr/bin/env python3
"""
Monetize360 LinkedIn scan backend.

This backend can:
- determine the active daily scan tiers
- collect posts from a JSON file/stdin or an optional collector API
- normalize and validate LinkedIn post records
- score relevance deterministically
- draft review-ready comments through OpenRouter, with a local fallback
- save scan results
- expose a small HTTP API for later frontend or automation work
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent


def app_data_dir() -> Path:
    override = os.getenv("LINKEDIN_SCAN_DATA_DIR")
    if override:
        return Path(override).expanduser()
    return BASE_DIR / "data"


DATA_DIR = app_data_dir()
SCAN_DIR = DATA_DIR / "scans"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
APIFY_RUNS_URL = "https://api.apify.com/v2/acts/{actor_id}/run-sync-get-dataset-items"


@dataclass(frozen=True)
class WatchTarget:
    tier: int
    kind: str
    name: str
    url: str
    reason: str


@dataclass
class LinkedInPost:
    source_name: str
    source_kind: str = "company"
    source_url: str = ""
    post_url: str = ""
    posted_at: str = ""
    text: str = ""
    reactions: int = 0
    comments: int = 0
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class ScoredPost:
    post: LinkedInPost
    relevance: str
    score: int
    matched_terms: list[str]
    reason: str
    draft_comments: list[str] = field(default_factory=list)
    post_as: str = "Monetize360 page"


@dataclass
class ScanResult:
    scan_date: str
    active_tiers: list[int]
    accounts_scanned: list[str]
    posts_reviewed: int
    high_relevance_posts_found: int
    high: list[ScoredPost]
    medium: list[ScoredPost]
    low: list[ScoredPost]
    collection_status: str
    digest: str


WATCHLIST: list[WatchTarget] = [
    WatchTarget(1, "company", "NVIDIA", "https://www.linkedin.com/company/nvidia", "AI Factory narrative, NCP program, infrastructure at scale"),
    WatchTarget(1, "company", "CoreWeave", "https://www.linkedin.com/company/coreweave", "Leading Neocloud, direct buyer profile"),
    WatchTarget(1, "company", "Firebird AI", "https://www.linkedin.com/company/firebird-ai", "AI Factory builder, active poster"),
    WatchTarget(1, "company", "Nebius", "https://www.linkedin.com/company/nebius", "Pure-play Neocloud, inference-first commercial model"),
    WatchTarget(1, "company", "Together AI", "https://www.linkedin.com/company/together-ai", "Inference + developer platform"),
    WatchTarget(1, "person", "Jensen Huang", "https://www.linkedin.com/in/jenhsunhuang", "Signals where AI Factory market is going"),
    WatchTarget(1, "person", "Kyle Poyar", "https://www.linkedin.com/in/kylepoyar", "Usage-based and consumption pricing"),
    WatchTarget(1, "person", "Tomasz Tunguz", "https://www.linkedin.com/in/tomasztunguz", "AI business models and unit economics"),
    WatchTarget(2, "company", "Groq", "https://www.linkedin.com/company/groq", "Inference at scale, token economics"),
    WatchTarget(2, "company", "Cerebras", "https://www.linkedin.com/company/cerebras-systems", "Enterprise inference, commercial model building"),
    WatchTarget(2, "company", "Mistral AI", "https://www.linkedin.com/company/mistralai", "Model and API provider building commercial stack"),
    WatchTarget(2, "company", "Cohere", "https://www.linkedin.com/company/cohere-ai", "Enterprise LLM, monetization-aware"),
    WatchTarget(2, "company", "Zuora", "https://www.linkedin.com/company/zuora", "Legacy billing incumbent, useful contrast"),
    WatchTarget(2, "company", "Siemens Energy", "https://www.linkedin.com/company/siemens-energy", "Utilities vertical buyer"),
    WatchTarget(2, "company", "JPMorgan Chase AI", "https://www.linkedin.com/company/jpmorganchase", "FSI vertical, large AI spender"),
    WatchTarget(2, "company", "Accenture", "https://www.linkedin.com/company/accenture", "Implements AI infrastructure for enterprise buyers"),
    WatchTarget(3, "company", "Stripe", "https://www.linkedin.com/company/stripe", "Developer billing, usage-based expansion"),
    WatchTarget(3, "company", "Chargebee", "https://www.linkedin.com/company/chargebee", "SaaS billing, consumption model shift"),
    WatchTarget(3, "person", "Andrew Ng", "https://www.linkedin.com/in/andrewyng", "Enterprise AI deployment education"),
    WatchTarget(3, "person", "Mustafa Suleyman", "https://www.linkedin.com/in/mustafa-suleyman", "Enterprise AI strategy signals"),
]


HASHTAGS = {
    1: ["#Neocloud", "#AIFactory", "#AIMonetization", "#TokenFactory"],
    2: ["#UsageBasedPricing", "#ConsumptionBilling", "#OutcomeBased", "#EnterpriseAI", "#GPUCloud", "#AIInfrastructure"],
    3: ["#AIROI", "#AIStrategy", "#AIDeployment", "#GenerativeAI"],
}


HIGH_TERMS = {
    "ai factory": 5,
    "neocloud": 5,
    "token economics": 5,
    "usage-based": 4,
    "usage based": 4,
    "consumption billing": 4,
    "outcome-based": 4,
    "outcome based": 4,
    "gpu cloud": 4,
    "inference": 3,
    "pricing": 3,
    "monetization": 4,
    "unit economics": 4,
    "margin": 3,
    "revenue infrastructure": 5,
    "billing": 3,
    "enterprise ai": 3,
    "ai roi": 4,
    "compute": 2,
    "nvidia": 2,
    "ncp": 3,
}

LOW_TERMS = {
    "hiring",
    "we're hiring",
    "culture",
    "award",
    "birthday",
    "anniversary",
    "team offsite",
    "webinar",
    "conference booth",
}


def load_env(env_path: Path = BASE_DIR / ".env") -> None:
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def json_response(handler: BaseHTTPRequestHandler, status: int, body: Any) -> None:
    payload = json.dumps(body, ensure_ascii=False, indent=2).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(payload)))
    handler.end_headers()
    handler.wfile.write(payload)


def scan_date(value: str | None) -> dt.date:
    if value:
        return dt.date.fromisoformat(value)
    return dt.date.today()


def active_tiers(day: dt.date) -> list[int]:
    tiers = [1]
    if day.weekday() == 0:
        tiers.append(2)
    if day.day == 1:
        tiers.append(3)
    return tiers


def targets_for(tiers: list[int]) -> list[WatchTarget]:
    return [target for target in WATCHLIST if target.tier in tiers]


def format_watchlist(targets: list[WatchTarget], tiers: list[int]) -> str:
    rows = []
    for target in targets:
        rows.append(f"- Tier {target.tier} {target.kind}: {target.name} ({target.url}) - {target.reason}")
    for tier in tiers:
        rows.append(f"- Tier {tier} hashtags: {', '.join(HASHTAGS[tier])}")
    return "\n".join(rows)


def coerce_int(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    text = str(value).replace(",", "").strip()
    if text.endswith("+"):
        text = text[:-1]
    try:
        return int(float(text))
    except ValueError:
        return 0


def normalize_post(item: dict[str, Any]) -> LinkedInPost:
    source_name = (
        item.get("source_name")
        or item.get("author")
        or item.get("authorName")
        or item.get("company")
        or item.get("name")
        or "Unknown"
    )
    source_kind = item.get("source_kind") or item.get("kind") or item.get("authorType") or "company"
    source_url = item.get("source_url") or item.get("authorUrl") or item.get("companyUrl") or ""
    post_url = item.get("post_url") or item.get("url") or item.get("postUrl") or ""
    posted_at = item.get("posted_at") or item.get("time") or item.get("date") or item.get("postedAt") or ""
    text = item.get("text") or item.get("content") or item.get("postText") or item.get("commentary") or ""
    reactions = item.get("reactions") or item.get("likes") or item.get("numLikes") or item.get("reactionCount")
    comments = item.get("comments") or item.get("numComments") or item.get("commentCount")

    return LinkedInPost(
        source_name=str(source_name).strip() or "Unknown",
        source_kind=str(source_kind).strip().lower() or "company",
        source_url=str(source_url).strip(),
        post_url=str(post_url).strip(),
        posted_at=str(posted_at).strip(),
        text=str(text).strip(),
        reactions=coerce_int(reactions),
        comments=coerce_int(comments),
        raw=item,
    )


def read_posts(path: Path | None) -> list[LinkedInPost]:
    if path:
        raw = path.read_text(encoding="utf-8")
    elif not sys.stdin.isatty():
        raw = sys.stdin.read()
    else:
        raw = "[]"

    if not raw.strip():
        return []

    data = json.loads(raw)
    if isinstance(data, dict) and "posts" in data:
        data = data["posts"]
    if not isinstance(data, list):
        raise ValueError("Posts JSON must be a list, or an object with a 'posts' list.")
    if not all(isinstance(item, dict) for item in data):
        raise ValueError("Every post must be a JSON object.")
    return [normalize_post(item) for item in data]


def collect_posts_with_apify(targets: list[WatchTarget], tiers: list[int], day: dt.date) -> tuple[list[LinkedInPost], str]:
    token = os.getenv("APIFY_TOKEN")
    actor_id = os.getenv("APIFY_LINKEDIN_ACTOR_ID")
    if not token or not actor_id:
        return [], "No collector configured. Set APIFY_TOKEN and APIFY_LINKEDIN_ACTOR_ID, or provide --posts-json."

    payload = {
        "urls": [target.url for target in targets],
        "hashtags": [tag for tier in tiers for tag in HASHTAGS[tier]],
        "dateFrom": day.isoformat(),
        "dateTo": day.isoformat(),
        "resultsLimit": coerce_int(os.getenv("COLLECTOR_RESULTS_LIMIT") or 50),
    }
    query = urllib.parse.urlencode({"token": token})
    request = urllib.request.Request(
        f"{APIFY_RUNS_URL.format(actor_id=urllib.parse.quote(actor_id, safe=''))}?{query}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Collector HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not reach collector: {exc.reason}") from exc

    if not isinstance(data, list):
        raise RuntimeError("Collector returned an unexpected response. Expected a JSON list.")
    return [normalize_post(item) for item in data if isinstance(item, dict)], f"Collected through configured Apify actor for {day.isoformat()}."


def parse_post_date(value: str) -> dt.date | None:
    text = value.strip()
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        return dt.datetime.fromisoformat(normalized).date()
    except ValueError:
        pass
    try:
        return dt.date.fromisoformat(text[:10])
    except ValueError:
        pass

    match = re.search(r"\b(\d{4})[-/](\d{1,2})[-/](\d{1,2})\b", text)
    if match:
        year, month, day = map(int, match.groups())
        try:
            return dt.date(year, month, day)
        except ValueError:
            return None

    match = re.search(r"\b(\d{1,2})[-/](\d{1,2})[-/](\d{4})\b", text)
    if match:
        month, day, year = map(int, match.groups())
        try:
            return dt.date(year, month, day)
        except ValueError:
            return None

    return None


def filter_posts_for_day(posts: list[LinkedInPost], day: dt.date) -> tuple[list[LinkedInPost], int]:
    filtered = []
    skipped = 0
    for post in posts:
        parsed = parse_post_date(post.posted_at)
        if parsed is None or parsed == day:
            filtered.append(post)
        else:
            skipped += 1
    return filtered, skipped


def score_post(post: LinkedInPost) -> ScoredPost:
    text = f"{post.source_name} {post.text}".lower()
    matched: list[str] = []
    score = 0
    for term, weight in HIGH_TERMS.items():
        if term in text:
            matched.append(term)
            score += weight

    low_hits = [term for term in LOW_TERMS if term in text]
    if low_hits and score < 5:
        relevance = "LOW"
        reason = f"Mostly low-priority content: {', '.join(low_hits[:3])}."
    elif score >= 7:
        relevance = "HIGH"
        reason = f"Direct monetization or AI infrastructure signal: {', '.join(matched[:4])}."
    elif score >= 3:
        relevance = "MEDIUM"
        reason = f"Related enterprise AI or compute signal: {', '.join(matched[:4])}."
    else:
        relevance = "LOW"
        reason = "No strong monetization, pricing, AI infrastructure, or ROI signal found."

    post_as = "Deborah" if post.source_kind == "person" else "Monetize360 page"
    return ScoredPost(post=post, relevance=relevance, score=score, matched_terms=matched, reason=reason, post_as=post_as)


def local_comment_templates(scored: ScoredPost) -> list[str]:
    name = scored.post.source_name
    return [
        (
            "The bigger shift is that AI infrastructure is becoming a revenue design problem, "
            "not only a capacity planning problem. As usage grows, pricing, billing, and margin "
            "controls have to evolve at the same speed as compute demand. Which teams will own "
            "that operating model?"
        ),
        (
            f"{name} is pointing at a market where technical scale and commercial precision are "
            "starting to converge. The winners will understand cost-to-serve, customer outcomes, "
            "and monetization paths before the invoice is generated. That is where AI scale turns "
            "into durable revenue."
        ),
    ]


def build_prompt(day: dt.date, tiers: list[int], targets: list[WatchTarget], scored_posts: list[ScoredPost]) -> str:
    posts_json = json.dumps(
        [
            {
                "post": asdict(item.post),
                "relevance": item.relevance,
                "score": item.score,
                "matched_terms": item.matched_terms,
                "reason": item.reason,
                "post_as": item.post_as,
            }
            for item in scored_posts
        ],
        ensure_ascii=False,
        indent=2,
    )
    tier_text = "+".join(str(tier) for tier in tiers)

    return f"""
You are running the Monetize360 Daily LinkedIn Scan.

Company context:
- Company: Monetize360
- Website: monetize360.ai
- Tagline: The only revenue platform built for outcome-based pricing and everything before it.
- One-liner: We build revenue infrastructure for the AI economy.
- Products: RevenueOS, Mbrix, M360 Agents
- Target buyers: Neoclouds, AI Factories, NVIDIA NCP partners, token factories, inference platforms, and large enterprises investing heavily in AI infrastructure.
- LinkedIn tone: confident and authoritative, never salesy. Add genuine insight, provoke useful questions, and sound like a senior enterprise monetization practitioner.

Active scan date: {day.isoformat()}
Active tier: {tier_text}

Watchlist for this run:
{format_watchlist(targets, tiers)}

Relevant posts selected by backend scoring:
{posts_json}

Comment rules:
- Draft comments only for HIGH posts, and optionally MEDIUM posts if the angle is unusually strong.
- Include each selected post's relevance score in the digest as "Score: N".
- Each comment must be 2-4 sentences.
- Start with insight, not "Great post", "Congrats", or generic agreement.
- Connect naturally to monetization, revenue infrastructure, unit economics, billing, pricing, or margin control.
- Do not mention Monetize360 by name.
- Do not sound like an ad.
- Do not mention competitors by name.
- End with a question, provocative statement, or forward-looking observation.
- Suggest posting as Monetize360 company page for company/Neocloud/AI Factory posts.
- Suggest posting as Deborah for individual posts.

Return a concise review-ready digest using the selected posts.
""".strip()


def call_openrouter(api_key: str, model: str, prompt: str, max_tokens: int) -> str:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are a precise B2B LinkedIn analyst and comment-writing assistant."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.45,
        "max_tokens": max_tokens,
    }
    request = urllib.request.Request(
        OPENROUTER_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://monetize360.ai",
            "X-Title": "Monetize360 LinkedIn Scan Bot",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenRouter HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not reach OpenRouter: {exc.reason}") from exc

    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"Unexpected OpenRouter response: {json.dumps(data)[:1000]}") from exc


def generate_digest(result: ScanResult) -> str:
    lines = [
        f"LinkedIn Scan - {result.scan_date} - Tier {'+'.join(map(str, result.active_tiers))}",
        f"Accounts scanned: {', '.join(result.accounts_scanned)}",
        f"Posts reviewed: {result.posts_reviewed}",
        f"High relevance posts found: {result.high_relevance_posts_found}",
        "",
        "HIGH",
    ]

    if not result.high:
        lines.append("No high relevance posts found.")
    for item in result.high:
        lines.extend(render_scored_post(item, include_comments=True))

    lines.extend(["", "MEDIUM relevance - noted but not drafted:"])
    if not result.medium:
        lines.append("- None")
    for item in result.medium:
        lines.append(f"- {item.post.source_name} (score {item.score}): {summary(item.post.text)}")

    lines.extend(["", "Skipped low relevance:"])
    if not result.low:
        lines.append("- None")
    for item in result.low[:25]:
        lines.append(f"- {item.post.source_name} (score {item.score}): {item.reason}")

    if result.posts_reviewed == 0:
        lines.extend(["", result.collection_status])
    return "\n".join(lines)


def render_scored_post(item: ScoredPost, include_comments: bool) -> list[str]:
    post = item.post
    lines = [
        f"{post.source_name} - {post.posted_at or 'time not provided'}",
        f"Score: {item.score}",
        f"Post summary: {summary(post.text)}",
        f"Why it's relevant: {item.reason}",
        f"Engagement: {post.reactions} reactions, {post.comments} comments",
    ]
    if post.post_url:
        lines.append(f"URL: {post.post_url}")
    if include_comments:
        comments = item.draft_comments or local_comment_templates(item)
        lines.extend(["Draft Comment A:", comments[0], "Draft Comment B:", comments[1], f"Post as: {item.post_as}", ""])
    return lines


def summary(text: str, max_chars: int = 180) -> str:
    cleaned = " ".join(text.split())
    if not cleaned:
        return "No post text provided."
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[: max_chars - 3].rstrip() + "..."


def run_scan(
    day: dt.date,
    posts: list[LinkedInPost] | None = None,
    use_collector: bool = False,
    use_ai: bool = True,
    max_tokens: int = 3500,
    save: bool = True,
) -> ScanResult:
    tiers = active_tiers(day)
    targets = targets_for(tiers)
    collection_status = "Posts provided by request."

    if posts is None and use_collector:
        posts, collection_status = collect_posts_with_apify(targets, tiers, day)
    elif posts is None:
        posts = []
        collection_status = "Post collection still needs to be connected. Provide --posts-json or configure APIFY_TOKEN and APIFY_LINKEDIN_ACTOR_ID."

    posts, skipped_by_date = filter_posts_for_day(posts, day)
    if skipped_by_date:
        collection_status = f"{collection_status} Filtered out {skipped_by_date} post(s) outside {day.isoformat()}."

    scored = [score_post(post) for post in posts]
    high = [item for item in scored if item.relevance == "HIGH"]
    medium = [item for item in scored if item.relevance == "MEDIUM"]
    low = [item for item in scored if item.relevance == "LOW"]

    for item in high:
        item.draft_comments = local_comment_templates(item)

    result = ScanResult(
        scan_date=day.isoformat(),
        active_tiers=tiers,
        accounts_scanned=[target.name for target in targets],
        posts_reviewed=len(posts),
        high_relevance_posts_found=len(high),
        high=high,
        medium=medium,
        low=low,
        collection_status=collection_status,
        digest="",
    )

    api_key = os.getenv("API_KEY") or os.getenv("OPENROUTER_API_KEY")
    model = os.getenv("MODEL") or os.getenv("OPENROUTER_MODEL") or "nvidia/nemotron-3-ultra-550b-a55b:free"
    if use_ai and api_key and high:
        prompt = build_prompt(day, tiers, targets, high + medium)
        try:
            result.digest = call_openrouter(api_key, model, prompt, max_tokens)
        except RuntimeError as exc:
            result.digest = generate_digest(result) + f"\n\nAI drafting failed, local fallback used: {exc}"
    else:
        result.digest = generate_digest(result)

    if save:
        save_scan(result)
    return result


def save_scan(result: ScanResult) -> Path:
    SCAN_DIR.mkdir(parents=True, exist_ok=True)
    path = SCAN_DIR / f"{result.scan_date}.json"
    path.write_text(json.dumps(scan_to_dict(result), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def scan_to_dict(result: ScanResult) -> dict[str, Any]:
    return asdict(result)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Monetize360 LinkedIn scan backend.")
    subparsers = parser.add_subparsers(dest="command")

    scan_parser = subparsers.add_parser("scan", help="Run a scan and print the digest.")
    scan_parser.add_argument("--posts-json", type=Path, help="Path to JSON file containing collected LinkedIn posts.")
    scan_parser.add_argument("--date", help="Scan date in YYYY-MM-DD format. Defaults to today.")
    scan_parser.add_argument("--collector", action="store_true", help="Collect through configured collector API.")
    scan_parser.add_argument("--no-ai", action="store_true", help="Use deterministic local draft comments only.")
    scan_parser.add_argument("--no-save", action="store_true", help="Do not save scan result JSON.")
    scan_parser.add_argument("--max-tokens", type=int, default=3500, help="Maximum response tokens for the model.")

    plan_parser = subparsers.add_parser("plan", help="Print today's scan plan.")
    plan_parser.add_argument("--date", help="Scan date in YYYY-MM-DD format. Defaults to today.")

    serve_parser = subparsers.add_parser("serve", help="Start the local backend HTTP API.")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8000)

    parser.add_argument("--posts-json", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--date", help=argparse.SUPPRESS)
    parser.add_argument("--dry-run", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--max-tokens", type=int, default=3500, help=argparse.SUPPRESS)
    return parser.parse_args()


class ScanHandler(BaseHTTPRequestHandler):
    server_version = "Monetize360LinkedInBackend/1.0"

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)

        if parsed.path == "/health":
            json_response(self, 200, {"ok": True})
            return
        if parsed.path == "/watchlist":
            day = scan_date(one(query.get("date")))
            tiers = active_tiers(day)
            json_response(self, 200, {"date": day.isoformat(), "active_tiers": tiers, "targets": [asdict(item) for item in targets_for(tiers)]})
            return
        if parsed.path == "/scan":
            day = scan_date(one(query.get("date")))
            result = run_scan(day, posts=None, use_collector=False, use_ai=False, save=False)
            json_response(self, 200, scan_to_dict(result))
            return
        json_response(self, 404, {"error": "Not found"})

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/scan":
            json_response(self, 404, {"error": "Not found"})
            return

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(content_length).decode("utf-8") or "{}")
            day = scan_date(payload.get("date"))
            raw_posts = payload.get("posts")
            posts = [normalize_post(item) for item in raw_posts] if isinstance(raw_posts, list) else None
            result = run_scan(
                day=day,
                posts=posts,
                use_collector=bool(payload.get("collector")),
                use_ai=not bool(payload.get("no_ai")),
                save=not bool(payload.get("no_save")),
            )
            json_response(self, 200, scan_to_dict(result))
        except Exception as exc:
            json_response(self, 400, {"error": str(exc)})

    def log_message(self, format: str, *args: Any) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), format % args))


def one(values: list[str] | None) -> str | None:
    if not values:
        return None
    return values[0]


def print_plan(day: dt.date) -> None:
    tiers = active_tiers(day)
    targets = targets_for(tiers)
    print(f"LinkedIn Scan Plan - {day.isoformat()} - Tier {'+'.join(map(str, tiers))}")
    print(format_watchlist(targets, tiers))


def main() -> int:
    load_env()
    args = parse_args()
    command = args.command or ("plan" if args.dry_run else "scan")

    if command == "plan":
        print_plan(scan_date(args.date))
        return 0

    if command == "serve":
        server = ThreadingHTTPServer((args.host, args.port), ScanHandler)
        print(f"Monetize360 LinkedIn backend running at http://{args.host}:{args.port}")
        print("Endpoints: GET /health, GET /watchlist, GET /scan, POST /scan")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down.")
        return 0

    posts = read_posts(args.posts_json) if args.posts_json or not sys.stdin.isatty() else None
    result = run_scan(
        day=scan_date(args.date),
        posts=posts,
        use_collector=getattr(args, "collector", False),
        use_ai=not getattr(args, "no_ai", False),
        max_tokens=args.max_tokens,
        save=not getattr(args, "no_save", False),
    )
    print(result.digest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
