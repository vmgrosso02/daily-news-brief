#!/usr/bin/env python3
import datetime as dt
import html
import json
import os
import re
import requests
import feedparser
import smtplib
from email.message import EmailMessage
from dataclasses import dataclass
from typing import Iterable
from google import genai

# ---------------------------------------------------------------------------
# CONFIG & SOURCE LOCKS
# ---------------------------------------------------------------------------
EMAIL_TO            = os.environ.get("EMAIL_TO")
RECIPIENT_NAME      = os.environ.get("RECIPIENT_NAME", "Michael")
GMAIL_USER          = os.environ.get("GMAIL_USER")
GMAIL_APP_PASSWORD  = os.environ.get("GMAIL_APP_PASSWORD")
GEMINI_API_KEY      = os.environ.get("GEMINI_API_KEY")

# Initialize Gemini Client safely
ai_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

TOP_N = 5
MAX_PER_TOPIC = 2
MAX_PER_SOURCE = 1      # Strict source diversity limit
MAX_AGE_HOURS = 24      # Hard age cutoff to guarantee fresh daily insights

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

FEEDS = [
    # --- Markets & Finance ---
    ("Reuters Business",        "https://feeds.reuters.com/reuters/businessNews",                   0.95),
    ("CNBC Business",           "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10001142", 0.95),
    ("Yahoo Finance",           "https://finance.yahoo.com/news/rssindex",                          0.85),
    ("MarketWatch Top",         "http://feeds.marketwatch.com/marketwatch/topstories/",             0.90),
    ("Benzinga Core",           "https://feeds.benzinga.com/benzinga",                              0.85),
    ("Federal Reserve News",    "https://www.federalreserve.gov/feeds/press_all.xml",               1.00),
    
    # --- AI & Tech ---
    ("Ars Technica",            "https://feeds.feedburner.com/arstechnica/index",                   0.95),
    ("The Verge",               "https://www.theverge.com/rss/index.xml",                           0.90),
    ("TechCrunch",              "https://techcrunch.com/feed/",                                     0.90),
    ("Hacker News Top",         "https://news.ycombinator.com/rss",                                 0.95),
    ("Tech Xplore",             "https://techxplore.com/feeds/",                                    0.90),
    ("Wired Top Stories",       "https://www.wired.com/feed/rss",                                   0.90),
    ("Engadget",                "https://www.engadget.com/rss.xml",                                 0.85),
    
    # --- Biotech & Neuroscience ---
    ("BioSpace Biotech",        "https://www.biospace.com/rss/",                                    0.95),
    ("ScienceDaily Mind/Brain", "https://www.sciencedaily.com/rss/mind_brain.xml",                  0.95),
    ("ScienceDaily Biotech",    "https://www.sciencedaily.com/rss/matter_energy/biotechnology.xml", 0.95),
    ("Medical Xpress",          "https://medicalxpress.com/rss-feed/",                              0.90),
    ("Phys.org Biology",        "https://phys.org/feeds/biology-news/",                             0.90),
    ("Fierce Biotech",          "https://www.fiercebiotech.com/fiercebiotechcom/rss-feeds",         0.90),
    ("EurekAlert Science",      "https://www.eurekalert.org/rss/technology.xml",                    0.85),
    
    # --- Sports ---
    ("NCAA Lacrosse",           "https://www.ncaa.com/news/lacrosse-men/d1/rss.xml",                1.00),
    ("Inside Lacrosse",         "https://www.insidelacrosse.com/rss",                               1.00),
    ("ESPN NBA",                "https://www.espn.com/espn/rss/nba/news",                           0.90),
    ("ESPN NFL",                "https://www.espn.com/espn/rss/nfl/news",                           0.90),
    ("Ramblin Wreck (GT)",      "https://ramblinwreck.com/feed/",                                   0.95),
    ("Bleacher Report",         "https://bleacherreport.com/articles/feed",                         0.85),
]

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
        age_h = max((dt.datetime.utcnow() - self.published).total_seconds() / 3600.0, 0)
        recency = 0.5 ** (age_h / 12.0)
        return self.topic_score * self.source_weight * recency

def _clean(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text or "")
    return html.unescape(text).strip()

def enrich_story_with_ai(title: str, summary: str) -> str:
    if not ai_client:
        return summary
    try:
        prompt = f"Summarize this news headline and details into 1-2 tight sentences explaining the ultimate significance. Start with 'The Takeaway: '. Do not repeat the title. Headline: {title}. Details: {summary}"
        response = ai_client.models.generate_content(model='gemini-2.0-flash', contents=prompt)
        return response.text.strip()
    except Exception as e:
        print(f"AI enrichment failed: {e}")
        return summary

def fetch_stories() -> list[Story]:
    stories = []
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    
    for name, url, weight in FEEDS:
        try:
            resp = requests.get(url, headers=headers, timeout=4)
            if resp.status_code != 200:
                continue
                
            parsed = feedparser.parse(resp.content)
            for entry in parsed.entries[:15]:
                dt_obj = dt.datetime(*(entry.get("published_parsed") or entry.get("updated_parsed") or dt.datetime.utcnow().timetuple())[:6])
                
                # Check 24-hour freshness rule
                age_h = (dt.datetime.utcnow() - dt_obj).total_seconds() / 3600.0
                if age_h > MAX_AGE_HOURS:
                    continue

                raw_summary = entry.get("summary") or entry.get("description") or ""
                cleaned_summary = _clean(raw_summary)
                
                stories.append(Story(
                    title=_clean(entry.get("title", "")),
                    link=entry.get("link", ""),
                    source=name,
                    source_weight=weight,
                    published=dt_obj,
                    summary=cleaned_summary
                ))
        except: 
            continue
    return stories

def score_story(story: Story):
    text = f"{story.title} {story.summary}".lower()
    best_topic, best_score = "general", 0.1
    
    for topic, cfg in INTERESTS.items():
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

    # 1. Mandatory Sports Slot Lock
    for s in ranked:
        if s.source in SPORTS_SOURCES and s.link not in seen:
            s.summary = enrich_story_with_ai(s.title, s.summary)
            picked_sports.append(s)
            seen.add(s.link)
            break 
    
    # 2. Fill the rest based on your strict source diversity limits
    per_topic = {}
    per_source = {}  

    for s in ranked:
        # Fallback: If no sports story was found, max general slots goes up to TOP_N (5) instead of 4
        max_general_slots = TOP_N - len(picked_sports)
        if len(picked_general) >= max_general_slots: break
        if s.link in seen or s.source in SPORTS_SOURCES: continue
        if per_topic.get(s.topic, 0) >= MAX_PER_TOPIC: continue
        if per_source.get(s.source, 0) >= MAX_PER_SOURCE: continue 
        
        s.summary = enrich_story_with_ai(s.title, s.summary)
        picked_general.append(s)
        per_topic[s.topic] = per_topic.get(s.topic, 0) + 1
        per_source[s.source] = per_source.get(s.source, 0) + 1  
        seen.add(s.link)

    return picked_general + picked_sports

def render_and_send(stories: list[Story], debug_mode=False, debug_msg=""):
    now = dt.datetime.utcnow()
    miami_time = now - dt.timedelta(hours=4)
    local_hour = miami_time.hour
    period = "Morning" if local_hour < 12 else "Evening"
    date_str = miami_time.strftime('%A, %B %d')
    
    if debug_mode:
        email_html = f"<html><body><h2>Briefing Error Diagnostic</h2><p>{debug_msg}</p></body></html>"
    else:
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
            <h2>Your {period} Briefing — {date_str}</h2>
            <p>Good {period}, {RECIPIENT_NAME}. Here is your news.</p>
            {arts_html}
        </body></html>"""

    msg = EmailMessage()
    msg['Subject'] = f"Your {period} Briefing — {date_str}" if not debug_mode else "Daily Brief Alert: No Stories Found"
    msg['From'] = f"Daily Brief <{GMAIL_USER}>"
    msg['To'] = EMAIL_TO
    msg.add_alternative(email_html, subtype='html')

    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
        smtp.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        smtp.send_message(msg)

if __name__ == "__main__":
    all_stories = fetch_stories()
    for s in all_stories: score_story(s)
    top_selection = pick_top(all_stories)
    
    if top_selection:
        render_and_send(top_selection)
    else:
        err_msg = f"Fetched total of {len(all_stories)} raw stories, but 0 cleared the 24-hour freshness or keyword filtering constraints."
        render_and_send([], debug_mode=True, debug_msg=err_msg)
