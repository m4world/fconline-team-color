"""
FC Online 랭커 선수 사용 현황 대시보드
FastAPI + SQLite 백엔드 (배포 최적화 버전)
"""
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
import requests
import json
import urllib.parse
import sqlite3
import os
from datetime import datetime
import threading
import time

app = FastAPI(title="FC Online 랭커 선수 분석")

# ── 경로 및 환경 설정 ──
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# DB 경로는 환경변수가 없으면 프로젝트 루트의 data.db 사용
DB_PATH = os.getenv("DB_PATH", os.path.join(BASE_DIR, "data.db"))
# 정적 파일 경로는 프로젝트 루트의 static 폴더 사용
STATIC_DIR = os.getenv("STATIC_DIR", os.path.join(BASE_DIR, "static"))
# API 키 설정
API_KEY = os.getenv("NEXON_API_KEY", "test_b36a7006f193284466b713677f984ca5d40ec95aee2ab2b590db67c06ac2a0b4efe8d04e6d233bd35cf2fabdeb93fb0d")

# 매핑 데이터
POSITION_MAP = {
    0: "GK", 1: "SW", 2: "RWB", 3: "RB", 4: "RCB", 5: "CB", 6: "LCB",
    7: "LB", 8: "LWB", 9: "RDM", 10: "CDM", 11: "LDM", 12: "RM",
    13: "RCM", 14: "CM", 15: "LCM", 16: "LM", 17: "RAM", 18: "CAM",
    19: "LAM", 20: "RF", 21: "CF", 22: "LF", 23: "RW", 24: "RS",
    25: "ST", 26: "LS", 27: "LW", 28: "SUB"
}

MATCHTYPE_MAP = {
    30: "리그 친선", 40: "클래식 1on1", 50: "공식경기", 52: "감독모드",
    60: "공식 친선", 204: "볼타 친선", 214: "볼타 공식", 224: "볼타 AI대전", 234: "볼타 커스텀"
}

# ── 데이터베이스 로직 ──
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS player_stats (
            id INTEGER PRIMARY KEY,
            spid INTEGER UNIQUE,
            spname TEXT,
            sp_position TEXT,
            matchtype INTEGER,
            matchtype_name TEXT,
            shoot REAL,
            effectiveShoot REAL,
            assist REAL,
            goal REAL,
            dribble REAL,
            dribbleTry REAL,
            dribbleSuccess REAL,
            passTry REAL,
            passSuccess REAL,
            block REAL,
            tackle REAL,
            matchCount INTEGER,
            createDate TEXT,
            fetchedAt TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS fetch_log (
            id INTEGER PRIMARY KEY,
            matchtype INTEGER,
            playerCount INTEGER,
            status TEXT,
            fetchedAt TEXT
        )
    """)
    conn.commit()
    conn.close()

# ── 메타데이터 및 API 호출 ──
def fetch_spid_meta():
    resp = requests.get('https://open.api.nexon.com/static/fconline/meta/spid.json', timeout=60)
    resp.raise_for_status()
    return resp.json()

def fetch_position_meta():
    resp = requests.get('https://open.api.nexon.com/static/fconline/meta/spposition.json', timeout=30)
    resp.raise_for_status()
    return resp.json()

def call_ranker_stats(players, matchtype=52):
    headers = {
        'x-nxopen-api-key': API_KEY,
        'User-Agent': 'Mozilla/5.0'
    }
    players_encoded = urllib.parse.quote(json.dumps(players), safe='')
    resp = requests.get(
        'https://open.api.nexon.com/fconline/v1/ranker-stats',
        headers=headers,
        params={'matchtype': matchtype, 'players': players_encoded},
        timeout=30
    )
    return resp.json() if resp.status_code == 200 else None

def batch_fetch_and_store(matchtype=52, batch_size=10):
    spid_data = fetch_spid_meta()
    position_data = fetch_position_meta()
    pos_map = {p['spposition']: p['desc'] for p in position_data}
    conn = get_db()
    existing = set(r['spid'] for r in conn.execute("SELECT spid FROM player_stats WHERE matchtype=?", (matchtype,)).fetchall())
    mt_name = MATCHTYPE_MAP.get(matchtype, f"기타({matchtype})")
    fetched_count = 0
    
    for i in range(0, len(spid_data), batch_size):
        batch = spid_data[i:i+batch_size]
        players = [{"id": p["id"], "po": 25} for p in batch]
        result = call_ranker_stats(players, matchtype)
        if result:
            for item in result:
                spid = item['spid']
                if spid in existing: continue
                player_name = next((p['name'] for p in batch if p['id'] == spid), "Unknown")
                conn.execute("""
                    INSERT INTO player_stats (
                        spid, spname, sp_position, matchtype, matchtype_name,
                        shoot, effectiveShoot, assist, goal, dribble, dribbleTry,
                        dribbleSuccess, passTry, passSuccess, block, tackle,
                        matchCount, createDate, fetchedAt
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    spid, player_name, pos_map.get(item['spPosition'], 'Unknown'),
                    matchtype, mt_name, item['status'].get('shoot', 0),
                    item['status'].get('effectiveShoot', 0), item['status'].get('assist', 0),
                    item['status'].get('goal', 0), item['status'].get('dribble', 0),
                    item['status'].get('dribbleTry', 0), item['status'].get('dribbleSuccess', 0),
                    item['status'].get('passTry', 0), item['status'].get('passSuccess', 0),
                    item['status'].get('block', 0), item['status'].get('tackle', 0),
                    item['status'].get('matchCount', 0), item.get('createDate', ''),
                    datetime.now().isoformat()
                ))
                existing.add(spid)
                fetched_count += 1
            conn.commit()
        time.sleep(0.1)
    
    conn.execute("INSERT INTO fetch_log (matchtype, playerCount, status, fetchedAt) VALUES (?,?,?,?)",
                 (matchtype, fetched_count, "completed", datetime.now().isoformat()))
    conn.commit()
    conn.close()
    return fetched_count

# ── API 엔드포인트 ──
@app.get("/api/overview")
def get_overview():
    conn = get_db()
    total = conn.execute("SELECT COUNT(*) as cnt FROM player_stats").fetchone()['cnt']
    by_matchtype = conn.execute("SELECT matchtype, matchtype_name, COUNT(*) as player_count FROM player_stats GROUP BY matchtype").fetchall()
    by_position = conn.execute("SELECT sp_position, COUNT(*) as count FROM player_stats GROUP BY sp_position ORDER BY count DESC LIMIT 20").fetchall()
    top_players = conn.execute("SELECT spid, spname, sp_position, matchCount FROM player_stats ORDER BY matchCount DESC LIMIT 50").fetchall()
    conn.close()
    return {"total_players": total, "by_matchtype": [dict(r) for r in by_matchtype], "by_position": [dict(r) for r in by_position], "top_players": [dict(r) for r in top_players]}

@app.post("/api/fetch")
def trigger_fetch(matchtype: int = 52):
    t = threading.Thread(target=batch_fetch_and_store, args=(matchtype,))
    t.start()
    return {"status": "fetching", "matchtype": matchtype}

# ── 정적 파일 서비스 (배포용 수정 핵심) ──
# 1. /static 경로를 실제 폴더와 연결 (이미지, CSS, JS 로딩용)
if os.path.exists(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# 2. 기본 접속 시 index.html 전송
@app.get("/")
async def serve_index():
    index_file = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index_file):
        return FileResponse(index_file)
    return {"error": f"index.html not found at {index_file}. Check your directory structure."}

# 서버 시작 시 DB 초기화
init_db()