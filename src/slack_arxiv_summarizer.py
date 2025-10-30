import os, re, time, json
from typing import Dict, List, Optional
import arxiv
from slack_sdk import WebClient
from slack_sdk.rtm_v2 import RTMClient
from openai import OpenAI
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET


# ======== 環境変数 ========
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
SLACK_APP_TOKEN = os.getenv("SLACK_APP_TOKEN") 
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5")

if not SLACK_BOT_TOKEN or not OPENAI_API_KEY:
    raise RuntimeError("SLACK_BOT_TOKEN と OPENAI_API_KEY を .env に設定してください。")
if not SLACK_APP_TOKEN:
    raise RuntimeError("SLACK_APP_TOKEN（xapp-）を .env に設定してください。")

# ======== クライアント ========
app = App(token=SLACK_BOT_TOKEN)
slack_web = app.client
rtm = RTMClient(token=SLACK_BOT_TOKEN)
openai_client = OpenAI(api_key=OPENAI_API_KEY)

# ======== arXiv URL 検出（abs/pdf両対応） ========
ARXIV_URL_RE = re.compile(
    r"""(?xi)
    https?://arxiv\.org/
    (?P<kind>abs|pdf)/
    (?P<raw>
        (?:\d{4}\.\d{4,5}            # modern: 2510.17844 / 2407.01234
        |[a-z\-]+(?:\.[A-Z\-]+)?/\d{7}  # legacy: cs/0601001, math.PR/0309136
        )
        (?:v\d+)?                    # optional version: v2
    )
    (?:\.pdf)?                       # optional .pdf
    (?:[\?#][^\s>]*)?                # optional ?query or #anchor
    """
)

def extract_arxiv_ids(text: str) -> List[str]:
    return list({m[1] for m in ARXIV_URL_RE.findall(text or "")})

# ======== arXivメタデータ取得 ========
# 先頭付近に追加
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET

def _meta_from_arxiv_paper(paper):
    authors = [a.name for a in paper.authors] if paper.authors else []
    pdf_url = ""
    for link in getattr(paper, "links", []) or []:
        if link.get("title") == "pdf" or link.get("type") == "application/pdf":
            pdf_url = link["href"]
    return {
        "title": (paper.title or "").strip(),
        "authors": authors,
        "summary": (paper.summary or "").strip(),  # Abstract
        "published": getattr(paper, "published", None),
        "updated": getattr(paper, "updated", None),
        "entry_id": getattr(paper, "entry_id", ""),
        "primary_category": getattr(paper, "primary_category", {}).get("term", ""),
        "categories": [t["term"] for t in getattr(paper, "tags", [])],
        "comment": getattr(paper, "comment", ""),
        "doi": getattr(paper, "doi", ""),
        "pdf_url": pdf_url,
        "arxiv_url": getattr(paper, "entry_id", ""),
        "arxiv_id": paper.get_short_id() if hasattr(paper, "get_short_id") else None,
    }

def _parse_export_api_xml(xml_bytes: bytes):
    ns = {"a": "http://www.w3.org/2005/Atom"}
    root = ET.fromstring(xml_bytes)
    entry = root.find("a:entry", ns)
    if entry is None:
        return None
    title = (entry.findtext("a:title", default="", namespaces=ns) or "").strip()
    summary = (entry.findtext("a:summary", default="", namespaces=ns) or "").strip()
    authors = [a.findtext("a:name", default="", namespaces=ns) or "" for a in entry.findall("a:author", ns)]
    pdf_url = ""
    for link in entry.findall("a:link", ns):
        if link.get("title") == "pdf" or link.get("type") == "application/pdf":
            pdf_url = link.get("href") or ""
    arxiv_url = entry.findtext("a:id", default="", namespaces=ns) or ""
    # primary_category / categories は別NSのことがあるが省略でもOK
    return {
        "title": title,
        "authors": authors,
        "summary": summary,
        "published": entry.findtext("a:published", default="", namespaces=ns),
        "updated": entry.findtext("a:updated", default="", namespaces=ns),
        "entry_id": arxiv_url,
        "primary_category": "",
        "categories": [],
        "comment": "",
        "doi": "",
        "pdf_url": pdf_url,
        "arxiv_url": arxiv_url,
        "arxiv_id": arxiv_url.rsplit("/", 1)[-1] if arxiv_url else None,
    }

def fetch_arxiv_metadata(arxiv_id: str) -> Optional[Dict]:
    """
    取得戦略（順に試す）:
      1) arxiv.Search(id_list=[arxiv_id])
      2) arxiv.Search(query=f"id:{arxiv_id}", max_results=1)
      3) Export API: https://export.arxiv.org/api/query?id_list=...
    """
    # 1) id_list
    try:
        client = arxiv.Client(page_size=1, delay_seconds=0, num_retries=2)
        results = list(client.results(arxiv.Search(id_list=[arxiv_id])))
        if results:
            meta = _meta_from_arxiv_paper(results[0])
            meta["arxiv_id"] = meta.get("arxiv_id") or arxiv_id
            return meta
    except Exception:
        pass

    # 2) query:id:...
    try:
        client = arxiv.Client(page_size=1, delay_seconds=0, num_retries=2)
        results = list(client.results(arxiv.Search(query=f"id:{arxiv_id}", max_results=1)))
        if results:
            meta = _meta_from_arxiv_paper(results[0])
            meta["arxiv_id"] = meta.get("arxiv_id") or arxiv_id
            return meta
    except Exception:
        pass

    # 3) Export API 直叩き
    try:
        url = "https://export.arxiv.org/api/query?" + urllib.parse.urlencode({"id_list": arxiv_id})
        with urllib.request.urlopen(url, timeout=10) as resp:
            xml_bytes = resp.read()
        meta = _parse_export_api_xml(xml_bytes)
        if meta:
            meta["arxiv_id"] = meta.get("arxiv_id") or arxiv_id
            return meta
    except Exception:
        pass

    return None


# ======== OpenAI要約 ========
def build_messages(meta: Dict):
    meta_text = f"""
Title: {meta.get('title')}
Authors: {', '.join(meta.get('authors', []))}
arXiv: {meta.get('arxiv_id')} | URL: {meta.get('arxiv_url')}
Primary Category: {meta.get('primary_category')}
Categories: {', '.join(meta.get('categories', []))}
DOI: {meta.get('doi')}
Comment: {meta.get('comment')}
Published: {meta.get('published')}
Updated: {meta.get('updated')}

Abstract:
{meta.get('summary')}
""".strip()

    system = (
        "あなたは研究支援アシスタントです。"
        "次のarXiv論文情報を基に、事実に忠実な日本語の研究要約を作成してください。"
        "出力はSlack投稿向けに、以下6項目をすべてMarkdown形式で出力します。\n\n"
        "各項目には1〜3行の箇条書き(-)を含めてください。"
        "推測や誇張は禁止。不明点は'N/A'と記載。"
    )

    user = (
        "出力フォーマット:\n"
        "arXiv 要約（日本語）\n\n"
        "*【背景】*\n- ...\n\n"
        "*【目的】*\n- ...\n\n"
        "*【手法】*\n- ...\n\n"
        "*【実験方法】*\n- ...\n\n"
        "*【実験結果】*\n- ...\n\n"
        "*【考察】*\n- ...\n\n"
        "入力:\n" + meta_text
    )

    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def summarize_with_openai(meta: Dict) -> str:
    resp = openai_client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=build_messages(meta),
    )
    return resp.choices[0].message.content.strip()

# ======== Slack出力 ========
def build_slack_blocks(meta: Dict, summary_text: str):
    title = meta.get("title", "Untitled")
    url = meta.get("arxiv_url") or meta.get("pdf_url") or ""
    authors = ", ".join(meta.get("authors", [])) or "N/A"

    return [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "🧠 arXiv 要約（日本語）", "emoji": True},
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*<{url}|{title}>*\n{authors}"},
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": summary_text},
        },
        {"type": "divider"},
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"arXiv: `{meta.get('arxiv_id')}` | DOI: {meta.get('doi') or 'N/A'}",
                }
            ],
        },
    ]

# ======== arXivごとの処理 ========
def handle_one_arxiv(channel: str, parent_ts: str, arxiv_id: str):
    slack_web.chat_postMessage(channel=channel, thread_ts=parent_ts, text=f":mag_right: 要約中… `{arxiv_id}`")
    meta = fetch_arxiv_metadata(arxiv_id)
    if not meta:
        slack_web.chat_postMessage(
            channel=channel,
            thread_ts=parent_ts,
            text=f"見つかりませんでした: `{arxiv_id}`\n"
                 f"- 入力IDの形式が正しいか（例: 2305.10310）\n"
                 f"- 一時的なアクセス失敗の可能性（時間をおいて再投稿）\n"
                 f"- それでもNGならIDをそのまま貼ってください"
        )
        return
    ...


# ======== Slackメッセージ監視 ========
@app.event("message")
def handle_message_events(body, event, say, logger):
    # botの投稿や編集イベント（subtype）を除外
    if event.get("bot_id") or event.get("subtype"):
        return

    text = event.get("text", "") or ""
    channel = event.get("channel")
    parent_ts = event.get("ts")

    ids = extract_arxiv_ids(text)
    if not ids or not channel or not parent_ts:
        return

    for aid in ids:
        try:
            # 進捗メッセージは同一スレッドへ
            slack_web.chat_postMessage(channel=channel, thread_ts=parent_ts, text=f"🔎 要約中… `{aid}`")
            meta = fetch_arxiv_metadata(aid)
            if not meta:
                slack_web.chat_postMessage(channel=channel, thread_ts=parent_ts, text=f"見つかりませんでした: {aid}")
                continue
            summary_text = summarize_with_openai(meta)   # ← 箇条書きフォーマットで返す版
            if not summary_text:
                slack_web.chat_postMessage(channel=channel, thread_ts=parent_ts, text="要約生成に失敗しました。")
                continue
            blocks = build_slack_blocks(meta, summary_text)
            slack_web.chat_postMessage(channel=channel, thread_ts=parent_ts, blocks=blocks, text="arXiv要約")
            time.sleep(0.8)  # 軽い緩和
        except Exception as e:
            logger.exception(e)
            slack_web.chat_postMessage(channel=channel, thread_ts=parent_ts, text=f"エラー: {e}")


if __name__ == "__main__":
    print("Waiting for arXiv URLs in Slack...")
    SocketModeHandler(app, SLACK_APP_TOKEN).start()
