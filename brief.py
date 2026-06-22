#!/usr/bin/env python3
import datetime as dt
import html
import os
import re
import smtplib
from email.message import EmailMessage
import requests
import feedparser
from google import genai

# --- CONFIG ---
FEEDS = [
    ("Reuters", "https://feeds.reuters.com/reuters/businessNews", 1.0),
    ("MIT Tech Review", "https://www.technologyreview.com/feed/", 0.9),
]
EMAIL_TO = os.environ.get("EMAIL_TO")
GMAIL_USER = os.environ.get("GMAIL_USER")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
ai_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

# --- AI & FETCHING ---
def enrich_story_with_ai(title, summary):
    if not ai_client: return summary
    try:
        prompt = f"Summarize in 1-2 sentences. Start with 'The Takeaway:'. Headline: {title}. Summary: {summary}"
        response = ai_client.models.generate_content(model='gemini-2.0-flash', contents=prompt)
        return response.text.strip()
    except: return f"The Takeaway: {summary[:150]}..."

def fetch_stories():
    all_stories = []
    for name, url, weight in FEEDS:
        try:
            resp = requests.get(url, timeout=5)
            parsed = feedparser.parse(resp.content)
            for entry in parsed.entries[:5]:
                title = html.unescape(re.sub(r"<[^>]+>", "", entry.get("title", "")))
                summary = html.unescape(re.sub(r"<[^>]+>", "", entry.get("summary") or entry.get("description", "")))
                enriched = enrich_story_with_ai(title, summary)
                all_stories.append({"title": title, "summary": enriched, "link": entry.get("link"), "source": name})
        except: continue
    return all_stories

# --- EMAIL ---
def send_email(html_body):
    msg = EmailMessage()
    msg['Subject'] = f"Daily Brief — {dt.date.today()}"
    msg['From'] = GMAIL_USER
    msg['To'] = EMAIL_TO
    msg.set_content("Please enable HTML view.")
    msg.add_alternative(html_body, subtype='html')
    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
        smtp.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        smtp.send_message(msg)

if __name__ == "__main__":
    stories = fetch_stories()
    # Build your HTML
    html_content = "<html><body>"
    for s in stories:
        html_content += f"<h3>{s['title']}</h3><p>{s['summary']}</p><a href='{s['link']}'>{s['source']}</a><br>"
    html_content += "</body></html>"
    send_email(html_content)
