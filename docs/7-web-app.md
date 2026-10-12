# 7 · The web app

[← 6 · API](6-api-reference.md) · [README](../README.md) · Next: [8 · Deployment](8-deployment.md)

A single-page app in plain HTML, CSS and JavaScript modules — no build step. The API serves it at
**<http://127.0.0.1:8080>**. Charts use Chart.js 4.4.6, stored locally in `frontend/vendor/` (MIT).

Code: [`frontend/`](../frontend/)

| File | Job |
|---|---|
| `index.html` | App shell: sidebar, top bar, the six pages, the history drawer, the sign-in screen |
| `config.js` | `apiBase`: empty = same site as the API; or the backend URL when hosted separately |
| `css/styles.css` | Design tokens, light / dark theme, responsive layout, animations |
| `js/app.js` | Router, theme, live status, drawer |
| `js/lib/` | `api.js` (fetch wrapper), `dom.js` (safe element helpers), `meta.js` (labels, colours), `charts.js` |
| `js/components/result.js` | The analysis report (verdict, charts, reasoning, findings, email) |
| `js/views/` | One module per page: `overview`, `review`, `history`, `how`, `system` |

## Pages

| Page | What it shows |
|---|---|
| **Overview** | KPI tiles with sparklines, latest review with Agent A's reasoning, risk & next actions, review volume per day, outcome mix, top issues, supplier health |
| **New review** | ① choose documents (demo PDF pairs, four Kaggle datasets, or your own upload under *Advanced options*) → ② check the data (invoice and PO side by side, colour-marked) → ③ the analysis report |
| **History** | Every saved review, with filters, search, paging and a detail drawer |
| **How it works** | Animated 7-step walkthrough (Slow / Normal speed for presenting) and a looping system map |
| **Agents & system** | Both live Agent Cards and the status of every service, explained in plain English |

## The analysis report

| Tab | Contents |
|---|---|
| **Summary** | Verdict banner, checks passed / mismatches / missing / total vs PO, outcome donut, invoice-vs-PO bars, *Why this result* (AI explanation, possible causes, fact-check badge), *Needs attention* cards, next steps |
| **AI reasoning** | *How Agent A reasoned* timeline, money at stake, supplier track record and duplicate warning |
| **All checks** | Every finding in one table |
| **Supplier email** | The drafted query with *Copy* and *Open in mail app* |
| **Technical** | Agent A and Agent B task IDs, Supabase save status, reasoning source, assumptions |

## Design and safety

- Light and dark themes (follows the system, with a toggle); works down to phone width; respects
  reduced motion.
- All data is inserted as text, never as HTML; the page talks only to the NexusAI API.
- When the backend has an access password, a sign-in screen appears first; the token is kept in
  this browser for 12 hours, and the top-bar button signs out.
