"""
AI Assessment Analyzer - Backend
=================================
Simple language mein: Ye backend teacher ke uploaded question-paper aur
answer-sheet ko leta hai, pages ko images mein todta hai, Gemini (vision
model) se questions aur handwritten answers nikalwata hai, dono ko map
karta hai, aur ek clean JSON result deta hai jisse frontend answer-region
ko highlight kar sake.

Flow (also see README.md):
  upload -> pdf_to_images -> extract_questions -> extract_answers
  -> map_answers -> result

No database. Everything lives in the in-memory ASSESSMENTS dict, which is
fine for this assignment (resets if the server restarts).

AI provider: Google Gemini (genuinely free tier via Google AI Studio - no
credit card needed). Get a key at https://aistudio.google.com/apikey
"""

import os
import re
import json
import uuid
import shutil
import traceback
from pathlib import Path
from typing import Optional

import pymupdf as fitz  # PyMuPDF
from fastapi import FastAPI, UploadFile, File, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from google import genai
from google.genai import types

# --------------------------------------------------------------------------
# Setup
# --------------------------------------------------------------------------

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
ALLOWED_ORIGINS = os.environ.get("ALLOWED_ORIGINS", "*").split(",")

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)

app = FastAPI(title="AI Assessment Analyzer")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

# In-memory "database". Key = assessment_id
ASSESSMENTS: dict = {}

STAGES = [
    "uploaded",
    "converting_pages",
    "extracting_questions",
    "extracting_answers",
    "mapping",
    "completed",
]


def set_stage(aid: str, stage: str, error: Optional[str] = None):
    ASSESSMENTS[aid]["stage"] = stage
    ASSESSMENTS[aid]["progress"] = int(
        (STAGES.index(stage) / (len(STAGES) - 1)) * 100
    ) if stage in STAGES else ASSESSMENTS[aid].get("progress", 0)
    if error:
        ASSESSMENTS[aid]["error"] = error
        ASSESSMENTS[aid]["stage"] = "failed"


# --------------------------------------------------------------------------
# Step 1: PDF / image -> page PNGs
# --------------------------------------------------------------------------

def file_to_page_images(upload_path: Path, out_dir: Path, prefix: str) -> list[dict]:
    """Converts an uploaded PDF or image file into one PNG per page.
    Returns a list of {page, path, width, height} sorted by page number.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    pages = []
    suffix = upload_path.suffix.lower()

    if suffix == ".pdf":
        doc = fitz.open(upload_path)
        for i, page in enumerate(doc, start=1):
            # Render at ~200 DPI for legible handwriting
            pix = page.get_pixmap(matrix=fitz.Matrix(200 / 72, 200 / 72))
            out_path = out_dir / f"{prefix}-page-{i}.png"
            pix.save(out_path)
            pages.append({"page": i, "path": str(out_path), "width": pix.width, "height": pix.height})
        doc.close()
    else:
        # Treat as a single-page image
        from PIL import Image
        img = Image.open(upload_path).convert("RGB")
        out_path = out_dir / f"{prefix}-page-1.png"
        img.save(out_path, "PNG")
        pages.append({"page": 1, "path": str(out_path), "width": img.width, "height": img.height})

    return pages


# --------------------------------------------------------------------------
# Step 2: Gemini vision calls
# --------------------------------------------------------------------------

def call_gemini_vision(image_path: str, prompt: str, response_schema: dict | None = None) -> dict:
    """Sends one page image + instruction to Gemini, expects raw JSON back.

    We use response_mime_type="application/json" (plus an explicit
    response_schema when given) so Gemini is constrained to return valid,
    correctly-shaped JSON directly - no markdown-fence stripping needed.
    """
    if client is None:
        raise RuntimeError(
            "GEMINI_API_KEY not set on the backend. Add it to backend/.env "
            "(get a free key at https://aistudio.google.com/apikey)"
        )

    with open(image_path, "rb") as f:
        image_bytes = f.read()

    config_kwargs = {"response_mime_type": "application/json", "temperature": 0.1}
    if response_schema is not None:
        config_kwargs["response_schema"] = response_schema

    resp = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=[
            types.Part.from_bytes(data=image_bytes, mime_type="image/png"),
            prompt,
        ],
        config=types.GenerateContentConfig(**config_kwargs),
    )
    text = (resp.text or "").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Fallback: salvage the first {...} or [...] block in case anything slipped through.
        match = re.search(r"\{.*\}|\[.*\]", text, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        raise


QUESTIONS_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "questions": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "number": {"type": "STRING"},
                    "text": {"type": "STRING"},
                },
                "required": ["number", "text"],
            },
        }
    },
    "required": ["questions"],
}

ANSWERS_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "answers": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "question_number": {"type": "STRING", "nullable": True},
                    "text": {"type": "STRING"},
                    "bbox": {
                        "type": "OBJECT",
                        "properties": {
                            "x": {"type": "NUMBER"},
                            "y": {"type": "NUMBER"},
                            "width": {"type": "NUMBER"},
                            "height": {"type": "NUMBER"},
                        },
                        "required": ["x", "y", "width", "height"],
                    },
                },
                "required": ["question_number", "text", "bbox"],
            },
        }
    },
    "required": ["answers"],
}


QUESTION_PROMPT = """You are reading page {page} of a printed exam QUESTION PAPER.

Extract every question and every labelled sub-part exactly as printed on this page.

Rules:
- Preserve the original numbering exactly as printed, e.g. "1", "2", "11(a)", "11(b)".
- If a question has labelled sub-parts like (a), (b), (c), treat EACH sub-part as its
  own separate entry with a number like "11(a)" - never merge sub-parts into one entry.
- "text" should be the question text itself (verbatim from the page, trimmed).
- If this page has no questions on it (e.g. it's a cover page), return an empty list.
- Output ONLY valid JSON matching this exact schema, nothing else - no markdown
  fences, no explanation:

{{"questions": [{{"number": "1", "text": "..."}}]}}
"""

ANSWER_PROMPT = """You are reading page {page} of a STUDENT'S HANDWRITTEN answer sheet.

For each distinct answer written on this page, identify:
- "question_number": the question number the student wrote/intended, normalised the
  same way as a question paper would number it (e.g. "3", "11(a)"). If no number is
  visible or legible for a piece of writing, set this to null.
- "text": a best-effort transcription of the handwritten content (doesn't need to be
  perfect).
- "bbox": a bounding box drawn tightly around JUST that answer's content on this page,
  expressed as FRACTIONS of the page (0 to 1), not pixels:
  {{"x": left, "y": top, "width": w, "height": h}}
  where x,y is the top-left corner. x+width <= 1 and y+height <= 1.

Rules:
- If handwriting for one answer clearly continues onto this page from a previous page
  (no visible number, just continuation), still return it with the last-known
  question_number if inferable from context, else null.
- If the student wrote something extra that doesn't look like a real question number
  from a normal exam (e.g. a stray "99."), still extract it as-is with that number -
  do not discard it.
- If the page is blank / has no handwriting, return an empty list.
- Output ONLY valid JSON matching this exact schema, nothing else - no markdown
  fences, no explanation:

{{"answers": [{{"question_number": "3", "text": "...", "bbox": {{"x":0.1,"y":0.2,"width":0.8,"height":0.15}}}}]}}
"""


def _items(data, key: str) -> list:
    """Gemini sometimes returns the array directly (e.g. `[...]`) instead of
    the requested `{"key": [...]}` wrapper, even when told not to. Handle
    both shapes so a stray response format never crashes the pipeline.
    """
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        val = data.get(key, [])
        return val if isinstance(val, list) else []
    return []


def extract_questions(pages: list[dict]) -> list[dict]:
    questions = []
    order = 0
    for p in pages:
        data = call_gemini_vision(p["path"], QUESTION_PROMPT.format(page=p["page"]), QUESTIONS_SCHEMA)
        for q in _items(data, "questions"):
            order += 1
            questions.append(
                {
                    "id": f"q{order}",
                    "number": str(q["number"]).strip(),
                    "text": q.get("text", "").strip(),
                    "order": order,
                    "page": p["page"],
                }
            )
    return questions


def extract_answers(pages: list[dict]) -> list[dict]:
    answers = []
    for p in pages:
        data = call_gemini_vision(p["path"], ANSWER_PROMPT.format(page=p["page"]), ANSWERS_SCHEMA)
        for a in _items(data, "answers"):
            answers.append(
                {
                    "question_number": (
                        str(a["question_number"]).strip() if a.get("question_number") else None
                    ),
                    "text": a.get("text", "").strip(),
                    "page": p["page"],
                    "bbox": a.get("bbox"),
                }
            )
    return answers


# --------------------------------------------------------------------------
# Step 3: Mapping
# --------------------------------------------------------------------------

def normalise_number(n: Optional[str]) -> Optional[str]:
    """'Q11 (a)' / '11.a' / '11(a)' -> '11a'.  '2.' -> '2'."""
    if not n:
        return None
    n = n.lower().strip()
    n = re.sub(r"^q\.?\s*", "", n)          # drop leading "Q"
    n = re.sub(r"[().\s]", "", n)           # drop punctuation/spaces
    n = n.rstrip(".")
    return n or None


def map_answers(questions: list[dict], answers: list[dict]) -> dict:
    by_norm = {}
    for q in questions:
        by_norm[normalise_number(q["number"])] = q

    mapped = {q["id"]: {**q, "status": "unanswered", "regions": [], "confidence": None} for q in questions}
    unmatched = []

    for a in answers:
        norm = normalise_number(a["question_number"])
        target_id = None

        if norm and norm in by_norm:
            target_id = by_norm[norm]["id"]
            confidence = "matched"
        elif norm:
            # loose match: strip trailing letters (e.g. "11a" -> "11") as a fallback,
            # only used if it uniquely identifies one question's sub-part family.
            base = re.match(r"^(\d+)", norm)
            candidates = [qid for k, q in by_norm.items() if k and k.startswith(base.group(1))] if base else []
            if len(candidates) == 1:
                target_id = by_norm[[k for k in by_norm if by_norm[k]["id"] == candidates[0]][0]]["id"]
                confidence = "review"
            else:
                confidence = None
        else:
            confidence = None

        if target_id and a.get("bbox"):
            mapped[target_id]["regions"].append({"page": a["page"], "bbox": a["bbox"], "text": a["text"]})
            mapped[target_id]["status"] = "answered"
            # keep the weaker confidence if multiple regions disagree
            if mapped[target_id]["confidence"] != "review":
                mapped[target_id]["confidence"] = confidence
        else:
            unmatched.append(a)

    ordered_questions = sorted(mapped.values(), key=lambda q: q["order"])
    answered = sum(1 for q in ordered_questions if q["status"] == "answered")
    return {
        "questions": ordered_questions,
        "unmatched_answers": unmatched,
        "summary": {
            "total_questions": len(ordered_questions),
            "answered": answered,
            "unanswered": len(ordered_questions) - answered,
            "unmatched": len(unmatched),
        },
    }


# --------------------------------------------------------------------------
# Background pipeline
# --------------------------------------------------------------------------

def run_pipeline(aid: str):
    rec = ASSESSMENTS[aid]
    try:
        set_stage(aid, "converting_pages")
        q_pages = file_to_page_images(Path(rec["question_paper_path"]), Path(rec["dir"]), "question")
        a_pages = file_to_page_images(Path(rec["answer_sheet_path"]), Path(rec["dir"]), "answer")
        rec["question_pages"] = q_pages
        rec["answer_pages"] = a_pages

        set_stage(aid, "extracting_questions")
        questions = extract_questions(q_pages)

        set_stage(aid, "extracting_answers")
        answers = extract_answers(a_pages)

        set_stage(aid, "mapping")
        result = map_answers(questions, answers)
        result["answer_pages"] = [{"page": p["page"], "width": p["width"], "height": p["height"]} for p in a_pages]
        result["question_pages"] = [{"page": p["page"], "width": p["width"], "height": p["height"]} for p in q_pages]
        rec["result"] = result

        set_stage(aid, "completed")
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        set_stage(aid, "failed", error=str(e))


# --------------------------------------------------------------------------
# API routes
# --------------------------------------------------------------------------

@app.get("/api/health")
def health():
    return {"ok": True, "ai_configured": client is not None}


@app.post("/api/upload")
async def upload(
    question_paper: UploadFile = File(...),
    answer_sheet: UploadFile = File(...),
):
    aid = str(uuid.uuid4())[:8]
    work_dir = DATA_DIR / aid
    work_dir.mkdir(parents=True, exist_ok=True)

    q_path = work_dir / f"question_paper{Path(question_paper.filename).suffix}"
    a_path = work_dir / f"answer_sheet{Path(answer_sheet.filename).suffix}"

    with open(q_path, "wb") as f:
        shutil.copyfileobj(question_paper.file, f)
    with open(a_path, "wb") as f:
        shutil.copyfileobj(answer_sheet.file, f)

    ASSESSMENTS[aid] = {
        "id": aid,
        "dir": str(work_dir),
        "question_paper_path": str(q_path),
        "answer_sheet_path": str(a_path),
        "stage": "uploaded",
        "progress": 0,
        "result": None,
        "error": None,
    }
    return {"assessment_id": aid}


@app.post("/api/process/{aid}")
def process(aid: str, background_tasks: BackgroundTasks):
    if aid not in ASSESSMENTS:
        raise HTTPException(404, "Unknown assessment id")
    background_tasks.add_task(run_pipeline, aid)
    return {"started": True}


@app.get("/api/status/{aid}")
def status(aid: str):
    if aid not in ASSESSMENTS:
        raise HTTPException(404, "Unknown assessment id")
    rec = ASSESSMENTS[aid]
    return {"stage": rec["stage"], "progress": rec["progress"], "error": rec.get("error")}


@app.get("/api/result/{aid}")
def result(aid: str):
    if aid not in ASSESSMENTS:
        raise HTTPException(404, "Unknown assessment id")
    rec = ASSESSMENTS[aid]
    if rec["stage"] != "completed":
        raise HTTPException(409, f"Not ready yet, current stage = {rec['stage']}")
    return rec["result"]


@app.get("/api/page-image/{aid}/{doc_type}/{page}")
def page_image(aid: str, doc_type: str, page: int):
    if aid not in ASSESSMENTS:
        raise HTTPException(404, "Unknown assessment id")
    if doc_type not in ("question", "answer"):
        raise HTTPException(400, "doc_type must be 'question' or 'answer'")
    path = Path(ASSESSMENTS[aid]["dir"]) / f"{doc_type}-page-{page}.png"
    if not path.exists():
        raise HTTPException(404, "Page not found")
    return FileResponse(path, media_type="image/png")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)