from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import requests
import json
import urllib.parse
import sqlite3
import os
from datetime import datetime
import threading
import time
import asyncio

app = FastAPI(title="FC Online 랭커 분석 시스템")

# --- 설정 ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.getenv("DB_PATH", os.path.join(BASE_DIR, "data.db"))
STATIC_DIR = os.path.join(BASE_DIR, "static")
API_KEY = os.getenv("NEXON_API_KEY", "test_b36a7006f193284466b713677f984ca5d40ec95aee2ab2b590db67c06ac2a0b4efe8d04e6d233bd35cf2fabdeb93fb0d")

MATCHTYPE_MAP = {
    30: "리그 친선", 40: "클래식 1on1", 50: "공식경기", 52: "감독모드",
    60: "공식 친선", 204: "볼타 친선", 214: "볼타 공식", 224: "볼타 AI대전", 234: "볼타 커스텀"
}

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
    conn.commit()
    conn.close()

# --- 수집 로직 ---
def call_ranker_stats(players, matchtype):
    headers = {'x-nxopen-api-key': API_KEY, 'User-Agent': 'Mozilla/5.0'}
    players_encoded = urllib.parse.quote(json.dumps(players), safe='')
    try:
        resp = requests.get(
            'https://open.api.nexon.com/fconline/v1/ranker-stats',
            headers=headers, params={'matchtype': matchtype, 'players': players_encoded}, timeout=30
        )
        return resp.json() if resp.status_code == 200 else None
    except:
        return None

def batch_fetch_and_store(matchtype=50):
    try:
        spid_resp = requests.get('https://open.api.nexon.com/static/fconline/meta/spid.json')
        spid_data = spid_resp.json()
        pos_resp = requests.get('https://open.api.nexon.com/static/fconline/meta/spposition.json')
        pos_map = {p['spposition']: p['desc'] for p in pos_resp.json()}
        
        conn = get_db()
        mt_name = MATCHTYPE_MAP.get(matchtype, "공식경기")
        
        for i in range(0, len(spid_data), 10):
            batch = spid_data[i:i+10]
            players = [{"id": p["id"], "po": 25} for p in batch]
            result = call_ranker_stats(players, matchtype)
            
            if result:
                for item in result:
                    spid = item['spid']
                    p_name = next((p['name'] for p in batch if p['id'] == spid), "Unknown")
                    conn.execute("""
                        INSERT OR REPLACE INTO player_stats (
                            spid, spname, sp_position, matchtype, matchtype_name,
                            shoot, effectiveShoot, assist, goal, dribble,
                            dribbleTry, dribbleSuccess, passTry, passSuccess, block,
                            tackle, matchCount, createDate, fetchedAt
                        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """, (
                        spid, p_name, pos_map.get(item['spPosition'], 'Unknown'),
                        matchtype, mt_name, item['status'].get('shoot', 0),
                        item['status'].get('effectiveShoot', 0), item['status'].get('assist', 0),
                        item['status'].get('goal', 0), item['status'].get('dribble', 0),
                        item['status'].get('dribbleTry', 0), item['status'].get('dribbleSuccess', 0),
                        item['status'].get('passTry', 0), item['status'].get('passSuccess', 0),
                        item['status'].get('block', 0), item['status'].get('tackle', 0),
                        item['status'].get('matchCount', 0), item.get('createDate', ''),
                        datetime.now().isoformat()
                    ))
                conn.commit()
            time.sleep(0.2)
        conn.close()
    except Exception as e:
        print(f"Error: {e}")

async def auto_fetch_loop():
    while True:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, batch_fetch_and_store, 50)
        await asyncio.sleep(3600)

# --- API ---
@app.on_event("startup")
async def startup_event():
    init_db()
    asyncio.create_task(auto_fetch_loop())

@app.get("/api/overview")
def get_overview():
    conn = get_db()
    # 경기 수 기준 내림차순 정렬
    rows = conn.execute("""
        SELECT spid, spname, sp_position, matchtype_name, goal, assist, matchCount,
               (goal / CAST(matchCount AS REAL)) as avg_goal
        FROM player_stats 
        WHERE matchCount > 0
        ORDER BY matchCount DESC
    """).fetchall()
    
    positions = {}
    for r in rows:
        pos = r['sp_position']
        if pos not in positions: positions[pos] = []
        positions[pos].append(dict(r))
    
    conn.close()
    return {"positions": positions}

@app.post("/api/fetch")
def trigger_fetch():
    threading.Thread(target=batch_fetch_and_store, args=(50,)).start()
    return {"status": "fetching"}

if os.path.exists(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

@app.get("/")
async def serve_index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))