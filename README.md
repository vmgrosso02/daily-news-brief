# Daily News OS — Free Automated Morning Brief

One intelligent email every morning. **Total cost: $0.**

## What it does

Every day at 6 AM, a free GitHub Actions cron job:

1. Pulls RSS from ~14 quality sources (Reuters, AP, BBC, FT, WSJ, Bloomberg, Economist, Nature, STAT, MIT Tech Review, ESPN, …)
2. Scores each story by topic relevance (finance, AI, biotech, sports) × source quality × recency
3. Penalizes outrage / engagement-bait keywords
4. Picks the top 5 (max 2 per topic so the brief stays balanced)
5. Renders a calm HTML brief — headline, short summary, source, link to full article
6. Emails the brief to you

No paid APIs. No LLM. No database. No server. No app.

---

## Final file structure

```
News/
├── brief.py                          ← the script that does everything
├── requirements.txt                  ← feedparser, requests (2 deps)
├── .env.example                      ← template for local testing
├── .gitignore                        ← keeps .env out of git
├── README.md                         ← this file
└── .github/
    └── workflows/
        └── daily-brief.yml           ← cron job: 0 13 * * *  (6 AM PT)
```

Six files. That's the whole system.

---

## Setup — 15–20 minutes, one time

### 1. Pick an email provider (Resend OR Gmail SMTP)

**Both are free.** Resend is faster to set up (no app password). Gmail SMTP is the most "official" free path.

#### Option A — Resend (recommended)

1. Sign up at **https://resend.com** (free, no credit card).
2. **API Keys → Create API Key**, copy the value (`re_...`).
3. You can send from `onboarding@resend.dev` immediately — no domain needed.
4. Free tier: **3,000 emails/month**, **100/day**. You'll use 30/month.

#### Option B — Gmail SMTP

1. Make sure **2-Step Verification** is on for your Google account: https://myaccount.google.com/security
2. Go to **https://myaccount.google.com/apppasswords**.
3. App name: `daily-news-brief`. Generate. Copy the 16-character password.
4. You'll use this as `GMAIL_APP_PASSWORD` (the spaces don't matter).

### 2. Push the project to a GitHub repo

```bash
cd "C:\Users\vmgro\OneDrive\Documents\Claude\Projects\News"
git init
git add .
git commit -m "Daily News OS"
```

Create a **private** repo at https://github.com/new (e.g. `daily-news-brief`). Then:

```bash
git remote add origin https://github.com/YOUR-USERNAME/daily-news-brief.git
git branch -M main
git push -u origin main
```

### 3. Add GitHub Actions Secrets

In the repo: **Settings → Secrets and variables → Actions → New repository secret**.

**Always set these two:**

| Secret name         | Value                  |
| ------------------- | ---------------------- |
| `EMAIL_TO`          | `vmgrosso02@yahoo.com` |
| `RECIPIENT_NAME`    | `Michael`              |

**Then add the ones for your chosen email provider:**

**If you picked Resend:**

| Secret name         | Value                                  |
| ------------------- | -------------------------------------- |
| `RESEND_API_KEY`    | `re_...`                               |
| `EMAIL_FROM`        | `Daily Brief <onboarding@resend.dev>`  |

**If you picked Gmail SMTP:**

| Secret name           | Value                            |
| --------------------- | -------------------------------- |
| `GMAIL_USER`          | `your.address@gmail.com`         |
| `GMAIL_APP_PASSWORD`  | `xxxx xxxx xxxx xxxx`            |

(You only need one provider's secrets. If you set both, Resend wins.)

### 4. Test it

Go to **Actions tab → Daily Morning Brief → Run workflow**. The first email arrives in ~2 minutes.

If it lands in Spam, mark it "Not Spam" once and Yahoo will whitelist all future emails from that sender.

That's it. Cron runs automatically every morning from now on.

---

## How the scheduling works

```yaml
on:
  schedule:
    - cron: "0 13 * * *"   # 13:00 UTC every day
  workflow_dispatch:
```

GitHub's cron runs in UTC. `13:00 UTC` is:

- **6:00 AM Pacific Daylight Time** (mid-March → early November)
- **5:00 AM Pacific Standard Time** (rest of the year)

To pick a different local time, change the hour in the cron:

| Local time you want   | Cron line          |
| --------------------- | ------------------ |
| 5 AM PT (DST)         | `"0 12 * * *"`     |
| 6 AM PT (DST)         | `"0 13 * * *"` ← default |
| 7 AM PT (DST)         | `"0 14 * * *"`     |
| 6 AM ET (DST)         | `"0 10 * * *"`     |

GitHub does not honor local timezones in cron — you always compute in UTC.

`workflow_dispatch` adds a "Run workflow" button in the Actions tab so you can trigger an on-demand brief any time without editing cron.

---

## How the email gets sent

`brief.py` auto-selects the provider:

```python
def send_email(subject, html_body):
    if RESEND_API_KEY:
        return send_via_resend(...)          # one HTTP POST
    if GMAIL_USER and GMAIL_APP_PASSWORD:
        return send_via_gmail(...)           # smtplib.SMTP_SSL(smtp.gmail.com, 465)
    raise RuntimeError("No email credentials found.")
```

**Resend path:** one HTTPS POST to `api.resend.com/emails`. No SMTP. No DKIM. Free.

**Gmail path:** standard `smtplib` SSL connection to `smtp.gmail.com:465`, authenticates with your app password, sends a multipart email. Free.

---

## Where the API keys live

| Location              | Used by         | Stored as                                  |
| --------------------- | --------------- | ------------------------------------------ |
| GitHub repo Secrets   | Production cron | Encrypted; injected as env vars at runtime |
| `.env` (local only)   | Local testing   | Gitignored — never committed               |
| **Never** hardcoded in `brief.py` | |

The script reads everything from `os.environ.get(...)`. Same code, two environments.

---

## Local testing

```bash
# install deps
pip install -r requirements.txt

# create your .env from the template
cp .env.example .env
# then edit .env and paste in your real keys

# dry run (generates HTML, doesn't send email)
DRY_RUN=1 python brief.py
# open brief_YYYY-MM-DD.html in a browser

# real send
python brief.py
```

On Windows PowerShell:

```powershell
$env:DRY_RUN="1"; python brief.py
```

---

## Hosting / deployment

**GitHub Actions** — that's it. Free tier gives you 2,000 minutes/month; this job uses ~2 minutes/day = ~60/month. Nothing to maintain.

Why not other options:

- **Your laptop on cron** — only runs when the laptop is on. Skip.
- **Cron-job.org + Replit/PythonAnywhere** — works, but adds a second free service to manage.
- **AWS Lambda + EventBridge** — overkill for this volume and adds an account to maintain.

GitHub Actions wins on simplicity.

---

## Maintenance

**Monthly:** none.

**Yearly:** skim `FEEDS` in `brief.py` — if a source's RSS quietly broke (rare), swap it for another. The script logs entry counts per feed so you'll see a `0 entries` line in the Actions logs if something dies.

**If a brief fails to arrive:**
- Repo → **Actions** tab → click the latest run → expand the failing step. 99% of the time it's an expired/typo'd secret.
- If Resend ever throttles you (extremely unlikely at 30/month), add the Gmail secrets — the script will fall through.

---

## Tweaking what gets surfaced

All the knobs are at the top of `brief.py`:

- `FEEDS` — add/remove RSS sources; per-source quality weight (0–1)
- `INTERESTS` — add keywords, retune topic weights, add a new topic
- `PENALTY_KEYWORDS` — anything you never want to see
- `TOP_N` — number of stories per brief (default 5)
- `MAX_PER_TOPIC` — diversity cap (default 2)
- `RECENCY_HALF_LIFE_HOURS` — how fast old stories decay (default 18)
- `SUMMARY_MAX_CHARS` — summary trim length (default 360)

Commit, push, next 6 AM run picks it up.

---

## What the email contains

```
GOOD MORNING, MICHAEL
Morning Brief — Tuesday, May 12, 2026
Top 5 stories · curated for signal, not engagement

01  MARKETS
    [Headline]
    [2–4 sentence summary from the source feed]
    Source: Reuters Business · Read full article →

02  AI & TECH
    ...

03  BIOTECH / NEURO
    ...

04  MARKETS
    ...

05  SPORTS
    ...

Generated 2026-05-12 13:00 UTC
No outrage. No engagement bait. One brief a day.
```

---

## What this system does NOT do (by design)

- No "why it matters" paragraphs (no LLM)
- No dashboard
- No saved articles
- No notifications
- No database
- No accounts to manage
- No social features
- No analytics

One brief. Every morning. $0. Done.
