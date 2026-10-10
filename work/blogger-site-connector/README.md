# blogger-site-connector — DEFERRED

Deferred from `work/content-publish-pipeline/` (user-spec interview 2026-06-06).

## Why deferred
MVP of content-publish-pipeline targets Telegram channel @mnemonik only. Adding
publish to mnemonik.xyz webapp blog page is a SEPARATE feature — needs:
- Decision on webapp stack (Ghost / Hugo / Next.js / custom CMS)
- Custom blogger adapter (mnemonik-dev/blogger has no webapp adapter)
- Auth model for the publish API

## When to revive
After content-publish-pipeline ships and the TG-only flow is validated in
production, run a fresh user-spec interview for site-connector with answers to:
1. What is the webapp blog: stack, URL, deploy mechanism
2. Auth/API for posting
3. How attestations show up on the public web page (cite the Mnemonik MCP receipt?)
