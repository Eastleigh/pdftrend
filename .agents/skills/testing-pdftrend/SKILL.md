---
name: testing-pdftrend
description: Test the PDF Trend Finder app end-to-end. Use when verifying search, trends, comparison, auth, save, export, theme, and SEO features.
---

# Testing PDF Trend Finder

## Setup

1. Install dependencies:
   ```bash
   cd /home/ubuntu/pdf-trend-finder
   pip install -e ".[dev]"
   ```

2. Start the server:
   ```bash
   uvicorn app.main:app --reload --port 8000
   ```

3. App is available at `http://localhost:8000`

## Key Pages

- `/` — Landing page (categories, stats, CTA)
- `/search` — Main search page (query input, region selector, compare button, category pills, recent searches)
- `/search?q=<query>&region=<code>` — Search results (suggestions, competition, chart, outline, related/rising queries, trending sidebar)
- `/signup` — Create account
- `/login` — Login
- `/saved` — Saved ideas (requires auth) with Export CSV button
- `/api/trends/export` — CSV download endpoint (requires auth)

## Features to Test

### 1. Multi-Region Search
- Region dropdown has 8 options: US, GB, CA, AU, IN, DE, FR, BR
- Selecting a region adds `&region=<code>` to URL
- "Trending Now" sidebar header shows region code in parentheses

### 2. Suggestions (PDF Guide Ideas)
- Shows ~40 suggestions per search from Google Autocomplete
- Badge shows count next to "PDF Guide Ideas" heading
- Each suggestion is a clickable pill with a "+" save button (visible when logged in)

### 3. Competition Score
- Card shows Low/Medium/High competition with color coding
- **Known issue:** Google may block the scraping request from test environments, causing "Competition data unavailable". The fallback UI still renders correctly. This might work in production environments.

### 4. Interest Over Time Chart
- Chart.js line chart showing 3-month trend data from pytrends
- May occasionally fail if Google Trends rate-limits the request

### 5. PDF Outline Generator
- Shows "The Ultimate Guide to <Topic>" with 5 sections
- "Download Outline as Text" button creates a .txt file via client-side Blob

### 6. Topic Comparison
- Click "Compare Topics" button to open modal
- Enter 2-3 topics and click "Compare"
- Renders a Chart.js line chart with labeled datasets below the search results

### 7. Dark/Light Mode Toggle
- Moon/sun icon button in the nav bar
- Toggles `data-theme="light"` on `<html>` element
- Persists via `localStorage.setItem('theme', ...)`
- Must persist across page navigation

### 8. Recent Searches
- After searching, "Recent:" tags appear below the controls row
- Clicking a recent tag triggers a new search
- Stored server-side in SQLite `recent_searches` table, keyed by session cookie

### 9. Auth + Save Ideas
- Sign up with email/password at `/signup`
- After signup, nav changes to show "Saved Ideas" and "Log Out"
- Click "+" on any suggestion pill to save — toast says "Idea saved!"
- Visit `/saved` to see saved ideas with date and Research/Remove buttons

### 10. CSV Export
- On `/saved` page, click "Export CSV" button
- Downloads `pdf-trend-ideas.csv` with headers: `Idea,Category,Notes,Saved Date`

### 11. SEO Meta Tags
- Verify via `curl -s http://localhost:8000/ | grep -E '(og:|twitter:|canonical)'`
- Should have `og:title`, `og:description`, `og:type`, `twitter:card`, `twitter:title`, `canonical`

## Devin Secrets Needed

No secrets required — the app runs fully locally with SQLite.

## Tips

- The app uses pytrends which can be rate-limited by Google. If trend data stops loading, wait a few minutes.
- Competition score scraping is blocked in many environments. Test the fallback UI instead of expecting real data.
- The SQLite database is auto-created on first run (`trends.db`).
- Auth uses bcrypt for password hashing and in-memory session store (`SESSION_STORE` dict).
