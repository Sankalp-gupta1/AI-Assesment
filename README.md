# AI Assessment Analyzer

## **Live App:** https://ai-assesment-nine.vercel.app/


An AI-powered tool that helps a teacher check a student's answer sheet faster. Upload a question paper and one student's handwritten answer sheet — the app reads both, matches every question to its answer, and shows exactly where on the answer sheet that answer is written, highlighted.

<img width="1917" height="872" alt="image" src="https://github.com/user-attachments/assets/1892894c-2e40-4cff-9538-3629cc9b6516" />

---

## What it does

1. **Upload** a question paper and a student's answer sheet (PDF or image).
2. The app converts every page into an image, then sends it to **Google Gemini** (vision model) to read:
   - Every question from the paper — including labelled sub-parts like `11(a)` and `11(b)`, kept as separate entries.
   - Every handwritten answer block from the sheet, along with the exact region (bounding box) it's written in.
3. It **maps** each answer to its question by number, with a fallback for messy handwriting.
4. On the results page, click any question — the matching answer region gets **highlighted** on the answer sheet image, even if the answer spans multiple pages.
5. Questions the student skipped are marked **unanswered**, and any stray answer that doesn't match a real question (e.g. `Q99`) is shown separately as **unmatched**.

---

## Why it's useful

Checking handwritten answer sheets by hand is slow — flipping pages back and forth to find which answer belongs to which question. This tool does that lookup instantly, so a teacher can jump straight to grading instead of searching.

---

## Tech Stack

| Part | Technology |
|---|---|
| Frontend | Plain HTML, CSS, JavaScript |
| Backend | Python, FastAPI |
| PDF → image conversion | PyMuPDF |
| AI model | Google Gemini (`gemini-2.5-flash`), via the free-tier Gemini API |
| Storage | In-memory (no database needed — resets if the server restarts) |
| Hosting | Frontend on Vercel |

---

## How it works, step by step

**Step 1 — Convert pages to images**
Every uploaded PDF page (or image) is converted into a PNG at ~200 DPI, so handwriting stays legible for the AI to read.

**Step 2 — Extract questions**
Each question-paper page image is sent to Gemini with instructions to return every question in printed order, preserving the original numbering, and treating each labelled sub-part as its own entry.

**Step 3 — Extract answers**
Each answer-sheet page image is sent to Gemini with instructions to find every handwritten answer block, read the question number the student wrote, transcribe the text, and return a tight bounding box around it.

**Step 4 — Map answers to questions**
Answers are matched to questions by their (normalised) question number — so `"Q.11 (a)"` and `"11a"` are treated as the same. If a number is unclear, a loose fallback tries to resolve it; if it still can't be resolved confidently, the answer is flagged for review instead of being guessed.

**Step 5 — Show results**
The frontend displays the question list next to the answer sheet. Clicking a question loads the right page and draws the highlight box over the matched region, using the exact coordinates returned by the AI.

---

## Running it locally

**Backend**
```
cd backend
python -m venv venv
venv\Scripts\activate        # Windows
pip install -r requirements.txt
```
Create a `.env` file inside `backend/` with:
```
GEMINI_API_KEY=your_key_here
```
(Get a free key at https://aistudio.google.com/apikey)

Then start the server:
```
uvicorn main:app --reload
```

**Frontend**
Just open `frontend/index.html` in a browser, or serve it with any static file server.

---

## Assumptions & Limitations

- Handwriting that is very messy or faint may lower transcription and bounding-box accuracy — such answers are flagged for manual review rather than guessed.
- No login, no database — everything is processed per session and kept in memory, as allowed by the assignment.
- Built and tested for one question paper + one student's answer sheet at a time.
