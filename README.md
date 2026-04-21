# アサーション練習（公開版）

自分の気持ちを相手に伝える練習をAIと対話しながら進めるツール。
マルチユーザー対応・スマホ対応。Streamlit Community Cloud × Supabase（PostgreSQL）で動作。

## 特徴

- **マルチユーザー対応**：UUID + 復元キー方式（メアド不要）
- **AI対話**：Claude Sonnet による対話・セリフ生成・傾向診断
- **危機ワード検知**：自傷・虐待ワードを検知してサポート情報を提示
- **週次レポート**：記録を自動で週単位に集約

## ローカル実行

```bash
pip install -r requirements.txt

# .env にAPIキーを設定
echo "ANTHROPIC_API_KEY=sk-ant-xxxxx" > .env

streamlit run app.py
```

`DATABASE_URL` が未設定ならローカルの `assertion.db`（SQLite）にフォールバック。

## クラウド公開手順

mood-tracker-public と**同じSupabaseプロジェクトを共有**します（テーブル prefix `assertion_` で衝突回避）。

### 1. GitHub push

```bash
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/onepieceshintaro/assertion-bot-public.git
git push -u origin main
```

### 2. Streamlit Community Cloud へデプロイ

1. <https://share.streamlit.io> → New app
2. Repository: `onepieceshintaro/assertion-bot-public` / Branch: `main` / Main file: `app.py`
3. **Advanced settings → Secrets** に以下を貼り付け：

   ```toml
   DATABASE_URL = "postgresql://postgres.xxxx:PASSWORD@aws-0-ap-northeast-1.pooler.supabase.com:5432/postgres"
   ANTHROPIC_API_KEY = "sk-ant-xxxxxxxxxx"
   ```

4. Deploy

### 3. 既存データの移行（任意）

```bash
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
# DATABASE_URL と ANTHROPIC_API_KEY を編集

python migrate.py --dry-run
python migrate.py
```

冪等：weekly_reports は upsert、assertion_records と risk_scores は重複の可能性があるので**初回のみ流す**のが安全。

## テーブル構成（Supabase内）

mood-tracker-public と同じ Supabase プロジェクトを共有しているため、テーブル名には `assertion_` prefix を付けています：

| テーブル | 役割 |
| --- | --- |
| `assertion_records` | 各アサーション記録（1件＝1出来事） |
| `assertion_weekly_reports` | 週次レポート |
| `assertion_risk_scores` | 危機ワード検知ログ |

## ファイル構成

| ファイル | 役割 |
| --- | --- |
| `app.py` | Streamlit 本体 |
| `storage.py` | DB操作（SQLAlchemy・SQLite/Postgres両対応） |
| `db.py` | SQLAlchemy エンジン |
| `_user.py` | ユーザーID・復元キー・サイドバーUI |
| `assertion_engine.py` | Claude との対話・セリフ生成 |
| `prompts.py` | プロンプトテンプレート |
| `reports.py` | 週次レポート生成 |
| `risk.py` | 危機スコアリング |
| `migrate.py` | ローカルSQLite → Supabase 移行 |
