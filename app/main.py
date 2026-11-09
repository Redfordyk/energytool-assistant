import hashlib
import os
import re
import secrets
import sqlite3
import threading
import zipfile
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from core.rag import answer, load_knowledge

app = FastAPI(title='Energytool consultant')
DB = os.getenv('DATABASE_PATH', 'energytool.db')
# Bounded lock striping keeps requests in one session ordered without
# serializing every visitor or accumulating a lock per session forever.
SESSION_LOCKS = tuple(threading.Lock() for _ in range(64))


def session_lock(session_id):
    index = int.from_bytes(hashlib.sha256(session_id.encode()).digest()[:4], 'big')
    return SESSION_LOCKS[index % len(SESSION_LOCKS)]


def connect():
    connection = sqlite3.connect(DB, timeout=10)
    connection.execute('PRAGMA foreign_keys=ON')
    return connection


def init_db():
    with connect() as db:
        db.executescript('''CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, token_hash TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id), role TEXT NOT NULL, content TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS leads (id INTEGER PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id), request_key TEXT NOT NULL, name TEXT NOT NULL, phone TEXT NOT NULL, company TEXT NOT NULL, UNIQUE(session_id, request_key));''')


init_db()


def authorize(session_id, authorization):
    token = (authorization or '').removeprefix('Bearer ')
    digest = hashlib.sha256(token.encode()).hexdigest()
    with connect() as db:
        row = db.execute('SELECT token_hash FROM sessions WHERE id=?', (session_id,)).fetchone()
    if not row or not secrets.compare_digest(row[0], digest):
        raise HTTPException(404, 'Диалог не найден')


class Message(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class Lead(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    phone: str = Field(min_length=5, max_length=30)
    company: str = Field(min_length=2, max_length=160)
    consent: bool
    request_key: str = Field(min_length=8, max_length=100)


@app.get('/')
def index():
    return FileResponse(Path(__file__).with_name('index.html'))


@app.post('/api/sessions')
def create_session():
    session_id, token = secrets.token_urlsafe(24), secrets.token_urlsafe(32)
    with connect() as db:
        db.execute('INSERT INTO sessions VALUES (?,?)', (session_id, hashlib.sha256(token.encode()).hexdigest()))
    return {'id': session_id, 'token': token}


@app.get('/api/sessions/{session_id}/messages')
def history(session_id: str, authorization: str = Header(default='')):
    authorize(session_id, authorization)
    with connect() as db:
        rows = db.execute('SELECT role, content FROM messages WHERE session_id=? ORDER BY id', (session_id,)).fetchall()
    return [{'role': role, 'content': content} for role, content in rows]


@app.post('/api/sessions/{session_id}/messages')
def chat(session_id: str, message: Message, authorization: str = Header(default='')):
    authorize(session_id, authorization)
    text = message.text.strip()
    if not text:
        raise HTTPException(422, 'Введите сообщение')
    with session_lock(session_id):
        previous = history(session_id, authorization)
        path = Path(os.getenv('DATA_DIR', 'data')) / 'knowledge-base.zip'
        try:
            result = answer(text, load_knowledge(path), previous)
        except (OSError, ValueError, zipfile.BadZipFile):
            from core.rag import FALLBACK
            result = FALLBACK
        with connect() as db:
            db.executemany('INSERT INTO messages(session_id,role,content) VALUES (?,?,?)', [(session_id, 'user', text), (session_id, 'assistant', result)])
    return {'answer': result}


@app.post('/api/sessions/{session_id}/leads')
def lead(session_id: str, lead: Lead, authorization: str = Header(default='')):
    authorize(session_id, authorization)
    if not lead.consent or not re.fullmatch(r'[+\d ()-]{5,30}', lead.phone) or not 10 <= len(re.sub(r'\D', '', lead.phone)) <= 15 or not lead.name.strip() or not lead.company.strip():
        raise HTTPException(422, 'Проверьте контакты и подтвердите согласие')
    with connect() as db:
        db.execute('INSERT OR IGNORE INTO leads(session_id,request_key,name,phone,company) VALUES (?,?,?,?,?)', (session_id, lead.request_key, lead.name.strip(), lead.phone.strip(), lead.company.strip()))
        row = db.execute('SELECT id,name,phone,company FROM leads WHERE session_id=? AND request_key=?', (session_id, lead.request_key)).fetchone()
        if row[1:] != (lead.name.strip(), lead.phone.strip(), lead.company.strip()):
            raise HTTPException(409, 'Ключ уже использован для другой заявки')
    return {'id': row[0], 'status': 'saved'}
