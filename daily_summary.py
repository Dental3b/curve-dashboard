name: Daily Summary

on:
  schedule:
    # Monday-Friday: every 15 min from 8am-8pm EST (12:00-00:00 UTC)
    - cron: '*/15 12-23 * * 1-5'
    # Saturday: every 15 min from 8am-6pm EST (12:00-22:00 UTC)
    - cron: '*/15 12-21 * * 6'
  workflow_dispatch:

jobs:
  send-summary:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      
      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      
      - name: Install dependencies
        run: pip install requests
      
      - name: Run daily summary
        run: python daily_summary.py
        env:
          CURVE_CLIENT_ID: ${{ secrets.CURVE_CLIENT_ID }}
          CURVE_CLIENT_SECRET: ${{ secrets.CURVE_CLIENT_SECRET }}
          TWILIO_SID: ${{ secrets.TWILIO_SID }}
          TWILIO_TOKEN: ${{ secrets.TWILIO_TOKEN }}
