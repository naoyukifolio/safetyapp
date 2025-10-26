import os
import json
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo
import pandas as pd
import streamlit as st

APP_TITLE = "安否確認（QR自動登録・管理）"
TZ = ZoneInfo("Asia/Tokyo")

# ✅ DB 永続保存パス（Streamlit Cloud 推奨領域）
DB_DIR = os.path.join(os.getcwd(), ".streamlit")
os.makedirs(DB_DIR, exist_ok=True)
DB_PATH = os.path.join(DB_DIR, "safetycheck.db")


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
                status TEXT DEFAULT '無事',
                raw_params TEXT,
                sms_sent INTEGER DEFAULT 0,
                user_agent TEXT
            )
            """
        )
        conn.commit()


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
    keys = {"nick": "", "addr": "", "school": "", "tel": ""}
    mapping = {
        "nick": ["nick", "n"],
        "addr": ["addr", "a"],
        "school": ["school", "s"],
        "tel": ["tel", "p", "phone"],
    }
    for std, aliases in mapping.items():
        for k in aliases:
            if k in params:
                keys[std] = params[k]
                break
    return keys


# ==========================================================
# DB 操作
# ==========================================================
def insert_record(payload: dict):
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO checkins (
                ts, nick, addr, school, tel, status,
                raw_params, sms_sent, user_agent
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload["ts"], payload.get("nick"), payload.get("addr"),
                payload.get("school"), payload.get("tel"),
                payload.get("status", "無事"),
                json.dumps(payload.get("raw_params"), ensure_ascii=False),
                int(payload.get("sms_sent", 0)),
                payload.get("user_agent"),
            ),
        )
        conn.commit()


def load_history(limit=None, nick_filter=None):
    sql = "SELECT * FROM checkins WHERE 1=1"
    params = []
    if nick_filter:
        sql += " AND nick LIKE ?"
        params.append(f"%{nick_filter}%")
    sql += " ORDER BY id DESC"
    if limit:
        sql += f" LIMIT {int(limit)}"
    with sqlite3.connect(DB_PATH) as conn:
        return pd.read_sql_query(sql, conn, params=params)


# ==========================================================
# 自動登録
# ==========================================================
def auto_register(params, raw_params):
    payload = {
        "ts": now_jst_iso(),
        "nick": params["nick"],
        "addr": params["addr"],
        "school": params["school"],
        "tel": params["tel"],
        "status": "無事",
        "raw_params": raw_params,
        "sms_sent": 0,
        "user_agent": st.session_state.get("_user_agent_", "")
    }
    insert_record(payload)
    st.success("安否情報を自動登録しました（ステータス：無事）")


# ==========================================================
# MAIN
# ==========================================================
def main():
    st.set_page_config(page_title=APP_TITLE, page_icon="🛟", layout="wide")
    st.title(APP_TITLE)

    init_db()

    mode = st.sidebar.radio("モード選択", ["利用者", "管理者"], index=0)
    raw_params = get_query_params()
    params = normalize_params(raw_params)

    if mode == "利用者":
        st.caption("QR読み取りでこのページを開くと自動で安否情報が記録されます。")

        with st.container(border=True):
            st.subheader("QRデータ")
            st.write(f"ニックネーム：**{params['nick']}**")
            st.write(f"住所：{params['addr']}")
            st.write(f"学校：{params['school']}")
            st.write(f"保護者電話：{params['tel']}")

        if params["nick"]:
            auto_register(params, raw_params)

        st.subheader("最近の登録履歴")
        df = load_history(limit=20, nick_filter=params["nick"])
        st.dataframe(df, use_container_width=True)

    else:
        st.sidebar.subheader("管理者パスワード入力")
        pw = st.sidebar.text_input("パスワード", type="password")
        admin_pw = os.environ.get("ADMIN_PASSWORD", "")

        if not admin_pw:
            st.error("管理パスワードが環境設定されていません。")
            return

        if pw != admin_pw:
            st.warning("パスワードが一致しません。")
            return

        st.success("管理者アクセス許可")

        st.subheader("履歴一覧")
        nick_filter = st.text_input("ニックネーム検索")
        limit = st.number_input("表示件数", min_value=20, max_value=2000, value=200, step=20)

        df = load_history(limit=int(limit), nick_filter=nick_filter)
        st.dataframe(df, use_container_width=True)

        st.download_button(
            "CSVダウンロード",
            df.to_csv(index=False).encode("utf-8-sig"),
            file_name="safetycheck_history.csv",
            mime="text/csv"
        )


if __name__ == "__main__":
    main()
