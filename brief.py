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

# CONFIG
EMAIL_TO = os.environ.get("EMAIL_TO")
GMAIL_USER = os.environ.get("GMAIL_USER")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# Initialize AI
ai_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

# ... [Keep your existing FEEDS, INTERESTS, SPORTS_SOURCES, and TOPIC_LABELS here] ...

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
        prompt = f"Summarize this news in 1-2 sentences. Start with 'The Takeaway:'. Headline: {title}. Summary: {summary}"
        response = ai_client.models.generate_content(model='gemini-2.5-flash', contents=prompt)
        return response.text.strip()
    except Exception as e:
        return f"The Takeaway: (AI Synthesis Pending) {summary[:200]}..."

def fetch_stories() -> list[Story]:
    stories = []
    headers = {"User-Agent": "Mozilla/5.0"}
    for name, url, weight in FEEDS:
        try:
            resp = requests.get(url, headers=headers, timeout=5)
            parsed = feedparser.parse(resp.content)
            for entry in parsed.entries[:5]: # Limit to 5 per feed to save API time
                dt_obj = dt.datetime(*(entry.get("published_parsed") or dt.datetime.utcnow().timetuple())[:6])
                raw_summary = _clean(entry.get("summary") or entry.get("description") or "")
                
                # Enrich
                enriched = enrich_story_with_ai(_clean(entry.get("title", "")), raw_summary)
                
                stories.append(Story(
                    title=_clean(entry.get("title", "")),
                    link=entry.get("link", ""),
                    source=name,
                    source_weight=weight,
                    published=dt_obj,
                    summary=enriched
                ))
        except: continue
    return stories

def send_email(subject, html_body):
    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = GMAIL_USER
    msg['To'] = EMAIL_TO
    msg.add_alternative(html_body, subtype='html')
    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
        smtp.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        smtp.send_message(msg)

if __name__ == "__main__":
    all_stories = fetch_stories()
    for s in all_stories: score_story(s)
    top_selection = pick_top(all_stories)
    if top_selection:
        # Generate your HTML here using top_selection...
        send_email(f"Your Briefing — {dt.date.today()}", email_html)
