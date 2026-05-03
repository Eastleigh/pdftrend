import csv
import io
import secrets
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

import bcrypt
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware

from .database import get_db, init_db

# ─── Rate Limiting ────────────────────────────────────────────────

class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: FastAPI, requests_per_minute: int = 60):
        super().__init__(app)
        self.requests_per_minute = requests_per_minute
        self.requests: dict[str, list[float]] = defaultdict(list)

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        client_ip = request.client.host if request.client else "unknown"
        now = time.time()
        window = now - 60
        self.requests[client_ip] = [t for t in self.requests[client_ip] if t > window]
        if not self.requests[client_ip]:
            del self.requests[client_ip]
        if len(self.requests.get(client_ip, [])) >= self.requests_per_minute:
            return JSONResponse(
                {"detail": "Too many requests. Please try again later."}, status_code=429
            )
        self.requests[client_ip].append(now)
        return await call_next(request)


# ─── App Setup ────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    await init_db()
    yield


app = FastAPI(title="PDF Trend Finder", version="2.0.0", lifespan=lifespan)
app.add_middleware(RateLimitMiddleware, requests_per_minute=60)

BASE_DIR = Path(__file__).parent
_static_dir = BASE_DIR / "static"
_static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

SESSION_COOKIE = "ptf_session"
RECENT_COOKIE = "ptf_recent"
SESSION_STORE: dict[str, int] = {}
_trend_executor = ThreadPoolExecutor(max_workers=2)

# ─── Region support ──────────────────────────────────────────────

SUPPORTED_REGIONS = [
    {"code": "US", "name": "United States", "pn": "united_states"},
    {"code": "GB", "name": "United Kingdom", "pn": "united_kingdom"},
    {"code": "CA", "name": "Canada", "pn": "canada"},
    {"code": "AU", "name": "Australia", "pn": "australia"},
    {"code": "IN", "name": "India", "pn": "india"},
    {"code": "DE", "name": "Germany", "pn": "germany"},
    {"code": "FR", "name": "France", "pn": "france"},
    {"code": "BR", "name": "Brazil", "pn": "brazil"},
]

NICHE_CATEGORIES = [
    {"id": "health", "name": "Health & Wellness", "icon": "heart-pulse",
     "queries": ["weight loss tips", "mental health guide", "healthy meal prep",
                 "home workout plan", "sleep better naturally"]},
    {"id": "finance", "name": "Personal Finance", "icon": "wallet",
     "queries": ["budgeting for beginners", "passive income ideas", "how to save money",
                 "investing for beginners", "side hustle ideas"]},
    {"id": "tech", "name": "Technology", "icon": "cpu",
     "queries": ["AI tools for beginners", "learn python programming", "cybersecurity basics",
                 "no-code app builder", "chatgpt prompts"]},
    {"id": "business", "name": "Business & Marketing", "icon": "briefcase",
     "queries": ["social media marketing", "email marketing guide", "start online business",
                 "content marketing strategy", "branding tips"]},
    {"id": "education", "name": "Education & Learning", "icon": "book-open",
     "queries": ["study techniques", "speed reading", "learn a new language",
                 "online course creation", "note taking methods"]},
    {"id": "lifestyle", "name": "Lifestyle & Self-Help", "icon": "sparkles",
     "queries": ["morning routine", "productivity hacks", "minimalism guide",
                 "journaling prompts", "time management"]},
    {"id": "parenting", "name": "Parenting & Family", "icon": "baby",
     "queries": ["toddler activities", "homeschool curriculum", "parenting tips",
                 "family budget planner", "kids meal ideas"]},
    {"id": "creative", "name": "Creative & DIY", "icon": "palette",
     "queries": ["canva design tips", "photography for beginners", "DIY home decor",
                 "digital art tutorial", "craft business ideas"]},
]


def get_current_user_id(request: Request) -> int | None:
    token = request.cookies.get(SESSION_COOKIE)
    if token and token in SESSION_STORE:
        return SESSION_STORE[token]
    return None


def _get_session_id(request: Request) -> str:
    sid = request.cookies.get(RECENT_COOKIE)
    if sid:
        return sid
    return secrets.token_urlsafe(16)


# ─── Pages ────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def home(request: Request) -> HTMLResponse:
    user_id = get_current_user_id(request)
    return templates.TemplateResponse(
        request, "index.html", {"user_id": user_id, "categories": NICHE_CATEGORIES}
    )


@app.get("/search", response_class=HTMLResponse)
async def search_page(request: Request) -> HTMLResponse:
    user_id = get_current_user_id(request)
    q = request.query_params.get("q", "")
    region = request.query_params.get("region", "US")
    session_id = _get_session_id(request)

    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT DISTINCT query FROM recent_searches WHERE session_id = ? "
            "ORDER BY searched_at DESC LIMIT 10",
            (session_id,),
        )
        recent = [row["query"] for row in await cursor.fetchall()]
    finally:
        await db.close()

    resp = templates.TemplateResponse(
        request, "search.html", {
            "user_id": user_id, "query": q, "categories": NICHE_CATEGORIES,
            "regions": SUPPORTED_REGIONS, "current_region": region,
            "recent_searches": recent,
        }
    )
    if not request.cookies.get(RECENT_COOKIE):
        resp.set_cookie(RECENT_COOKIE, session_id, httponly=True, max_age=30 * 24 * 3600)
    return resp


@app.get("/saved", response_class=HTMLResponse)
async def saved_page(request: Request) -> HTMLResponse:
    user_id = get_current_user_id(request)
    if not user_id:
        return RedirectResponse("/login", status_code=302)

    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM saved_trends WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        )
        saved = [dict(r) for r in await cursor.fetchall()]
    finally:
        await db.close()

    return templates.TemplateResponse(
        request, "saved.html", {"user_id": user_id, "saved_trends": saved}
    )


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "auth.html", {"mode": "login", "error": None})


@app.get("/signup", response_class=HTMLResponse)
async def signup_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "auth.html", {"mode": "signup", "error": None})


# ─── Auth API ─────────────────────────────────────────────────────

@app.post("/api/signup")
async def api_signup(request: Request) -> Response:
    form = await request.form()
    email = str(form.get("email", "")).strip().lower()
    password = str(form.get("password", ""))

    if not email or not password:
        return templates.TemplateResponse(
            request, "auth.html", {"mode": "signup", "error": "Email and password are required."}
        )
    if len(password) < 6:
        return templates.TemplateResponse(
            request, "auth.html",
            {"mode": "signup", "error": "Password must be at least 6 characters."},
        )

    db = await get_db()
    try:
        cursor = await db.execute("SELECT id FROM users WHERE email = ?", (email,))
        if await cursor.fetchone():
            return templates.TemplateResponse(
                request, "auth.html",
                {"mode": "signup", "error": "An account with this email already exists."},
            )

        pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        cursor = await db.execute(
            "INSERT INTO users (email, password_hash) VALUES (?, ?)", (email, pw_hash)
        )
        await db.commit()
        user_id = cursor.lastrowid

        token = secrets.token_urlsafe(32)
        SESSION_STORE[token] = user_id
        response = RedirectResponse("/search", status_code=302)
        response.set_cookie(
            SESSION_COOKIE, token, httponly=True, max_age=30 * 24 * 3600, samesite="lax"
        )
        return response
    finally:
        await db.close()


@app.post("/api/login")
async def api_login(request: Request) -> Response:
    form = await request.form()
    email = str(form.get("email", "")).strip().lower()
    password = str(form.get("password", ""))

    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT id, password_hash FROM users WHERE email = ?", (email,)
        )
        user = await cursor.fetchone()
        if not user or not bcrypt.checkpw(password.encode(), user["password_hash"].encode()):
            return templates.TemplateResponse(
                request, "auth.html", {"mode": "login", "error": "Invalid email or password."}
            )

        token = secrets.token_urlsafe(32)
        SESSION_STORE[token] = user["id"]
        response = RedirectResponse("/search", status_code=302)
        response.set_cookie(
            SESSION_COOKIE, token, httponly=True, max_age=30 * 24 * 3600, samesite="lax"
        )
        return response
    finally:
        await db.close()


@app.get("/logout")
async def logout(request: Request) -> RedirectResponse:
    token = request.cookies.get(SESSION_COOKIE)
    if token and token in SESSION_STORE:
        del SESSION_STORE[token]
    response = RedirectResponse("/", status_code=302)
    response.delete_cookie(SESSION_COOKIE)
    return response


# ─── Trend API ────────────────────────────────────────────────────

@app.get("/api/trends/search")
async def api_trends_search(q: str = "", geo: str = "US") -> JSONResponse:
    if not q:
        return JSONResponse({"error": "Provide a query (q)"}, status_code=400)

    import asyncio

    from pytrends.request import TrendReq

    loop = asyncio.get_event_loop()

    results: dict = {"query": q, "geo": geo, "related_queries": [], "interest_over_time": []}

    try:
        def _fetch_trends() -> dict:
            pytrends = TrendReq(hl="en-US", tz=360, timeout=(10, 25))
            data: dict = {
                "related_queries": [],
                "rising_queries": [],
                "interest_over_time": [],
            }

            pytrends.build_payload([q], timeframe="today 3-m", geo=geo)

            try:
                iot = pytrends.interest_over_time()
                if not iot.empty and q in iot.columns:
                    data["interest_over_time"] = [
                        {"date": str(d.date()), "value": int(v)}
                        for d, v in zip(iot.index, iot[q])
                    ]
            except Exception:
                pass

            try:
                rq = pytrends.related_queries()
                if q in rq:
                    top = rq[q].get("top")
                    rising = rq[q].get("rising")
                    if top is not None and not top.empty:
                        data["related_queries"] = [
                            {"query": row["query"], "value": int(row["value"])}
                            for _, row in top.head(15).iterrows()
                        ]
                    if rising is not None and not rising.empty:
                        data["rising_queries"] = [
                            {"query": row["query"], "value": str(row["value"])}
                            for _, row in rising.head(10).iterrows()
                        ]
            except Exception:
                pass

            return data

        trend_data = await loop.run_in_executor(_trend_executor, _fetch_trends)
        results.update(trend_data)
    except Exception as e:
        results["error"] = str(e)

    return JSONResponse(results)


@app.get("/api/trends/compare")
async def api_trends_compare(
    q1: str = "", q2: str = "", q3: str = "", geo: str = "US"
) -> JSONResponse:
    queries = [q for q in [q1, q2, q3] if q.strip()]
    if len(queries) < 2:
        return JSONResponse(
            {"error": "Provide at least 2 queries (q1, q2, and optionally q3)"}, status_code=400
        )

    import asyncio

    from pytrends.request import TrendReq

    loop = asyncio.get_event_loop()

    try:
        def _fetch_comparison() -> dict:
            pytrends = TrendReq(hl="en-US", tz=360, timeout=(10, 25))
            pytrends.build_payload(queries[:3], timeframe="today 3-m", geo=geo)

            data: dict = {"queries": queries, "series": {}}
            try:
                iot = pytrends.interest_over_time()
                if not iot.empty:
                    dates = [str(d.date()) for d in iot.index]
                    data["dates"] = dates
                    for q in queries:
                        if q in iot.columns:
                            data["series"][q] = [int(v) for v in iot[q]]
            except Exception:
                pass
            return data

        result = await loop.run_in_executor(_trend_executor, _fetch_comparison)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

    return JSONResponse(result)


@app.get("/api/trends/suggestions")
async def api_trends_suggestions(q: str = "") -> JSONResponse:
    if not q:
        return JSONResponse({"suggestions": []})

    import httpx

    question_prefixes = [
        f"{q}",
        f"how to {q}",
        f"best {q}",
        f"why {q}",
        f"{q} guide",
        f"{q} tips",
        f"{q} for beginners",
        f"what is {q}",
    ]

    all_suggestions: list[str] = []
    seen: set[str] = set()

    async with httpx.AsyncClient(timeout=10.0) as client:
        for prefix in question_prefixes:
            try:
                resp = await client.get(
                    "https://suggestqueries.google.com/complete/search",
                    params={"client": "firefox", "q": prefix},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    if isinstance(data, list) and len(data) > 1:
                        for s in data[1]:
                            s_lower = s.lower().strip()
                            if s_lower not in seen:
                                seen.add(s_lower)
                                all_suggestions.append(s)
            except Exception:
                continue

    return JSONResponse({"suggestions": all_suggestions[:40]})


@app.get("/api/trends/trending")
async def api_trends_trending(region: str = "US") -> JSONResponse:
    import asyncio

    from pytrends.request import TrendReq

    loop = asyncio.get_event_loop()

    region_map = {r["code"]: r["pn"] for r in SUPPORTED_REGIONS}
    pn = region_map.get(region, "united_states")

    try:
        def _fetch_trending() -> list[dict]:
            pytrends = TrendReq(hl="en-US", tz=360, timeout=(10, 25))
            df = pytrends.trending_searches(pn=pn)
            items: list[dict] = []
            for _, row in df.head(20).iterrows():
                items.append({"query": row[0]})
            return items

        trending = await loop.run_in_executor(_trend_executor, _fetch_trending)
    except Exception:
        trending = []

    return JSONResponse({"trending": trending, "region": region})


@app.get("/api/trends/competition")
async def api_trends_competition(q: str = "") -> JSONResponse:
    if not q:
        return JSONResponse({"error": "Provide a query (q)"}, status_code=400)

    import httpx

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                "https://www.google.com/search",
                params={"q": f"{q} PDF guide filetype:pdf"},
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                    )
                },
            )
            import re
            text = resp.text
            match = re.search(r'About ([\d,]+) results', text)
            result_count = int(match.group(1).replace(",", "")) if match else 0

            if result_count < 10000:
                level = "low"
                label = "Low Competition"
                color = "#22C55E"
            elif result_count < 100000:
                level = "medium"
                label = "Medium Competition"
                color = "#F59E0B"
            else:
                level = "high"
                label = "High Competition"
                color = "#EF4444"

            return JSONResponse({
                "query": q,
                "result_count": result_count,
                "level": level,
                "label": label,
                "color": color,
            })
    except Exception:
        return JSONResponse({
            "query": q,
            "result_count": 0,
            "level": "unknown",
            "label": "Unable to check",
            "color": "#7878A0",
        })


@app.get("/api/trends/outline")
async def api_trends_outline(q: str = "") -> JSONResponse:
    if not q:
        return JSONResponse({"error": "Provide a query (q)"}, status_code=400)

    import httpx

    suggestions: list[str] = []
    prefixes = [f"how to {q}", f"{q} tips", f"{q} guide", f"best {q}", f"why {q}"]

    async with httpx.AsyncClient(timeout=10.0) as client:
        for prefix in prefixes:
            try:
                resp = await client.get(
                    "https://suggestqueries.google.com/complete/search",
                    params={"client": "firefox", "q": prefix},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    if isinstance(data, list) and len(data) > 1:
                        suggestions.extend(data[1][:5])
            except Exception:
                continue

    seen: set[str] = set()
    unique: list[str] = []
    for s in suggestions:
        key = s.lower().strip()
        if key not in seen:
            seen.add(key)
            unique.append(s)

    title = q.title()
    outline = {
        "title": f"The Ultimate Guide to {title}",
        "subtitle": f"Everything You Need to Know About {title}",
        "sections": [
            {
                "heading": f"Introduction to {title}",
                "points": [
                    f"What is {q} and why it matters",
                    "Who this guide is for",
                    "What you'll learn",
                ],
            },
        ],
    }

    if len(unique) >= 3:
        outline["sections"].append({
            "heading": f"Getting Started with {title}",
            "points": unique[:3],
        })
    if len(unique) >= 6:
        outline["sections"].append({
            "heading": f"Advanced {title} Strategies",
            "points": unique[3:6],
        })
    if len(unique) >= 9:
        outline["sections"].append({
            "heading": f"Common {title} Mistakes to Avoid",
            "points": unique[6:9],
        })

    outline["sections"].append({
        "heading": "Conclusion & Next Steps",
        "points": [
            "Summary of key takeaways",
            "Actionable next steps",
            "Additional resources",
        ],
    })

    return JSONResponse(outline)


# ─── Recent Searches API ─────────────────────────────────────────

@app.post("/api/recent-search")
async def api_record_search(request: Request) -> JSONResponse:
    session_id = _get_session_id(request)
    body = await request.json()
    query = str(body.get("query", "")).strip()
    if not query:
        return JSONResponse({"status": "skipped"})

    db = await get_db()
    try:
        await db.execute(
            "DELETE FROM recent_searches WHERE session_id = ? AND query = ?",
            (session_id, query),
        )
        await db.execute(
            "INSERT INTO recent_searches (session_id, query) VALUES (?, ?)",
            (session_id, query),
        )
        await db.execute(
            "DELETE FROM recent_searches WHERE session_id = ? AND id NOT IN "
            "(SELECT id FROM recent_searches WHERE session_id = ? ORDER BY searched_at DESC LIMIT 10)",
            (session_id, session_id),
        )
        await db.commit()
    finally:
        await db.close()

    response = JSONResponse({"status": "recorded"})
    if not request.cookies.get(RECENT_COOKIE):
        response.set_cookie(RECENT_COOKIE, session_id, httponly=True, max_age=30 * 24 * 3600)
    return response


# ─── Saved Trends API ────────────────────────────────────────────

@app.post("/api/trends/save")
async def api_trends_save(request: Request) -> JSONResponse:
    user_id = get_current_user_id(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Login required to save trends")

    body = await request.json()
    query = str(body.get("query", "")).strip()
    category = str(body.get("category", "")).strip()
    notes = str(body.get("notes", "")).strip()

    if not query:
        raise HTTPException(status_code=400, detail="Query is required")

    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO saved_trends (user_id, query, category, notes) VALUES (?, ?, ?, ?)",
            (user_id, query, category, notes),
        )
        await db.commit()
    finally:
        await db.close()

    return JSONResponse({"status": "saved", "query": query})


@app.delete("/api/trends/save/{trend_id}")
async def api_trends_unsave(request: Request, trend_id: int) -> JSONResponse:
    user_id = get_current_user_id(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Login required")

    db = await get_db()
    try:
        await db.execute(
            "DELETE FROM saved_trends WHERE id = ? AND user_id = ?", (trend_id, user_id)
        )
        await db.commit()
    finally:
        await db.close()

    return JSONResponse({"status": "deleted"})


@app.get("/api/trends/export")
async def api_trends_export(request: Request) -> StreamingResponse:
    user_id = get_current_user_id(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Login required to export")

    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT query, category, notes, created_at FROM saved_trends "
            "WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        )
        rows = await cursor.fetchall()
    finally:
        await db.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Idea", "Category", "Notes", "Saved Date"])
    for row in rows:
        writer.writerow([row["query"], row["category"], row["notes"], row["created_at"]])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=pdf-trend-ideas.csv"},
    )


# ─── Error Handlers ──────────────────────────────────────────────

@app.exception_handler(404)
async def custom_404(request: Request, exc: HTTPException) -> HTMLResponse:
    return HTMLResponse(
        "<h1>404 - Page Not Found</h1><p><a href='/'>Go home</a></p>", status_code=404
    )


@app.exception_handler(429)
async def custom_429(request: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(
        {"detail": "Too many requests. Please try again later."}, status_code=429
    )
