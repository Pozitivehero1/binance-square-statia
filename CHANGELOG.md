# Changelog

## 2.2.0 — Binance public market data

- Standardized the required Mistral secret/environment variable as `MISTRAL_API`.
- Removed the previous third-party market-data dependency entirely.
- Market topics now use Binance Spot public market data from `data-api.binance.vision/api/v3/ticker/24hr` with no Binance API key.
- Added dynamic liquidity/activity filtering for USDT spot pairs and removed stable/leveraged-token noise.
- Market fact briefs now preserve USDT as the quote unit and reject LLM attempts to relabel USDT figures as USD.
- Removed the separate exchange-brand setting; the project is Binance-specific and the brand is fixed internally.
- Added Binance market-row unit coverage and updated GitHub Actions/README/.env examples.

## 2.1.0 — Production hardening

- Multi-candidate Mistral structured script generation with strict validation.
- Curated evergreen fact briefs and market snapshot guards for percentages, windows, direction and money values.
- Scene-level Pexels selection, recent-media deduplication and credential-free CDN downloads.
- Thematic local fallback visuals for trading, security, liquidity/order-book, wallet and infrastructure topics.
- Edge/ElevenLabs word timing, provider fallback and language-aware Edge voice selection.
- Adaptive ASS captions/hook/CTA with safe wrapping and SRT export.
- Voice mastering, optional music fallback and FFprobe render QA.
- Channel-profile CTA because Shorts description/comment URLs are non-clickable.
- YouTube resumable upload, transient retry, synthetic-media setting and paid-promotion disclosure attempt.
- Per-video retry isolation, state/history deduplication, Docker secret exclusions and GitHub Actions smoke/unit/self tests.
