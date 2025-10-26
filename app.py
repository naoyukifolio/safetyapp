# app.py
import os
import json
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo
import pandas as pd
import streamlit as st

APP_TITLE = "安否確認（QR自動登録・管理）"
TZ = ZoneInfo("Asia/Tokyo")

# DB 永続保存パス（Streamlit Cloud 推奨領域）
DB_DIR = os.path.join(os.getcwd(), ".streamlit")
os.makedirs(DB_DIR, exist_ok=True)
DB_PATH = os.path.join(DB_DIR, "safetycheck.db")
BACKUP_DIR = os.path.join(DB_DIR, "backups")
os.makedirs(BACKUP_DIR, exist_ok=True)

# ---------------------------
# DB 初期化（checkins + deletions 監査）
# ---------------------------
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
        cur.execute(
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

# ---------------------------
# ユーティリティ
# ---------------------------
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

# ---------------------------
# DB 操作
# ---------------------------
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

# ---------------------------
# 削除関連ユーティリティ
# ---------------------------
def backup_rows(rows_df: pd.DataFrame, tag: str = "") -> str:
    """バックアップCSVを作成してパスを返す"""
    ts = datetime.now(TZ).strftime("%Y%m%d_%H%M%S")
    fname = f"backup_{tag}_{ts}.csv" if tag else f"backup_{ts}.csv"
    path = os.path.join(BACKUP_DIR, fname)
    rows_df.to_csv(path, index=False, encoding="utf-8-sig")
    return path

def log_deletions(rows: list, deleted_by: str = "", reason: str = ""):
    """削除した行の内容を deletions テーブルに記録"""
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        for row in rows:
            cur.execute(
                """
                INSERT INTO deletions (deleted_at, deleted_by, reason, deleted_row_json)
                VALUES (?, ?, ?, ?)
                """,
                (
                    now_jst_iso(),
                    deleted_by,
                    reason,
                    json.dumps(row, ensure_ascii=False)
                )
            )
        conn.commit()

def delete_rows_by_ids(ids: list):
    if not ids:
        return 0
    placeholders = ",".join(["?"] * len(ids))
    sql = f"DELETE FROM checkins WHERE id IN ({placeholders})"
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute(sql, ids)
        affected = cur.rowcount
        conn.commit()
    return affected

def get_rows_by_ids(ids: list) -> list:
    if not ids:
        return []
    placeholders = ",".join(["?"] * len(ids))
    sql = f"SELECT * FROM checkins WHERE id IN ({placeholders})"
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute(sql, ids)
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    return rows

# ---------------------------
# 自動登録
# ---------------------------
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

# ---------------------------
# MAIN
# ---------------------------
def main():
    st.set_page_config(page_title=APP_TITLE, page_icon="🛟", layout="wide")
    st.title(APP_TITLE)

    init_db()

    mode = st.sidebar.radio("モード選択", ["利用者", "管理者"], index=0)
    raw_params = get_query_params()
    params = normalize_params(raw_params)

    if mode == "利用者":
        st.caption("QR読み取りでこのページを開くと自動で安否情報が記録されます。")

        with st.container():
            st.subheader("QR情報")
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

        # 管理者 UI: フィルタと一覧
        st.subheader("履歴一覧（削除可）")
        nick_filter = st.text_input("ニックネームで絞り込み")
        limit = st.number_input("表示件数", min_value=20, max_value=5000, value=200, step=20)

        df = load_history(limit=int(limit), nick_filter=nick_filter)

        if df.empty:
            st.info("該当データはありません。")
        else:
            # 表示と選択（ID選択式）
            st.markdown("**削除したい行を選択してください（複数選択可）**")
            id_list = df["id"].astype(str).tolist()
            # 表示上は "id: nick / ts" 形式で分かりやすく
            choices = [f"{r['id']}: {r['nick']} / {r['ts']}" for _, r in df.iterrows()]
            selected = st.multiselect("選択", options=choices)

            # 日時指定で一括削除も可能
            st.markdown("---")
            st.markdown("**日付条件で一括削除**")
            cutoff = st.date_input("この日より前のデータを削除（指定がなければ無効）")
            cutoff_submit = st.button("この日より前のデータを一覧に追加（確認）")
            if cutoff_submit:
                cutoff_dt = datetime.combine(cutoff, datetime.min.time()).isoformat()
                with sqlite3.connect(DB_PATH) as conn:
                    q = "SELECT * FROM checkins WHERE ts < ? ORDER BY id DESC"
                    df_cut = pd.read_sql_query(q, conn, params=[cutoff_dt])
                if df_cut.empty:
                    st.info("指定日より前のデータはありません。")
                else:
                    st.markdown(f"以下 {len(df_cut)} 件が対象です（一覧に追加されます）")
                    for r in df_cut.itertuples(index=False):
                        label = f"{r.id}: {r.nick} / {r.ts}"
                        if label not in choices:
                            choices.append(label)
                    # 再-render: シンプルに通知してユーザーに再選択を促す
                    st.info("対象は上の選択リストに表示されています。削除する場合は選択してください。")

            st.markdown("---")
            st.markdown("**削除操作（確認が必要）**")
            reason = st.text_input("削除理由（簡単に記載）")
            confirm_text = st.text_input('確認のため "DELETE" と入力してください')
            if st.button("選択行を削除する"):
                if confirm_text != "DELETE":
                    st.error('確認テキストが "DELETE" と一致しません。削除は中止します。')
                elif not selected:
                    st.warning("削除対象が選択されていません。")
                else:
                    # parse selected IDs
                    ids = [int(s.split(":")[0]) for s in selected]
                    rows = get_rows_by_ids(ids)
                    df_backup = pd.DataFrame(rows)
                    backup_path = backup_rows(df_backup, tag="manual_delete")
                    log_deletions(rows, deleted_by=os.environ.get("ADMIN_USER", "admin"), reason=reason)
                    affected = delete_rows_by_ids(ids)
                    st.success(f"{affected} 件を削除しました。バックアップ: {backup_path}")
                    # refresh: reload df
                    df = load_history(limit=int(limit), nick_filter=nick_filter)
                    st.experimental_rerun()

            st.markdown("---")
            st.markdown("**その他の削除オプション**")
            if st.button("全データをバックアップして全削除（危険）"):
                c = st.checkbox("本当に全件削除します（チェックで有効化）")
                if c:
                    confirm_all = st.text_input('全削除の確認テキスト "DELETE ALL" と入力してください')
                    if st.button("全削除実行"):
                        if confirm_all != "DELETE ALL":
                            st.error("確認テキストが一致しません。実行中止。")
                        else:
                            # backup all
                            full_df = load_history(limit=1000000)
                            backup_path = backup_rows(full_df, tag="full_delete")
                            # log each row
                            rows = full_df.to_dict(orient="records")
                            log_deletions(rows, deleted_by=os.environ.get("ADMIN_USER", "admin"), reason="full_delete")
                            # delete all
                            with sqlite3.connect(DB_PATH) as conn:
                                cur = conn.cursor()
                                cur.execute("DELETE FROM checkins")
                                conn.commit()
                            st.success(f"全件削除しました。バックアップ: {backup_path}")
                            st.experimental_rerun()
                    else:
                        st.info("全削除の最終確認を行ってください。")

        # 管理者向け: 削除ログの参照
        st.markdown("---")
        st.subheader("削除監査ログ（最近100件）")
        with sqlite3.connect(DB_PATH) as conn:
            df_del = pd.read_sql_query("SELECT * FROM deletions ORDER BY id DESC LIMIT 100", conn)
        if df_del.empty:
            st.info("削除ログはまだありません。")
        else:
            st.dataframe(df_del, use_container_width=True)

        # CSVダウンロード機能
        st.markdown("---")
        st.subheader("データエクスポート")
        df_export = load_history(limit=1000000)
        st.download_button(
            "CSVダウンロード（全件）",
            df_export.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"checkins_export_{datetime.now(TZ).strftime('%Y%m%d_%H%M%S')}.csv",
            mime="text/csv"
        )

if __name__ == "__main__":
    main()
