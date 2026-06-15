#!/usr/bin/env python3
import datetime as dt
import html
import json
import os
import re
import requests
import feedparser
from dataclasses import dataclass
from typing import Iterable

# ---------------------------------------------------------------------------
# CONFIG & SOURCE LOCKS
# ---------------------------------------------------------------------------
EMAIL_TO            = os.environ.get("EMAIL_TO")
RECIPIENT_NAME      = os.environ.get("RECIPIENT_NAME", "Michael")
RESEND_API_KEY      = os.environ.get("RESEND_API_KEY", "")
EMAIL_FROM_RESEND   = "Daily Brief <onboarding@resend.dev>"

TOP_N = 5
MAX_PER_TOPIC = 2

# ONLY these sources are allowed to fill the "Sports" slot
SPORTS_SOURCES = [
    "NCAA Lacrosse", "Inside Lacrosse", "ESPN NBA", "ESPN NFL", 
    "Ramblin Wreck (GT)", "Miami Herald Sports"
]

TOPIC_LABELS = {
    "finance_markets": "Markets",
    "ai_tech": "AI & Tech",
    "biotech_neuro": "Biotech / Neuro",
    "sports": "Sports",
    "general": "Briefing",
}

FEEDS = [
    ("Reuters Business",        "https://feeds.reuters.com/reuters/businessNews",                  0.95),
    ("MarketWatch Top",         "http://feeds.marketwatch.com/marketwatch/topstories/",           0.92),
    ("The Economist Finance",   "https://www.economist.com/finance-and-economics/rss.xml",         0.88),
    ("MIT Tech Review",         "https://www.technologyreview.com/feed/",                          0.90),
    ("Nature",                  "https://www.nature.com/nature.rss",                               0.95),
    ("STAT Biotech",            "https://www.statnews.com/feed/",                                  0.92),
    ("NCAA Lacrosse",           "https://www.ncaa.com/news/lacrosse-men/d1/rss.xml",               1.00),
    ("Inside Lacrosse",         "https://www.insidelacrosse.com/rss",                              1.00),
    ("ESPN NBA",                "https://www.espn.com/espn/rss/nba/news",                          0.90),
    ("ESPN NFL",                "https://www.espn.com/espn/rss/nfl/news",                          0.85),
    ("Ramblin Wreck (GT)",      "https://ramblinwreck.com/feed/",                                  0.95),
    ("Miami Herald Sports",     "https://www.miamiherald.com/sports/index.rss",                    0.90),
]

INTERESTS = {
    "finance_markets": {"weight": 1.0, "keywords": ["fed", "inflation", "yield", "s&p 500", "nasdaq", "nvda"]},
    "ai_tech": {"weight": 1.0, "keywords": ["ai agents", "robotics", "openai", "claude", "gpu", "llm"]},
    "biotech_neuro": {"weight": 1.2, "keywords": ["fda", "crispr", "neuroscience", "neuralink", "clinical trial"]},
    "sports": {"weight": 3.0, "keywords": ["lacrosse", "lax", "ncaa", "quarterback", "nba", "nfl", "yellow jackets", "touchdown", "playoffs"]},
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

    @property
    def total_score(self) -> float:
        # Recency calculation (half-life of 12 hours)
        age_h = max((dt.datetime.utcnow() - self.published).total_seconds() / 3600.0, 0)
        recency = 0.5 ** (age_h / 12.0)
        return self.topic_score * self.source_weight * recency

def _clean(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text or "")
    return html.unescape(text).strip()

def fetch_stories() -> list[Story]:
    stories = []
    for name, url, weight in FEEDS:
        try:
            parsed = feedparser.parse(url)
            for entry in parsed.entries[:15]:
                dt_obj = dt.datetime(*(entry.get("published_parsed") or entry.get("updated_parsed") or dt.datetime.utcnow().timetuple())[:6])
                stories.append(Story(
                    title=_clean(entry.get("title", "")),
                    link=entry.get("link", ""),
                    source=name,
                    source_weight=weight,
                    published=dt_obj,
                    summary=_clean(entry.get("summary", ""))[:350] + "..."
                ))
        except: continue
    return stories

def score_story(story: Story):
    text = f"{story.title} {story.summary}".lower()
    best_topic, best_score = "general", 0.1
    
    for topic, cfg in INTERESTS.items():
        # SOURCE LOCK: Only allow "Sports" if source is in the sports list
        if topic == "sports" and story.source not in SPORTS_SOURCES:
            continue
            
        hits = sum(1 for kw in cfg["keywords"] if kw in text)
        score = (cfg["weight"] * hits) if hits > 0 else 0.1
        if score > best_score:
            best_score, best_topic = score, topic
            
    story.topic, story.topic_score = best_topic, best_score

def pick_top(stories: Iterable[Story]) -> list[Story]:
    ranked = sorted(stories, key=lambda s: s.total_score, reverse=True)
    picked_general = []
    picked_sports = []
    seen = set()

    # 1. Mandatory Sports Slot (Must be from a sports source)
    for s in ranked:
        if s.source in SPORTS_SOURCES and s.link not in seen:
            picked_sports.append(s)
            seen.add(s.link)
            break # Only need one primary sports story
    
    # 2. Fill the rest with non-sports news
    per_topic = {}
    for s in ranked:
        if len(picked_general) >= (TOP_N - 1): break
        if s.link in seen or s.source in SPORTS_SOURCES: continue
        if per_topic.get(s.topic, 0) >= MAX_PER_TOPIC: continue
        
        picked_general.append(s)
        per_topic[s.topic] = per_topic.get(s.topic, 0) + 1
        seen.add(s.link)

    return picked_general + picked_sports

def render_and_send(stories: list[Story]):
    now = dt.datetime.utcnow()
    # Miami Time Adjustment (UTC-4)
    local_hour = (now.hour - 4) % 24
    period = "Morning" if local_hour < 12 else "Evening"
    
    # Simple HTML assembly
    arts_html = ""
    for i, s in enumerate(stories, 1):
        label = TOPIC_LABELS.get(s.topic, "Briefing")
        arts_html += f"""
        <div style="border-bottom:1px solid #eee; padding:15px 0;">
            <small style="color:#666;">{i:02d} | {label}</small>
            <h3 style="margin:5px 0;">{s.title}</h3>
            <p style="font-size:14px; color:#333;">{s.summary}</p>
            <a href="{s.link}" style="color:#007bff; font-size:12px;">Read Source: {s.source} →</a>
        </div>"""

    email_html = f"""<html><body style="font-family:sans-serif; max-width:600px; margin:auto;">
        <h2>{period} Briefing — {now.strftime('%B %d')}</h2>
        <p>Good {period}, {RECIPIENT_NAME}. Here is your news.</p>
        {arts_html}
    </body></html>"""

    if RESEND_API_KEY and EMAIL_TO:
        requests.post("https://api.resend.com/emails", 
            headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
            data=json.dumps({"from": EMAIL_FROM_RESEND, "to": [EMAIL_TO], "subject": f"{period} Briefing", "html": email_html}))

if __name__ == "__main__":
    all_stories = fetch_stories()
    for s in all_stories: score_story(s)
    top_selection = pick_top(all_stories)
    if top_selection:
        render_and_send(top_selection)
