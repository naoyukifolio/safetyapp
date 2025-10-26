import os
import json
import sqlite3
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

# Twilio (SMS)
TWILIO_READY = False
try:
    from twilio.rest import Client as TwilioClient
    TWILIO_READY = True
except Exception:
    TWILIO_READY = False

APP_TITLE = "安否確認（QR自動登録・HTTP対応版）"
DB_PATH = "safetycheck.db"
TZ = ZoneInfo("Asia/Tokyo")

# 環境変数
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM_NUMBER = os.environ.get("TWILIO_FROM_NUMBER", "")

SMS_ENABLED = TWILIO_READY and all([
    TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER
])

# ==========================================================
# DB 初期化
# ==========================================================
def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS checkins (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                nick TEXT,
                addr TEXT,
                school TEXT,
                tel TEXT,
                status TEXT,
                raw_params TEXT,
                sms_sent INTEGER DEFAULT 0,
                user_agent TEXT
            )
            """
        )
        conn.commit()


# ==========================================================
# DB 入出力・重複対策
# ==========================================================
def insert_record(payload: dict):
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO checkins (
                ts, nick, addr, school, tel, status,
                raw_params, sms_sent, user_agent
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload["ts"],
                payload.get("nick"),
                payload.get("addr"),
                payload.get("school"),
                payload.get("tel"),
                payload.get("status"),
                json.dumps(payload.get("raw_params"), ensure_ascii=False),
                int(payload.get("sms_sent", 0)),
                payload.get("user_agent"),
            ),
        )
        conn.commit()


def load_history(filters: dict | None = None, limit: int | None = None):
    sql = "SELECT * FROM checkins WHERE 1=1"
    params = []
    if filters:
        if filters.get("nick"):
            sql += " AND nick LIKE ?"
            params.append(f"%{filters['nick']}%")
    sql += " ORDER BY id DESC"
    if limit:
        sql += f" LIMIT {int(limit)}"
    with sqlite3.connect(DB_PATH) as conn:
        df = pd.read_sql_query(sql, conn, params=params)
    return df


def dedup_key(payload: dict):
    t_bucket = datetime.now(TZ).replace(second=0, microsecond=0).isoformat()
    return f"{t_bucket}|{payload.get('nick')}|{payload.get('status')}"


def save_once(payload: dict) -> bool:
    key = dedup_key(payload)
    if "saved_keys" not in st.session_state:
        st.session_state["saved_keys"] = set()
    if key in st.session_state["saved_keys"]:
        return False
    insert_record(payload)
    st.session_state["saved_keys"].add(key)
    return True


# ==========================================================
# UTILS
# ==========================================================
def now_jst_iso():
    return datetime.now(TZ).isoformat(timespec="seconds")


def get_query_params():
    try:
        params = st.query_params.to_dict()
    except Exception:
        params = st.experimental_get_query_params()
        params = {k: (v[0] if isinstance(v, list) else v) for k, v in params.items()}
    return params


def normalize_params(params: dict):
    out = {"nick": "", "addr": "", "school": "", "tel": ""}
    mapping = {
        "nick": ["nick", "name"],
        "addr": ["addr", "address"],
        "school": ["school"],
        "tel": ["tel", "phone"],
    }
    for std, aliases in mapping.items():
        for k in aliases:
            if k in params and params[k]:
                out[std] = params[k]
                break
    return out


def try_send_sms(to_number: str, message: str) -> bool:
    if not SMS_ENABLED:
        return False
    try:
        client = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        client.messages.create(
            to=to_number,
            from_=TWILIO_FROM_NUMBER,
            body=message
        )
        return True
    except Exception as e:
        st.warning(f"SMS送信に失敗: {e}")
        return False


# ==========================================================
# ユーザーモード（自動登録）
# ==========================================================
def auto_register(params, raw_params):
    payload = {
        "ts": now_jst_iso(),
        "nick": params["nick"],
        "addr": params["addr"],
        "school": params["school"],
        "tel": params["tel"],
        "status": "無事",               # 初期値
        "raw_params": raw_params,
        "sms_sent": 0,
        "user_agent": st.session_state.get("_user_agent_", ""),
    }
    saved = save_once(payload)
    if saved:
        st.success("安否情報を自動登録しました（無事）。")


# ==========================================================
# MAIN
# ==========================================================
def main():
    st.set_page_config(page_title=APP_TITLE, page_icon="🛟", layout="wide")
    st.title(APP_TITLE)

    init_db()
    mode = st.sidebar.radio("モード", ["ユーザー", "管理者"], index=0)

    raw_params = get_query_params()
    params = normalize_params(raw_params)

    if mode == "ユーザー":
        st.caption("QR読み取りで開くだけで、自動で安否登録されます。（HTTP対応：位置情報は記録しません）")

        st.markdown("### QRデータ")
        st.write(f"ニックネーム: **{params['nick'] or '（未指定）'}**")
        st.write(f"住所: **{params['addr'] or '（未指定）'}**")
        st.write(f"学校: **{params['school'] or '（未指定）'}**")
        st.write(f"保護者電話: **{params['tel'] or '（未指定）'}**")

        auto_register(params, raw_params)

        st.markdown("### 直近履歴")
        df = load_history(filters={"nick": params.get("nick")}, limit=50)
        st.dataframe(df, use_container_width=True)

    else:
        st.sidebar.markdown("### 管理者ログイン")
        pw = st.sidebar.text_input("パスワード", type="password")
        if not ADMIN_PASSWORD:
            st.error("ADMIN_PASSWORD が未設定です。")
            return
        if pw != ADMIN_PASSWORD:
            st.warning("正しいパスワードを入力してください。")
            return

        st.success("管理者モード中")

        st.markdown("### フィルタ")
        nick_f = st.text_input("ニックネーム検索")

        df = load_history(filters={"nick": nick_f}, limit=500)
        st.dataframe(df, use_container_width=True)

        st.download_button(
            "CSVダウンロード",
            df.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"checkins_{datetime.now(TZ).strftime('%Y%m%d_%H%M%S')}.csv",
            mime="text/csv"
        )

        st.caption("※HTTP版は地図表示は無効")


if __name__ == "__main__":
    main()
