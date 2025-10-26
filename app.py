# app.py （削除後 rerun 安定版）
import os
import json
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo
import pandas as pd
import streamlit as st

APP_TITLE = "安否確認（QR自動登録・管理）"
TZ = ZoneInfo("Asia/Tokyo")

# DB 永続保存パス
DB_DIR = os.path.join(os.getcwd(), ".streamlit")
os.makedirs(DB_DIR, exist_ok=True)
DB_PATH = os.path.join(DB_DIR, "safetycheck.db")
BACKUP_DIR = os.path.join(DB_DIR, "backups")
os.makedirs(BACKUP_DIR, exist_ok=True)

def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS checkins (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                nick TEXT, addr TEXT, school TEXT, tel TEXT,
                status TEXT DEFAULT '無事',
                raw_params TEXT,
                sms_sent INTEGER DEFAULT 0,
                user_agent TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS deletions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                deleted_at TEXT NOT NULL,
                deleted_by TEXT,
                reason TEXT,
                deleted_row_json TEXT
            )
            """
        )
        conn.commit()

def now_jst_iso():
    return datetime.now(TZ).isoformat(timespec="seconds")

def get_query_params():
    try:
        return st.query_params.to_dict()
    except Exception:
        params = st.experimental_get_query_params()
        return {k: v[0] for k, v in params.items()}

def normalize_params(params: dict):
    keys = {"nick": "", "addr": "", "school": "", "tel": ""}
    mapping = {"nick": ["nick"], "addr": ["addr"], "school": ["school"], "tel": ["tel"]}
    for std, aliases in mapping.items():
        for k in aliases:
            if k in params:
                keys[std] = params[k]
    return keys

def insert_record(payload):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO checkins (ts, nick, addr, school, tel, status, raw_params, sms_sent, user_agent)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload["ts"], payload["nick"], payload["addr"], payload["school"],
                payload["tel"], payload["status"], json.dumps(payload["raw_params"], ensure_ascii=False),
                0, payload["user_agent"],
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
        sql += f" LIMIT {limit}"
    return pd.read_sql_query(sql, sqlite3.connect(DB_PATH), params=params)

def backup_rows(df, tag=""):
    ts = datetime.now(TZ).strftime("%Y%m%d_%H%M%S")
    fname = f"backup_{tag}_{ts}.csv"
    path = os.path.join(BACKUP_DIR, fname)
    df.to_csv(path, index=False, encoding="utf-8-sig")
    return path

def log_deletions(rows, user="admin", reason=""):
    with sqlite3.connect(DB_PATH) as conn:
        for row in rows:
            conn.execute(
                """
                INSERT INTO deletions (deleted_at, deleted_by, reason, deleted_row_json)
                VALUES (?, ?, ?, ?)
                """,
                (
                    now_jst_iso(),
                    user,
                    reason,
                    json.dumps(row, ensure_ascii=False),
                ),
            )
        conn.commit()

def delete_rows(ids):
    if not ids:
        return 0
    placeholders = ",".join("?" * len(ids))
    sql = f"DELETE FROM checkins WHERE id IN ({placeholders})"
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute(sql, ids)
        cnt = cur.rowcount
        conn.commit()
    return cnt

def auto_register(params, raw_params):
    payload = {
        "ts": now_jst_iso(),
        "nick": params["nick"],
        "addr": params["addr"],
        "school": params["school"],
        "tel": params["tel"],
        "status": "無事",
        "raw_params": raw_params,
        "user_agent": st.session_state.get("_ua", ""),
    }
    insert_record(payload)
    st.success("安否情報を登録しました（無事）")


def main():
    st.set_page_config(page_title=APP_TITLE, page_icon="🛟", layout="wide")

    init_db()
    mode = st.sidebar.radio("モード選択", ["利用者", "管理者"])

    raw_params = get_query_params()
    params = normalize_params(raw_params)

    if mode == "利用者":
        st.title("安否自動登録 完了")
        st.write(f"ニックネーム：{params['nick']}")
        if params["nick"]:
            auto_register(params, raw_params)
        st.dataframe(load_history(20, params["nick"]))

    else:
        st.title("管理者モード")
        pw = st.sidebar.text_input("パスワード", type="password")
        admin_pw = os.environ.get("ADMIN_PASSWORD")

        if pw != admin_pw:
            st.warning("パスワードが一致しません")
            return

        df = load_history(limit=2000)
        st.dataframe(df, use_container_width=True)

        st.subheader("削除操作")
        selected_ids = st.multiselect("削除対象ID", df["id"].astype(int).tolist())
        reason = st.text_input("削除理由")
        if st.button("選択削除"):
            rows = df[df["id"].isin(selected_ids)].to_dict("records")
            path = backup_rows(pd.DataFrame(rows), "manual_delete")
            log_deletions(rows, reason=reason)
            delete_rows(selected_ids)
            st.success(f"{len(rows)} 件削除しました。バックアップ: {path}")
            st.session_state["_need_rerun"] = True

        if st.session_state.get("_need_rerun"):
            st.session_state["_need_rerun"] = False
            st.rerun()


if __name__ == "__main__":
    main()
