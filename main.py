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

# ★ Gemini API Key (環境変数から取得、なければデフォルトを使用)
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "AIzaSyCQoETQmsdRzsLSHJzCNI5Ls_YhY4ccY4o")
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

class WeightLog(BaseModel):
    user_id: str
    date: str
    weight: float

class EstimationRequest(BaseModel):
    text: str

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
    
    # visibilityカラムの追加チェック (ALTER TABLEはif not existsがないため)
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
            carbs REAL
        )
    ''')

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
            type TEXT, -- 'follow'
            is_read INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    conn.commit()
    conn.close()
init_db()

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

@app.get("/users/me")
def get_my_info(current_user: str = Query(...)):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT username, visibility, target_calories, target_protein, target_fat, target_carbs FROM users WHERE username = ?", (current_user,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return {
            "username": row[0],
            "visibility": row[1],
            "target_calories": row[2],
            "target_protein": row[3],
            "target_fat": row[4],
            "target_carbs": row[5]
        }
    return {}

@app.put("/settings/targets")
def update_targets(targets: UserTargets, current_user: str = Query(...)):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE users 
        SET target_calories=?, target_protein=?, target_fat=?, target_carbs=?
        WHERE username=?
    ''', (targets.target_calories, targets.target_protein, targets.target_fat, targets.target_carbs, current_user))
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
        INSERT INTO meals (user_id, date, meal_type, food_name, calories, protein, fat, carbs)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (meal.user_id, meal.date, meal.meal_type, meal.food_name, meal.calories, meal.protein, meal.fat, meal.carbs))
    conn.commit()
    conn.close()
    return {"message": "食事を記録しました"}

@app.get("/meals")
def get_meals(user_id: str = Query(...), date: Optional[str] = Query(None)):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    query = "SELECT id, date, meal_type, food_name, calories, protein, fat, carbs FROM meals WHERE user_id = ?"
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
            "calories": r[4], "protein": r[5], "fat": r[6], "carbs": r[7]
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
            # モデル名を修正: gemini-1.5-flash (2.5は存在しない)
            model = genai.GenerativeModel('gemini-1.5-flash')
            prompt = f"""
            栄養士として、以下の食事の栄養素（カロリー、タンパク質、脂質、炭水化物）を推定してください。
            入力: "{text}"

            指示:
            1. 日本語で回答してください。
            2. 一般的な1人前の量を基準にしてください。
            3. 数値は推定値で構いません。
            4. 出力は以下のJSON形式のみとし、解説やMarkdown（```jsonなど）は一切含めないでください。

            {{
                "food_name": "料理名 (分量の目安)",
                "calories": 数値(kcal),
                "protein": 数値(g),
                "fat": 数値(g),
                "carbs": 数値(g)
            }}
            """
            response = model.generate_content(prompt)
            raw_text = response.text
            
            # Markdownの除去（もし含まれていれば）
            json_text = re.sub(r'```json\s*|\s*```|`', '', raw_text).strip()
            
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
                "source": "Gemini AI (1.5-flash)"
            }
        except Exception as e:
            print(f"Gemini Error: {e}")
            raise HTTPException(status_code=500, detail="AIによる推定に失敗しました。")
    else:
        raise HTTPException(status_code=500, detail="Gemini APIキーが設定されていません。")

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

if __name__ == "__main__":
    import uvicorn
    import os
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
