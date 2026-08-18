# Changelog

## 2.3.0 — Visual/timing quality pass

- Removed the `-shortest` final-mux failure mode; video and audio are padded/trimmed to the speech-led target duration.
- Added exact render-duration and audio-truncation FFprobe guards.
- Rebuilt caption grouping around punctuation/phrases and enforced non-overlapping SRT/ASS cues.
- Preserved punctuation from narration when TTS word-boundary providers omit it.
- Prevented thousands-separated numbers such as `50 000` from being split awkwardly across captions.
- Added per-scene `visual_mode` (`stock` / `graphic`) to the structured Mistral plan.
- Routed exact data, bid/ask, spread/order-book mechanics and CTA to deterministic local graphics.
- Added conservative Pexels relevance scoring so obviously unrelated lifestyle footage is rejected.
- Sanitized impossible/over-specific stock queries before calling Pexels.
- Added safe visual zones so generated graphics do not fight the hook, beat overlays or bottom captions.
- Replaced model-written referral sales copy with a deterministic neutral Binance profile CTA.
- Added local content guards for common misleading evergreen simplifications.
- Expanded unit/integration tests, including the real early-cut regression.

## 2.2.0 — Binance public market data

- Removed CoinGecko and its API key requirement.
- Switched current market topics to public Binance Spot market-data endpoints with no Binance API key.
- Renamed the required Mistral secret to `MISTRAL_API`.
- Removed user-facing `EXCHANGE_NAME`; Binance is the project brand.

## 2.1.0 — Production hardening

- Multi-candidate Mistral structured script generation with strict validation.
- Curated evergreen fact briefs and market claim guards.
- Scene-level Pexels selection, recent-media deduplication and credential-free CDN downloads.
- Edge/ElevenLabs word timing, provider fallback and language-aware Edge voice selection.
- Adaptive ASS captions/hook/CTA with SRT export.
- Voice mastering, optional music fallback and FFprobe render QA.
- YouTube resumable upload, retry and disclosure support.
- Per-video retry isolation, state/history deduplication, Docker secret exclusions and GitHub Actions tests.
