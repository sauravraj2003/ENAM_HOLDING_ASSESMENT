# FINN: what's left before submitting

## You need to do (Claude can't do these for you)
1. **Model API key**: set `GROQ_API_KEY` in `backend/.env` for local runs and as a Render environment variable after deployment.
   The model-dependent flows still need a live-key demo check: promised-vs-delivered ledger, bull/bear, business snapshot, model-written Q&A answers and the model-written management bio.
2. **GitHub**: create a repo, then `git remote add origin <url>` and `git push -u origin master`.
3. **Deploy**: push the repository to GitHub, create a Render Blueprint from `render.yaml`, enter `GROQ_API_KEY`, and test the deployed link on a phone. The blueprint uses Gunicorn and Groq's current GPT-OSS model IDs.
4. **AI build history**: export this Claude Code conversation (and any other chats you used).
5. **Covering email** to assessment@enam.com, subject "ENAM - IIT screening - 2027". Note: the deadline in the brief was Sun 1 Nov 2026.
   Body: use the README sections (what's in it, architecture and cost, left out, next).

## Nice-to-have (not done)
- Custom date-range picker in the Brief (today: 24h / 3d / week / 30d presets).
- Results numbers shown on the card itself (today they appear when you tap the card).
- PWA manifest so the app can be installed to the home screen.
- Full filing history (screener exposes only about 5 recent filings per company; NSE/BSE APIs block scripts).

## Verified working
Digest and triage on live data · price/volume alerts with "why" context · results card from the real
filing (RELIANCE Q2: matches screener, QoQ/YoY filled) · fundamentals dossier for any ticker ·
Q&A passage retrieval · rules · SSE push · phone-width UI.
