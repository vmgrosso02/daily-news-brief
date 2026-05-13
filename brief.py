#!/usr/bin/env python3
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

import feedparser   
import requests     

# ---------------------------------------------------------------------------
# CONFIG & GLOBAL CONSTANTS
# ---------------------------------------------------------------------------

EMAIL_TO            = os.environ.get("EMAIL_TO", "vmgrosso02@gmail.com")
RECIPIENT_NAME      = os.environ.get("RECIPIENT_NAME", "Michael")
DRY_RUN             = os.environ.get("DRY_RUN", "0") == "1"

RESEND_API_KEY      = os.environ.get("RESEND_API_KEY", "")
EMAIL_FROM_RESEND   = "Daily Brief <onboarding@resend.dev>"
GMAIL_USER          = os.environ.get("GMAIL_USER", "")
GMAIL_APP_PASSWORD  = os.environ.get("GMAIL_APP_PASSWORD", "")

TOP_N                   = 5
RECENCY_HALF_LIFE_HOURS = 12 
MAX_PER_TOPIC           = 2
SUMMARY_MAX_CHARS       = 360

PENALTY_KEYWORDS = ["kardashian", "tiktok drama", "outrage", "slams", "rips", "destroys"]

TOPIC_LABELS = {
    "finance_markets": "Markets",
    "ai_tech": "AI & Tech",
    "biotech_neuro": "Biotech / Neuro",
    "sports": "Sports",
    "general": "Briefing",
}

FEEDS: list[tuple[str, str, float]] = [
    ("Reuters Business",        "https://feeds.reuters.com/reuters/businessNews",                 0.95),
    ("MarketWatch Top",         "http://feeds.marketwatch.com/marketwatch/topstories/",           0.92),
    ("Seeking Alpha",           "https://seekingalpha.com/market_news.rss",                       0.90),
    ("The Economist Finance",   "https://www.economist.com/finance-and-economics/rss.xml",         0.88),
    ("MIT Tech Review",         "https://www.technologyreview.com/feed/",                         0.90),
    ("Ars Technica",            "https://feeds.arstechnica.com/arstechnica/index",                0.88),
    ("Nature",                  "https://www.nature.com/nature.rss",                              0.95),
    ("STAT Biotech",            "https://www.statnews.com/feed/",                                 0.92),
    ("NCAA Lacrosse",           "https://www.ncaa.com/news/lacrosse-men/d1/rss.xml",              1.00),
    ("Inside Lacrosse",         "https://www.insidelacrosse.com/rss",                             1.00),
    ("ESPN NBA",                "https://www.espn.com/espn/rss/nba/news",                         0.90),
    ("ESPN NFL",                "https://www.espn.com/espn/rss/nfl/news",                         0.85),
    ("Ramblin Wreck (GT)",      "https://ramblinwreck.com/feed/",                                 0.95),
]

# REFINED KEYWORDS: Focuses on specific sport terms to avoid science crossovers
INTERESTS: dict[str, dict] = {
    "finance_markets": {"weight": 1.0, "keywords": ["fed", "inflation", "yield", "s&p 500", "nasdaq", "dow jones"]},
    "ai_tech": {"weight": 1.0, "keywords": ["ai agents", "robotics", "nvda", "openai", "claude", "gemini", "gpu"]},
    "biotech_neuro": {"weight": 1.0, "keywords": ["fda", "pharma", "crispr", "neuroscience", "neuralink", "clinical trial"]},
    "sports": {"weight": 2.0, "keywords": ["lacrosse", "score", "game", "quarterback", "nba", "nfl", "gt athletics", "yellow jackets", "lax", "ncaa", "bracket"]},
}

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

def _clean(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text or "")
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()

def _trim(text: str, n: int = SUMMARY_MAX_CHARS) -> str:
    if len(text) <= n: return text
    cut = text[:n]
    m = re.search(r"[.!?]\s+\S[^.!?]*$", cut)
    if m and m.start() > n * 0.5: return cut[: m.start() + 1]
    return cut.rsplit(" ", 1)[0] + "…"

def _parse_date(entry) -> dt.datetime:
    for key in ("published_parsed", "updated_parsed"):
        v = entry.get(key)
        if v: return dt.datetime(*v[:6])
    return dt.datetime.utcnow()

def fetch_stories() -> list[Story]:
    stories: list[Story] = []
    for name, url, weight in FEEDS:
        try:
            parsed = feedparser.parse(url)
            for entry in parsed.entries[:20]:
                stories.append(Story(
                    title=_clean(entry.get("title", "")),
                    link=entry.get("link", ""),
                    source=name,
                    source_weight=weight,
                    published=_parse_date(entry),
                    summary=_trim(_clean(entry.get("summary", ""))),
                ))
        except Exception as e:
            print(f"Error fetching {name}: {e}", file=sys.stderr)
    return stories

def score_story(story: Story, now: dt.datetime) -> None:
    text = f"{story.title} {story.summary}".lower()
    best_topic, best_score = "general", 0.01 
    
    for topic, cfg in INTERESTS.items():
        hits = sum(1 for kw in cfg["keywords"] if kw in text)
        if hits > 0:
            # Sports gets a boost but requires at least one hard keyword hit
            score = (cfg["weight"] * hits) 
            if score > best_score:
                best_score, best_topic = score, topic
    
    story.topic, story.topic_score = best_topic, best_score
    
    # 24-HOUR FILTER: Slashes score for anything from yesterday
    age_h = max((now - story.published).total_seconds() / 3600.0, 0)
    if age_h > 22: # Slightly under 24 to ensure fresh content
        story.recency_score = 0.01 
    else:
        story.recency_score = 0.5 ** (age_h / RECENCY_HALF_LIFE_HOURS)
        
    story.penalty = sum(2.0 for bad in PENALTY_KEYWORDS if bad in text)

def pick_top(stories: Iterable[Story], n: int = TOP_N) -> list[Story]:
    ranked = sorted(stories, key=lambda s: s.total_score, reverse=True)
    
    picked_general: list[Story] = []
    picked_sports: list[Story] = []
    per_topic: dict[str, int] = {}
    seen_links: set[str] = set()

    # 1. Identify valid Sports stories (must be from a sports source or have high sport score)
    sports_sources = ["NCAA Lacrosse", "Inside Lacrosse", "ESPN NBA", "ESPN NFL", "Ramblin Wreck (GT)"]
    sports_candidates = [
        s for s in ranked 
        if (s.topic == "sports" or s.source in sports_sources) 
        and s.total_score > 0
    ]
    
    if sports_candidates:
        top_sport = sports_candidates[0]
        picked_sports.append(top_sport)
        seen_links.add(top_sport.link)

    # 2. Fill the rest of the slots
    for s in ranked:
        if len(picked_general) >= (n - 1): break
        if s.link in seen_links: continue
        if s.topic == "sports" or s.source in sports_sources: continue
        if per_topic.get(s.topic, 0) >= MAX_PER_TOPIC: continue
        
        picked_general.append(s)
        per_topic[s.topic] = per_topic.get(s.topic, 0) + 1
        seen_links.add(s.link)

    # 3. Combine: General News [1-4], Sports [5]
    return picked_general + picked_sports

ARTICLE_TEMPLATE = """
<div style="background:#ffffff;border:1px solid #e5e3df;border-radius:14px;padding:20px 22px;margin:14px 0;">
  <div><span style="color:#2e5d8a;font-weight:700;font-size:13px;letter-spacing:0.08em;">{idx:02d}</span>
       <span style="display:inline-block;font-size:11px;font-weight:600;background:#f1ede6;color:#6e6e73;padding:3px 8px;border-radius:999px;margin-left:8px;letter-spacing:0.04em;">{topic_label}</span></div>
  <h2 style="font-size:19px;line-height:1.3;margin:8px 0 10px;font-weight:600;color:#1c1c1e;">{title}</h2>
  <div style="margin:6px 0 14px;color:#1c1c1e;">{summary}</div>
  <div style="font-size:13px;color:#6e6e73;margin-top:14px;border-top:1px solid #e5e3df;padding-top:10px;">
    Source: <span style="color:#2e5d8a;">{source}</span> · <a href="{link}" style="color:#2e5d8a;text-decoration:none;">Read full article →</a>
  </div>
</div>
"""

HTML_TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#faf8f5;color:#1c1c1e;font:16px/1.55 system-ui,sans-serif;">
<div style="max-width:640px;margin:0 auto;padding:28px 20px 60px;">
  <div style="font-size:13px;text-transform:uppercase;color:#6e6e73;">{greeting}, {name}</div>
  <h1 style="font-size:28px;margin:4px 0 0;font-weight:600;color:#1c1c1e;">{period} Briefing — {date_human}</h1>
  {articles}
</div></body></html>"""

def render(stories: list[Story], now: dt.datetime) -> str:
    # Adjusting for EST (UTC-4)
    local_hour = (now.hour - 4) % 24 
    if local_hour < 12: greeting, period = "Good Morning", "Morning"
    elif local_hour < 17: greeting, period = "Good Afternoon", "Afternoon"
    else: greeting, period = "Good Evening", "Evening"
        
    arts = [ARTICLE_TEMPLATE.format(
        idx=i, topic_label=TOPIC_LABELS.get(s.topic, "Briefing"), 
        title=html.escape(s.title), summary=html.escape(s.summary), 
        link=html.escape(s.link), source=html.escape(s.source)
    ) for i, s in enumerate(stories, 1)]
    
    return HTML_TEMPLATE.format(greeting=greeting, period=period, name=RECIPIENT_NAME, date_human=now.strftime("%A, %B %d, %Y"), articles="\n".join(arts))

def send_email(subject: str, html_body: str) -> None:
    if RESEND_API_KEY:
        payload = {
            "from": EMAIL_FROM_RESEND,
            "to": [EMAIL_TO], 
            "subject": subject, 
            "html": html_body
        }
        r = requests.post(
            "https://api.resend.com/emails", 
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}", 
                "Content-Type": "application/json"
            }, 
            data=json.dumps(payload)
        )
        if r.status_code not in [200, 201]:
            print(f"Resend Error: {r.status_code} - {r.text}")
            r.raise_for_status()
    elif GMAIL_USER and GMAIL_APP_PASSWORD:
        msg = EmailMessage()
        msg["Subject"], msg["From"], msg["To"] = subject, f"Daily Brief <{GMAIL_USER}>", EMAIL_TO
        msg.add_alternative(html_body, subtype="html")
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context()) as s:
            s.login(GMAIL_USER, GMAIL_APP_PASSWORD)
            s.send_message(msg)

def main() -> None:
    now = dt.datetime.utcnow()
    stories = fetch_stories()
    for s in stories: score_story(s, now)
    top = pick_top(stories, TOP_N)
    if not top:
        print("No stories found.")
        return
    html_body = render(top, now)
    local_hour = (now.hour - 4) % 24
    period = "Morning" if local_hour < 12 else "Evening"
    if not DRY_RUN:
        send_email(f"{period} Briefing — {now.strftime('%b %d')}", html_body)
    print(f"Sent {len(top)} stories to {EMAIL_TO}.")

if __name__ == "__main__":
    main()
