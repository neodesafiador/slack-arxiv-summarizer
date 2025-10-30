# Slack arXiv Summarizer (gpt-5)

Slack に **arXiv 論文の URL**（例：`https://arxiv.org/abs/2510.17844`）を投稿すると、
同じスレッドに **日本語で構造化された研究要約**（背景・目的・手法・実験方法・実験結果・考察）を自動返信するボットです。

---

## 機能

* arXiv の **abs / pdf 両形式 URL** を自動検出（`v2`、`?context`、`<url|label>` 形式対応）
* `Abstract` ＋ メタデータを根拠に **gpt-5** が要約生成
* 出力形式は **日本語の箇条書き（6項目）**
* Slack Blocks で整形された読みやすい投稿
* 複数 URL を含むメッセージも順次処理

---

## 使用技術

| 項目        | 技術                             |
| --------- | ------------------------------ |
| 言語        | Python 3.9+                    |
| Slack SDK | `slack-bolt`, `slack-sdk`      |
| モデル       | `gpt-5`（OpenAI API）            |
| arXiv取得   | `arxiv` + Export API fallback  |
| 実行方式      | 手動起動 (`bash scripts/run.sh`)   |
| 形式        | Socket Mode（App-Level Token対応） |

---

## ディレクトリ構成

```
slack-arxiv-summarizer/
├─ README.md
├─ requirements.txt
├─ .env
├─ .gitignore
├─ scripts/
│   └─ run.sh
└─ src/
    └─ slack_arxiv_summarizer.py
```

---

## セットアップ手順

### ① リポジトリの取得

```bash
git clone https://github.com/yourname/slack-arxiv-summarizer.git
cd slack-arxiv-summarizer
```

### ② 仮想環境の構築

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### ③ `.env` の作成

中身を以下のように設定：

```dotenv
SLACK_BOT_TOKEN=xoxb-your-bot-token
SLACK_APP_TOKEN=xapp-your-app-level-token
OPENAI_API_KEY=sk-your-openai-key
OPENAI_MODEL=gpt-5
```

> ✅ `SLACK_BOT_TOKEN` は **Bot User OAuth Token (xoxb-)**
> ✅ `SLACK_APP_TOKEN` は **App Level Token (xapp-)**
> ✅ `Socket Mode` を **ON** にして `connections:write` スコープを付与してください。

---

## Slack App の設定

1. [https://api.slack.com/apps](https://api.slack.com/apps) にアクセス
2. 「**Create New App → From scratch**」で新規作成
3. **OAuth & Permissions → Bot Token Scopes** に以下を追加

   * `chat:write`
   * `channels:history`
   * （必要に応じて）`groups:history`, `im:history`
4. **Socket Mode** を **ON** にして App-Level Token を発行（`xapp-...`）
5. **Event Subscriptions** → Bot Events に以下を追加

   * `message.channels`
   * `message.groups`
   * `message.im`
6. 「**Reinstall to Workspace**」をクリック
7. Slack チャンネルで `/invite @arxiv-summarizer-bot`

---

## 実行方法

```bash
bash scripts/run.sh
```

起動ログ：

```
Starting Slack Socket Mode...
Waiting for arXiv URLs in Slack...
```

その状態で Slack チャンネルに

```
https://arxiv.org/abs/2510.17844
```

を投稿すると、同スレッドに日本語要約が返ります。

---

## 出力例

```
arXiv 要約（日本語）

*【背景】*
- 深層学習モデルの一般化性能に関する課題を背景に、より効率的な学習方法を探求。

*【目的】*
- 提案手法が従来手法に比べてデータ効率を高めるかを検証する。

*【手法】*
- Transformerベースのアーキテクチャに正則化モジュールを導入。

*【実験方法】*
- ImageNetおよびCIFARデータセットで性能比較を実施。

*【実験結果】*
- 提案手法はベースラインに対して約5%精度向上を示した。

*【考察】*
- モデルの表現力強化が少量データ学習に寄与することを確認。
```
