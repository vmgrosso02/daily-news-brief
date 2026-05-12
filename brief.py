name: Daily News Brief

on:
  schedule:
    # 13:00 UTC is 8:00 AM CDT (Waco Time)
    - cron: '0 13 * * *'
    # 23:00 UTC is 6:00 PM CDT (Waco Time)
    - cron: '0 23 * * *'
  workflow_dispatch: # Allows you to run it manually anytime

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout code
        uses: actions/checkout@v3

      - name: Set up Python
        uses: actions/checkout@v4
        with:
          python-version: '3.9'

      - name: Install dependencies
        run: pip install feedparser requests

      - name: Run script
        env:
          RESEND_API_KEY: ${{ secrets.RESEND_API_KEY }}
          EMAIL_TO: "vmgrosso02@yahoo.com"
          RECIPIENT_NAME: "Michael"
          # GMAIL_USER: ${{ secrets.GMAIL_USER }}
          # GMAIL_APP_PASSWORD: ${{ secrets.GMAIL_APP_PASSWORD }}
        run: python brief.py
