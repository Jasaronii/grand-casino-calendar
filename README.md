# Grand Casino Arena live calendar

Community-built, unofficial iCalendar feed of **all** Grand Casino Arena events.

## Publish the feed

1. Create a **public** GitHub repository, e.g. `grand-casino-calendar`.
2. Upload all files from this folder, **including `.github/workflows/update.yml`**.
3. In GitHub, open **Settings → Actions → General** and give workflows **Read and write permissions** if needed.
4. Go to **Actions → Update arena calendar → Run workflow**. Confirm it succeeds and commits `events.ics`.
5. In **Settings → Pages**, select **Deploy from a branch**, branch `main`, folder `/ (root)`, then Save.
6. Your subscription URL is `https://YOUR-USERNAME.github.io/grand-casino-calendar/events.ics` (replace username and repository name if different).
7. On iPhone: **Settings → Apps → Calendar → Calendar Accounts → Add Account → Other → Add Subscribed Calendar** and paste that URL. On some iOS versions the menu path differs; Calendar → Calendars → Add Calendar → Add Subscription Calendar also works.

## How it works

GitHub Actions runs every six hours, opens the venue's public events listing, loads more events, checks linked event detail pages, and extracts structured schema.org Event data. It publishes only events with a verifiable start date/time. The output includes title, event link, address, and Central-time-aware start/end timestamps. Where no end time is published, a **three-hour placeholder duration** is used.

**Important limitations:** The venue may not expose schema.org Event data on every page. The first workflow run is a live compatibility test, not a guarantee. If it finds no structured events it fails rather than replacing your calendar with an empty one. Recurring updates may be delayed by GitHub. A deleted listing disappears from the next generated feed, but individual calendar apps decide when to synchronize. Calendar subscriptions are read-only.

This is an unofficial tool and is not affiliated with Grand Casino Arena.
