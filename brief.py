import datetime as dt
import html
import os
import re
import smtplib
from email.message import EmailMessage
import requests
import feedparser
from google import genai

# --- CONFIGURATION (GLOBAL CONSTANTS) ---
# Define these at the top level
FEEDS = [
    ("Reuters", "https://feeds.reuters.com/reuters/businessNews", 1.0),
    ("MIT Tech Review", "https://www.technologyreview.com/feed/", 0.9),
    # Add your other feeds here
]

# Load credentials from environment variables
EMAIL_TO = os.environ.get("EMAIL_TO")
GMAIL_USER = os.environ.get("GMAIL_USER")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# Initialize AI
ai_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

# --- FUNCTIONS ---

def _clean(text: str) -> str:
    """Helper to strip HTML and clean strings."""
    return html.unescape(re.sub(r"<[^>]+>", "", text or "")).strip()

def enrich_story_with_ai(title: str, summary: str) -> str:
    """Uses Gemini to generate a personalized summary."""
    if not ai_client:
        return summary
    try:
        prompt = f"Summarize this news in 1-2 sentences. Start with 'The Takeaway:'. Headline: {title}. Summary: {summary}"
        response = ai_client.models.generate_content(model='gemini-2.0-flash', contents=prompt)
        return response.text.strip()
    except Exception as e:
        return f"AI Summary unavailable: {summary[:150]}..."

def fetch_stories():
    """Fetches and enriches stories from FEEDS."""
    all_stories = []
    for name, url, weight in FEEDS:
        try:
            resp = requests.get(url, timeout=5)
            parsed = feedparser.parse(resp.content)
            for entry in parsed.entries[:5]:
                title = _clean(entry.get("title", ""))
                summary = _clean(entry.get("summary", ""))
                enriched = enrich_story_with_ai(title, summary)
                # Store as dict or object; here using a simple dict
                all_stories.append({"title": title, "summary": enriched, "link": entry.get("link")})
        except Exception as e:
            print(f"Error fetching {name}: {e}")
    return all_stories

def send_email(subject, content):
    """Sends email via Gmail SMTP."""
    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = GMAIL_USER  # This makes it come from you
    msg['To'] = EMAIL_TO
    msg.set_content(content)
    
    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
        smtp.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        smtp.send_message(msg)

# --- EXECUTION ---
if __name__ == "__main__":
    stories = fetch_stories()
    # Build your HTML string here...
    email_body = "Your daily news update..." 
    send_email(f"Daily Brief — {dt.date.today()}", email_body)
