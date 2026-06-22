#!/usr/bin/env python3
import datetime as dt
import html, json, os, re, requests, feedparser
from dataclasses import dataclass
from typing import Iterable
from google import genai

# --- CONFIG & SOURCE LOCKS ---
EMAIL_TO = os.environ.get("EMAIL_TO")
RECIPIENT_NAME = os.environ.get("RECIPIENT_NAME", "Michael")
GMAIL_USER = os.environ.get("GMAIL_USER")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

ai_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

TOP_N, MAX_PER_TOPIC, MAX_PER_SOURCE, MAX_AGE_HOURS = 5, 2, 1, 24
SPORTS_SOURCES = ["NCAA Lacrosse", "Inside Lacrosse", "ESPN NBA", "ESPN NFL", "Ramblin Wreck (GT)", "Bleacher Report"]
TOPIC_LABELS = {"finance_markets": "Markets", "ai_tech": "AI & Tech", "biotech_neuro": "Biotech / Neuro", "sports": "Sports", "general": "Briefing"}

# ... [Keep your existing FEEDS and INTERESTS dictionaries here] ...

@dataclass
class Story:
    title: str; link: str; source: str; source_weight: float; published: dt.datetime; summary: str
    topic: str = "general"; topic_score: float = 0.0

    @property
    def total_score(self) -> float:
        age_h = max((dt.datetime.utcnow() - self.published).total_seconds() / 3600.0, 0)
        recency = 0.5 ** (age_h / 12.0)
        return self.topic_score * self.source_weight * recency

def enrich_story_with_ai(title: str, summary: str) -> str:
    if not ai_client: return summary
    try:
        prompt = f"Summarize this news in 1-2 sentences. Start with 'The Takeaway:'. Headline: {title}. Summary: {summary}"
        response = ai_client.models.generate_content(model='gemini-2.0-flash', contents=prompt)
        return response.text.strip()
    except: return summary

def fetch_stories() -> list[Story]:
    stories = []
    for name, url, weight in FEEDS:
        try:
            resp = requests.get(url, timeout=5)
            parsed = feedparser.parse(resp.content)
            for entry in parsed.entries[:10]:
                dt_obj = dt.datetime(*(entry.get("published_parsed") or dt.datetime.utcnow().timetuple())[:6])
                if (dt.datetime.utcnow() - dt_obj).total_seconds() / 3600.0 > MAX_AGE_HOURS: continue
                
                title = _clean(entry.get("title", ""))
                summary = _clean(entry.get("summary") or entry.get("description") or "")
                # AI ENRICHMENT HAPPENS HERE
                enriched_summary = enrich_story_with_ai(title, summary)
                
                stories.append(Story(title, entry.get("link", ""), name, weight, dt_obj, enriched_summary))
        except: continue
    return stories

def pick_top(stories: Iterable[Story]) -> list[Story]:
    ranked = sorted(stories, key=lambda s: s.total_score, reverse=True)
    picked, seen, per_topic, per_source = [], set(), {}, {}

    # 1. Mandatory Sports Slot
    for s in ranked:
        if s.source in SPORTS_SOURCES:
            picked.append(s); seen.add(s.link); break
    
    # 2. Fill rest
    for s in ranked:
        if len(picked) >= TOP_N or s.link in seen or per_topic.get(s.topic, 0) >= MAX_PER_TOPIC or per_source.get(s.source, 0) >= MAX_PER_SOURCE: continue
        picked.append(s); seen.add(s.link); per_topic[s.topic] = per_topic.get(s.topic, 0) + 1; per_source[s.source] = per_source.get(s.source, 0) + 1
    return picked

# ... [Include render_and_send() using the SMTP logic we fixed earlier] ...
