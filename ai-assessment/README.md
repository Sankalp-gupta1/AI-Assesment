# AI Assessment Analyzer

Teacher question paper + student answer sheet upload karta hai → app dono ko
padhta hai (Gemini (Google) ke vision model se) → har question ko uske correct answer
se map karta hai → question par click karne se answer sheet ka **exact
region highlight** hota hai.

> **Simple bhasha mein:** Isko sirf OCR mat samjho. Teen cheezein important
> hain — (1) questions sahi order/numbering mein nikalna, (2) un questions
> ko student ke answers se sahi jodna, (3) us answer ka exact box highlight
> karna. Baaki (grading, feedback) optional bonus hai.

---

## 1. Kya bana hai (what's inside)

```
ai-assessment/
├── backend/          FastAPI server — sab AI/processing yahin hota hai
│   ├── main.py
│   ├── requirements.txt
│   └── .env.example
├── frontend/          ek hi index.html — koi build step nahi chahiye
│   └── index.html
├── sample-data/        (apne test PDFs yahan daal sakte ho)
└── README.md           (yehi file)
```

**Tech choice & why:** Backend = **FastAPI (Python)** kyunki PDF-to-image
aur AI-vision calls Python mein sabse aasan hain. Frontend = ek plain
**HTML + Tailwind (CDN) + vanilla JS** file — Next.js bhi use kar sakte ho
(assignment mein "recommended, not mandatory" likha hai), par ek single
file rakhne se **koi npm install/build step nahi chahiye** — bas file
kholo aur chalao. Chaho toh isi HTML ko Next.js page mein copy-paste kar
sakte ho, logic same rahega.

**AI model:** Gemini (Google, free tier), vision-capable — question paper aur
answer sheet ke page-images seedha Gemini ko bhejte hain aur structured
JSON माँगते hain (questions ka number+text, answers ka number+text+bbox).

---

## 2. Setup — step by step

### Step A — Gemini API key lo (FREE, no card needed)
1. https://aistudio.google.com/apikey par jaake apne Google account se
   sign in karo, aur "Create API key" par click karo. Isme **koi credit
   card ya billing setup nahi chahiye** — Gemini 2.5 Flash free tier
   permanent hai (1500 requests/day tak).
   → **Isse aapko milega:** ek key jaisi `AIzaSy...`

2. `backend/.env.example` ko copy karke `backend/.env` banao aur key paste
   karo:
   ```
   cp backend/.env.example backend/.env
   ```
   Phir `.env` file kholo aur `GEMINI_API_KEY=` ke aage apni key likho.
   → **Isse aapko milega:** backend ab AI calls kar payega.

### Step B — Backend chalao
```bash
cd backend
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
python main.py
```
→ **Isse aapko milega:** terminal mein `Uvicorn running on
http://0.0.0.0:8000` dikhega. Browser mein
`http://localhost:8000/api/health` kholo — `{"ok": true, "ai_configured":
true}` aana chahiye.

### Step C — Frontend kholo
`frontend/index.html` ko seedha double-click karke browser mein kholo
(ya VS Code "Live Server" extension use karo).
→ **Isse aapko milega:** "AI Assessment Analyzer" upload page dikhega.

Agar backend kisi doosre URL par chal raha ho (deploy karne ke baad), toh
`index.html` ke `<script>` tag ke andar ye line badal do:
```js
const API_BASE = window.API_BASE || "http://localhost:8000";
```
ise apne deployed backend URL se replace kar do.

### Step D — Test karo
1. Ek question paper (PDF/image) aur ek handwritten answer sheet
   (PDF/image) upload karo.
2. "Analyze Assessment" par click karo.
   → **Isse aapko milega:** ek progress screen — "Extracting questions" →
   "Extracting handwritten answers" → "Mapping answers" → results.
3. Kisi bhi question par click karo.
   → **Isse aapko milega:** right side mein answer sheet ka wahi page open
   hoga aur us answer ke around ek **peela highlight box** dikhega — yehi
   assignment ka core hai.

---

## 3. Kaise kaam karta hai (approach)

```
Upload (PDF/image)
      │
      ▼
PDF → PNG pages  (PyMuPDF, ~200 DPI)
      │
      ├──► Question paper pages ──► Gemini (vision) ──► [{number, text}]
      │                                                  "11(a)" and "11(b)"
      │                                                  alag entries
      │
      └──► Answer sheet pages ────► Gemini (vision) ──► [{question_number,
                                                            text, bbox}]
                                                          bbox = 0..1
                                                          fraction of page
      │
      ▼
Deterministic mapping (Python, backend/main.py → map_answers())
  1. Exact number match (normalised: "Q11 (a)" → "11a")
  2. Loose fallback if a sub-part number is ambiguous
  3. No match → goes into "Unmatched answers"
      │
      ▼
Final JSON: per-question status (answered / unanswered), list of
regions (page + bbox, multiple pages allowed), unmatched list, summary
      │
      ▼
Frontend renders it — click a question → jump to that page → draw the
highlight box by multiplying the fraction bbox by the *displayed* image
size (no DPI/resolution math needed, because bbox is stored as a fraction,
not pixels).
```

### Edge cases jo handle hote hain
| Case | Kaise handle hua |
|---|---|
| `11(a)` / `11(b)` sub-parts | Vision prompt explicitly bolta hai inhe alag entries banao |
| Out-of-order answers (student ne 3, 1, 4, 2 order mein likha) | Mapping number se hoti hai, page order se nahi |
| Unanswered question | Us question ko koi answer region nahi milta → status `unanswered` |
| Answer jo kisi question se match nahi karta (e.g. Q99) | `unmatched_answers` list mein dikhta hai, click karke dekh sakte ho |
| Answer jo 2+ pages mein failta hai | Same question ke multiple `regions` (har region apna page+bbox) |
| Number unclear/missing handwriting | `confidence: "review"` (⚠) ya unmatched mein chala jaata hai |

---

## 4. Assumptions & limitations (submission form ke liye)

- **AI model used:** Google Gemini (free tier via AI Studio), vision-capable model, called
  per page with a strict JSON-only prompt.
- Semantic-similarity (embeddings-based) fallback matching **is not**
  implemented in this MVP — mapping is number-based + a light fallback for
  ambiguous sub-parts. This was a deliberate MVP-first choice (per the
  assignment's own recommended build order); it can be added as a v2
  enhancement using an embeddings API.
- Grading / marks / AI feedback are **not** implemented — assignment marks
  these as optional ("may include"). The core three requirements
  (extraction, mapping, highlighting) were prioritised instead.
- One student answer sheet at a time (as scoped by the assignment).
- No database — everything is in-memory / temp files, so results are lost
  if the backend restarts (matches "no database required").
- Currently supports **one** student answer sheet per run, PDF or image
  input for both files.

---

## 5. Deploying to a live URL

### Backend → Render (or Railway / Fly.io)
1. Push this repo to GitHub.
2. On Render: **New → Web Service**, point at the repo, root directory
   `backend/`.
3. Build command: `pip install -r requirements.txt`
   Start command: `uvicorn main:app --host 0.0.0.0 --port $PORT`
4. Add environment variable `GEMINI_API_KEY` in Render's dashboard
   (never put it in the frontend).
   → **Isse aapko milega:** ek live backend URL jaisa
   `https://your-app.onrender.com`.

### Frontend → Vercel / Netlify / GitHub Pages
Since it's a single static `index.html`, any static host works:
1. Set `window.API_BASE = "https://your-app.onrender.com"` at the top of
   `index.html` (or edit the `API_BASE` line directly).
2. Deploy the `frontend/` folder as-is (Vercel: "New Project" → framework
   preset "Other" → root `frontend/`).
   → **Isse aapko milega:** ek live frontend URL — yehi teacher ko bhejo.

### Important
Never put `GEMINI_API_KEY` in the frontend/browser code — it must only
live on the backend server's environment variables.
