import re
import time
import threading
import html
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
MODEL_NAME = "DeepSeek-R1-0528"

SESSION_TTL_SECONDS = 600
REQUEST_TIMEOUT_SECONDS = 60

app = FastAPI(title="Local Script API")

_lock = threading.Lock()
_session: Optional[requests.Session] = None
_session_created_at: float = 0.0

class ChatReq(BaseModel):
    question: str

def _extract_challenge_values(html_text: str) -> tuple[bytes, bytes, bytes]:
    matches = re.findall(r'toNumbers\("([a-f0-9]+)"\)', html_text, flags=re.IGNORECASE)
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
    # 🧠 STRICT PROMPT: Job filtering & No Hallucinations
    strict_prompt = (
        f"You are an expert career assistant. Read the LinkedIn post below.\n"
        f"STEP 1: Check if it is a GENUINE job offer located in Algeria AND is recent. If NO, reply strictly with the word NO.\n"
        f"STEP 2: Check the Job Title. It MUST be one of the following levels: Ingénieur, Technicien, or Opérateur. The field MUST be related to: Automatisme, Maintenance, Électricité, Instrumentation, or Électromécanique. If the job is for a Senior Management role (Directeur, Responsable, Manager, Chef), reply strictly with the word NO.\n\n"
        f"STEP 3: If YES to both, write a VERY SHORT, highly natural email application in French.\n"
        f"EMAIL RULES:\n"
        f"- Do NOT list technical skills.\n"
        f"- Keep it to exactly 2 or 3 short sentences.\n"
        f"- NEVER include phone numbers, fake email addresses, placeholders, or LinkedIn URLs in the text.\n"
        f"- Sign off strictly and only as: 'Cordialement, Mohamed Ayad GHALMI'.\n"
        f"- Output strictly plain text. No HTML.\n\n"
        f"FINAL OUTPUT FORMAT:\n"
        f"YES\n"
        f"TITLE: [Job Title]\n"
        f"EMAIL: [Your generated email body]\n\n"
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
    
    answer_text = html.unescape(answer_text)
    answer_text = re.sub(r'<think>.*?</think>', '', answer_text, flags=re.DOTALL | re.IGNORECASE)
    
    if '</think>' in answer_text:
        answer_text = answer_text.split('</think>')[-1]

    answer_text = re.sub(r'<br\s*/?>', '\n', answer_text, flags=re.IGNORECASE)
    answer_text = re.sub(r'<[^>]+>', '', answer_text).strip()
    
    default_email = "Bonjour,\n\nJe suis très intéressé par le poste que vous avez publié. Mon profil technique correspond à vos besoins et je vous joins mon CV pour plus de détails.\n\nCordialement,\nMohamed Ayad GHALMI"

    if "YES" in answer_text.upper():
        title_match = re.search(r'TITLE:\s*([^\n]+)', answer_text, re.IGNORECASE)
        title = title_match.group(1).strip() if title_match else "Ingénieur / Technicien"
        
        parts = re.split(r'EMAIL:\s*', answer_text, flags=re.IGNORECASE)
        email_body = parts[-1].strip() if len(parts) > 1 else default_email
            
        return {"answer": "YES", "title": title, "email_body": email_body}
    
    return {"answer": "NO"}
