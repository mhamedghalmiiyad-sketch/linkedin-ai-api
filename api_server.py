import re
import time
import threading
from typing import Optional

import requests
from Crypto.Cipher import AES

from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel

BASE_URL = "https://asmodeus.free.nf"
HOME_URL = f"{BASE_URL}/"
WARMUP_URL = f"{BASE_URL}/index.php?i=1"
CHAT_URL = f"{BASE_URL}/deepseek.php"
COOKIE_DOMAIN = "asmodeus.free.nf"

API_KEY: Optional[str] = "20262025"
MODEL_NAME = "DeepSeek-R1-0528" # DeepSeek R1 is excellent at writing emails

SESSION_TTL_SECONDS = 600
REQUEST_TIMEOUT_SECONDS = 60

app = FastAPI(title="Local Script API")

_lock = threading.Lock()
_session: Optional[requests.Session] = None
_session_created_at: float = 0.0

class ChatReq(BaseModel):
    question: str

def _extract_challenge_values(html: str) -> tuple[bytes, bytes, bytes]:
    matches = re.findall(r'toNumbers\("([a-f0-9]+)"\)', html, flags=re.IGNORECASE)
    if len(matches) < 3:
        raise RuntimeError("Challenge values not found in HTML.")
    key = bytes.fromhex(matches[0])
    iv = bytes.fromhex(matches[1])
    data = bytes.fromhex(matches[2])
    return key, iv, data

def _build_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0 (Android)"})

    r = s.get(HOME_URL, timeout=REQUEST_TIMEOUT_SECONDS)
    r.raise_for_status()

    key, iv, data = _extract_challenge_values(r.text)
    test_cookie = AES.new(key, AES.MODE_CBC, iv).decrypt(data).hex()
    s.cookies.set("__test", test_cookie, domain=COOKIE_DOMAIN)

    s.get(WARMUP_URL, timeout=REQUEST_TIMEOUT_SECONDS)
    time.sleep(0.2)

    return s

def _get_session() -> requests.Session:
    global _session, _session_created_at
    with _lock:
        now = time.time()
        if _session is None or (now - _session_created_at) > SESSION_TTL_SECONDS:
            _session = _build_session()
            _session_created_at = now
        return _session

def _post_chat(session: requests.Session, question: str) -> requests.Response:
    # 🧠 THE ULTIMATE PROMPT: Rules + Your Resume
    resume_data = """
    Applicant: GHALMI Mohamed Ayad
    Role: Industrial Automation Engineer (Master's Degree, 2025)
    Location: Boumerdes, Algeria
    Skills: Siemens SIMATIC S7-1200/S5, Allen-Bradley SLC 500, TIA Portal, RSLogix 500, SCADA, HMI (SMKON), VFDs (Schneider, ABB), Electrical Wiring & Troubleshooting, Corrective/Preventive Maintenance.
    Experience: Automation Engineer at Alwaha International (07/2025 - Present).
    Languages: Arabic, English, French.
    """

    strict_prompt = (
        f"You are an expert career assistant. Read the LinkedIn post below.\n"
        f"STEP 1: Check if it is a GENUINE job offer located in Algeria (e.g., Algérie, Alger, Oran, Boumerdès) AND is recent (not older than 2 weeks). If NO, reply strictly with the word NO.\n\n"
        f"STEP 2: If YES, write a short, highly professional, human-sounding email application in French applying for this specific job. Tailor the email to match the job description using the applicant's resume below. Keep it concise (3-4 short paragraphs maximum) and natural. Do NOT include subject lines in the email body, just start with 'Bonjour,'.\n\n"
        f"Applicant Resume:\n{resume_data}\n\n"
        f"You MUST format your output exactly like this:\n"
        f"YES\nTITLE: [Extract a short Job Title in French]\nEMAIL:\n[Your generated email body]\n\n"
        f"Post:\n{question}"
    )
    
    return session.post(
        CHAT_URL,
        params={"i": "1"},
        data={"model": MODEL_NAME, "question": strict_prompt},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )

@app.get("/health")
def health():
    return {"ok": True}

@app.post("/chat")
def chat(req: ChatReq, x_api_key: Optional[str] = Header(default=None)):
    if API_KEY is not None and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")

    session = _get_session()

    try:
        r = _post_chat(session, req.question)
        r.raise_for_status()
    except Exception:
        with _lock:
            global _session
            _session = None
        session = _get_session()
        r = _post_chat(session, req.question)
        r.raise_for_status()

    m = re.search(r'<div class="response-content">(.*?)</div>', r.text, flags=re.DOTALL | re.IGNORECASE)
    answer_text = m.group(1).strip() if m else ""
    
    # 🛑 STRIP AI "THINKING" TAGS (Crucial for DeepSeek-R1)
    answer_text = re.sub(r'<think>.*?</think>', '', answer_text, flags=re.DOTALL).strip()

    if answer_text.startswith("YES") or "TITLE:" in answer_text:
        title_match = re.search(r'TITLE:\s*(.*)', answer_text, re.IGNORECASE)
        email_match = re.search(r'EMAIL:\s*(.*)', answer_text, re.IGNORECASE | re.DOTALL)
        
        title = title_match.group(1).strip() if title_match else "Ingénieur en Automatique"
        email_body = email_match.group(1).strip() if email_match else "Bonjour,\n\nJe vous soumets ma candidature pour ce poste.\n\nCordialement,\nGHALMI Mohamed Ayad"
        
        return {"answer": "YES", "title": title, "email_body": email_body}
    
    return {"answer": "NO"}
