# KinApp

筋力トレーニング、食事、体重を記録するWebアプリです。

## ローカル起動

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn main:app --reload
```

ブラウザで `http://127.0.0.1:8000` を開き、新規登録またはログインしてください。

AI機能を利用する場合は、環境変数 `GEMINI_API_KEY` を設定します。`.env` とローカルDBはGitへ追加しないでください。

## テスト

```powershell
pip install -r requirements-dev.txt
pytest -q
```

## 認証

- パスワードはPBKDF2-SHA256で保存されます。
- 旧SHA-256形式のパスワードは、正しいパスワードでログインした際に自動更新されます。
- APIはログイン時に発行されるBearerトークンを必要とします。
- トークンはDB内ではSHA-256ハッシュとして保存され、有効期間は30日です。
