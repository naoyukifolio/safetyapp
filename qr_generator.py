import os
import urllib.parse
import pandas as pd
import streamlit as st
import qrcode
from PIL import Image     # ✅追加
from io import BytesIO
from reportlab.pdfgen import canvas
from reportlab.lib.units import mm

APP_TITLE = "QRコード発行アプリ（安否登録・Excel対応）"

CONFIRM_BASE_URL = os.environ.get("CONFIRM_BASE_URL", "http://localhost:8501")

# ✅ クレジットカードサイズ
credit_card = (85.6 * mm, 54.0 * mm)


def make_qr_url(nick, addr, school, tel):
    params = dict(nick=nick, addr=addr, school=school, tel=tel)
    query = urllib.parse.urlencode(params, encoding="utf-8", safe="=")
    return f"{CONFIRM_BASE_URL}/?{query}"


def generate_qr_image(data: str) -> bytes:
    qr = qrcode.QRCode(version=3, box_size=8, border=2)
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def create_pdf(cards):
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=credit_card)

    for i, card in enumerate(cards):
        if i > 0:
            c.showPage()

        qr_size = 30 * mm
        qr_img = Image.open(BytesIO(card["qr_img"]))   # ✅修正

        c.drawInlineImage(qr_img, 50 * mm, 10 * mm, qr_size, qr_size)

        c.setFont("Helvetica-Bold", 14)
        c.drawString(5 * mm, 40 * mm, card["nick"])

        c.setFont("Helvetica", 8)
        c.drawString(5 * mm, 36 * mm, card["school"])
        c.drawString(5 * mm, 32 * mm, f"TEL: {card['tel']}")

        c.setFont("Helvetica", 7)
        c.drawString(50 * mm, 8 * mm, "読み込んで安否登録")

    c.save()
    pdf_data = buffer.getvalue()
    buffer.close()
    return pdf_data


def main():
    st.set_page_config(page_title=APP_TITLE)
    st.title(APP_TITLE)

    st.info("Excel（.xlsx）からQR名札PDFを発行できます。")
    st.caption("必要列：nick, addr, school, tel")

    uploaded = st.file_uploader("児童情報Excelをアップロード", type=["xlsx"])

    if uploaded:
        df = pd.read_excel(uploaded, dtype=str).fillna("")
        st.dataframe(df, use_container_width=True)

        required = {"nick", "addr", "school", "tel"}
        if not required.issubset(df.columns):
            st.error(f"Excelに必要列がありません: {required}")
            return

        if st.button("QR名札PDFを作成"):
            cards = []
            for _, row in df.iterrows():
                url = make_qr_url(row["nick"], row["addr"], row["school"], row["tel"])
                qr_data = generate_qr_image(url)
                cards.append({
                    "nick": row["nick"],
                    "addr": row["addr"],
                    "school": row["school"],
                    "tel": row["tel"],
                    "qr_img": qr_data,
                })

            pdf = create_pdf(cards)
            st.success("PDFを生成しました。")

            st.download_button(
                label="QR名札PDFダウンロード",
                data=pdf,
                file_name="qr_cards.pdf",
                mime="application/pdf",
            )

    st.caption("※ CONFIRM_BASE_URL を適切なURLに設定してください。")


if __name__ == "__main__":
    main()
