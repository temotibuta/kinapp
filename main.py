from fastapi import FastAPI, Query, HTTPException
from pydantic import BaseModel
from typing import Optional, List
import sqlite3
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import hashlib
import os
import urllib.request
import json
import google.generativeai as genai
import re

from datetime import datetime
app = FastAPI()

# ★ Gemini API Key (環境変数からのみ取得)
# ENV loading (optional)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ★ Gemini API Key (環境変数からのみ取得)
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

# CORS設定（ローカルHTMLとの連携に必要）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 静的ファイルのサーブ
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
async def read_index():
    return FileResponse('static/index.html')

# データモデル定義
class UserCreate(BaseModel):
    username: str
    password: str

class UserSettings(BaseModel):
    visibility: str # 'public', 'friends', 'private'

class UserTargets(BaseModel):
    target_calories: int
    target_protein: float
    target_fat: float
    target_carbs: float
    target_salt: float = 7.0
    target_fiber: float = 20.0

class FriendRequest(BaseModel):
    friend_username: str

class Memo(BaseModel):
    user_id: str
    date: str
    exercise: str
    weight: float
    reps: int
    note: str

class Meal(BaseModel):
    user_id: str
    date: str
    meal_type: str # 'Breakfast', 'Lunch', 'Dinner', 'Snack'
    food_name: str
    calories: int
    protein: float
    fat: float
    carbs: float
    salt: float = 0.0
    fiber: float = 0.0

class WeightLog(BaseModel):
    user_id: str
    date: str
    weight: float

class EstimationRequest(BaseModel):
    text: str

class AdviceRequest(BaseModel):
    meals: list
    targets: dict

# ★ Workout Management Models ★
class WorkoutSessionCreate(BaseModel):
    user_id: str
    date: str
    duration: Optional[int] = None
    session_memo: Optional[str] = None

class WorkoutExerciseCreate(BaseModel):
    session_id: int
    exercise_name: str
    exercise_memo: Optional[str] = None
    body_part: Optional[str] = None
    sort_order: int = 0

class SetLogCreate(BaseModel):
    workout_exercise_id: int
    set_number: int
    weight: float
    reps: int

DB_FILE = "memo.db"

# 初期化関数
def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS memos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            date TEXT,
            exercise TEXT,
            weight REAL,
            reps INTEGER,
            note TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE,
            password TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS friends (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            friend_id TEXT,
            UNIQUE(user_id, friend_id)
        )
    ''')
    
    # visibilityカラムの追加チェック
    cursor.execute("PRAGMA table_info(users)")
    columns = [row[1] for row in cursor.fetchall()]
    if 'visibility' not in columns:
        cursor.execute("ALTER TABLE users ADD COLUMN visibility TEXT DEFAULT 'public'")
    if 'target_calories' not in columns:
        cursor.execute("ALTER TABLE users ADD COLUMN target_calories INTEGER DEFAULT 2000")
        cursor.execute("ALTER TABLE users ADD COLUMN target_protein REAL DEFAULT 60")
        cursor.execute("ALTER TABLE users ADD COLUMN target_fat REAL DEFAULT 60")
        cursor.execute("ALTER TABLE users ADD COLUMN target_carbs REAL DEFAULT 300")

    # Mealsテーブル
    cursor.execute("PRAGMA table_info(meals)")
    meal_columns = [row[1] for row in cursor.fetchall()]
    if not meal_columns:
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS meals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT,
                date TEXT,
                meal_type TEXT,
                food_name TEXT,
                calories INTEGER,
                protein REAL,
                fat REAL,
                carbs REAL,
                salt REAL DEFAULT 0.0,
                fiber REAL DEFAULT 0.0
            )
        ''')
    else:
        if 'salt' not in meal_columns:
            cursor.execute("ALTER TABLE meals ADD COLUMN salt REAL DEFAULT 0.0")
        if 'fiber' not in meal_columns:
            cursor.execute("ALTER TABLE meals ADD COLUMN fiber REAL DEFAULT 0.0")

    # usersテーブルの目標値カラム拡張
    cursor.execute("PRAGMA table_info(users)")
    user_columns = [row[1] for row in cursor.fetchall()]
    if 'target_salt' not in user_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN target_salt REAL DEFAULT 7.0")
    if 'target_fiber' not in user_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN target_fiber REAL DEFAULT 20.0")

    # Weightsテーブル
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS weights (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            date TEXT,
            weight REAL
        )
    ''')

    # Notificationsテーブル
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            from_user TEXT,
            type TEXT,
            is_read INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # ★ Workout Management Tables ★
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS workout_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            date TEXT NOT NULL,
            duration INTEGER,
            session_memo TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS workout_exercises (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            exercise_name TEXT NOT NULL,
            exercise_memo TEXT,
            sort_order INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (session_id) REFERENCES workout_sessions(id) ON DELETE CASCADE
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS set_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workout_exercise_id INTEGER NOT NULL,
            set_number INTEGER NOT NULL,
            weight REAL NOT NULL,
            reps INTEGER NOT NULL,
            estimated_1rm REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (workout_exercise_id) REFERENCES workout_exercises(id) ON DELETE CASCADE
        )
    ''')

    # Migration for body_part
    try:
        cursor.execute("ALTER TABLE workout_exercises ADD COLUMN body_part TEXT")
        print("Migrated: Added body_part column to workout_exercises")
    except sqlite3.OperationalError:
        pass  # Already exists

    conn.commit()
    conn.close()

# 部位推定関数
def estimate_body_part(exercise_name: str) -> str:
    name = exercise_name.lower()
    if any(k in name for k in ['bench', 'chest', 'fly', 'push up', 'pec', 'dips', 'ベンチ', 'チェスト', 'フライ', 'プッシュアップ', '胸']):
        return 'Chest'
    if any(k in name for k in ['pull', 'row', 'lat', 'chin', 'back', 'deadlift', 'プル', 'ロウ', 'ラット', 'チンニング', '背中', 'デッド']):
        return 'Back'
    if any(k in name for k in ['squat', 'leg', 'lunge', 'calf', 'hip', 'スクワット', 'レッグ', 'ランジ', 'カーフ', '足', '脚']):
        return 'Legs'
    if any(k in name for k in ['shoulder', 'raise', 'delt', 'military', 'ショルダー', 'レイズ', '肩', 'ミリタリー']):
        return 'Shoulders'
    if any(k in name for k in ['curl', 'tricep', 'bicep', 'extension', 'arm', 'カール', 'トライセプス', 'バイセプス', 'アーム', '腕']):
        return 'Arms'
    if any(k in name for k in ['crunch', 'sit up', 'plank', 'ab', 'クランチ', 'シットアップ', 'プランク', '腹']):
        return 'Abs'
    return 'Other'

init_db()

# --- Workout Management API ---

@app.post("/api/workout/sessions")
def create_workout_session(session: WorkoutSessionCreate):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO workout_sessions (user_id, date, duration, session_memo)
        VALUES (?, ?, ?, ?)
    ''', (session.user_id, session.date, session.duration, session.session_memo))
    session_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return {
        "session_id": session_id,
        "user_id": session.user_id,
        "date": session.date,
        "duration": session.duration,
        "session_memo": session.session_memo
    }

@app.get("/api/workout/sessions")
def get_workout_sessions(user_id: str = Query(...), date: Optional[str] = Query(None)):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    query = "SELECT id, user_id, date, duration, session_memo, created_at FROM workout_sessions WHERE user_id = ?"
    params = [user_id]
    if date:
        query += " AND date = ?"
        params.append(date)
    query += " ORDER BY date DESC"
    
    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()
    
    return [
        {
            "id": r[0], "user_id": r[1], "date": r[2], "duration": r[3], "session_memo": r[4], "created_at": r[5]
        }
        for r in rows
    ]

@app.post("/api/workout/exercises")
def add_workout_exercise(ex: WorkoutExerciseCreate):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # 部位の自動判定 (指定がない場合)
    body_part = ex.body_part
    if not body_part:
        body_part = estimate_body_part(ex.exercise_name)

    cursor.execute('''
        INSERT INTO workout_exercises (session_id, exercise_name, exercise_memo, body_part, sort_order)
        VALUES (?, ?, ?, ?, ?)
    ''', (ex.session_id, ex.exercise_name, ex.exercise_memo, body_part, ex.sort_order))
    ex_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return {
        "exercise_id": ex_id,
        "session_id": ex.session_id,
        "exercise_name": ex.exercise_name,
        "exercise_memo": ex.exercise_memo,
        "body_part": body_part,
        "sort_order": ex.sort_order
    }

@app.get("/api/workout/sessions/{session_id}")
def get_workout_session_detail(session_id: int):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # Exercises
    cursor.execute('''
        SELECT id, exercise_name, exercise_memo, body_part, sort_order 
        FROM workout_exercises 
        WHERE session_id = ? 
        ORDER BY sort_order
    ''', (session_id,))
    ex_rows = cursor.fetchall()
    
    exercises = []
    for ex in ex_rows:
        # Sets
        cursor.execute('''
            SELECT id, set_number, weight, reps, estimated_1rm 
            FROM set_logs 
            WHERE workout_exercise_id = ? 
            ORDER BY set_number
        ''', (ex[0],))
        sets = [
            {"id": s[0], "set_number": s[1], "weight": s[2], "reps": s[3], "estimated_1rm": s[4]}
            for s in cursor.fetchall()
        ]
        exercises.append({
            "id": ex[0],
            "exercise_name": ex[1],
            "exercise_memo": ex[2],
            "body_part": ex[3],
            "sort_order": ex[4],
            "sets": sets
        })
        
    conn.close()
    return {"session_id": session_id, "exercises": exercises}

# 履歴検索API
@app.get("/api/workout/history")
def get_workout_history(
    user_id: str = Query(...),
    body_part: Optional[str] = Query(None),
    exercise_name: Optional[str] = Query(None),
    limit: int = Query(20)
):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    query = '''
        SELECT 
            s.date, 
            e.exercise_name, 
            e.body_part, 
            l.weight, 
            l.reps, 
            l.estimated_1rm,
            l.set_number
        FROM set_logs l
        JOIN workout_exercises e ON l.workout_exercise_id = e.id
        JOIN workout_sessions s ON e.session_id = s.id
        WHERE s.user_id = ?
    '''
    params = [user_id]
    
    if body_part and body_part != 'All':
        query += " AND e.body_part = ?"
        params.append(body_part)
        
    if exercise_name:
        query += " AND e.exercise_name LIKE ?"
        params.append(f"%{exercise_name}%")
        
    query += " ORDER BY s.date DESC, e.id DESC, l.set_number ASC LIMIT ?"
    params.append(limit * 5) # セット単位なので少し多めに取得してフロントでまとめるか、ここでまとめるか。今回はフラットに返す
    
    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()
    
    return [
        {
            "date": r[0],
            "exercise_name": r[1],
            "body_part": r[2],
            "weight": r[3],
            "reps": r[4],
            "estimated_1rm": r[5],
            "set_number": r[6]
        }
        for r in rows
    ]

# ★ 1RM推移グラフAPI ★
@app.get("/api/workout/progress")
def get_workout_progress(
    user_id: str = Query(...),
    exercise_name: Optional[str] = Query(None)
):
    """種目別の推定1RM推移データを返す"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    if exercise_name:
        # 特定種目の推移
        cursor.execute('''
            SELECT s.date, MAX(l.estimated_1rm) as best_1rm, e.body_part
            FROM set_logs l
            JOIN workout_exercises e ON l.workout_exercise_id = e.id
            JOIN workout_sessions s ON e.session_id = s.id
            WHERE s.user_id = ? AND e.exercise_name = ?
            GROUP BY s.date
            ORDER BY s.date ASC
        ''', (user_id, exercise_name))
        rows = cursor.fetchall()
        conn.close()
        return {
            "exercise_name": exercise_name,
            "data": [{"date": r[0], "estimated_1rm": r[1], "body_part": r[2]} for r in rows]
        }
    else:
        # 全種目リスト（ユニーク）
        cursor.execute('''
            SELECT DISTINCT e.exercise_name, e.body_part
            FROM workout_exercises e
            JOIN workout_sessions s ON e.session_id = s.id
            WHERE s.user_id = ?
            ORDER BY e.exercise_name
        ''', (user_id,))
        exercises = [{"name": r[0], "body_part": r[1]} for r in cursor.fetchall()]
        conn.close()
        return {"exercises": exercises}

# ★ AIコーチAPI ★
@app.post("/api/ai/coach")
def ai_coach(user_id: str = Query(...), exercise_name: Optional[str] = Query(None)):
    """停滞検知 + AIプログラム提案"""
    if not GEMINI_API_KEY:
        raise HTTPException(status_code=500, detail="Gemini API key not configured")
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # 直近のトレーニング履歴を取得
    cursor.execute('''
        SELECT s.date, e.exercise_name, e.body_part, l.weight, l.reps, l.estimated_1rm
        FROM set_logs l
        JOIN workout_exercises e ON l.workout_exercise_id = e.id
        JOIN workout_sessions s ON e.session_id = s.id
        WHERE s.user_id = ?
        ORDER BY s.date DESC, e.id ASC, l.set_number ASC
        LIMIT 100
    ''', (user_id,))
    history = cursor.fetchall()
    conn.close()
    
    if not history:
        return {"advice": "まだトレーニング記録がありません。まずはワークアウトを記録しましょう！", "program": []}
    
    # 履歴をテキスト化
    history_text = "日付 | 種目 | 部位 | 重量(kg) | 回数 | 推定1RM(kg)\n"
    for h in history[:50]:
        history_text += f"{h[0]} | {h[1]} | {h[2]} | {h[3]} | {h[4]} | {h[5]}\n"
    
    focus = f"特に「{exercise_name}」について重点的にアドバイスしてください。" if exercise_name else ""
    
    prompt = f"""あなたは経験豊富なパーソナルトレーナーです。以下のトレーニング履歴を分析し、科学的根拠に基づいたアドバイスをしてください。

【ユーザーの直近トレーニング履歴】
{history_text}

{focus}

以下の形式でJSON形式で回答してください：
{{
  "analysis": "現在の状況分析（停滞しているか、順調か等）を2-3文で",
  "advice": "具体的なアドバイスを3-4文で。POF法、マンデルブロトレーニング、5/3/1メソッドなどの理論を適宜紹介",
  "program": [
    {{"exercise": "種目名", "sets": "セット数", "reps": "回数", "weight_guide": "重量の目安（例：1RMの80%）", "note": "ポイント"}},
    ...
  ]
}}

重要: JSONのみ返してください。マークダウンのコードブロックは使わないでください。"""

    try:
        model = genai.GenerativeModel("gemini-2.0-flash")
        response = model.generate_content(prompt)
        text = response.text.strip()
        
        # JSONパース
        if text.startswith("```"):
            text = re.sub(r'^```\w*\n?', '', text)
            text = re.sub(r'\n?```$', '', text)
        
        result = json.loads(text)
        return result
    except json.JSONDecodeError:
        return {"analysis": "分析中...", "advice": response.text if response else "AIからの応答を取得できませんでした", "program": []}
    except Exception as e:
        return {"analysis": "エラー", "advice": f"AI分析中にエラーが発生しました: {str(e)}", "program": []}

# ★ 実績バッジ＆レベルAPI ★
ACHIEVEMENTS = [
    {"id": "first_session", "name": "初トレーニング", "icon": "🎉", "desc": "初めてのワークアウトを記録"},
    {"id": "streak_3", "name": "3日連続", "icon": "🔥", "desc": "3日連続でトレーニング"},
    {"id": "streak_7", "name": "7日連続", "icon": "🔥🔥", "desc": "7日連続でトレーニング"},
    {"id": "volume_1000", "name": "1トンリフター", "icon": "💪", "desc": "1日の総ボリュームが1,000kgを突破"},
    {"id": "volume_5000", "name": "5トンリフター", "icon": "🦍", "desc": "1日の総ボリュームが5,000kgを突破"},
    {"id": "bench_100", "name": "ベンチ100kg", "icon": "🏆", "desc": "ベンチプレスの推定1RMが100kgを突破"},
    {"id": "squat_100", "name": "スクワット100kg", "icon": "🦵", "desc": "スクワットの推定1RMが100kgを突破"},
    {"id": "deadlift_100", "name": "デッドリフト100kg", "icon": "🏋️", "desc": "デッドリフトの推定1RMが100kgを突破"},
    {"id": "exercises_10", "name": "マルチプレイヤー", "icon": "🎯", "desc": "10種類以上の種目を記録"},
    {"id": "sessions_10", "name": "常連トレーニー", "icon": "📅", "desc": "10回以上のセッションを記録"},
    {"id": "sessions_50", "name": "ジムの主", "icon": "👑", "desc": "50回以上のセッションを記録"},
    {"id": "pr_broken", "name": "自己ベスト更新！", "icon": "⭐", "desc": "いずれかの種目で推定1RMを更新"},
]

@app.get("/api/achievements")
def get_achievements(user_id: str = Query(...)):
    """ユーザーの獲得済み実績を判定して返す"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    earned = []
    
    # 1. セッション数
    cursor.execute("SELECT COUNT(*) FROM workout_sessions WHERE user_id = ?", (user_id,))
    session_count = cursor.fetchone()[0]
    if session_count >= 1:
        earned.append("first_session")
    if session_count >= 10:
        earned.append("sessions_10")
    if session_count >= 50:
        earned.append("sessions_50")
    
    # 2. 連続日数
    cursor.execute("SELECT DISTINCT date FROM workout_sessions WHERE user_id = ? ORDER BY date DESC", (user_id,))
    dates = [r[0] for r in cursor.fetchall()]
    if len(dates) >= 2:
        from datetime import timedelta
        streak = 1
        max_streak = 1
        for i in range(1, len(dates)):
            try:
                d1 = datetime.strptime(dates[i-1], "%Y-%m-%d")
                d2 = datetime.strptime(dates[i], "%Y-%m-%d")
                if (d1 - d2).days == 1:
                    streak += 1
                    max_streak = max(max_streak, streak)
                else:
                    streak = 1
            except:
                streak = 1
        if max_streak >= 3:
            earned.append("streak_3")
        if max_streak >= 7:
            earned.append("streak_7")
    
    # 3. ボリューム（1日最大）
    cursor.execute('''
        SELECT s.date, SUM(l.weight * l.reps) as vol
        FROM set_logs l
        JOIN workout_exercises e ON l.workout_exercise_id = e.id
        JOIN workout_sessions s ON e.session_id = s.id
        WHERE s.user_id = ?
        GROUP BY s.date
        ORDER BY vol DESC LIMIT 1
    ''', (user_id,))
    vol_row = cursor.fetchone()
    if vol_row:
        if vol_row[1] >= 1000:
            earned.append("volume_1000")
        if vol_row[1] >= 5000:
            earned.append("volume_5000")
    
    # 4. 種目数
    cursor.execute('''
        SELECT COUNT(DISTINCT e.exercise_name)
        FROM workout_exercises e
        JOIN workout_sessions s ON e.session_id = s.id
        WHERE s.user_id = ?
    ''', (user_id,))
    ex_count = cursor.fetchone()[0]
    if ex_count >= 10:
        earned.append("exercises_10")
    
    # 5. 特定種目1RM
    for check_name, badge_id in [("bench", "bench_100"), ("squat", "squat_100"), ("deadlift", "deadlift_100")]:
        cursor.execute('''
            SELECT MAX(l.estimated_1rm)
            FROM set_logs l
            JOIN workout_exercises e ON l.workout_exercise_id = e.id
            JOIN workout_sessions s ON e.session_id = s.id
            WHERE s.user_id = ? AND LOWER(e.exercise_name) LIKE ?
        ''', (user_id, f"%{check_name}%"))
        max_1rm = cursor.fetchone()[0]
        if max_1rm and max_1rm >= 100:
            earned.append(badge_id)
    
    conn.close()
    
    # レベル計算 (earned数 × 10 + session_count)
    level = len(earned) * 10 + session_count
    level_name = "Beginner"
    if level >= 100: level_name = "Advanced"
    elif level >= 50: level_name = "Intermediate"
    elif level >= 20: level_name = "Novice"
    
    return {
        "earned": earned,
        "all": ACHIEVEMENTS,
        "level": level,
        "level_name": level_name,
        "session_count": session_count,
        "exercise_count": ex_count if 'ex_count' in dir() else 0
    }


def calculate_estimated_1rm(weight: float, reps: int) -> float:
    """
    Epley式による推定1RM計算
    1RM = weight × (1 + reps / 30)
    """
    return round(weight * (1 + reps / 30), 2)

def get_best_set(sets: List[dict]) -> dict:
    """
    推定1RMが最大のセットを返す
    """
    if not sets:
        return {}
    return max(sets, key=lambda s: s.get('estimated_1rm', 0))

def calculate_total_volume(sets: List[dict]) -> float:
    """
    総負荷計算: Σ(weight × reps)
    """
    return sum(s['weight'] * s['reps'] for s in sets)

# ユーザー登録
@app.post("/register")
def register_user(user: UserCreate):
    hashed_pw = hashlib.sha256(user.password.encode()).hexdigest()
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO users (username, password) VALUES (?, ?)", (user.username, hashed_pw))
        conn.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="このユーザー名は既に存在します")
    finally:
        conn.close()
    return {"message": "ユーザー登録成功"}

# メモ登録
@app.post("/memo")
def add_memo(memo: Memo):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO memos (user_id, date, exercise, weight, reps, note)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (memo.user_id, memo.date, memo.exercise, memo.weight, memo.reps, memo.note))
    memo_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return {"message": "DBにメモを保存しました", "id": memo_id, "memo": memo}

# メモ取得（検索にも対応）
@app.get("/memo")
def get_memos(
    id: Optional[int] = Query(None),
    user_id: Optional[str] = Query(None),
    date: Optional[str] = Query(None),
    exercise: Optional[str] = Query(None)
):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    conditions = []
    values = []
    
    # フィルタリングロジックの強化
    # 1. user_id指定がある場合 -> そのユーザーの公開範囲チェック
    # 2. user_id指定がない場合（全件取得） -> visibility=public OR (visibility=friends AND is_friend)
    
    # 今回は簡略化のため、SQLでJOINして一括取得・フィルタリングする
    # 取得したいのはメモテーブルの全カラム
    base_query = """
        SELECT m.id, m.user_id, m.date, m.exercise, m.weight, m.reps, m.note, u.visibility 
        FROM memos m
        JOIN users u ON m.user_id = u.username
    """
    
    # 条件組み立て
    if id is not None:
        conditions.append("m.id = ?")
        values.append(id)
    if user_id:
        conditions.append("m.user_id = ?")
        values.append(user_id)
    if date:
        conditions.append("m.date LIKE ?")
        values.append(f"%{date}")
    if exercise:
        conditions.append("m.exercise LIKE ?")
        values.append(f"%{exercise}%")
        
    if conditions:
        base_query += " WHERE " + " AND ".join(conditions)
    
    cursor.execute(base_query, values)
    rows = cursor.fetchall()
    
    # メモリ上でフィルタリング（SQLだけで完結させるのは複雑なため）
    # current_user_id (閲覧者) がわかればSQLでできるが、GETパラメータに含める必要がある
    # ここでは、呼び出し元が閲覧者IDを渡してくれていると仮定するか、
    # 簡易的に「全件取得時はPublicのみ」などのルールを設ける
    
    # ★要件: フレンド機能
    # リクエストパラメータにviewer_idを追加してもらうのが良いが、
    # 既存コードへの影響を最小限にするため、
    # 「user_id指定あり」-> その人のvisibilityに従う
    # 「user_id指定なし」-> visibility='public' のみ返す (または全返ししてフロントで制御？いやバックエンドでやるべき)
    
    # 改めて: user_id引数は「フィルタ対象の投稿者ID」。
    # 閲覧者IDが不明だと「フレンド限定」の判定ができない。
    # よって、APIに viewer_id (閲覧者) を追加する。
    pass # 下記のリターン文で処理
    
    # 閲覧者のフレンドリストを取得しておく（本来は引数でviewer_idをもらうべきだが、一旦全データ取得後にPythonでフィルタも可）
    # しかしパフォーマンスが悪い。
    # ここでは、「全員の記録」リクエストの際、閲覧者が「誰か」を知る必要がある。
    # フロントエンドから viewer_id を送ってもらいましょう。
    
    conn.close()
    
    # 整形して返す (visibility情報は落とすか、デバッグ用に残す)
    return [
        {
            "id": row[0],
            "user_id": row[1],
            "date": row[2],
            "exercise": row[3],
            "weight": row[4],
            "reps": row[5],
            "note": row[6],
            # "visibility": row[7] # 必要なら返す
        }
        for row in rows
    ]

@app.get("/memo_v2")
def get_memos_v2(
    viewer_id: str = Query(..., description="閲覧しているユーザーID"),
    target_user: Optional[str] = Query(None, description="特定ユーザーで絞る場合"),
    filter_mode: str = Query("all", description="all:全員(権限あり), friends:フォロー中のみ, mine:自分のみ"),
    exercise: Optional[str] = Query(None)
):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # フォローしているユーザーリストを取得
    cursor.execute("SELECT friend_id FROM friends WHERE user_id = ?", (viewer_id,))
    following = {row[0] for row in cursor.fetchall()}
    following.add(viewer_id) # 自分も含む
    
    query = """
        SELECT m.id, m.user_id, m.date, m.exercise, m.weight, m.reps, m.note, u.visibility 
        FROM memos m
        JOIN users u ON m.user_id = u.username
    """
    conditions = []
    values = []
    
    # 1. ターゲットユーザー絞り込み
    if target_user:
        conditions.append("m.user_id = ?")
        values.append(target_user)
        
    # 2. フィルタモード
    if filter_mode == 'mine':
        conditions.append("m.user_id = ?")
        values.append(viewer_id)
    elif filter_mode == 'friends':
        # フォローしている人のみ
        placeholders = ','.join(['?'] * len(following))
        conditions.append(f"m.user_id IN ({placeholders})")
        values.extend(following)
    
    # 3. その他検索
    if exercise:
        conditions.append("m.exercise LIKE ?")
        values.append(f"%{exercise}%")
        
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
        
    cursor.execute(query, values)
    rows = cursor.fetchall()
    
    results = []
    for row in rows:
        m_id, m_uid, m_date, m_ex, m_w, m_r, m_n, u_vis = row
        
        # 権限チェック
        # 自分自身の投稿は無条件OK
        if m_uid == viewer_id:
            results.append(dict(id=m_id, user_id=m_uid, date=m_date, exercise=m_ex, weight=m_w, reps=m_r, note=m_n))
            continue
            
        # 他人の投稿
        if u_vis == 'private':
            continue
        elif u_vis == 'friends':
            # 投稿者(m_uid)が「フレンドのみ公開」にしている場合、
            # 「投稿者にとってのフレンド」＝「閲覧者(viewer_id)が投稿者のフレンドリストにいるか」？
            # それとも「相互フォロー」？ 
            # 一般的には「Friend Only」は「My Friends (people I follow or mutual) can see」ではなく
            # 「People who follow me can see」または「Mutual friends」。
            # 今回はシンプルに「Follower Only」モデル（Twitterの鍵垢）と仮定すると、
            # viewer_id が m_uid によって承認されている必要がある。
            # しかし実装簡易化のため、「相互フォロー」または「自分が一方的にフォローしていればOK」とする？
            # ユーザーの要望「フレンド機能を追加してフレンドのみ記録を見せることができたり」
            # -> 「見せる」側が主語なので、「私をフォローしている人だけに見せる」または「相互」。
            # ここでは「相手が自分をフォローしているか」をチェックすべきだが、
            # 簡易的に「自分が相手をフォローしていれば見れる（Twitter公開垢）」＋「相手がFriendsOnlyなら相互必須」とするのが妥当。
            
            # 今回はもっと単純に:
            # visibility='friends' -> 閲覧者が、投稿者のfriendsテーブル（follower）に含まれている必要がある。
            # 逆(following)ではなく逆(follower)を取得してチェック。
            cursor.execute("SELECT 1 FROM friends WHERE user_id = ? AND friend_id = ?", (viewer_id, m_uid))
            # ここで user_id=viewer(自分), friend_id=target(相手) なら、自分が相手をフォローしている状態。
            # 相手が「フレンド限定」の場合、「相手と友達（＝相手も自分を知っている）」必要があるか？
            # 定義：「Friend Only」= 「相互フォローのみ閲覧可」としましょう。
            
            # 自分が相手をフォローしているか
            is_following = m_uid in following
            # 相手が自分をフォローしているか
            cursor.execute("SELECT 1 FROM friends WHERE user_id = ? AND friend_id = ?", (m_uid, viewer_id))
            is_followed_by = cursor.fetchone() is not None
            
            if is_following and is_followed_by:
                results.append(dict(id=m_id, user_id=m_uid, date=m_date, exercise=m_ex, weight=m_w, reps=m_r, note=m_n))
        else: # public
            results.append(dict(id=m_id, user_id=m_uid, date=m_date, exercise=m_ex, weight=m_w, reps=m_r, note=m_n))
            
    conn.close()
    return results

# --- Friend API ---

@app.post("/friends")
def add_friend(req: FriendRequest, current_user: str = Query(...)):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    # 自分自身は追加できない
    if req.friend_username == current_user:
         conn.close()
         raise HTTPException(status_code=400, detail="自分自身はフォローできません")
         
    # 相手が存在するかチェック
    cursor.execute("SELECT 1 FROM users WHERE username = ?", (req.friend_username,))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="ユーザーが見つかりません")

    try:
        cursor.execute("INSERT INTO friends (user_id, friend_id) VALUES (?, ?)", (current_user, req.friend_username))
        # 通知を作成
        cursor.execute("INSERT INTO notifications (user_id, from_user, type) VALUES (?, ?, ?)", 
                       (req.friend_username, current_user, 'follow'))
        conn.commit()
    except sqlite3.IntegrityError:
        pass # 既に登録済み
    finally:
        conn.close()
    return {"message": f"{req.friend_username} をフォローしました"}

@app.delete("/friends/{friend_name}")
def remove_friend(friend_name: str, current_user: str = Query(...)):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM friends WHERE user_id = ? AND friend_id = ?", (current_user, friend_name))
    conn.commit()
    conn.close()
    return {"message": f"{friend_name} のフォローを解除しました"}

@app.get("/friends")
def get_friends(current_user: str = Query(...)):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    # 自分がフォローしている人
    cursor.execute("SELECT friend_id FROM friends WHERE user_id = ?", (current_user,))
    following = [row[0] for row in cursor.fetchall()]
    
    # 自分をフォローしている人（フォロワー）
    cursor.execute("SELECT user_id FROM friends WHERE friend_id = ?", (current_user,))
    followers = [row[0] for row in cursor.fetchall()]
    
    conn.close()
    return {"following": following, "followers": followers}

# --- Notifications API ---

@app.get("/notifications")
def get_notifications(current_user: str = Query(...)):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT id, from_user, type, is_read, created_at FROM notifications WHERE user_id = ? ORDER BY created_at DESC LIMIT 50", (current_user,))
    rows = cursor.fetchall()
    conn.close()
    
    res = []
    for r in rows:
        res.append({
            "id": r[0],
            "from_user": r[1],
            "type": r[2],
            "is_read": bool(r[3]),
            "created_at": r[4]
        })
    return res

@app.post("/notifications/read")
def mark_notifications_read(current_user: str = Query(...)):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("UPDATE notifications SET is_read = 1 WHERE user_id = ?", (current_user,))
    conn.commit()
    conn.close()
    return {"message": "既読にしました"}
    followers = [row[0] for row in cursor.fetchall()]
    
    conn.close()
    return {"following": following, "followers": followers}

# --- Notification API ---
@app.get("/notifications")
def get_notifications(current_user: str = Query(...)):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT id, from_user, type, is_read, created_at 
        FROM notifications 
        WHERE user_id = ? 
        ORDER BY created_at DESC LIMIT 20
    ''', (current_user,))
    rows = cursor.fetchall()
    conn.close()
    return [
        {
            "id": r[0],
            "from_user": r[1],
            "type": r[2],
            "is_read": bool(r[3]),
            "created_at": r[4]
        }
        for r in rows
    ]

@app.post("/notifications/read")
def mark_notifications_read(current_user: str = Query(...)):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("UPDATE notifications SET is_read = 1 WHERE user_id = ?", (current_user,))
    conn.commit()
    conn.close()
    return {"message": "通知を既読にしました"}

# --- Settings API ---
@app.put("/settings/visibility")
def update_visibility(settings: UserSettings, current_user: str = Query(...)):
    if settings.visibility not in ['public', 'friends', 'private']:
        raise HTTPException(status_code=400, detail="不正な設定値です")
        
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET visibility = ? WHERE username = ?", (settings.visibility, current_user))
    conn.commit()
    conn.close()
    return {"message": f"公開設定を {settings.visibility} に変更しました"}

@app.get("/users/{username}")
def get_user_profile(username: str, current_user: str = Query(...)):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # 基本情報
    cursor.execute("SELECT username, visibility, target_calories, target_protein, target_fat, target_carbs FROM users WHERE username = ?", (username,))
    u_row = cursor.fetchone()
    if not u_row:
        conn.close()
        raise HTTPException(status_code=404, detail="User not found")
    
    # フォロー状態
    cursor.execute("SELECT 1 FROM friends WHERE user_id = ? AND friend_id = ?", (current_user, username))
    is_following = cursor.fetchone() is not None
    cursor.execute("SELECT 1 FROM friends WHERE user_id = ? AND friend_id = ?", (username, current_user))
    is_follower = cursor.fetchone() is not None
    
    # 統計（実績ツールを再利用せずシンプルにカウント）
    cursor.execute("SELECT COUNT(*) FROM workout_sessions WHERE user_id = ?", (username,))
    workout_count = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM meals WHERE user_id = ?", (username,))
    meal_count = cursor.fetchone()[0]
    
    conn.close()
    return {
        "username": u_row[0],
        "visibility": u_row[1],
        "targets": {
            "cal": u_row[2],
            "p": u_row[3],
            "f": u_row[4],
            "c": u_row[5]
        },
        "stats": {
            "workouts": workout_count,
            "meals": meal_count
        },
        "is_following": is_following,
        "is_follower": is_follower
    }

@app.get("/api/activity/feed")
def get_activity_feed(current_user: str = Query(...), limit: int = 20):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # フォローしているユーザーを取得
    cursor.execute("SELECT friend_id FROM friends WHERE user_id = ?", (current_user,))
    following = [row[0] for row in cursor.fetchall()]
    following.append(current_user) # 自分自身の活動も含む
    
    if not following:
        conn.close()
        return []

    placeholders = ','.join(['?'] * len(following))
    
    activities = []
    
    # 1. 最近のワークアウト
    cursor.execute(f'''
        SELECT s.user_id, s.date, s.created_at, u.visibility 
        FROM workout_sessions s
        JOIN users u ON s.user_id = u.username
        WHERE s.user_id IN ({placeholders})
        ORDER BY s.created_at DESC LIMIT ?
    ''', (*following, limit))
    for row in cursor.fetchall():
        uid, date, created, vis = row
        # 公開設定チェック
        if vis == 'private' and uid != current_user: continue
        # TODO: 'friends'設定の厳密な相互フォローチェックは将来的に追加
        activities.append({
            "type": "workout",
            "user_id": uid,
            "date": date,
            "created_at": created,
            "content": f"logged a workout session"
        })
        
    # 2. 最近の食事
    cursor.execute(f'''
        SELECT m.user_id, m.date, m.food_name, m.calories, u.visibility 
        FROM meals m
        JOIN users u ON m.user_id = u.username
        WHERE m.user_id IN ({placeholders})
        ORDER BY m.id DESC LIMIT ?
    ''', (*following, limit))
    for row in cursor.fetchall():
        uid, date, food, cal, vis = row
        if vis == 'private' and uid != current_user: continue
        activities.append({
            "type": "meal",
            "user_id": uid,
            "date": date,
            "content": f"ate {food} ({cal} kcal)"
        })

    # まとめてソート（食事にはcreated_atがないため日付で簡易ソート）
    activities.sort(key=lambda x: x.get('created_at', x['date']), reverse=True)
    
    conn.close()
    return activities[:limit]

@app.get("/users/me")
def get_my_info(current_user: str = Query(...)):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT username, visibility, target_calories, target_protein, target_fat, target_carbs, target_salt, target_fiber FROM users WHERE username = ?", (current_user,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return {
            "username": row[0],
            "visibility": row[1],
            "target_calories": row[2],
            "target_protein": row[3],
            "target_fat": row[4],
            "target_carbs": row[5],
            "target_salt": row[6],
            "target_fiber": row[7]
        }
    # ユーザーが見つからない場合はデフォルト値を返す
    return {
        "username": current_user,
        "visibility": "public",
        "target_calories": 2000,
        "target_protein": 60,
        "target_fat": 60,
        "target_carbs": 300,
        "target_salt": 7.0,
        "target_fiber": 20.0
    }

@app.put("/settings/targets")
def update_targets(targets: UserTargets, current_user: str = Query(...)):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    # INSERT OR REPLACE (UPSERT)
    cursor.execute('''
        INSERT OR REPLACE INTO users (
            username, target_calories, target_protein, target_fat, target_carbs, target_salt, target_fiber, visibility, password
        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?, 
            COALESCE((SELECT visibility FROM users WHERE username=?), 'public'),
            COALESCE((SELECT password FROM users WHERE username=?), 'nopass')
        )
    ''', (
        current_user, targets.target_calories, targets.target_protein, targets.target_fat, targets.target_carbs, targets.target_salt, targets.target_fiber,
        current_user, current_user
    ))
    conn.commit()
    conn.close()
    return {"message": "目標値を更新しました"}

@app.get("/users/search")
def search_users(q: str = Query("")):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    if q:
        cursor.execute("SELECT username FROM users WHERE username LIKE ? LIMIT 10", (f"%{q}%",))
    else:
        cursor.execute("SELECT username FROM users ORDER BY RANDOM() LIMIT 10") # ランダムに10人表示
    users = [row[0] for row in cursor.fetchall()]
    conn.close()
    return users

# --- Meal Management API ---

@app.post("/meals")
def add_meal(meal: Meal):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO meals (user_id, date, meal_type, food_name, calories, protein, fat, carbs, salt, fiber)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (meal.user_id, meal.date, meal.meal_type, meal.food_name, meal.calories, meal.protein, meal.fat, meal.carbs, meal.salt, meal.fiber))
    conn.commit()
    conn.close()
    return {"message": "食事を記録しました"}

@app.get("/meals")
def get_meals(user_id: str = Query(...), date: Optional[str] = Query(None)):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    query = "SELECT id, date, meal_type, food_name, calories, protein, fat, carbs, salt, fiber FROM meals WHERE user_id = ?"
    params = [user_id]
    
    if date:
        query += " AND date = ?"
        params.append(date)
        
    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()
    
    return [
        {
            "id": r[0], "date": r[1], "meal_type": r[2], "food_name": r[3],
            "calories": r[4], "protein": r[5], "fat": r[6], "carbs": r[7], "salt": r[8], "fiber": r[9]
        }
        for r in rows
    ]

@app.delete("/meals/{meal_id}")
def delete_meal(meal_id: int):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM meals WHERE id = ?", (meal_id,))
    conn.commit()
    conn.close()
    return {"message": "削除しました"}

@app.post("/api/estimate_nutrition")
def estimate_nutrition(req: EstimationRequest):
    text = req.text
    
    # 1. Gemini AI Estimate (High Priority)
    if GEMINI_API_KEY:
        try:
            model = genai.GenerativeModel('gemini-flash-latest')
            prompt = f"""
            栄養士として、以下の食事の栄養素（カロリー、タンパク質、脂質、炭水化物、塩分、食物繊維）を精密に推定してください。
            入力: "{text}"

            指示:
            1. 日本語で回答してください。
            2. 一般的な1人前の量を基準にしてください。
            3. 数値は推定値ですが、栄養士としてできるだけ正確な数値を考えてください。
            4. 栄養バランスに関する「アドバイス」と、なぜその数値になったかの「内訳（推定根拠）」も含めてください。
            5. 出力は以下のJSON形式のみとし、Markdown（```jsonなど）は一切含めないでください。

            {{
                "food_name": "料理名 (分量の目安)",
                "calories": 数値(kcal),
                "protein": 数値(g),
                "fat": 数値(g),
                "carbs": 数値(g),
                "salt": 数値(g),
                "fiber": 数値(g),
                "breakdown": "推定の根拠（例: ご飯200g、焼き鮭80gとして計算）",
                "advice": "栄養士からのアドバイス"
            }}
            """
            
            # 2パターンの生成を試みる（バージョンの互換性のため）
            try:
                # パターンA: response_mime_type を使用 (新しいライブラリ向け)
                response = model.generate_content(
                    prompt,
                    generation_config={"response_mime_type": "application/json"}
                )
                json_text = response.text
            except Exception:
                # パターンB: 通常のテキスト生成 (古いライブラリ向け)
                response = model.generate_content(prompt)
                json_text = response.text

            # Markdownコードブロックの除去
            json_text = re.sub(r'```json\s*|\s*```|`', '', json_text).strip()
            
            # JSON部分の抽出（余計なテキストが混ざる対策）
            match = re.search(r'\{.*\}', json_text, re.DOTALL)
            if match:
                json_text = match.group(0)
            
            data = json.loads(json_text)
            
            return {
                "food_name": data.get("food_name", text),
                "calories": int(data.get("calories", 0)),
                "protein": float(data.get("protein", 0)),
                "fat": float(data.get("fat", 0)),
                "carbs": float(data.get("carbs", 0)),
                "salt": float(data.get("salt", 0.0)),
                "fiber": float(data.get("fiber", 0.0)),
                "breakdown": data.get("breakdown", ""),
                "advice": data.get("advice", ""),
                "source": "Gemini AI (1.5-flash)"
            }
        except Exception as e:
            error_msg = str(e)
            print(f"Gemini Error: {error_msg}")
            if "429" in error_msg or "ResourceExhausted" in error_msg:
                raise HTTPException(status_code=429, detail="AIの利用上限（Quota）に達しました。しばらく待ってから再度お試しください。")
            raise HTTPException(status_code=500, detail=f"AI推定エラー: {error_msg}")
    else:
        raise HTTPException(status_code=500, detail="Gemini APIキーが設定されていません。")

@app.post("/api/daily_advice")
def get_daily_advice(req: AdviceRequest):
    meals = req.meals
    targets = req.targets
    
    if not GEMINI_API_KEY:
        raise HTTPException(status_code=500, detail="Gemini APIキーが設定されていません。")

    if not meals:
        return {"advice": "まだ食事の記録がありません。今日食べたものを入力してください！"}

    meal_summary = "\n".join([f"- {m['meal_type']}: {m['food_name']} ({m['calories']}kcal, P:{m['protein']}g, F:{m['fat']}g, C:{m['carbs']}g, 塩分:{m.get('salt', 0)}g, 繊維:{m.get('fiber', 0)}g)" for m in meals])
    
    total_cal = sum(m['calories'] for m in meals)
    total_p = sum(m['protein'] for m in meals)
    total_f = sum(m['fat'] for m in meals)
    total_c = sum(m['carbs'] for m in meals)
    total_salt = sum(m.get('salt', 0) for m in meals)
    total_fiber = sum(m.get('fiber', 0) for m in meals)

    prompt = f"""
    プロのトレーナー兼栄養士として、今日の食事内容に基づいたアドバイスを200文字程度で提供してください。
    
    【目標値】
    - カロリー: {targets.get('target_calories')}kcal
    - タンパク質: {targets.get('target_protein')}g
    - 脂質: {targets.get('target_fat')}g
    - 炭水化物: {targets.get('target_carbs')}g
    - 塩分: {targets.get('target_salt')}g
    - 食物繊維: {targets.get('target_fiber')}g

    【摂取実績】
    - 総カロリー: {total_cal}kcal
    - 総タンパク質: {total_p}g
    - 総脂質: {total_f}g
    - 総炭水化物: {total_c}g
    - 総塩分: {total_salt}g
    - 総食物繊維: {total_fiber}g

    【食事リスト】
    {meal_summary}

    指示:
    1. 日本語で、親しみやすくもプロフェッショナルな口調で回答してください。
    2. あすけんの「うさぎの先生」やパーソナルトレーナーのような、励ましと具体的な改善案を含めてください。
    3. 塩分過多や食物繊維不足などがあれば、具体的に指摘してください。
    4. Markdownは使わず、プレーンテキストで回答してください。
    """
    
    try:
        model = genai.GenerativeModel('gemini-flash-latest')
        response = model.generate_content(prompt)
        return {"advice": response.text.strip()}
    except Exception as e:
        print(f"Advice Gemini Error: {e}")
        raise HTTPException(status_code=500, detail="アドバイスの生成に失敗しました。")

# メモ更新
@app.put("/memo/{memo_id}")
def update_memo(memo_id: int, memo: Memo):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE memos
        SET user_id = ?, date = ?, exercise = ?, weight = ?, reps = ?, note = ?
        WHERE id = ?
    ''', (memo.user_id, memo.date, memo.exercise, memo.weight, memo.reps, memo.note, memo_id))
    conn.commit()
    conn.close()
    return {"message": "メモを更新しました", "memo": memo}

# ログイン
@app.post("/login")
def login(user: UserCreate):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT password FROM users WHERE username = ?", (user.username,))
    row = cursor.fetchone()
    conn.close()

    if not row:
         raise HTTPException(status_code=400, detail="ユーザー名またはパスワードが間違っています")
    
    hashed_pw = hashlib.sha256(user.password.encode()).hexdigest()
    if row[0] != hashed_pw:
         raise HTTPException(status_code=400, detail="ユーザー名またはパスワードが間違っています")

    return {"message": "ログイン成功", "username": user.username}

# メモ削除
@app.delete("/memo/{memo_id}")
def delete_memo(memo_id: int):
    conn.commit()
    conn.close()
    return {"message": f"メモ（ID: {memo_id}）を削除しました"}

# --- 種目管理 ---

# 種目テーブル作成と初期データ
def init_exercises():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS exercises (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE
        )
    ''')
    # 初期データ
    default_exercises = ["ベンチプレス", "スクワット", "デッドリフト", "懸垂", "ショルダープレス", "ダンベルカール", "腹筋"]
    for ex in default_exercises:
        try:
           cursor.execute("INSERT INTO exercises (name) VALUES (?)", (ex,))
        except sqlite3.IntegrityError:
           pass
    conn.commit()
    conn.close()

init_exercises()

class Exercise(BaseModel):
    name: str

@app.get("/exercises")
def get_exercises():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT id, name FROM exercises ORDER BY id")
    rows = cursor.fetchall()
    conn.close()
    return [{"id": r[0], "name": r[1]} for r in rows]

@app.post("/exercises")
def add_exercise(ex: Exercise):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO exercises (name) VALUES (?)", (ex.name,))
        conn.commit()
        new_id = cursor.lastrowid
        conn.close()
        return {"message": "種目を追加しました", "id": new_id, "name": ex.name}
    except sqlite3.IntegrityError:
        conn.close()
        raise HTTPException(status_code=400, detail="その種目は既に存在します")

@app.delete("/exercises/{ex_id}")
def delete_exercise(ex_id: int):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM exercises WHERE id = ?", (ex_id,))
    conn.commit()
    conn.close()
    return {"message": "種目を削除しました"}

# --- Weight Management API ---

@app.post("/weights")
def add_weight(log: WeightLog):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO weights (user_id, date, weight)
        VALUES (?, ?, ?)
    ''', (log.user_id, log.date, log.weight))
    conn.commit()
    conn.close()
    return {"message": "体重を記録しました"}

@app.get("/weights")
def get_weights(user_id: str = Query(...)):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT id, date, weight FROM weights
        WHERE user_id = ?
        ORDER BY date ASC
    ''', (user_id,))
    rows = cursor.fetchall()
    conn.close()
    return [{"id": r[0], "date": r[1], "weight": r[2]} for r in rows]

# ★ Workout Management APIs ★

@app.post("/api/workout/sessions")
def create_workout_session(session: WorkoutSessionCreate):
    """ワークアウトセッションを作成"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO workout_sessions (user_id, date, duration, session_memo)
        VALUES (?, ?, ?, ?)
    ''', (session.user_id, session.date, session.duration, session.session_memo))
    session_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return {"session_id": session_id, **session.dict()}

@app.get("/api/workout/sessions")
def get_workout_sessions(user_id: str = Query(...), date: Optional[str] = Query(None)):
    """ユーザーのワークアウトセッション一覧を取得"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    if date:
        cursor.execute('''
            SELECT id, user_id, date, duration, session_memo, created_at
            FROM workout_sessions
            WHERE user_id = ? AND date = ?
            ORDER BY created_at DESC
        ''', (user_id, date))
    else:
        cursor.execute('''
            SELECT id, user_id, date, duration, session_memo, created_at
            FROM workout_sessions
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT 30
        ''', (user_id,))
    
    rows = cursor.fetchall()
    conn.close()
    
    return [
        {
            "id": r[0],
            "user_id": r[1],
            "date": r[2],
            "duration": r[3],
            "session_memo": r[4],
            "created_at": r[5]
        }
        for r in rows
    ]

@app.get("/api/workout/sessions/{session_id}")
def get_workout_session_detail(session_id: int):
    """セッション詳細（エクササイズ・セット含む）を取得"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # セッション情報
    cursor.execute('''
        SELECT id, user_id, date, duration, session_memo, created_at
        FROM workout_sessions
        WHERE id = ?
    ''', (session_id,))
    session_row = cursor.fetchone()
    
    if not session_row:
        conn.close()
        raise HTTPException(status_code=404, detail="Session not found")
    
    # エクササイズと セット情報
    cursor.execute('''
        SELECT we.id, we.exercise_name, we.exercise_memo, we.sort_order,
               sl.id, sl.set_number, sl.weight, sl.reps, sl.estimated_1rm
        FROM workout_exercises we
        LEFT JOIN set_logs sl ON we.id = sl.workout_exercise_id
        WHERE we.session_id = ?
        ORDER BY we.sort_order, sl.set_number
    ''', (session_id,))
    
    exercise_rows = cursor.fetchall()
    conn.close()
    
    # データ整形
    exercises = {}
    for row in exercise_rows:
        ex_id, ex_name, ex_memo, sort_order, set_id, set_num, weight, reps, est_1rm = row
        
        if ex_id not in exercises:
            exercises[ex_id] = {
                "id": ex_id,
                "exercise_name": ex_name,
                "exercise_memo": ex_memo,
                "sort_order": sort_order,
                "sets": []
            }
        
        if set_id:
            exercises[ex_id]["sets"].append({
                "id": set_id,
                "set_number": set_num,
                "weight": weight,
                "reps": reps,
                "estimated_1rm": est_1rm
            })
    
    return {
        "id": session_row[0],
        "user_id": session_row[1],
        "date": session_row[2],
        "duration": session_row[3],
        "session_memo": session_row[4],
        "created_at": session_row[5],
        "exercises": list(exercises.values())
    }

@app.post("/api/workout/exercises")
def create_workout_exercise(exercise: WorkoutExerciseCreate):
    """セッションに種目を追加"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO workout_exercises (session_id, exercise_name, exercise_memo, sort_order)
        VALUES (?, ?, ?, ?)
    ''', (exercise.session_id, exercise.exercise_name, exercise.exercise_memo, exercise.sort_order))
    exercise_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return {"exercise_id": exercise_id, **exercise.dict()}

@app.post("/api/workout/sets")
def create_set_log(set_log: SetLogCreate):
    """セットを追加（推定1RMを自動計算）"""
    # 推定1RM計算
    estimated_1rm = calculate_estimated_1rm(set_log.weight, set_log.reps)
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO set_logs (workout_exercise_id, set_number, weight, reps, estimated_1rm)
        VALUES (?, ?, ?, ?, ?)
    ''', (set_log.workout_exercise_id, set_log.set_number, set_log.weight, set_log.reps, estimated_1rm))
    set_id = cursor.lastrowid
    conn.commit()
    conn.close()
    
    return {
        "set_id": set_id,
        "estimated_1rm": estimated_1rm,
        **set_log.dict()
    }

@app.get("/api/workout/today-summary")
def get_today_summary(user_id: str = Query(...), date: str = Query(...)):
    """
    Home画面用Today Summary API
    - ハイライト（最大推定1RMの種目）
    - Total Volume（今日の総負荷）
    - Total Volume前回比
    - 種目一覧（ベストセット・前回比含む）
    """
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # 今日のセッション取得
    cursor.execute('''
        SELECT id FROM workout_sessions
        WHERE user_id = ? AND date = ?
        ORDER BY created_at DESC
        LIMIT 1
    ''', (user_id, date))
    session_row = cursor.fetchone()
    
    if not session_row:
        conn.close()
        return {
            "highlight": None,
            "total_volume": 0,
            "total_volume_change": None,
            "workouts": []
        }
    
    session_id = session_row[0]
    
    # 今日のエクササイズとセット
    cursor.execute('''
        SELECT we.id, we.exercise_name, we.exercise_memo,
               sl.weight, sl.reps, sl.estimated_1rm
        FROM workout_exercises we
        LEFT JOIN set_logs sl ON we.id = sl.workout_exercise_id
        WHERE we.session_id = ?
        ORDER BY we.sort_order, sl.set_number
    ''', (session_id,))
    
    rows = cursor.fetchall()
    
    # データ整形
    exercises_data = {}
    all_sets = []
    
    for row in rows:
        ex_id, ex_name, ex_memo, weight, reps, est_1rm = row
        
        if ex_id not in exercises_data:
            exercises_data[ex_id] = {
                "exercise_name": ex_name,
                "exercise_memo": ex_memo,
                "sets": []
            }
        
        if weight is not None and reps is not None:
            set_data = {"weight": weight, "reps": reps, "estimated_1rm": est_1rm}
            exercises_data[ex_id]["sets"].append(set_data)
            all_sets.append(set_data)
    
    # Total Volume計算
    total_volume = calculate_total_volume(all_sets)
    
    # 前回のトレーニング日を取得
    cursor.execute('''
        SELECT date FROM workout_sessions
        WHERE user_id = ? AND date < ?
        ORDER BY date DESC
        LIMIT 1
    ''', (user_id, date))
    prev_date_row = cursor.fetchone()
    
    total_volume_change = None
    if prev_date_row:
        prev_date = prev_date_row[0]
        
        # 前回のセッションID取得
        cursor.execute('''
            SELECT id FROM workout_sessions
            WHERE user_id = ? AND date = ?
            ORDER BY created_at DESC
            LIMIT 1
        ''', (user_id, prev_date))
        prev_session_row = cursor.fetchone()
        
        if prev_session_row:
            prev_session_id = prev_session_row[0]
            
            # 前回のTotal Volume計算
            cursor.execute('''
                SELECT sl.weight, sl.reps
                FROM set_logs sl
                JOIN workout_exercises we ON sl.workout_exercise_id = we.id
                WHERE we.session_id = ?
            ''', (prev_session_id,))
            prev_sets = [{"weight": r[0], "reps": r[1]} for r in cursor.fetchall()]
            prev_total_volume = calculate_total_volume(prev_sets)
            
            if prev_total_volume > 0:
                total_volume_change = round((total_volume - prev_total_volume) / prev_total_volume * 100, 1)
    
    # 各種目のベストセットと前回比を計算
    workouts = []
    highlight = None
    max_1rm = 0
    
    for ex_data in exercises_data.values():
        if not ex_data["sets"]:
            continue
        
        best_set = get_best_set(ex_data["sets"])
        best_1rm = best_set.get("estimated_1rm", 0)
        
        # 前回比計算（同一種目）
        cursor.execute('''
            SELECT sl.estimated_1rm
            FROM set_logs sl
            JOIN workout_exercises we ON sl.workout_exercise_id = we.id
            JOIN workout_sessions ws ON we.session_id = ws.id
            WHERE ws.user_id = ? AND ws.date < ? AND we.exercise_name = ?
            ORDER BY ws.date DESC, sl.estimated_1rm DESC
            LIMIT 1
        ''', (user_id, date, ex_data["exercise_name"]))
        
        prev_best_row = cursor.fetchone()
        previous_comparison = None
        
        if prev_best_row:
            prev_best_1rm = prev_best_row[0]
            diff = round(best_1rm - prev_best_1rm, 2)
            if diff > 0:
                previous_comparison = f"+{diff}kg"
            elif diff < 0:
                previous_comparison = f"{diff}kg"
            else:
                previous_comparison = "="
        
        workout_item = {
            "exercise_name": ex_data["exercise_name"],
            "exercise_memo": ex_data["exercise_memo"],
            "best_set": {
                "weight": best_set["weight"],
                "reps": best_set["reps"],
                "estimated_1rm": best_1rm
            },
            "previous_comparison": previous_comparison
        }
        
        workouts.append(workout_item)
        
        # ハイライト（最大1RM）
        if best_1rm > max_1rm:
            max_1rm = best_1rm
            highlight = workout_item
    
    conn.close()
    
    return {
        "highlight": highlight,
        "total_volume": total_volume,
        "total_volume_change": total_volume_change,
        "workouts": workouts
    }

if __name__ == "__main__":
    import uvicorn
    import os
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
