# FINN: what's left before submitting

## You need to do (Claude can't do these for you)
1. **Model API key**: add a free `GEMINI_API_KEY` (aistudio.google.com) or `GROQ_API_KEY` to `backend/.env`.
   The keyless fallback ran out of quota after one call, so these features haven't been fully tested:
   the promised-vs-delivered ledger, bull/bear, the business snapshot, model-written Q&A answers and the model-written management bio.
2. **GitHub**: create a repo, then `git remote add origin <url>` and `git push -u origin master`.
3. **Deploy**: Render or Railway, start command `python backend/server.py`, with the key set as an environment variable. Test the link on your phone.
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
