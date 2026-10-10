# blogger-extra-platforms — DEFERRED

Deferred from `work/content-publish-pipeline/` (user-spec interview 2026-06-06).
MVP = Telegram channel @mnemonik only.

## Platforms to add later (NONE have upstream adapters in mnemonik-dev/blogger):

| Platform | Status | Adapter needed |
|----------|--------|----------------|
| **Webapp blog page** (mnemonik.xyz/blog or similar) | DEFERRED — see also work/blogger-site-connector/ | Yes — custom CMS adapter, depends on stack choice |
| **LinkedIn** | DEFERRED | Yes — LinkedIn API requires OAuth + company-page write access (Marketing API tier?) |
| **Discord** | Token exists per user, adapter exists upstream | Just enable in /etc/blogger.env (DISCORD_WEBHOOK_URL) |
| **X (Twitter)** | BLOCKED — paid Basic tier ($100/mo) required for write API access | Adapter exists upstream; deferred until budget |
| **Farcaster** | Token exists per user, adapter exists upstream | Just enable in /etc/blogger.env (FARCASTER_NEYNAR_API_KEY + FARCASTER_SIGNER_UUID) |

## Order of next-iteration work
1. **Discord** + **Farcaster** — cheap, adapter ready, just secrets
2. **Webapp** — needs stack decision first (work/blogger-site-connector/)
3. **LinkedIn** — needs OAuth flow + company-page access
4. **X** — wait for budget
