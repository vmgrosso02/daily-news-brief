#!/usr/bin/env python3
"""
Daily News Brief — fully free, end-to-end.
==========================================
1. Pulls headlines from ~14 quality RSS feeds.
2. Scores each by topic relevance × source quality × recency,
   penalizes outrage / engagement-bait keywords.
3. Picks the top 5 (max 2 per topic for diversity).
4. Renders a calm HTML brief (headline + summary + source link).
5. Sends the brief by email.

No paid APIs. No databases. No LLM. Just RSS + Python + email.

Email provider is auto-selected from environment variables:
  - If RESEND_API_KEY is set      -> Resend (free 3,000/mo)
  - Else GMAIL_USER + GMAIL_APP_PASSWORD -> Gmail SMTP (free)

Run locally:   python brief.py            (sends email)
                DRY_RUN=1 python brief.py  (writes HTML only)
Run on cron:   handled by .github/workflows/daily-brief.yml
"""
from __future__ import annotations

import datetime as dt
import html
import json
import os
import re
import smtplib
import ssl
import sys
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Iterable

import feedparser   # pip install feedparser
import requests     # pip install requests


# ---------------------------------------------------------------------------
# CONFIG (env-driven)
# ---------------------------------------------------------------------------

EMAIL_TO            = os.environ.get("EMAIL_TO", "vmgrosso02@yahoo.com")
RECIPIENT_NAME      = os.environ.get("RECIPIENT_NAME", "Michael")
DRY_RUN             = os.environ.get("DRY_RUN", "0") == "1"

# Resend (preferred — simpler, no app password)
RESEND_API_KEY      = os.environ.get("RESEND_API_KEY", "")
EMAIL_FROM_RESEND   = os.environ.get("EMAIL_FROM", "Daily Brief <onboarding@resend.dev>")

# Gmail SMTP (fallback — fully free)
GMAIL_USER          = os.environ.get("GMAIL_USER", "")
GMAIL_APP_PASSWORD  = os.environ.get("GMAIL_APP_PASSWORD", "")


# Quality-first sources. Outrage farms intentionally excluded.
FEEDS: list[tuple[str, str, float]] = [
    ("Reuters Business",        "https://feeds.reuters.com/reuters/businessNews",                 1.00),
    ("Reuters Technology",      "https://feeds.reuters.com/reuters/technologyNews",               1.00),
    ("Reuters Science",         "https://feeds.reuters.com/reuters/scienceNews",                  1.00),
    ("AP Top News",             "https://apnews.com/index.rss",                                   1.00),
    ("BBC Business",            "http://feeds.bbci.co.uk/news/business/rss.xml",                  0.95),
    ("FT Markets",              "https://www.ft.com/markets?format=rss",                          0.95),
    ("WSJ Markets",             "https://feeds.a.dj.com/rss/RSSMarketsMain.xml",                  0.95),
    ("Bloomberg Markets",       "https://feeds.bloomberg.com/markets/news.rss",                   0.95),
    ("The Economist Finance",   "https://www.economist.com/finance-and-economics/rss.xml",        0.90),
    ("Nature",                  "https://www.nature.com/nature.rss",                              0.95),
    ("Science Daily Neuro",     "https://www.sciencedaily.com/rss/mind_brain/neuroscience.xml",   0.85),
    ("STAT Biotech",            "https://www.statnews.com/feed/",                                 0.90),
    ("MIT Tech Review",         "https://www.technologyreview.com/feed/",                         0.85),
    ("ESPN MLB",                "https://www.espn.com/espn/rss/mlb/news",                         0.80),
]

INTERESTS: dict[str, dict] = {
    "finance_markets": {
        "weight": 1.00,
        "keywords": [
            "fed", "fomc", "interest rate", "rate cut", "rate hike", "inflation",
            "cpi", "ppi", "yield", "treasury", "bond", "s&p", "nasdaq", "dow",
            "earnings", "guidance", "capex", "buyback", "ipo", "merger",
            "powell", "warsh", "ecb", "boj", "oil", "opec", "crude",
            "recession", "gdp", "unemployment", "jobs report", "payroll",
        ],
    },
    "ai_tech": {
        "weight": 1.00,
        "keywords": [
            "ai", "artificial intelligence", "llm", "gpt", "claude", "gemini",
            "nvidia", "amd", "tsmc", "semiconductor", "chip", "data center",
            "openai", "anthropic", "microsoft", "google", "alphabet", "apple",
            "meta", "amazon", "tesla", "robotics", "autonomous", "quantum",
            "infrastructure", "gpu", "model", "training",
        ],
    },
    "biotech_neuro": {
        "weight": 1.00,
        "keywords": [
            "fda", "clinical trial", "phase 1", "phase 2", "phase 3",
            "biotech", "pharma", "drug", "therapy", "vaccine", "gene therapy",
            "stem cell", "crispr", "antibody", "oncology", "cancer",
            "alzheimer", "parkinson", "neuroscience", "neural", "brain",
            "cognitive", "dopamine", "synapse", "neurodegenerative",
        ],
    },
    "sports": {
        "weight": 0.70,
        "keywords": [
            "mlb", "dodgers", "yankees", "world series", "playoffs",
            "nba", "nfl", "super bowl", "champions league", "trade", "draft",
        ],
    },
}

PENALTY_KEYWORDS = [
    "kardashian", "tiktok drama", "outrage", "slams", "rips", "destroys",
    "you won't believe", "shocking", "horrific", "goes viral", "twitter feud",
    "celeb", "celebrity gossip",
]

TOP_N                   = 5
RECENCY_HALF_LIFE_HOURS = 18
MAX_PER_TOPIC           = 2
SUMMARY_MAX_CHARS       = 360

TOPIC_LABELS = {
    "finance_markets": "Markets",
    "ai_tech": "AI & Tech",
    "biotech_neuro": "Biotech / Neuro",
    "sports": "Sports",
    "general": "Briefing",
}


# ---------------------------------------------------------------------------
# DATA MODEL
# ---------------------------------------------------------------------------

@dataclass
class Story:
    title: str
    link: str
    source: str
    source_weight: float
    published: dt.datetime
    summary: str
    topic: str = "general"
    topic_score: float = 0.0
    recency_score: float = 0.0
    penalty: float = 0.0

    @property
    def total_score(self) -> float:
        return (self.topic_score * self.source_weight * self.recency_score) - self.penalty


# ---------------------------------------------------------------------------
# FETCH + SCORE
# ---------------------------------------------------------------------------

def _clean(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text or "")
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text

def _trim(text: str, n: int = SUMMARY_MAX_CHARS) -> str:
    if len(text) <= n:
        return text
    # cut at the last sentence boundary before n if possible
    cut = text[:n]
    m = re.search(r"[.!?]\s+\S[^.!?]*$", cut)
    if m and m.start() > n * 0.5:
        return cut[: m.start() + 1]
    return cut.rsplit(" ", 1)[0] + "…"

def _parse_date(entry) -> dt.datetime:
    for key in ("published_parsed", "updated_parsed"):
        v = entry.get(key)
        if v:
            return dt.datetime(*v[:6])
    return dt.datetime.utcnow()

def fetch_stories() -> list[Story]:
    stories: list[Story] = []
    for name, url, weight in FEEDS:
        try:
            parsed = feedparser.parse(url)
        except Exception as e:
            print(f"  ! {name}: {e}", file=sys.stderr)
            continue
        for entry in parsed.entries[:25]:
            stories.append(Story(
                title=_clean(entry.get("title", "")),
                link=entry.get("link", ""),
                source=name,
                source_weight=weight,
                published=_parse_date(entry),
                summary=_trim(_clean(entry.get("summary", ""))),
            ))
        print(f"  + {name}: {len(parsed.entries)} entries", file=sys.stderr)
    return stories

def score_story(story: Story, now: dt.datetime) -> None:
    text = f"{story.title} {story.summary}".lower()

    best_topic, best_score = "general", 0.0
    for topic, cfg in INTERESTS.items():
        hits = sum(1 for kw in cfg["keywords"] if kw in text)
        score = cfg["weight"] * min(hits, 3) / 3.0
        if score > best_score:
            best_score, best_topic = score, topic
    story.topic, story.topic_score = best_topic, best_score

    age_h = max((now - story.published).total_seconds() / 3600.0, 0)
    story.recency_score = 0.5 ** (age_h / RECENCY_HALF_LIFE_HOURS)

    story.penalty = sum(1.0 for bad in PENALTY_KEYWORDS if bad in text)

def pick_top(stories: Iterable[Story], n: int = TOP_N) -> list[Story]:
    ranked = sorted(stories, key=lambda s: s.total_score, reverse=True)
    picked: list[Story] = []
    per_topic: dict[str, int] = {}
    seen_titles: set[str] = set()
    for s in ranked:
        if s.total_score <= 0:
            continue
        key = s.title.lower()
        if key in seen_titles:
            continue
        if per_topic.get(s.topic, 0) >= MAX_PER_TOPIC:
            continue
        picked.append(s)
        per_topic[s.topic] = per_topic.get(s.topic, 0) + 1
        seen_titles.add(key)
        if len(picked) >= n:
            break
    return picked


# ---------------------------------------------------------------------------
# RENDER HTML
# ---------------------------------------------------------------------------

HTML_TEMPLATE = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Morning Brief — {date_human}</title>
</head>
<body style="margin:0;padding:0;background:#faf8f5;color:#1c1c1e;font:16px/1.55 -apple-system,BlinkMacSystemFont,'SF Pro Text','Inter',system-ui,sans-serif;-webkit-font-smoothing:antialiased;">
<div style="max-width:640px;margin:0 auto;padding:28px 20px 60px;">
  <div style="font-size:13px;letter-spacing:0.12em;text-transform:uppercase;color:#6e6e73;">{greeting}, {name}</div>
  <h1 style="font-size:28px;line-height:1.2;margin:4px 0 0;font-weight:600;letter-spacing:-0.01em;color:#1c1c1e;">Morning Brief — {date_human}</h1>
  <div style="color:#6e6e73;font-size:14px;margin-top:6px;">Top {count} stories · curated for signal, not engagement</div>
  {articles}
  <div style="text-align:center;color:#6e6e73;font-size:12px;margin-top:32px;line-height:1.6;">
    Generated {generated_at}<br>
    <span style="opacity:.6">No outrage. No engagement bait. One brief a day.</span>
  </div>
</div></body></html>
"""

ARTICLE_TEMPLATE = """
<div style="background:#ffffff;border:1px solid #e5e3df;border-radius:14px;padding:20px 22px;margin:14px 0;">
  <div><span style="color:#2e5d8a;font-weight:700;font-size:13px;letter-spacing:0.08em;">{idx:02d}</span>
       <span style="display:inline-block;font-size:11px;font-weight:600;background:#f1ede6;color:#6e6e73;padding:3px 8px;border-radius:999px;margin-left:8px;letter-spacing:0.04em;">{topic_label}</span></div>
  <h2 style="font-size:19px;line-height:1.3;margin:8px 0 10px;font-weight:600;color:#1c1c1e;">{title}</h2>
  <div style="margin:6px 0 14px;color:#1c1c1e;">{summary}</div>
  <div style="font-size:13px;color:#6e6e73;margin-top:14px;border-top:1px solid #e5e3df;padding-top:10px;">
    Source: <a href="{link}" style="color:#2e5d8a;text-decoration:none;">{source}</a> · <a href="{link}" style="color:#2e5d8a;text-decoration:none;">Read full article →</a>
  </div>
</div>
"""

def render(stories: list[Story], now: dt.datetime) -> str:
    hour = now.hour
    greeting = "Good Morning" if hour < 12 else "Good Afternoon" if hour < 18 else "Good Evening"
    arts = []
    for i, s in enumerate(stories, 1):
        arts.append(ARTICLE_TEMPLATE.format(
            idx=i,
            topic_label=TOPIC_LABELS.get(s.topic, "Briefing"),
            title=html.escape(s.title),
            summary=html.escape(s.summary) or "(no summary provided by source)",
            link=html.escape(s.link),
            source=html.escape(s.source),
        ))
    try:
        date_human = now.strftime("%A, %B %-d, %Y")
    except ValueError:
        date_human = now.strftime("%A, %B %d, %Y")
    return HTML_TEMPLATE.format(
        greeting=greeting,
        name=html.escape(RECIPIENT_NAME),
        date_human=date_human,
        count=len(stories),
        articles="\n".join(arts),
        generated_at=now.strftime("%Y-%m-%d %H:%M UTC"),
    )


# ---------------------------------------------------------------------------
# EMAIL — Resend (preferred) or Gmail SMTP (fallback)
# ---------------------------------------------------------------------------

def send_via_resend(subject: str, html_body: str) -> None:
    payload = {
        "from": EMAIL_FROM_RESEND,
        "to": [EMAIL_TO],
        "subject": subject,
        "html": html_body,
    }
    r = requests.post(
        "https://api.resend.com/emails",
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json",
        },
        data=json.dumps(payload),
        timeout=30,
    )
    if r.status_code >= 300:
        print(f"!! Resend error {r.status_code}: {r.text}", file=sys.stderr)
        r.raise_for_status()
    print(f"  ✓ Email sent via Resend to {EMAIL_TO}", file=sys.stderr)

def send_via_gmail(subject: str, html_body: str) -> None:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"]    = GMAIL_USER
    msg["To"]      = EMAIL_TO
    msg.set_content("Your email client does not support HTML — please view the HTML version.")
    msg.add_alternative(html_body, subtype="html")
    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx, timeout=30) as s:
        s.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        s.send_message(msg)
    print(f"  ✓ Email sent via Gmail SMTP to {EMAIL_TO}", file=sys.stderr)

def send_email(subject: str, html_body: str) -> None:
    if RESEND_API_KEY:
        return send_via_resend(subject, html_body)
    if GMAIL_USER and GMAIL_APP_PASSWORD:
        return send_via_gmail(subject, html_body)
    raise RuntimeError(
        "No email credentials found. Set RESEND_API_KEY, "
        "or set GMAIL_USER + GMAIL_APP_PASSWORD."
    )


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main() -> None:
    now = dt.datetime.utcnow()

    print("Fetching feeds...", file=sys.stderr)
    stories = fetch_stories()
    print(f"\nFetched {len(stories)} stories. Scoring...", file=sys.stderr)
    for s in stories:
        score_story(s, now)
    top = pick_top(stories, TOP_N)
    print(f"\nPicked top {len(top)}:", file=sys.stderr)
    for i, s in enumerate(top, 1):
        print(f"  {i}. [{s.topic}] {s.title[:70]}  (score={s.total_score:.2f})", file=sys.stderr)

    html_body = render(top, now)

    # Always save a local copy for audit
    out_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        f"brief_{now.strftime('%Y-%m-%d')}.html",
    )
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html_body)
    print(f"\nWrote {out_path}", file=sys.stderr)

    if DRY_RUN:
        print("DRY_RUN=1 — not sending email.", file=sys.stderr)
        return

    subject = f"Morning Brief — {now.strftime('%a %b %d')}"
    send_email(subject, html_body)


if __name__ == "__main__":
    main()
