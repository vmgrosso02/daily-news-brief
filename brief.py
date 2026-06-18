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
MAX_PER_SOURCE = 1      # Strict source diversity limit
MAX_AGE_HOURS = 24      # Hard age cutoff to guarantee fresh daily insights

# ONLY these sources are allowed to fill the "Sports" slot
SPORTS_SOURCES = [
    "NCAA Lacrosse", "Inside Lacrosse", "ESPN NBA", "ESPN NFL", 
    "Ramblin Wreck (GT)", "Bleacher Report"
]

TOPIC_LABELS = {
    "finance_markets": "Markets",
    "ai_tech": "AI & Tech",
    "biotech_neuro": "Biotech / Neuro",
    "sports": "Sports",
    "general": "Briefing",
}

# EXPANDED, 100% FREE & NON-PAYWALLED FEEDS (Comprehensive Stream Matrix)
FEEDS = [
    # --- Markets & Finance ---
    ("Reuters Business",        "https://feeds.reuters.com/reuters/businessNews",                  0.95),
    ("CNBC Business",           "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10001142", 0.95),
    ("Yahoo Finance",           "https://finance.yahoo.com/news/rssindex",                          0.85),
    ("MarketWatch Top",         "http://feeds.marketwatch.com/marketwatch/topstories/",           0.90),
    ("Benzinga Core",           "https://feeds.benzinga.com/benzinga",                             0.85),
    ("Federal Reserve News",    "https://www.federalreserve.gov/feeds/press_all.xml",              1.00),
    
    # --- AI & Tech ---
    ("Ars Technica",            "https://feeds.feedburner.com/arstechnica/index",                  0.95),
    ("The Verge",               "https://www.theverge.com/rss/index.xml",                          0.90),
    ("TechCrunch",              "https://techcrunch.com/feed/",                                    0.90),
    ("Hacker News Top",         "https://news.ycombinator.com/rss",                                0.95),
    ("Tech Xplore",             "https://techxplore.com/feeds/",                                   0.90),
    ("Wired Top Stories",       "https://www.wired.com/feed/rss",                                  0.90),
    ("Engadget",                "https://www.engadget.com/rss.xml",                                0.85),
    
    # --- Biotech & Neuroscience ---
    ("BioSpace Biotech",        "https://www.biospace.com/rss/",                                   0.95),
    ("ScienceDaily Mind/Brain", "https://www.sciencedaily.com/rss/mind_brain.xml",                  0.95),
    ("ScienceDaily Biotech",    "https://www.sciencedaily.com/rss/matter_energy/biotechnology.xml", 0.95),
    ("Medical Xpress",          "https://medicalxpress.com/rss-feed/",                             0.90),
    ("Phys.org Biology",        "https://phys.org/feeds/biology-news/",                            0.90),
    ("Fierce Biotech",          "https://www.fiercebiotech.com/fiercebiotechcom/rss-feeds",        0.90),
    ("EurekAlert Science",      "https://www.eurekalert.org/rss/technology.xml",                   0.85),
    
    # --- Sports ---
    ("NCAA Lacrosse",           "https://www.ncaa.com/news/lacrosse-men/d1/rss.xml",                1.00),
    ("Inside Lacrosse",         "https://www.insidelacrosse.com/rss",                              1.00),
    ("ESPN NBA",                "https://www.espn.com/espn/rss/nba/news",                          0.90),
    ("ESPN NFL",                "https://www.espn.com/espn/rss/nfl/news",                          0.90),
    ("Ramblin Wreck (GT)",      "https://ramblinwreck.com/feed/",                                  0.95),
    ("Bleacher Report",         "https://bleacherreport.com/articles/feed",                        0.85),
]

# DEEPLY EXPANDED KEYWORDS FOR SCALED MINING
INTERESTS = {
    "finance_markets": {
        "weight": 1.0, 
        "keywords": [
            "fed", "inflation", "yield", "s&p 500", "nasdaq", "nvda", "stocks", "rate cut", 
            "recession", "jerome powell", "treasury", "bear market", "bull market", "macroeconomics", 
            "earnings report", "dow jones", "interest rates", "ipo", "equity", "bonds", "wall street"
        ]
    },
    "ai_tech": {
        "weight": 1.0, 
        "keywords": [
            "ai agents", "robotics", "openai", "claude", "gpu", "llm", "chatgpt", "generative ai", 
            "nvidia", "silicon", "machine learning", "deep learning", "anthropic", "copilot", 
            "quantum computing", "semiconductor", "tsmc", "amd", "transformers", "neural network", "hbm"
        ]
    },
    "biotech_neuro": {
        "weight": 1.2, 
        "keywords": [
            "fda", "crispr", "neuroscience", "neuralink", "clinical trial", "gene therapy", 
            "alzheimer", "brain-computer", "biopharma", "immunotherapy", "oncology", "pharma", 
            "mrna", "therapeutic", "neurodegenerative", "dementia", "synapse", "optogenetics", 
            "genomics", "car-t", "brain mapping", "axon", "parkinson"
        ]
    },
    "sports": {
        "weight": 3.0, 
        "keywords": [
            "lacrosse", "lax", "ncaa", "quarterback", "nba", "nfl", "yellow jackets", "touchdown", 
            "playoffs", "espn", "championship", "draft", "super bowl", "finals", "bracket", 
            "completions", "touchdowns", "halftime", "mvp", "gridiron"
        ]
    },
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
    # Using a modern user agent string keeps standard firewalls from dropping the request
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    
    for name, url, weight in FEEDS:
        try:
            # SCALING UPGRADE: Raw requests engine with a 4-second timeout limit.
            # This protects the GitHub Action routine if any single free source is down or slow.
            resp = requests.get(url, headers=headers, timeout=4)
            if resp.status_code != 200:
                continue
                
            parsed = feedparser.parse(resp.content)
            for entry in parsed.entries[:15]:
                dt_obj = dt.datetime(*(entry.get("published_parsed") or entry.get("updated_parsed") or dt.datetime.utcnow().timetuple())[:6])
                
                # Drop the story entirely if it's older than 24 hours
                age_h = (dt.datetime.utcnow() - dt_obj).total_seconds() / 3600.0
                if age_h > MAX_AGE_HOURS:
                    continue

                # Fallback to "description" if "summary" is missing/empty
                raw_summary = entry.get("summary") or entry.get("description") or ""
                cleaned_summary = _clean(raw_summary)
                final_summary = cleaned_summary[:350] + "..." if cleaned_summary else "No summary provided by source."

                stories.append(Story(
                    title=_clean(entry.get("title", "")),
                    link=entry.get("link", ""),
                    source=name,
                    source_weight=weight,
                    published=dt_obj,
                    summary=final_summary
                ))
        except: 
            continue
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
    per_source = {}  # Track how many times a source is used

    for s in ranked:
        if len(picked_general) >= (TOP_N - 1): break
        if s.link in seen or s.source in SPORTS_SOURCES: continue
        if per_topic.get(s.topic, 0) >= MAX_PER_TOPIC: continue
        if per_source.get(s.source, 0) >= MAX_PER_SOURCE: continue 
        
        picked_general.append(s)
        per_topic[s.topic] = per_topic.get(s.topic, 0) + 1
        per_source[s.source] = per_source.get(s.source, 0) + 1  
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
