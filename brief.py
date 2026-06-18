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
EMAIL_TO            = os.environ.get("EMAIL_TO") [cite: 43]
RECIPIENT_NAME      = os.environ.get("RECIPIENT_NAME", "Michael") [cite: 43]
RESEND_API_KEY      = os.environ.get("RESEND_API_KEY", "") [cite: 43]
EMAIL_FROM_RESEND   = "Daily Brief <onboarding@resend.dev>" [cite: 43]

TOP_N = 5 [cite: 43]
MAX_PER_TOPIC = 2 [cite: 43]
MAX_PER_SOURCE = 1      # NEW: Limits any single feed to 1 story per brief
MAX_AGE_HOURS = 24      # NEW: Hard cutoff to prevent repeating old stories

# ONLY these sources are allowed to fill the "Sports" slot
SPORTS_SOURCES = [ [cite: 43]
    "NCAA Lacrosse", "Inside Lacrosse", "ESPN NBA", "ESPN NFL",  [cite: 43]
    "Ramblin Wreck (GT)", "Miami Herald Sports" [cite: 43]
] [cite: 43]

TOPIC_LABELS = { [cite: 44]
    "finance_markets": "Markets", [cite: 44]
    "ai_tech": "AI & Tech", [cite: 44]
    "biotech_neuro": "Biotech / Neuro", [cite: 44]
    "sports": "Sports", [cite: 44]
    "general": "Briefing", [cite: 44]
} [cite: 44]

FEEDS = [ [cite: 44]
    ("Reuters Business",        "https://feeds.reuters.com/reuters/businessNews",                  0.95), [cite: 44]
    ("MarketWatch Top",         "http://feeds.marketwatch.com/marketwatch/topstories/",           0.92), [cite: 44]
    ("The Economist Finance",   "https://www.economist.com/finance-and-economics/rss.xml",         0.88), [cite: 44]
    ("MIT Tech Review",         "https://www.technologyreview.com/feed/",                          0.90), [cite: 44]
    ("Nature",                  "https://www.nature.com/nature.rss",                               0.95), [cite: 44, 45]
    ("STAT Biotech",            "https://www.statnews.com/feed/",                                  0.92), [cite: 45]
    ("NCAA Lacrosse",           "https://www.ncaa.com/news/lacrosse-men/d1/rss.xml",                1.00), [cite: 45]
    ("Inside Lacrosse",         "https://www.insidelacrosse.com/rss",                              1.00), [cite: 45]
    ("ESPN NBA",                "https://www.espn.com/espn/rss/nba/news",                          0.90), [cite: 45]
    ("ESPN NFL",                "https://www.espn.com/espn/rss/nfl/news",                          0.85), [cite: 45, 46]
    ("Ramblin Wreck (GT)",      "https://ramblinwreck.com/feed/",                                  0.95), [cite: 46]
    ("Miami Herald Sports",     "https://www.miamiherald.com/sports/index.rss",                    0.90), [cite: 46]
] [cite: 46]

INTERESTS = { [cite: 47]
    "finance_markets": {"weight": 1.0, "keywords": ["fed", "inflation", "yield", "s&p 500", "nasdaq", "nvda"]}, [cite: 47]
    "ai_tech": {"weight": 1.0, "keywords": ["ai agents", "robotics", "openai", "claude", "gpu", "llm"]}, [cite: 47]
    "biotech_neuro": {"weight": 1.2, "keywords": ["fda", "crispr", "neuroscience", "neuralink", "clinical trial"]}, [cite: 47]
    "sports": {"weight": 3.0, "keywords": ["lacrosse", "lax", "ncaa", "quarterback", "nba", "nfl", "yellow jackets", "touchdown", "playoffs"]}, [cite: 47]
} [cite: 47]

@dataclass [cite: 47]
class Story: [cite: 47]
    title: str [cite: 47]
    link: str [cite: 47]
    source: str [cite: 47]
    source_weight: float [cite: 47]
    published: dt.datetime [cite: 47]
    summary: str [cite: 47]
    topic: str = "general" [cite: 47]
    topic_score: float = 0.0 [cite: 47]

    @property [cite: 48]
    def total_score(self) -> float: [cite: 48]
        # Recency calculation (half-life of 12 hours) [cite: 48]
        age_h = max((dt.datetime.utcnow() - self.published).total_seconds() / 3600.0, 0) [cite: 48]
        recency = 0.5 ** (age_h / 12.0) [cite: 48]
        return self.topic_score * self.source_weight * recency [cite: 48]

def _clean(text: str) -> str: [cite: 48]
    text = re.sub(r"<[^>]+>", "", text or "") [cite: 48]
    return html.unescape(text).strip() [cite: 48]

def fetch_stories() -> list[Story]: [cite: 48]
    stories = [] [cite: 48]
    for name, url, weight in FEEDS: [cite: 48]
        try: [cite: 48]
            parsed = feedparser.parse(url) [cite: 48]
            for entry in parsed.entries[:15]: [cite: 48, 49]
                dt_obj = dt.datetime(*(entry.get("published_parsed") or entry.get("updated_parsed") or dt.datetime.utcnow().timetuple())[:6]) [cite: 49]
                
                # NEW: Drop the story entirely if it's older than 24 hours
                age_h = (dt.datetime.utcnow() - dt_obj).total_seconds() / 3600.0
                if age_h > MAX_AGE_HOURS:
                    continue

                # NEW: Fallback to "description" if "summary" is missing/empty
                raw_summary = entry.get("summary") or entry.get("description") or ""
                cleaned_summary = _clean(raw_summary)
                final_summary = cleaned_summary[:350] + "..." if cleaned_summary else "No summary provided by source."

                stories.append(Story( [cite: 49]
                    title=_clean(entry.get("title", "")), [cite: 49]
                    link=entry.get("link", ""), [cite: 49]
                    source=name, [cite: 49]
                    source_weight=weight, [cite: 49]
                    published=dt_obj, [cite: 49]
                    summary=final_summary [cite: 49]
                )) [cite: 49]
        except: continue [cite: 50]
    return stories [cite: 50]

def score_story(story: Story): [cite: 51]
    text = f"{story.title} {story.summary}".lower() [cite: 51]
    best_topic, best_score = "general", 0.1 [cite: 51]
     [cite: 51]
    for topic, cfg in INTERESTS.items(): [cite: 51]
        # SOURCE LOCK: Only allow "Sports" if source is in the sports list [cite: 51]
        if topic == "sports" and story.source not in SPORTS_SOURCES: [cite: 51]
            continue [cite: 51]
             [cite: 51]
        hits = sum(1 for kw in cfg["keywords"] if kw in text) [cite: 51]
        score = (cfg["weight"] * hits) if hits > 0 else 0.1 [cite: 51]
        if score > best_score: [cite: 51]
            best_score, best_topic = score, topic [cite: 52]
             [cite: 52]
    story.topic, story.topic_score = best_topic, best_score [cite: 52]

def pick_top(stories: Iterable[Story]) -> list[Story]: [cite: 53]
    ranked = sorted(stories, key=lambda s: s.total_score, reverse=True) [cite: 53]
    picked_general = [] [cite: 53]
    picked_sports = [] [cite: 53]
    seen = set() [cite: 53]

    # 1. Mandatory Sports Slot (Must be from a sports source) [cite: 53]
    for s in ranked: [cite: 53]
        if s.source in SPORTS_SOURCES and s.link not in seen: [cite: 53]
            picked_sports.append(s) [cite: 53]
            seen.add(s.link) [cite: 53]
            break # Only need one primary sports story [cite: 53]
     [cite: 53]
    # 2. Fill the rest with non-sports news [cite: 53]
    per_topic = {} [cite: 53]
    per_source = {}  # NEW: Track how many times a source is used

    for s in ranked: [cite: 54]
        if len(picked_general) >= (TOP_N - 1): break [cite: 54]
        if s.link in seen or s.source in SPORTS_SOURCES: continue [cite: 54]
        if per_topic.get(s.topic, 0) >= MAX_PER_TOPIC: continue [cite: 54]
        
        # NEW: Skip if we already have a story from this source
        if per_source.get(s.source, 0) >= MAX_PER_SOURCE: continue 
        
        picked_general.append(s) [cite: 54]
        per_topic[s.topic] = per_topic.get(s.topic, 0) + 1 [cite: 54]
        per_source[s.source] = per_source.get(s.source, 0) + 1  # NEW: Increment source count
        seen.add(s.link) [cite: 54]

    return picked_general + picked_sports [cite: 55]

def render_and_send(stories: list[Story]): [cite: 55]
    now = dt.datetime.utcnow() [cite: 55]
    # Miami Time Adjustment (UTC-4) [cite: 55]
    local_hour = (now.hour - 4) % 24 [cite: 55]
    period = "Morning" if local_hour < 12 else "Evening" [cite: 55]
     [cite: 55]
    # Simple HTML assembly [cite: 55]
    arts_html = "" [cite: 55]
    for i, s in enumerate(stories, 1): [cite: 55]
        label = TOPIC_LABELS.get(s.topic, "Briefing") [cite: 55]
        arts_html += f""" [cite: 55]
        <div style="border-bottom:1px solid #eee; padding:15px 0;"> [cite: 55]
            <small style="color:#666;">{i:02d} | {label}</small> [cite: 55]
            <h3 style="margin:5px 0;">{s.title}</h3> [cite: 55]
            <p style="font-size:14px; color:#333;">{s.summary}</p> [cite: 55]
            <a href="{s.link}" style="color:#007bff; font-size:12px;">Read Source: {s.source} →</a> [cite: 55, 56]
        </div>""" [cite: 56]

    email_html = f"""<html><body style="font-family:sans-serif; max-width:600px; margin:auto;"> [cite: 57]
        <h2>{period} Briefing — {now.strftime('%B %d')}</h2> [cite: 57]
        <p>Good {period}, {RECIPIENT_NAME}. Here is your news.</p> [cite: 57]
        {arts_html} [cite: 57]
    </body></html>""" [cite: 57]

    if RESEND_API_KEY and EMAIL_TO: [cite: 57]
        requests.post("https://api.resend.com/emails",  [cite: 57]
            headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"}, [cite: 57]
            data=json.dumps({"from": EMAIL_FROM_RESEND, "to": [EMAIL_TO], "subject": f"{period} Briefing", "html": email_html})) [cite: 57]

if __name__ == "__main__": [cite: 58]
    all_stories = fetch_stories() [cite: 58]
    for s in all_stories: score_story(s) [cite: 58]
    top_selection = pick_top(all_stories) [cite: 58]
    if top_selection: [cite: 58]
        render_and_send(top_selection) [cite: 58]
