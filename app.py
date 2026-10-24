import streamlit as st
import pandas as pd
from datetime import datetime, date, timedelta
import requests
import sqlite3
import hashlib
import time
import base64
from PIL import Image, ImageDraw
import io
import plotly.express as px
from streamlit_cropper import st_cropper

# 1. 頁面基本設定
st.set_page_config(page_title="記帳本", layout="wide")

# 2. SQLite 資料庫初始化與工具函式
DB_FILE = "tracker.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            password_hash TEXT NOT NULL,
            avatar_type TEXT DEFAULT 'char',
            avatar_data TEXT DEFAULT ''
        )
    ''')
    
    for col, default_val in [("avatar_type", "'char'"), ("avatar_data", "''")]:
        try:
            c.execute(f"ALTER TABLE users ADD COLUMN {col} TEXT DEFAULT {default_val}")
        except sqlite3.OperationalError:
            pass

    c.execute('''
        CREATE TABLE IF NOT EXISTS expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            date TEXT NOT NULL,
            year INTEGER NOT NULL,
            month TEXT NOT NULL,
            item TEXT NOT NULL,
            category TEXT NOT NULL,
            original_amount TEXT NOT NULL,
            currency TEXT NOT NULL,
            rate_used REAL NOT NULL,
            amount_twd INTEGER NOT NULL,
            is_favorite INTEGER DEFAULT 0,
            note TEXT DEFAULT '',
            FOREIGN KEY (username) REFERENCES users (username)
        )
    ''')
    for col, default_val in [("is_favorite", "0"), ("note", "''")]:
        try:
            c.execute(f"ALTER TABLE expenses ADD COLUMN {col} TEXT DEFAULT {default_val}")
        except sqlite3.OperationalError:
            pass

    conn.commit()
    conn.close()

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def register_user(username, password):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    default_char = username[0].upper() if username else "🌸"
    try:
        c.execute(
            "INSERT INTO users (username, password_hash, avatar_type, avatar_data) VALUES (?, ?, 'char', ?)", 
            (username, hash_password(password), default_char)
        )
        conn.commit()
        conn.close()
        return True
    except sqlite3.IntegrityError:
        conn.close()
        return False

def authenticate_user(username, password):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT password_hash, avatar_type, avatar_data FROM users WHERE username = ?", (username,))
    row = c.fetchone()
    conn.close()
    if row and row[0] == hash_password(password):
        avt_type = row[1] if row[1] else "char"
        avt_data = row[2] if row[2] else (username[0].upper() if username else "🌸")
        return True, avt_type, avt_data
    return False, "char", "🌸"

def update_user_avatar(username, avt_type, avt_data):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE users SET avatar_type = ?, avatar_data = ? WHERE username = ?", (avt_type, avt_data, username))
    conn.commit()
    conn.close()

def delete_user_account(username):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("DELETE FROM expenses WHERE username = ?", (username,))
    c.execute("DELETE FROM users WHERE username = ?", (username,))
    conn.commit()
    conn.close()

def load_user_expenses(username):
    conn = sqlite3.connect(DB_FILE)
    df = pd.read_sql_query("SELECT * FROM expenses WHERE username = ? ORDER BY date DESC", conn, params=(username,))
    conn.close()
    return df

def add_expense_db(username, date_str, year_num, month_str, item, category, orig_amt, currency, rate, amt_twd, is_favorite=0, note=""):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''
        INSERT INTO expenses (username, date, year, month, item, category, original_amount, currency, rate_used, amount_twd, is_favorite, note)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (username, date_str, year_num, month_str, item, category, orig_amt, currency, rate, amt_twd, is_favorite, note))
    conn.commit()
    conn.close()

def update_expense_db(expense_id, date_str, year_num, month_str, item, category, orig_amt, currency, rate, amt_twd, note=""):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''
        UPDATE expenses 
        SET date=?, year=?, month=?, item=?, category=?, original_amount=?, currency=?, rate_used=?, amount_twd=?, note=?
        WHERE id=?
    ''', (date_str, year_num, month_str, item, category, orig_amt, currency, rate, amt_twd, note, expense_id))
    conn.commit()
    conn.close()

def toggle_favorite_db(expense_id, current_fav_status):
    new_status = 0 if current_fav_status == 1 else 1
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE expenses SET is_favorite = ? WHERE id = ?", (new_status, expense_id))
    conn.commit()
    conn.close()
    return new_status

def delete_expense_db(expense_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("DELETE FROM expenses WHERE id = ?", (expense_id,))
    conn.commit()
    conn.close()

init_db()

# 幣別與對應基準
CURRENCY_MAP = {
    "NT 新台幣": {"code": "TWD", "fallback": 1.0},
    "US 美元": {"code": "USD", "fallback": 32.50},
    "¥ 日圓": {"code": "JPY", "fallback": 0.2100},
    "¥ 人民幣": {"code": "CNY", "fallback": 4.50},
    "₩ 韓元": {"code": "KRW", "fallback": 0.0230},
    "€ 歐元": {"code": "EUR", "fallback": 35.50},
    "£ 英鎊": {"code": "GBP", "fallback": 42.00}
}

# 📊 2年 ~ 10年歷史數據庫
RATESVIEWER_10Y_DB = {
    2017: {"USD": 30.43, "JPY": 0.2710, "CNY": 4.500, "KRW": 0.0270, "EUR": 34.35, "GBP": 39.20},
    2018: {"USD": 30.15, "JPY": 0.2730, "CNY": 4.560, "KRW": 0.0270, "EUR": 35.60, "GBP": 40.20},
    2019: {"USD": 30.91, "JPY": 0.2830, "CNY": 4.470, "KRW": 0.0260, "EUR": 34.60, "GBP": 39.50},
    2020: {"USD": 29.58, "JPY": 0.2770, "CNY": 4.280, "KRW": 0.0250, "EUR": 33.80, "GBP": 37.90},
    2021: {
        "USD": 27.88, "JPY": 0.2540, "CNY": 4.330, "KRW": 0.0240, "EUR": 33.05, "GBP": 38.50,
        "monthly": {
            "2021-01": {"USD": 27.98}, "2021-02": {"USD": 27.85}, "2021-03": {"USD": 28.20},
            "2021-04": {"USD": 28.25}, "2021-05": {"USD": 27.97}, "2021-06": {"USD": 27.81},
            "2021-07": {"USD": 27.892, "JPY": 0.2520}, "2021-08": {"USD": 27.84},
            "2021-09": {"USD": 27.69}, "2021-10": {"USD": 27.83}, "2021-11": {"USD": 27.75},
            "2021-12": {"USD": 27.68}
        }
    },
    2022: {"USD": 29.80, "JPY": 0.2270, "CNY": 4.420, "KRW": 0.0230, "EUR": 31.35, "GBP": 36.80},
    2023: {"USD": 31.15, "JPY": 0.2200, "CNY": 4.350, "KRW": 0.0240, "EUR": 33.70, "GBP": 38.70},
    2024: {"USD": 32.20, "JPY": 0.2080, "CNY": 4.450, "KRW": 0.0230, "EUR": 34.80, "GBP": 41.20},
    2025: {"USD": 32.50, "JPY": 0.2120, "CNY": 4.480, "KRW": 0.0230, "EUR": 35.20, "GBP": 41.80}
}

# 🏛️ 10年 ~ 30年中央銀行外匯歷史數據庫 (擴充至 1995 年)
CBC_LONGTERM_DB = {
    2016: {"USD": 32.26, "JPY": 0.2970, "CNY": 4.850, "KRW": 0.0270, "EUR": 35.70, "GBP": 43.60},
    2015: {"USD": 31.89, "JPY": 0.2640, "CNY": 5.080, "KRW": 0.0280, "EUR": 35.30, "GBP": 48.80},
    2014: {"USD": 30.37, "JPY": 0.2870, "CNY": 4.940, "KRW": 0.0288, "EUR": 40.26, "GBP": 50.08},
    2013: {"USD": 29.77, "JPY": 0.3050, "CNY": 4.850, "KRW": 0.0272, "EUR": 39.50, "GBP": 46.50},
    2012: {"USD": 29.62, "JPY": 0.3710, "CNY": 4.690, "KRW": 0.0263, "EUR": 38.04, "GBP": 46.90},
    2011: {"USD": 29.47, "JPY": 0.3690, "CNY": 4.560, "KRW": 0.0266, "EUR": 41.02, "GBP": 47.20},
    2010: {"USD": 31.64, "JPY": 0.3610, "CNY": 4.670, "KRW": 0.0274, "EUR": 41.98, "GBP": 48.90},
    2009: {"USD": 33.05, "JPY": 0.3530, "CNY": 4.830, "KRW": 0.0259, "EUR": 46.00, "GBP": 51.60},
    2008: {"USD": 31.52, "JPY": 0.3060, "CNY": 4.530, "KRW": 0.0286, "EUR": 46.30, "GBP": 58.20},
    2007: {"USD": 32.84, "JPY": 0.2790, "CNY": 4.320, "KRW": 0.0354, "EUR": 44.90, "GBP": 65.60},
    2006: {"USD": 32.53, "JPY": 0.2790, "CNY": 4.080, "KRW": 0.0341, "EUR": 40.80, "GBP": 59.90},
    2005: {"USD": 32.17, "JPY": 0.2920, "CNY": 3.930, "KRW": 0.0314, "EUR": 40.10, "GBP": 58.50},
    2004: {"USD": 33.43, "JPY": 0.3090, "CNY": 4.040, "KRW": 0.0292, "EUR": 41.50, "GBP": 61.20},
    2003: {"USD": 34.42, "JPY": 0.2970, "CNY": 4.160, "KRW": 0.0289, "EUR": 38.90, "GBP": 56.20},
    2002: {"USD": 34.58, "JPY": 0.2760, "CNY": 4.180, "KRW": 0.0276, "EUR": 32.60, "GBP": 51.90},
    2001: {"USD": 33.80, "JPY": 0.2780, "CNY": 4.080, "KRW": 0.0262, "EUR": 30.30, "GBP": 48.70},
    2000: {"USD": 31.23, "JPY": 0.2900, "CNY": 3.770, "KRW": 0.0276, "EUR": 28.80, "GBP": 47.30},
    1999: {"USD": 32.27, "JPY": 0.2830, "CNY": 3.900, "KRW": 0.0271, "EUR": 34.40, "GBP": 52.20},
    1998: {"USD": 33.46, "JPY": 0.2560, "CNY": 4.040, "KRW": 0.0239, "EUR": 37.50, "GBP": 55.40},
    1997: {"USD": 28.70, "JPY": 0.2370, "CNY": 3.460, "KRW": 0.0302, "EUR": 32.40, "GBP": 47.00},
    1996: {"USD": 27.49, "JPY": 0.2530, "CNY": 3.310, "KRW": 0.0342, "EUR": 35.10, "GBP": 42.90},
    1995: {"USD": 26.49, "JPY": 0.2820, "CNY": 3.170, "KRW": 0.0343, "EUR": 34.70, "GBP": 41.80}
}

CATEGORIES = ["🍔 美食飲食", "🛍️ 購物買買", "🚗 交通出行", "🎮 娛樂遊玩", "🏠 居家日常", "💊 醫療健康", "✨ 其他雜項"]

MACARON_COLORS = ['#ffb7c5', '#ffdac1', '#e2f0cb', '#b5ead7', '#c7ceea', '#e0bbe4', '#fec8d8']

# ⚡ 精準匯率檢索
def fetch_rate_by_date(currency_label, target_date):
    if currency_label == "NT 新台幣":
        return 1.0
        
    code = CURRENCY_MAP[currency_label]["code"]
    fallback = CURRENCY_MAP[currency_label]["fallback"]
    
    today = date.today()
    check_date = min(target_date, today)
    days_diff = (today - check_date).days
    target_year = check_date.year
    target_month_str = check_date.strftime("%Y-%m")

    if days_diff <= 365:
        try:
            url = f"https://open.er-api.com/v6/latest/{code}"
            res = requests.get(url, timeout=2.0)
            if res.status_code == 200:
                data = res.json()
                if "rates" in data and "TWD" in data["rates"]:
                    val = float(data["rates"]["TWD"])
                    if code in ["USD", "EUR", "GBP"] and val < 10:
                        return fallback
                    if code == "JPY" and val > 1.0:
                        return fallback
                    if code == "KRW" and val > 0.2:
                        return fallback
                    if code == "CNY" and (val < 2 or val > 10):
                        return fallback
                    return round(val, 4)
        except Exception:
            pass

    if target_year in RATESVIEWER_10Y_DB:
        year_db = RATESVIEWER_10Y_DB[target_year]
        if "monthly" in year_db and target_month_str in year_db["monthly"]:
            month_db = year_db["monthly"][target_month_str]
            if code in month_db:
                return month_db[code]
        if code in year_db:
            return year_db[code]

    if target_year in CBC_LONGTERM_DB and code in CBC_LONGTERM_DB[target_year]:
        return CBC_LONGTERM_DB[target_year][code]

    return fallback

# 3. 馬卡龍風格 CSS 樣式
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=M+PLUS+Rounded+1c:wght@500;700;800&family=Noto+Sans+TC:wght@500;700;900&display=swap');
    
    html, body, [class*="css"], .stApp { 
        background-color: #fffaf5; 
        font-family: 'M PLUS Rounded 1c', 'Noto Sans TC', sans-serif !important; 
        color: #5c4b51; 
    }
    
    .stMarkdown a, .cute-header a { display: none !important; }
    
    .cute-header {
        background: linear-gradient(135deg, #ffb7c5, #ffe4e1);
        padding: 2.2rem 1.5rem;
        border-radius: 26px;
        text-align: center;
        box-shadow: 0 8px 20px rgba(255, 183, 197, 0.35);
        margin-bottom: 2rem;
        width: 100%;
    }
    
    .cute-header h1 { 
        font-family: 'M PLUS Rounded 1c', 'Noto Sans TC', sans-serif;
        font-weight: 800;
        color: #ffffff; 
        font-size: 2.5rem; 
        margin: 0 auto; 
        padding: 0;
        text-shadow: 2px 3px 5px rgba(210, 110, 130, 0.25); 
        letter-spacing: 2px;
        line-height: 1.3;
        text-align: center;
    }
    
    .cute-header p { 
        font-family: 'M PLUS Rounded 1c', 'Noto Sans TC', sans-serif;
        color: #7d5a60; 
        margin: 0.6rem auto 0 auto; 
        padding: 0;
        font-size: 1.1rem; 
        font-weight: 700; 
        text-align: center;
    }

    .popup-box {
        text-align: center;
        padding: 1.5rem 1rem;
    }
    
    .popup-icon {
        font-size: 3.2rem;
        margin-bottom: 0.8rem;
        animation: pulse 1.2s infinite alternate;
    }

    @keyframes pulse {
        0% { transform: scale(1); }
        100% { transform: scale(1.15); }
    }
    
    .sidebar-user-card {
        background-color: #ffffff;
        border: 2px solid #ffd1dc;
        border-radius: 18px;
        padding: 1.2rem;
        text-align: center;
        box-shadow: 0 4px 12px rgba(255, 209, 220, 0.25);
        margin-bottom: 1.2rem;
    }
    
    .avatar-circle-img {
        width: 75px;
        height: 75px;
        border-radius: 50%;
        object-fit: cover;
        border: 3px solid #ffb7c5;
        box-shadow: 0 4px 10px rgba(255, 183, 197, 0.4);
        margin: 0 auto 0.6rem auto;
        display: block;
    }

    .avatar-circle-char {
        width: 75px;
        height: 75px;
        border-radius: 50%;
        background: linear-gradient(135deg, #ffb7c5, #ffdac1);
        color: white;
        font-size: 2.3rem;
        font-weight: 900;
        line-height: 75px;
        text-align: center;
        box-shadow: 0 4px 10px rgba(255, 183, 197, 0.4);
        margin: 0 auto 0.6rem auto;
    }

    .sidebar-user-card h3 {
        color: #ff6b81;
        font-size: 1.3rem;
        margin-bottom: 0.3rem;
        font-weight: 800;
    }

    .stat-card {
        background-color: #ffffff;
        border: 2px solid #ffd1dc;
        border-radius: 20px;
        padding: 1.2rem;
        text-align: center;
        box-shadow: 0 4px 15px rgba(255, 209, 220, 0.2);
        margin-bottom: 1.5rem;
    }
    .stat-label { font-size: 0.95rem; color: #8b7d83; font-weight: 700; }
    .stat-value { font-size: 2.1rem; font-weight: 800; color: #ff6b81; margin-top: 0.3rem; }
    
    .amount-right-align {
        text-align: right !important;
        font-weight: 700;
        color: #5c4b51;
        width: 100%;
    }

    div.stButton > button[kind="primary"] {
        background-color: #e05263 !important;
        border-color: #e05263 !important;
        color: white !important;
        font-weight: 700 !important;
        border-radius: 14px !important;
        box-shadow: 0 4px 12px rgba(224, 82, 99, 0.25) !important;
        transition: all 0.2s ease-in-out !important;
    }
    div.stButton > button[kind="primary"]:hover {
        background-color: #c94353 !important;
        border-color: #c94353 !important;
        box-shadow: 0 6px 16px rgba(201, 67, 83, 0.35) !important;
    }

    div[data-testid="stColumn"] button[help="點擊設定/取消為常用項目"] {
        padding: 2px 6px !important;
        font-size: 0.85rem !important;
        min-height: 32px !important;
    }

    #MainMenu {visibility: hidden;} footer {visibility: hidden;}
</style>
""", unsafe_allow_html=True)

# 4. Session State 初始化
if 'logged_in' not in st.session_state:
    st.session_state.logged_in = False
if 'username' not in st.session_state:
    st.session_state.username = ""
if 'avatar_type' not in st.session_state:
    st.session_state.avatar_type = "char"
if 'avatar_data' not in st.session_state:
    st.session_state.avatar_data = "🌸"
if 'guest_expenses' not in st.session_state:
    st.session_state.guest_expenses = []
if 'form_reset_key' not in st.session_state:
    st.session_state.form_reset_key = 0
if 'confirm_delete_account' not in st.session_state:
    st.session_state.confirm_delete_account = False
if 'show_popup_type' not in st.session_state:
    st.session_state.show_popup_type = None

# 🌟 5. 可愛的提示視窗
@st.dialog("🌸 系統訊息")
def cute_status_dialog(msg_type):
    if msg_type == "switching":
        st.markdown("""
        <div class="popup-box">
            <div class="popup-icon">🔄✨</div>
            <h3 style="color: #ff6b81; font-weight: 800;">切換中，請稍後...</h3>
            <p style="color: #8b7d83; font-weight: 700;">正在為您精準連線並獲取最新匯率數字喔！</p>
        </div>
        """, unsafe_allow_html=True)
        bar = st.progress(0)
        for i in range(100):
            time.sleep(0.01)
            bar.progress(i + 1)
            
    elif msg_type == "saved":
        st.markdown("""
        <div class="popup-box">
            <div class="popup-icon">🎉💖</div>
            <h3 style="color: #ff6b81; font-weight: 800;">資料儲存成功！</h3>
            <p style="color: #8b7d83; font-weight: 700;">已經為你完美紀錄這筆生活消費囉！</p>
        </div>
        """, unsafe_allow_html=True)
        time.sleep(1.5)
        
    elif msg_type == "updated":
        st.markdown("""
        <div class="popup-box">
            <div class="popup-icon">✏️🌸</div>
            <h3 style="color: #ff6b81; font-weight: 800;">修改完成！</h3>
            <p style="color: #8b7d83; font-weight: 700;">消費紀錄已經更新完畢囉！</p>
        </div>
        """, unsafe_allow_html=True)
        time.sleep(1.5)

    st.session_state.show_popup_type = None
    st.rerun()

if st.session_state.show_popup_type:
    cute_status_dialog(st.session_state.show_popup_type)

# ✂️ 6. 彈出式「圖片自由裁切」小視窗（使用 streamlit-cropper）
@st.dialog("🖼️ 上傳與裁切個人頭像")
def avatar_upload_dialog():
    st.markdown("請選擇一張電腦中的圖片，並在畫面上**自由拖拉/縮放裁切框**：")
    uploaded_file = st.file_uploader("選擇圖片檔 (JPG, PNG, WEBP)", type=["jpg", "jpeg", "png", "webp"], key="dlg_avatar_uploader")
    
    if uploaded_file is not None:
        try:
            img = Image.open(uploaded_file).convert("RGBA")
            
            cropped_img = st_cropper(
                img,
                realtime_update=True,
                box_color="#ffb7c5",
                aspect_ratio=(1, 1),
                key="cropper_widget"
            )
            
            if cropped_img is not None:
                resized_img = cropped_img.resize((200, 200), Image.LANCZOS)
                mask = Image.new('L', (200, 200), 0)
                draw = ImageDraw.Draw(mask)
                draw.ellipse((0, 0, 200, 200), fill=255)
                
                final_output = Image.new('RGBA', (200, 200), (0, 0, 0, 0))
                final_output.paste(resized_img, (0, 0), mask)
                
                st.markdown("##### ✂️ 即時裁切預覽：")
                st.image(final_output, width=110)
                
                buffered = io.BytesIO()
                final_output.save(buffered, format="PNG")
                img_b64 = "data:image/png;base64," + base64.b64encode(buffered.getvalue()).decode()
                
                if st.button("💖 確定套用裁切圖片為頭像", type="primary", use_container_width=True):
                    if st.session_state.logged_in:
                        update_user_avatar(st.session_state.username, "img", img_b64)
                        st.session_state.avatar_type = "img"
                        st.session_state.avatar_data = img_b64
                        st.toast("🎉 頭像更新成功！", icon="✨")
                        st.rerun()
        except Exception as e:
            st.error("⚠️ 圖片解析失敗，請試試其他圖片檔喔！")

# 7. 彈出式修改記帳小視窗
@st.dialog("✏️ 修改記帳紀錄")
def edit_expense_dialog(rec, rec_id, prefix):
    unique_key = f"dlg_{prefix}_{rec_id}"
    
    def on_change_edit():
        st.session_state.show_popup_type = "switching"

    edit_date = st.date_input(
        "修改日期", 
        datetime.strptime(rec["date"], "%Y-%m-%d"), 
        min_value=date(1995, 1, 1),
        max_value=date.today(),
        key=f"d_{unique_key}", 
        on_change=on_change_edit
    )
    edit_item = st.text_input("修改項目名稱", value=rec["item"], key=f"i_{unique_key}")
    edit_category = st.selectbox("修改分類", CATEGORIES, index=CATEGORIES.index(rec["category"]), key=f"cat_{unique_key}")
    
    edit_note = st.text_input(
        "修改備註", 
        value=rec.get("note", "") if rec.get("note") else "", 
        placeholder="請輸入備註 (選填)...", 
        key=f"n_{unique_key}"
    )

    edit_currency = st.selectbox(
        "修改幣別", 
        list(CURRENCY_MAP.keys()), 
        index=list(CURRENCY_MAP.keys()).index(rec["currency"]), 
        key=f"c_{unique_key}",
        on_change=on_change_edit
    )

    currency_code = CURRENCY_MAP[edit_currency]["code"]
    dynamic_rate_key = f"rate_{unique_key}_{currency_code}_{edit_date.strftime('%Y%m%d')}"

    current_calc_rate = fetch_rate_by_date(edit_currency, edit_date)

    edit_amount_str = st.text_input(
        "修改金額", 
        value=str(rec["original_amount"]), 
        placeholder="請輸入金額...", 
        key=f"a_{unique_key}"
    )
    
    rate_val_input = st.number_input(
        "使用匯率 (可手動調整)", 
        value=float(current_calc_rate), 
        format="%.4f", 
        key=dynamic_rate_key
    )

    st.markdown("---")
    btn_col1, btn_col2 = st.columns(2)
    with btn_col1:
        if st.button("💾 儲存變更", key=f"save_{unique_key}", use_container_width=True, type="primary"):
            try:
                edit_amount_val = float(edit_amount_str) if edit_amount_str.strip() else None
            except ValueError:
                edit_amount_val = None

            if not edit_item.strip():
                st.warning("⚠️ 項目名稱不可空白！")
            elif edit_amount_val is None or edit_amount_val <= 0:
                st.warning("⚠️ 請輸入有效的數字金額！")
            else:
                amount_twd_val = int(round(edit_amount_val * rate_val_input))
                formatted_orig = str(int(edit_amount_val)) if edit_amount_val.is_integer() else str(edit_amount_val)
                
                if st.session_state.logged_in:
                    update_expense_db(
                        expense_id=rec_id,
                        date_str=edit_date.strftime("%Y-%m-%d"),
                        year_num=edit_date.year,
                        month_str=edit_date.strftime("%Y-%m"),
                        item=edit_item.strip(),
                        category=edit_category,
                        orig_amt=formatted_orig,
                        currency=edit_currency,
                        rate=round(rate_val_input, 4),
                        amt_twd=amount_twd_val,
                        note=edit_note.strip()
                    )
                else:
                    st.session_state.guest_expenses[rec_id] = {
                        "date": edit_date.strftime("%Y-%m-%d"),
                        "year": edit_date.year,
                        "month": edit_date.strftime("%Y-%m"),
                        "item": edit_item.strip(),
                        "category": edit_category,
                        "original_amount": formatted_orig,
                        "currency": edit_currency,
                        "rate_used": round(rate_val_input, 4),
                        "amount_twd": amount_twd_val,
                        "is_favorite": rec.get("is_favorite", 0),
                        "note": edit_note.strip()
                    }
                st.session_state.show_popup_type = "updated"
                st.rerun()

    with btn_col2:
        if st.button("🗑️ 刪除紀錄", key=f"del_{unique_key}", use_container_width=True):
            if st.session_state.logged_in:
                delete_expense_db(rec_id)
            else:
                st.session_state.guest_expenses.pop(rec_id)
            st.toast("🗑️ 已成功刪除該筆資料！", icon="✨")
            st.rerun()

# 8. 通用圖表繪製函式（圖表頂部邊距留白 t=70，確保文字完整）
def render_custom_chart(df_data, x_col, y_col, chart_type, key_name, is_month_x=False):
    chart_df = df_data.copy()
    
    if is_month_x and x_col == 'month':
        chart_df['month_label'] = chart_df['month'].apply(lambda x: f"{int(x.split('-')[1])}月" if '-' in str(x) else str(x))
        x_target = 'month_label'
    else:
        x_target = x_col

    if chart_type in ["🥧 圓餅圖 (Pie)", "🍩 環形圖 (Donut)"]:
        hole_val = 0.45 if chart_type == "🍩 環形圖 (Donut)" else 0
        fig = px.pie(chart_df, values=y_col, names=x_target, hole=hole_val, color_discrete_sequence=MACARON_COLORS)
        fig.update_traces(textposition='auto', textinfo='percent+label')
    elif chart_type == "📊 柱狀圖 (Bar)":
        fig = px.bar(chart_df, x=x_target, y=y_col, color=x_target, color_discrete_sequence=MACARON_COLORS, text_auto=',d')
        fig.update_traces(marker_cornerradius=12, textposition='outside')
        fig.update_layout(showlegend=False, xaxis_title="", yaxis_title="金額 (NT$)")
    elif chart_type == "📈 折線圖 (Line)":
        fig = px.line(chart_df, x=x_target, y=y_col, markers=True, color_discrete_sequence=['#ff6b81'], text=y_col)
        fig.update_traces(
            line=dict(width=3), 
            marker=dict(size=10),
            texttemplate='NT$ %{y:,.0f}',
            textposition='top center'
        )
        fig.update_layout(xaxis_title="", yaxis_title="金額 (NT$)")

    fig.update_xaxes(type='category')
    
    if chart_type in ["📊 柱狀圖 (Bar)", "📈 折線圖 (Line)"] and not chart_df.empty and y_col in chart_df.columns:
        max_y = chart_df[y_col].max()
        fig.update_yaxes(range=[0, max_y * 1.25], tickformat=',d', nticks=6)

    fig.update_layout(
        title="",
        margin=dict(t=70, b=30, l=30, r=30),
        paper_bgcolor='rgba(0,0,0,0)',
        plot_bgcolor='rgba(0,0,0,0)',
        font=dict(family="M PLUS Rounded 1c, Noto Sans TC, sans-serif", size=14, color="#5c4b51")
    )
    st.plotly_chart(fig, use_container_width=True, key=key_name)

# 9. 側邊欄：登入、註冊與上傳頭像
with st.sidebar:
    st.title("🌸 帳號管理")
    
    if not st.session_state.logged_in:
        auth_mode = st.radio("選擇操作", ["🔑 登入", "✨ 註冊新帳號"])
        
        if auth_mode == "🔑 登入":
            st.subheader("使用者登入")
            login_user = st.text_input("帳號", key="login_user")
            login_pwd = st.text_input("密碼", type="password", key="login_pwd")
            if st.button("💖 登入系統", use_container_width=True):
                is_valid, avt_type, avt_data = authenticate_user(login_user, login_pwd)
                if is_valid:
                    st.session_state.logged_in = True
                    st.session_state.username = login_user
                    st.session_state.avatar_type = avt_type
                    st.session_state.avatar_data = avt_data
                    st.toast(f"🎉 歡迎回來，{login_user}！", icon="🌸")
                    st.rerun()
                else:
                    st.error("⚠️ 帳號或密碼錯誤喔！")
                    
        else:
            st.subheader("建立新帳號")
            reg_user = st.text_input("設定帳號", key="reg_user")
            reg_pwd = st.text_input("設定密碼", type="password", key="reg_pwd")
            if st.button("🌸 註冊帳號", use_container_width=True):
                if not reg_user.strip() or not reg_pwd.strip():
                    st.warning("⚠️ 帳號與密碼不可空白！")
                elif register_user(reg_user.strip(), reg_pwd.strip()):
                    st.success("🎉 註冊成功！頭像預設為帳號首字，請登入體驗喔！")
                else:
                    st.warning("⚠️ 該名稱已有人使用")
    else:
        if st.session_state.avatar_type == "img" and st.session_state.avatar_data:
            avatar_html = f'<img src="{st.session_state.avatar_data}" class="avatar-circle-img">'
        else:
            first_char = st.session_state.avatar_data if st.session_state.avatar_data else st.session_state.username[0].upper()
            avatar_html = f'<div class="avatar-circle-char">{first_char}</div>'

        st.markdown(f"""
        <div class="sidebar-user-card">
            {avatar_html}
            <div style="font-size: 0.85rem; color: #8b7d83; font-weight: 700;">目前登入身分</div>
            <h3>{st.session_state.username}</h3>
        </div>
        """, unsafe_allow_html=True)

        if st.button("🖼️ 選擇並裁切照片頭像", use_container_width=True):
            avatar_upload_dialog()

        if st.session_state.avatar_type == "img":
            if st.button("🔄 恢復預設首字頭像", use_container_width=True):
                default_char = st.session_state.username[0].upper()
                update_user_avatar(st.session_state.username, "char", default_char)
                st.session_state.avatar_type = "char"
                st.session_state.avatar_data = default_char
                st.toast("🎉 已恢復為首字文字頭像！", icon="✨")
                st.rerun()

        if st.button("🚪 安全登出", use_container_width=True):
            st.session_state.logged_in = False
            st.session_state.username = ""
            st.session_state.avatar_type = "char"
            st.session_state.avatar_data = "🌸"
            st.session_state.confirm_delete_account = False
            st.toast("👋 已成功登出！", icon="✨")
            st.rerun()
            
        st.markdown("---")
        
        if not st.session_state.confirm_delete_account:
            if st.button("🚨 註銷帳號", use_container_width=True):
                st.session_state.confirm_delete_account = True
                st.rerun()
        else:
            st.error("⚠️ 確定要註銷帳號嗎？所有記帳資料將永遠清除無法恢復！")
            c_col1, c_col2 = st.columns(2)
            with c_col1:
                if st.button("❌ 取消", use_container_width=True):
                    st.session_state.confirm_delete_account = False
                    st.rerun()
            with c_col2:
                if st.button("💣 確定註銷", use_container_width=True):
                    delete_user_account(st.session_state.username)
                    st.session_state.logged_in = False
                    st.session_state.username = ""
                    st.session_state.confirm_delete_account = False
                    st.toast("🗑️ 帳號及相關資料已成功註銷！", icon="✨")
                    st.rerun()

# 10. 主頁面 Header
st.markdown("""
<div class="cute-header">
    <h1>🌸生活記帳本🌸</h1>
    <p>輕鬆紀錄每筆生活消費✨</p>
</div>
""", unsafe_allow_html=True)

# 11. 資料同步提醒
if st.session_state.logged_in and len(st.session_state.guest_expenses) > 0:
    st.info(f"💡 系統偵測到你在未登入時共有 **{len(st.session_state.guest_expenses)}** 筆記帳紀錄！")
    if st.button("📥 一鍵將舊紀錄匯入我的帳號", use_container_width=True):
        for rec in st.session_state.guest_expenses:
            add_expense_db(
                username=st.session_state.username,
                date_str=rec["date"],
                year_num=rec["year"],
                month_str=rec["month"],
                item=rec["item"],
                category=rec["category"],
                orig_amt=rec["original_amount"],
                currency=rec["currency"],
                rate=rec["rate_used"],
                amt_twd=rec["amount_twd"],
                is_favorite=rec.get("is_favorite", 0),
                note=rec.get("note", "")
            )
        st.session_state.guest_expenses = []
        st.toast("🎉 舊紀錄已完美匯入你的個人帳號中！", icon="✨")
        st.rerun()

# 12. 載入當前資料
if st.session_state.logged_in:
    current_user = st.session_state.username
    df = load_user_expenses(current_user)
else:
    df = pd.DataFrame(st.session_state.guest_expenses)

# 13. 記帳與分析主介面
tab_input, tab_month, tab_year = st.tabs(["✏️ 記帳", "📅 月度消費趨勢", "🎆 年度消費趨勢"])

# --- 分頁 1：記帳主介面 ---
with tab_input:
    reset_key = st.session_state.form_reset_key

    # Widget Keys
    k_fav = f"fav_{reset_key}"
    k_item = f"item_{reset_key}"
    k_cat = f"cat_{reset_key}"
    k_curr = f"curr_{reset_key}"
    k_amt = f"amt_{reset_key}"
    k_note = f"note_{reset_key}"
    k_date = f"date_{reset_key}"

    # State 初始化
    if k_item not in st.session_state:
        st.session_state[k_item] = ""
    if k_cat not in st.session_state:
        st.session_state[k_cat] = None
    if k_amt not in st.session_state:
        st.session_state[k_amt] = ""
    if k_curr not in st.session_state:
        st.session_state[k_curr] = "NT 新台幣"
    if k_note not in st.session_state:
        st.session_state[k_note] = ""

    # 常用項目選項 map
    fav_options = ["不選擇常用（手動輸入）"]
    fav_items_map = {}
    if not df.empty and "is_favorite" in df.columns:
        fav_df = df[df["is_favorite"] == 1]
        for _, f_row in fav_df.iterrows():
            lbl = f"⭐ {f_row['item']} | {f_row['category']} | {f_row['currency']} {f_row['original_amount']}"
            if lbl not in fav_items_map:
                fav_options.append(lbl)
                fav_items_map[lbl] = f_row

    # 常用選單切換 callback
    def on_fav_change():
        selected = st.session_state.get(k_fav)
        if selected and selected != "不選擇常用（手動輸入）" and selected in fav_items_map:
            f = fav_items_map[selected]
            st.session_state[k_item] = str(f["item"])
            st.session_state[k_cat] = f["category"] if f["category"] in CATEGORIES else None
            st.session_state[k_amt] = str(f["original_amount"])
            st.session_state[k_curr] = f["currency"] if f["currency"] in CURRENCY_MAP else "NT 新台幣"
            st.session_state[k_note] = ""  # 按常用時保持備註空白，不自動帶入舊備註
        else:
            st.session_state[k_item] = ""
            st.session_state[k_cat] = None
            st.session_state[k_amt] = ""
            st.session_state[k_curr] = "NT 新台幣"
            st.session_state[k_note] = ""

    def trigger_rerun_pop():
        st.session_state.show_popup_type = "switching"

    # 第一行：日期與常用選單
    top_col1, top_col2 = st.columns(2)
    with top_col1:
        input_date = st.date_input(
            "選擇日期 (年/月/日)", 
            datetime.now().date(), 
            min_value=date(1995, 1, 1),
            max_value=date.today(),
            key=k_date,
            on_change=trigger_rerun_pop
        )
    with top_col2:
        st.selectbox("常用選單", fav_options, key=k_fav, on_change=on_fav_change)

    # 第二行：消費項目與消費分類
    f_col1, f_col2 = st.columns(2)
    with f_col1:
        st.text_input("消費項目", placeholder="例如：韓國炸雞 🍗", key=k_item)
    with f_col2:
        st.selectbox("消費分類", CATEGORIES, index=None, placeholder="請選擇分類...", key=k_cat)

    # 第三行：幣別與花費金額
    amt_col1, amt_col2 = st.columns(2)
    with amt_col1:
        st.selectbox("選擇幣別", list(CURRENCY_MAP.keys()), key=k_curr, on_change=trigger_rerun_pop)
    with amt_col2:
        st.text_input("花費金額", placeholder="請輸入金額...", key=k_amt)

    # 第四行：備註欄位
    st.text_input("備註 (選填)", placeholder="例如：朋友聚餐分帳...", key=k_note)

    # 讀取即時輸入狀態
    curr_item = st.session_state.get(k_item, "")
    curr_cat = st.session_state.get(k_cat, None)
    curr_curr = st.session_state.get(k_curr, "NT 新台幣")
    curr_amt_str = st.session_state.get(k_amt, "")
    curr_note = st.session_state.get(k_note, "")

    # 計算牌告匯率與折算預覽
    current_live_rate = fetch_rate_by_date(curr_curr, input_date)
    try:
        input_amount = float(curr_amt_str.strip()) if curr_amt_str and curr_amt_str.strip() else 0.0
    except ValueError:
        input_amount = 0.0

    calc_twd = int(round(input_amount * current_live_rate)) if input_amount > 0 else 0
    curr_code_display = curr_curr.split()[1] if len(curr_curr.split()) > 1 else "TWD"
    st.info(f"💡 當前即時牌告匯率：**1 {curr_code_display} ≈ {current_live_rate} TWD** | 折算預覽：**🌸 折算後為 NT$ {calc_twd:,}** (按下Enter即可預覽折算金額!)")

    # 儲存按鈕處理
    if st.button("💖 儲存這筆紀錄", use_container_width=True, type="primary", key=f"submit_{reset_key}"):
        if not curr_item or not curr_item.strip():
            st.warning("⚠️ 請填寫消費項目名稱喔！")
        elif curr_cat is None:
            st.warning("⚠️ 請選擇消費分類喔！")
        elif input_amount <= 0:
            st.warning("⚠️ 請在上方的「花費金額」欄位輸入有效金額喔！")
        else:
            date_str = input_date.strftime("%Y-%m-%d")
            year_num = input_date.year
            month_str = input_date.strftime("%Y-%m")
            amount_twd_val = calc_twd
            formatted_orig = str(int(input_amount)) if input_amount.is_integer() else str(input_amount)
            
            if st.session_state.logged_in:
                add_expense_db(
                    username=st.session_state.username,
                    date_str=date_str,
                    year_num=year_num,
                    month_str=month_str,
                    item=curr_item.strip(),
                    category=curr_cat,
                    orig_amt=formatted_orig,
                    currency=curr_curr,
                    rate=round(current_live_rate, 4),
                    amt_twd=amount_twd_val,
                    note=curr_note.strip()
                )
            else:
                new_rec = {
                    "date": date_str,
                    "year": year_num,
                    "month": month_str,
                    "item": curr_item.strip(),
                    "category": curr_cat,
                    "original_amount": formatted_orig,
                    "currency": curr_curr,
                    "rate_used": round(current_live_rate, 4),
                    "amount_twd": amount_twd_val,
                    "is_favorite": 0,
                    "note": curr_note.strip()
                }
                st.session_state.guest_expenses.append(new_rec)

            st.session_state.form_reset_key += 1
            st.session_state.show_popup_type = "saved"
            st.rerun()

    st.markdown("---")
    st.subheader("📜 最近的記帳明細")
    if not df.empty:
        filter_col1, filter_col2 = st.columns(2)
        with filter_col1:
            selected_cat_filter = st.selectbox("🔍 按分類查看：", ["✨ 全部分類"] + CATEGORIES, key="input_cat_filter")
        with filter_col2:
            sort_order = st.selectbox("⏳ 日期排序方式：", ["📅 最新日期在前", "📅 最舊日期在前"], key="input_sort_order")
        
        expenses_list = df.to_dict('records')
        if selected_cat_filter != "✨ 全部分類":
            expenses_list = [item for item in expenses_list if item["category"] == selected_cat_filter]
            
        is_asc = (sort_order == "📅 最舊日期在前")
        expenses_list.sort(key=lambda x: x["date"], reverse=not is_asc)
        
        display_list = expenses_list[:10]
        prefix = "db" if st.session_state.logged_in else "guest"
        
        for idx, rec in enumerate(display_list):
            rec_id = rec.get("id", idx)
            is_fav = rec.get("is_favorite", 0)
            star_icon = "⭐" if is_fav == 1 else "☆"
            
            note_str = f" | 📝 {rec['note']}" if rec.get('note') else ""
            left_info = f"🗓️ {rec['date']} | {rec['category']} | {rec['item']}{note_str}"
            
            # 🌟 保留此處「🌸 折算後為 NT$」
            if rec['currency'] == "NT 新台幣":
                right_amount = f"🌸 折算後為 NT$ {rec['amount_twd']:,}"
            else:
                right_amount = f"{rec['currency']} {rec['original_amount']} (🌸 折算後為 NT$ {rec['amount_twd']:,}，匯率 {rec['rate_used']:.4f})"
            
            list_col1, list_col2, list_col3, list_col4 = st.columns([4, 5, 1, 1])
            with list_col1:
                st.markdown(f'<div class="item-left-align">{left_info}</div>', unsafe_allow_html=True)
            with list_col2:
                st.markdown(f'<div class="amount-right-align">{right_amount}</div>', unsafe_allow_html=True)
            with list_col3:
                if st.button("✏️ 修改", key=f"btn_edit_{prefix}_{rec_id}", use_container_width=True):
                    edit_expense_dialog(rec, rec_id, prefix)
            with list_col4:
                if st.button(star_icon, key=f"btn_fav_{prefix}_{rec_id}", use_container_width=True, help="點擊設定/取消為常用項目"):
                    if st.session_state.logged_in:
                        new_st = toggle_favorite_db(rec_id, is_fav)
                    else:
                        st.session_state.guest_expenses[rec_id]["is_favorite"] = 0 if is_fav == 1 else 1
                        new_st = st.session_state.guest_expenses[rec_id]["is_favorite"]
                    
                    msg = "已設為常用項目！" if new_st == 1 else "已從常用項目移除！"
                    st.toast(f"✨ {msg}", icon="⭐")
                    st.rerun()

            st.markdown("<hr style='margin: 0.2rem 0; border: none; border-top: 1px dashed #ffd1dc;'>", unsafe_allow_html=True)
            
        if len(expenses_list) > 10:
            st.caption("💡 僅顯示最近 10 筆紀錄，完整的歷史資料請切換至「月度」或「年度」分頁查看喔！")
    else:
        st.info("目前還沒有任何記帳紀錄喔，快新增第一筆吧！")

# --- 分頁 2：月度累積與趨勢 ---
with tab_month:
    if not df.empty:
        now_month_str = datetime.now().strftime("%Y-%m")
        all_months = sorted(df['month'].unique(), reverse=True)
        if now_month_str not in all_months:
            all_months.insert(0, now_month_str)
        default_m_idx = all_months.index(now_month_str)
        
        m_sel_col1, m_sel_col2 = st.columns(2)
        with m_sel_col1:
            selected_month = st.selectbox("🗓️ 請選擇月份：", all_months, index=default_m_idx, key="select_month_key")
        with m_sel_col2:
            m_chart_type = st.selectbox("📊 選擇圖表類型：", ["🍩 環形圖 (Donut)", "🥧 圓餅圖 (Pie)", "📊 柱狀圖 (Bar)"], index=0, key="m_chart_type")
        
        m_df = df[df['month'] == selected_month]
        month_total = m_df['amount_twd'].sum() if not m_df.empty else 0
        
        # 🌟 還原月度累積總消費卡片格式
        st.markdown(f'<div class="stat-card"><div class="stat-label">🌸 {selected_month} 當月累積總消費 (已折算台幣)</div><div class="stat-value">NT$ {month_total:,}</div></div>', unsafe_allow_html=True)
        
        if not m_df.empty:
            cat_m = m_df.groupby('category')['amount_twd'].sum().reset_index()
            render_custom_chart(cat_m, 'category', 'amount_twd', m_chart_type, "fig_month")

            st.markdown("---")
            st.markdown("##### 📈 本月消費列表明細")
            
            m_col1, m_col2 = st.columns(2)
            with m_col1:
                m_cat_filter = st.selectbox("🔍 按分類查看：", ["✨ 全部分類"] + CATEGORIES, key="m_cat")
            with m_col2:
                m_sort_order = st.selectbox("⏳ 月度列表排序方式：", ["📅 最新日期在前", "📅 最舊日期在前"], key="m_sort")
            
            m_display = m_df.copy()
            if m_cat_filter != "✨ 全部分類":
                m_display = m_display[m_display["category"] == m_cat_filter]
                
            m_is_asc = (m_sort_order == "📅 最舊日期在前")
            m_display = m_display.sort_values(by="date", ascending=m_is_asc)
            
            m_expenses = m_display.to_dict('records')
            prefix_m = "m_db" if st.session_state.logged_in else "m_guest"
            
            for idx, rec in enumerate(m_expenses):
                rec_id = rec.get("id", idx)
                item_note = f" (📝 {rec['note']})" if rec.get("note") else ""
                
                # 🌟 還原月度列表格式
                c_btn, c_date, c_item, c_cat, c_orig, c_twd = st.columns([1, 2, 3, 2, 2.5, 2])
                with c_btn:
                    if st.button("✏️", key=f"btn_m_edit_{prefix_m}_{rec_id}_{idx}"):
                        edit_expense_dialog(rec, rec_id, prefix_m)
                with c_date:
                    st.write(f"🗓️ {rec['date']}")
                with c_item:
                    st.write(f"**{rec['item']}**{item_note}")
                with c_cat:
                    st.write(rec['category'])
                with c_orig:
                    st.write(f"{rec['currency']} {rec['original_amount']}")
                with c_twd:
                    st.write(f"**NT$ {rec['amount_twd']:,}**")
                st.markdown("<hr style='margin: 0.2rem 0; border: none; border-top: 1px dashed #ffd1dc;'>", unsafe_allow_html=True)
        else:
            st.info(f"這個月 ({selected_month}) 還沒有任何記帳紀錄喔！")
    else:
        st.info("目前還沒有資料可以統計月度消費趨勢喔！")

# --- 分頁 3：年度累積與趨勢 ---
with tab_year:
    if not df.empty:
        now_year_num = datetime.now().year
        all_years = sorted(df['year'].unique(), reverse=True)
        if now_year_num not in all_years:
            all_years.insert(0, now_year_num)
        default_y_idx = all_years.index(now_year_num)
        
        y_sel_col1, y_sel_col2, y_sel_col3 = st.columns(3)
        with y_sel_col1:
            selected_year = st.selectbox("🎆 請選擇年份：", all_years, index=default_y_idx, key="select_year_key")
        with y_sel_col2:
            y_view_mode = st.selectbox("🔍 選擇分析維度：", ["🏷️ 各類別消費狀況", "🗓️ 各月份消費情況"], index=0, key="y_view_mode")
        with y_sel_col3:
            if y_view_mode == "🗓️ 各月份消費情況":
                y_chart_type = st.selectbox("📊 選擇圖表類型：", ["📈 折線圖 (Line)", "📊 柱狀圖 (Bar)"], index=0, key="y_chart_type")
            else:
                y_chart_type = st.selectbox("📊 選擇圖表類型：", ["🍩 環形圖 (Donut)", "🥧 圓餅圖 (Pie)", "📊 柱狀圖 (Bar)"], index=0, key="y_chart_type")
        
        y_df = df[df['year'] == selected_year]
        year_total = y_df['amount_twd'].sum() if not y_df.empty else 0
        
        # 🌟 還原年度累積總消費卡片格式
        st.markdown(f'<div class="stat-card" style="border-color: #a8e6cf;"><div class="stat-label" style="color: #4e8d7c;">🌟 {selected_year} 全年累積總消費 (已折算台幣)</div><div class="stat-value" style="color: #42b883;">NT$ {year_total:,}</div></div>', unsafe_allow_html=True)
        
        if not y_df.empty:
            if y_view_mode == "🗓️ 各月份消費情況":
                data_y = y_df.groupby('month')['amount_twd'].sum().reset_index().sort_values(by='month')
                render_custom_chart(data_y, 'month', 'amount_twd', y_chart_type, "fig_year_m", is_month_x=True)
            else:
                data_y = y_df.groupby('category')['amount_twd'].sum().reset_index()
                render_custom_chart(data_y, 'category', 'amount_twd', y_chart_type, "fig_year_c")

            st.markdown("---")
            st.markdown("##### 📈 全年消費列表明細")
            
            y_col1, y_col2 = st.columns(2)
            with y_col1:
                y_cat_filter = st.selectbox("🔍 按分類查看：", ["✨ 全部分類"] + CATEGORIES, key="y_cat")
            with y_col2:
                y_sort_order = st.selectbox("⏳ 年度列表排序方式：", ["📅 最新日期在前", "📅 最舊日期在前"], key="y_sort")
            
            y_display = y_df.copy()
            if y_cat_filter != "✨ 全部分類":
                y_display = y_display[y_display["category"] == y_cat_filter]
                
            y_is_asc = (y_sort_order == "📅 最舊日期在前")
            y_display = y_display.sort_values(by="date", ascending=y_is_asc)
            
            y_expenses = y_display.to_dict('records')
            prefix_y = "y_db" if st.session_state.logged_in else "y_guest"
            
            for idx, rec in enumerate(y_expenses):
                rec_id = rec.get("id", idx)
                item_note = f" (📝 {rec['note']})" if rec.get("note") else ""
                
                # 🌟 還原年度列表格式
                c_btn, c_date, c_item, c_cat, c_orig, c_twd = st.columns([1, 2, 3, 2, 2.5, 2])
                with c_btn:
                    if st.button("✏️", key=f"btn_y_edit_{prefix_y}_{rec_id}_{idx}"):
                        edit_expense_dialog(rec, rec_id, prefix_y)
                with c_date:
                    st.write(f"🗓️ {rec['date']}")
                with c_item:
                    st.write(f"**{rec['item']}**{item_note}")
                with c_cat:
                    st.write(rec['category'])
                with c_orig:
                    st.write(f"{rec['currency']} {rec['original_amount']}")
                with c_twd:
                    st.write(f"**NT$ {rec['amount_twd']:,}**")
                st.markdown("<hr style='margin: 0.2rem 0; border: none; border-top: 1px dashed #ffd1dc;'>", unsafe_allow_html=True)
        else:
            st.info(f"年份 ({selected_year}) 還沒有任何記帳紀錄喔！")
    else:
        st.info("目前還沒有資料可以統計年度消費趨勢喔！")
