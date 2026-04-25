"""
FC Online 랭커 선수 사용 현황 대시보드
FastAPI + SQLite 백엔드
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
from datetime import datetime, timedelta
import hashlib

app = FastAPI(title="FC Online 랭커 선수 분석")

# ── 환경변수 기반 설정 (로컬/배포 모두 호환) ──
DB_PATH = os.getenv("DB_PATH", "data.db")
API_KEY = os.getenv("NEXON_API_KEY", "test_b36a7006f193284466b713677f984ca5d40ec95aee2ab2b590db67c06ac2a0b4efe8d04e6d233bd35cf2fabdeb93fb0d")

# 포지션 매핑
POSITION_MAP = {
    0: "GK", 1: "SW", 2: "RWB", 3: "RB", 4: "RCB", 5: "CB", 6: "LCB",
    7: "LB", 8: "LWB", 9: "RDM", 10: "CDM", 11: "LDM", 12: "RM",
    13: "RCM", 14: "CM", 15: "LCM", 16: "LM", 17: "RAM", 18: "CAM",
    19: "LAM", 20: "RF", 21: "CF", 22: "LF", 23: "RW", 24: "RS",
    25: "ST", 26: "LS", 27: "LW", 28: "SUB"
}

# 경기 유형 매핑
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


def fetch_spid_meta():
    """선수 메타데이터 가져오기"""
    resp = requests.get('https://open.api.nexon.com/static/fconline/meta/spid.json', timeout=60)
    resp.raise_for_status()
    return resp.json()


def fetch_position_meta():
    """포지션 메타데이터 가져오기"""
    resp = requests.get('https://open.api.nexon.com/static/fconline/meta/spposition.json', timeout=30)
    resp.raise_for_status()
    return resp.json()


def call_ranker_stats(players, matchtype=52):
    """ranker-stats API 호출"""
    headers = {
        'x-nxopen-api-key': API_KEY,
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    }
    players_encoded = urllib.parse.quote(json.dumps(players), safe='')
    resp = requests.get(
        'https://open.api.nexon.com/fconline/v1/ranker-stats',
        headers=headers,
        params={'matchtype': matchtype, 'players': players_encoded},
        timeout=30
    )
    if resp.status_code == 200:
        return resp.json()
    return None


def batch_fetch_and_store(matchtype=52, batch_size=10):
    """선수를 배치로 나누어 fetch & DB 저장"""
    spid_data = fetch_spid_meta()
    position_data = fetch_position_meta()
    pos_map = {p['spposition']: p['desc'] for p in position_data}

    conn = get_db()
    existing = set(r['spid'] for r in conn.execute("SELECT spid FROM player_stats WHERE matchtype=?").fetchall())
    
    # matchtype_name
    mt_name = MATCHTYPE_MAP.get(matchtype, f"기타({matchtype})")
    
    fetched_count = 0
    total = len(spid_data)
    
    for i in range(0, total, batch_size):
        batch = spid_data[i:i+batch_size]
        players = [{"id": p["id"], "po": 25} for p in batch]  # 기본 ST 포지션
        
        result = call_ranker_stats(players, matchtype)
        
        if result:
            for item in result:
                spid = item['spid']
                if spid in existing:
                    continue
                conn.execute("""
                    INSERT INTO player_stats (
                        spid, spname, sp_position, matchtype, matchtype_name,
                        shoot, effectiveShoot, assist, goal, dribble, dribbleTry,
                        dribbleSuccess, passTry, passSuccess, block, tackle,
                        matchCount, createDate, fetchedAt
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    spid,
                    batch[[p['id'] for p in batch].index(spid)]['name'] if spid in [p['id'] for p in batch] else "Unknown",
                    pos_map.get(item['spPosition'], 'Unknown'),
                    matchtype, mt_name,
                    item['status'].get('shoot', 0),
                    item['status'].get('effectiveShoot', 0),
                    item['status'].get('assist', 0),
                    item['status'].get('goal', 0),
                    item['status'].get('dribble', 0),
                    item['status'].get('dribbleTry', 0),
                    item['status'].get('dribbleSuccess', 0),
                    item['status'].get('passTry', 0),
                    item['status'].get('passSuccess', 0),
                    item['status'].get('block', 0),
                    item['status'].get('tackle', 0),
                    item['status'].get('matchCount', 0),
                    item.get('createDate', ''),
                    datetime.now().isoformat()
                ))
                existing.add(spid)
                fetched_count += 1
            
            conn.commit()
            print(f"Batch {i//batch_size}: {fetched_count} players fetched")
        else:
            print(f"Batch {i//batch_size}: API failed")
        
        # Rate limiting
        import time
        time.sleep(0.5)
    
    conn.execute(
        "INSERT INTO fetch_log (matchtype, playerCount, status, fetchedAt) VALUES (?,?,?,?)",
        (matchtype, fetched_count, "completed", datetime.now().isoformat())
    )
    conn.commit()
    conn.close()
    
    return fetched_count


# API 엔드포인트

@app.get("/api/overview")
def get_overview():
    """대시보드 개요 데이터"""
    conn = get_db()
    
    # 총 저장된 선수 수
    total = conn.execute("SELECT COUNT(*) as cnt FROM player_stats").fetchone()['cnt']
    
    # matchtype별 통계
    by_matchtype = conn.execute("""
        SELECT matchtype, matchtype_name, COUNT(*) as player_count,
               AVG(goal) as avg_goals, AVG(assist) as avg_assists,
               AVG(dribble) as avg_dribbles, AVG(passSuccess) as avg_passes
        FROM player_stats GROUP BY matchtype
    """).fetchall()
    
    # 포지션별 TOP 20
    by_position = conn.execute("""
        SELECT sp_position, COUNT(*) as count, AVG(goal) as avg_goals,
               AVG(assist) as avg_assists, AVG(dribbleSuccess) as avg_dribble_success
        FROM player_stats GROUP BY sp_position ORDER BY count DESC LIMIT 20
    """).fetchall()
    
    # 전체 TOP 50 선수
    top_players = conn.execute("""
        SELECT spid, spname, sp_position, matchtype_name,
               goal, assist, dribble, passSuccess, matchCount
        FROM player_stats ORDER BY matchCount DESC LIMIT 50
    """).fetchall()
    
    conn.close()
    
    return {
        "total_players": total,
        "by_matchtype": [dict(r) for r in by_matchtype],
        "by_position": [dict(r) for r in by_position],
        "top_players": [dict(r) for r in top_players]
    }


@app.get("/api/position-stats")
def get_position_stats():
    """포지션별 상세 통계"""
    conn = get_db()
    stats = conn.execute("""
        SELECT sp_position, COUNT(*) as count,
               AVG(shoot) as avg_shoot, AVG(effectiveShoot) as avg_eff_shoot,
               AVG(assist) as avg_assist, AVG(goal) as avg_goal,
               AVG(dribble) as avg_dribble, AVG(dribbleSuccess) as avg_dribble_success,
               AVG(passTry) as avg_pass_try, AVG(passSuccess) as avg_pass_success,
               AVG(block) as avg_block, AVG(tackle) as avg_tackle,
               AVG(matchCount) as avg_matches
        FROM player_stats GROUP BY sp_position ORDER BY count DESC
    """).fetchall()
    conn.close()
    return [dict(r) for r in stats]


@app.get("/api/player/{spid}")
def get_player_stats(spid: int):
    """특정 선수의 상세 통계"""
    conn = get_db()
    player = conn.execute("SELECT * FROM player_stats WHERE spid=?", (spid,)).fetchone()
    conn.close()
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")
    return dict(player)


@app.get("/api/top-players")
def get_top_players(limit: int = 50):
    """사용 빈도 TOP N 선수"""
    conn = get_db()
    players = conn.execute("""
        SELECT spid, spname, sp_position, matchtype_name,
               COUNT(*) as usage_count, AVG(goal) as avg_goals,
               AVG(assist) as avg_assists, AVG(dribbleSuccess) as avg_dribble_success,
               AVG(passSuccess) as avg_pass_success
        FROM player_stats GROUP BY spid ORDER BY usage_count DESC LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in players]


@app.post("/api/fetch")
def trigger_fetch(matchtype: int = 52):
    """데이터 fetch 트리거 (비동기)"""
    import threading
    t = threading.Thread(target=batch_fetch_and_store, args=(matchtype,))
    t.start()
    return {"status": "fetching", "matchtype": matchtype}


# 정적 파일 제공
# 정적 파일 제공 (로컬/배포 모두 호환)
STATIC_DIR = os.getenv("STATIC_DIR", "static")

@app.get("/")
def serve_index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


# 초기화
init_db()
