import base64
import datetime
import hashlib
import json
import os
import random
import string
import psycopg2
import smtplib
import subprocess
import tempfile
import mimetypes
import altair as alt
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

def get_base64_video(uploaded_file):
    """將 Streamlit 上傳的影片檔轉換為 Base64 字串"""
    bytes_data = uploaded_file.getvalue()
    return base64.b64encode(bytes_data).decode('utf-8')

def get_mime_type(filename):
    """取得影片檔的 MIME 類型"""
    mime_type, _ = mimetypes.guess_type(filename)
    return mime_type if mime_type else "video/mp4"

# -------------------- 【頁面與全站 CSS 配置】 --------------------
st.set_page_config(page_title="HURDEX - 跨欄運動技術與防護分析系統", layout="wide", initial_sidebar_state="expanded")

st.markdown("""
    <style>
    .stApp { background-color: #f8fafc; color: #0f172a; }
    section[data-testid="stSidebar"] { background-color: #f1f5f9 !important; border-right: 1px solid #e2e8f0; }
    h1, h2, h3, h4, h5, h6 { color: #0f172a !important; font-weight: 700 !important; }
    p, label, span, div { color: #334155; }
    .stButton>button {
        background-color: #2563eb !important; color: #ffffff !important;
        border-radius: 6px !important; border: none !important;
        font-weight: 600 !important; box-shadow: 0 1px 2px rgba(0, 0, 0, 0.05);
        transition: all 0.2s ease;
    }
    .stButton>button p, .stButton>button span, .stButton>button div { color: #ffffff !important; font-weight: 600 !important; }
    .stButton>button:hover { background-color: #1d4ed8 !important; }
    .stTextInput>div>div>input, .stSelectbox>div>div>div {
        background-color: #ffffff !important; color: #0f172a !important;
        border: 1px solid #cbd5e1 !important; border-radius: 6px !important;
    }
    div[data-testid="stExpander"] { background-color: #ffffff !important; border: 1px solid #e2e8f0 !important; border-radius: 8px !important; }
    div[data-testid="stTextArea"]:has(textarea[aria-label*="body_map_img_b64"]),
    div[data-testid="stTextArea"]:has(textarea[aria-label*="angle_img_b64"]),
    div[data-testid="stTextArea"]:has(textarea[aria-label*="angle_data_json"]),
    div[data-testid="stTextArea"]:has(textarea[aria-label*="timer_data_json"]),
    div[data-testid="stTextArea"]:has(textarea[aria-label*="ghost_img_b64"]),
    div[data-testid="stTextArea"]:has(textarea[aria-label*="dual_img_b64"]),
    div[data-testid="stTextArea"]:has(textarea[aria-label*="dual_data_json"]) {
        position: absolute !important; left: -9999px !important; top: -9999px !important;
        width: 1px !important; height: 1px !important; overflow: hidden !important;
    }
    </style>
""", unsafe_allow_html=True)

# -------------------- 【雲端資料庫與發信驗證配置】 --------------------
COACH_SECRET_CODE = "09290808"
TRAINER_SECRET_CODE = "trainer2026"

SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
SENDER_EMAIL = "your_system_email@gmail.com"
SENDER_PASSWORD = "your_app_password"

def get_db_connection():
    return psycopg2.connect(st.secrets["postgres"]["url"])

def generate_random_team_code(length=6):
    prefix = "HRD-"
    rand_str = ''.join(random.choices(string.ascii_uppercase + string.digits, k=length-2))
    return f"{prefix}{rand_str}"

def init_db():
    conn = get_db_connection()
    c = conn.cursor()
    # 1. 使用者資料表
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL,
            team_code TEXT NOT NULL DEFAULT 'DEFAULT_TEAM',
            email TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT ''
        )
    ''')
    # 2. 密碼重設 Token
    c.execute('''
        CREATE TABLE IF NOT EXISTS reset_tokens (
            username TEXT PRIMARY KEY,
            otp_code TEXT NOT NULL,
            expires_at TEXT NOT NULL
        )
    ''')
    # 3. 動作分析紀錄表
    c.execute('''
        CREATE TABLE IF NOT EXISTS records (
            id SERIAL PRIMARY KEY,
            username TEXT NOT NULL,
            team_code TEXT NOT NULL DEFAULT 'DEFAULT_TEAM',
            module TEXT NOT NULL,
            title TEXT NOT NULL,
            details TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            FOREIGN KEY (username) REFERENCES users (username)
        )
    ''')
    # 4. 訓練日誌表
    c.execute('''
        CREATE TABLE IF NOT EXISTS training_plans (
            id SERIAL PRIMARY KEY,
            username TEXT NOT NULL,
            team_code TEXT NOT NULL DEFAULT 'DEFAULT_TEAM',
            plan_date TEXT NOT NULL,
            category TEXT NOT NULL,
            content TEXT NOT NULL,
            intensity TEXT NOT NULL,
            notes TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    ''')
    # 5. 比賽行事曆表
    c.execute('''
        CREATE TABLE IF NOT EXISTS competitions (
            id SERIAL PRIMARY KEY,
            username TEXT NOT NULL,
            team_code TEXT NOT NULL DEFAULT 'DEFAULT_TEAM',
            comp_name TEXT NOT NULL,
            comp_date TEXT NOT NULL,
            end_date TEXT DEFAULT '',
            location TEXT DEFAULT '',
            notes TEXT DEFAULT '',
            created_at TEXT NOT NULL
        )
    ''')
    # 6. 運動防護與傷勢報告表
    c.execute('''
        CREATE TABLE IF NOT EXISTS injury_feedbacks (
            id SERIAL PRIMARY KEY,
            athlete_username TEXT NOT NULL,
            trainer_username TEXT NOT NULL,
            team_code TEXT NOT NULL,
            feedback_date TEXT NOT NULL,
            body_part TEXT NOT NULL,
            status_tag TEXT NOT NULL,
            treatment_advice TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    ''')

    # 【資料庫自動修復移轉 Migration】
    migration_sqls = [
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS team_code TEXT NOT NULL DEFAULT 'DEFAULT_TEAM'",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS email TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS created_at TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE records ADD COLUMN IF NOT EXISTS team_code TEXT NOT NULL DEFAULT 'DEFAULT_TEAM'",
        "ALTER TABLE training_plans ADD COLUMN IF NOT EXISTS team_code TEXT NOT NULL DEFAULT 'DEFAULT_TEAM'",
        "ALTER TABLE competitions ADD COLUMN IF NOT EXISTS team_code TEXT NOT NULL DEFAULT 'DEFAULT_TEAM'",
        "ALTER TABLE competitions ADD COLUMN IF NOT EXISTS end_date TEXT DEFAULT ''"
    ]
    for q in migration_sqls:
        try:
            c.execute(q)
        except Exception:
            pass

    conn.commit()
    c.close()
    conn.close()

# -------------------- 【帳號與發訊認證 Logic】 --------------------
def hash_password(password):
    return hashlib.sha256(password.encode('utf-8')).hexdigest()
    
def delete_user_account(username):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("DELETE FROM users WHERE username = %s", (username,))
    c.execute("DELETE FROM records WHERE username = %s", (username,))
    c.execute("DELETE FROM training_plans WHERE username = %s", (username,))
    conn.commit()
    c.close()
    conn.close()

def register_user(username, password, role, team_code, email):
    username = username.strip()
    team_code = team_code.strip().upper()
    if not username: return False, "使用者名稱不可為空"
    if not team_code: return False, "團隊碼不可為空"

    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT username FROM users WHERE username = %s", (username,))
    if c.fetchone():
        c.close(); conn.close()
        return False, "該使用者名稱已被註冊"

    try:
        pw_hash = hash_password(password)
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        c.execute("INSERT INTO users (username, password_hash, role, team_code, email, created_at) VALUES (%s, %s, %s, %s, %s, %s)",
                  (username, pw_hash, role, team_code, email, now_str))
        conn.commit()
        c.close(); conn.close()
        return True, f"註冊成功！已加入團隊：{team_code}"
    except Exception as e:
        c.close(); conn.close()
        return False, f"註冊失敗: {str(e)}"

def check_login(username, password):
    conn = get_db_connection()
    c = conn.cursor()
    pw_hash = hash_password(password)
    c.execute("SELECT username, role, team_code, email FROM users WHERE username = %s AND password_hash = %s", (username, pw_hash))
    user = c.fetchone()
    c.close(); conn.close()
    if user:
        return True, {"username": user[0], "role": user[1], "team_code": user[2], "email": user[3]}
    return False, "帳號或密碼錯誤"

def send_reset_email(to_email, username, otp_code):
    try:
        msg = MIMEMultipart()
        msg['From'] = SENDER_EMAIL
        msg['To'] = to_email
        msg['Subject'] = "【HURDEX】重設密碼驗證碼 Password Reset Code"
        body = f"您好 {username}：\n您的重設密碼 6 位數驗證碼為：{otp_code} (10分鐘內有效)。\nHURDEX"
        msg.attach(MIMEText(body, 'plain', 'utf-8'))
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        server.send_message(msg)
        server.quit()
        return True, f"驗證碼已寄送至信箱 {to_email}"
    except Exception as e:
        return False, f"信件發送失敗: {str(e)}"

def request_password_reset(username):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT email FROM users WHERE username = %s", (username,))
    row = c.fetchone()
    if not row:
        c.close(); conn.close()
        return False, "找不到該使用者名稱"
    user_email = row[0]
    otp_code = f"{random.randint(100000, 999999)}"
    expires_at = (datetime.datetime.now() + datetime.timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S")

    c.execute("""
        INSERT INTO reset_tokens (username, otp_code, expires_at) VALUES (%s, %s, %s)
        ON CONFLICT (username) DO UPDATE SET otp_code = EXCLUDED.otp_code, expires_at = EXCLUDED.expires_at
    """, (username, otp_code, expires_at))
    conn.commit()
    c.close(); conn.close()

    ok, msg = send_reset_email(user_email, username, otp_code)
    if not ok:
        return True, f"【測試模式】驗證碼為：{otp_code} (信箱：{user_email})"
    return True, msg

def verify_and_reset_password(username, otp_code, new_password):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT otp_code, expires_at FROM reset_tokens WHERE username = %s", (username,))
    row = c.fetchone()
    if not row:
        c.close(); conn.close()
        return False, "請先索取驗證碼"
    db_otp, expires_at_str = row
    if datetime.datetime.now() > datetime.datetime.strptime(expires_at_str, "%Y-%m-%d %H:%M:%S"):
        c.close(); conn.close()
        return False, "驗證碼已過期"
    if otp_code.strip() != db_otp:
        c.close(); conn.close()
        return False, "驗證碼錯誤"

    pw_hash = hash_password(new_password)
    c.execute("UPDATE users SET password_hash = %s WHERE username = %s", (pw_hash, username))
    c.execute("DELETE FROM reset_tokens WHERE username = %s", (username,))
    conn.commit()
    c.close(); conn.close()
    return True, "密碼重設成功，請登入"

def update_username(old_username, new_username):
    new_username = new_username.strip()
    if not new_username: return False, "帳號不可為空"
    if old_username == new_username: return False, "名稱未改變"
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT username FROM users WHERE username = %s", (new_username,))
    if c.fetchone():
        c.close(); conn.close()
        return False, "名稱已存在"
    try:
        c.execute("UPDATE users SET username = %s WHERE username = %s", (new_username, old_username))
        c.execute("UPDATE records SET username = %s WHERE username = %s", (new_username, old_username))
        c.execute("UPDATE training_plans SET username = %s WHERE username = %s", (new_username, old_username))
        c.execute("UPDATE reset_tokens SET username = %s WHERE username = %s", (new_username, old_username))
        c.execute("UPDATE competitions SET username = %s WHERE username = %s", (new_username, old_username))
        c.execute("UPDATE injury_feedbacks SET athlete_username = %s WHERE athlete_username = %s", (new_username, old_username))
        c.execute("UPDATE injury_feedbacks SET trainer_username = %s WHERE trainer_username = %s", (new_username, old_username))
        conn.commit()
        c.close(); conn.close()
        return True, "更新成功"
    except Exception as e:
        c.close(); conn.close()
        return False, str(e)

def update_password(username, old_password, new_password):
    if not old_password or not new_password: return False, "請輸入密碼"
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT username FROM users WHERE username = %s AND password_hash = %s", (username, hash_password(old_password)))
    if not c.fetchone():
        c.close(); conn.close()
        return False, "舊密碼錯誤"
    c.execute("UPDATE users SET password_hash = %s WHERE username = %s", (hash_password(new_password), username))
    conn.commit()
    c.close(); conn.close()
    return True, "密碼修改成功"

# -------------------- 【團隊資料調取輔助】 --------------------
def get_team_athletes(team_code):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT username FROM users WHERE team_code = %s AND role = 'player' ORDER BY username ASC", (team_code,))
    rows = c.fetchall()
    c.close(); conn.close()
    return [r[0] for r in rows]

# -------------------- 【資料庫操作 (Team Scoped)】 --------------------
def save_competition(username, team_code, comp_name, start_date, end_date=None, location="", notes=""):
    conn = get_db_connection()
    c = conn.cursor()
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if not end_date: end_date = start_date
    c.execute("INSERT INTO competitions (username, team_code, comp_name, comp_date, end_date, location, notes, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
              (username, team_code, comp_name, str(start_date), str(end_date), str(location), str(notes), now_str))
    conn.commit()
    c.close(); conn.close()

def get_competitions(team_code, username=None):
    conn = get_db_connection()
    c = conn.cursor()
    query = "SELECT id, username, comp_name, comp_date, end_date, location, notes, created_at FROM competitions WHERE team_code = %s"
    params = [team_code]
    if username:
        query += " AND username = %s"
        params.append(username)
    query += " ORDER BY comp_date ASC"
    c.execute(query, tuple(params))
    rows = c.fetchall()
    c.close(); conn.close()
    return rows

def delete_competition(comp_id):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("DELETE FROM competitions WHERE id = %s", (comp_id,))
    conn.commit()
    c.close(); conn.close()
    
def update_competition(comp_id, name, start_date, end_date, notes):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        UPDATE competitions 
        SET comp_name = %s, comp_date = %s, end_date = %s, notes = %s
        WHERE id = %s
    """, (name, str(start_date), str(end_date), notes, comp_id))
    conn.commit()
    cur.close()
    conn.close()
    
def save_record(*args, **kwargs):
    conn = get_db_connection()
    c = conn.cursor()
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if len(args) == 5:
        username, _, module, title, details_dict = args
    elif len(args) == 4:
        username, module, title, details_dict = args
    else:
        username = kwargs.get("username", args[0] if len(args) > 0 else "")
        module = kwargs.get("module", kwargs.get("category", args[1] if len(args) > 1 else ""))
        title = kwargs.get("title", args[2] if len(args) > 2 else "")
        details_dict = kwargs.get("details_dict", kwargs.get("details", kwargs.get("content", args[3] if len(args) > 3 else {})))

    if isinstance(details_dict, (dict, list)):
        details_json = json.dumps(details_dict, ensure_ascii=False)
    else:
        details_json = str(details_dict)

    c.execute(
        "INSERT INTO records (username, module, title, details, timestamp) VALUES (%s, %s, %s, %s, %s)",
        (username, module, title, details_json, now_str)
    )
    conn.commit()
    c.close()
    conn.close()

def get_user_records(*args, **kwargs):
    conn = get_db_connection()
    c = conn.cursor()
    
    username = kwargs.get("username", None)
    module = kwargs.get("module", kwargs.get("category", None))
    
    if username is None and len(args) > 0:
        username = args[0]
    if module is None and len(args) > 1:
        module = args[1]
        
    query = "SELECT id, username, module, title, details, timestamp FROM records"
    params = []
    where_clauses = []
    
    if username and str(username).strip() and str(username) != "None":
        where_clauses.append("username = %s")
        params.append(str(username))
    if module and str(module).strip() and str(module) != "None":
        where_clauses.append("module = %s")
        params.append(str(module))
        
    if where_clauses:
        query += " WHERE " + " AND ".join(where_clauses)
    query += " ORDER BY id DESC"
    
    c.execute(query, tuple(params))
    rows = c.fetchall()
    c.close()
    conn.close()
    return rows

get_records = get_user_records

def update_record_details(record_id, updated_details_dict):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("UPDATE records SET details = %s WHERE id = %s", (json.dumps(updated_details_dict, ensure_ascii=False), record_id))
    conn.commit()
    c.close(); conn.close()

def delete_record(record_id):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("DELETE FROM records WHERE id = %s", (record_id,))
    conn.commit()
    c.close(); conn.close()

def save_training_plan(username, team_code, plan_date, category, content, intensity, notes):
    conn = get_db_connection()
    c = conn.cursor()
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    c.execute("INSERT INTO training_plans (username, team_code, plan_date, category, content, intensity, notes, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
              (username, team_code, plan_date, category, content, intensity, notes, now_str))
    conn.commit()
    c.close(); conn.close()

def get_training_plans(*args, **kwargs):
    conn = get_db_connection()
    c = conn.cursor()
    
    username = kwargs.get("username", None)
    if username is None and len(args) > 0:
        if len(args) == 1:
            username = args[0]
        elif len(args) >= 2:
            username = args[1]
            
    query = "SELECT id, username, plan_date, category, content, intensity, notes, created_at FROM training_plans"
    params = []
    if username and str(username).strip() and str(username) != "None":
        query += " WHERE username = %s"
        params.append(str(username))
    query += " ORDER BY plan_date ASC, id ASC"
    
    c.execute(query, tuple(params))
    rows = c.fetchall()
    c.close()
    conn.close()
    return rows

def delete_training_plan(plan_id):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("DELETE FROM training_plans WHERE id = %s", (plan_id,))
    conn.commit()
    c.close(); conn.close()

def save_injury_feedback(athlete_username, trainer_username, team_code, feedback_date, body_part, status_tag, advice):
    conn = get_db_connection()
    c = conn.cursor()
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    c.execute("""
        INSERT INTO injury_feedbacks 
        (athlete_username, trainer_username, team_code, feedback_date, body_part, status_tag, treatment_advice, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    """, (athlete_username, trainer_username, team_code, str(feedback_date), body_part, status_tag, advice, now_str))
    conn.commit()
    c.close(); conn.close()

def get_injury_feedbacks(team_code, athlete_username=None):
    conn = get_db_connection()
    c = conn.cursor()
    query = "SELECT id, athlete_username, trainer_username, feedback_date, body_part, status_tag, treatment_advice, created_at FROM injury_feedbacks WHERE team_code = %s"
    params = [team_code]
    if athlete_username:
        query += " AND athlete_username = %s"; params.append(athlete_username)
    query += " ORDER BY feedback_date DESC, id DESC"
    c.execute(query, tuple(params))
    rows = c.fetchall()
    c.close(); conn.close()
    return rows

def delete_injury_feedback(fb_id):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("DELETE FROM injury_feedbacks WHERE id = %s", (fb_id,))
    conn.commit()
    c.close(); conn.close()

# -------------------- 【影片轉碼庫】 --------------------
def process_and_convert_video(uploaded_file):
    if uploaded_file is None: return None, None
    suffix = os.path.splitext(uploaded_file.name)[1]
    if not suffix: suffix = ".mp4"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp_in:
        tmp_in.write(uploaded_file.getvalue())
        in_path = tmp_in.name
    out_path = in_path + "_converted.mp4"

    cmd = ["ffmpeg", "-y", "-i", in_path, "-vcodec", "libx264", "-acodec", "aac", "-crf", "25", "-preset", "ultrafast", out_path]
    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        if os.path.exists(out_path):
            with open(out_path, "rb") as f: converted_bytes = f.read()
            if os.path.exists(in_path): os.remove(in_path)
            if os.path.exists(out_path): os.remove(out_path)
            return converted_bytes, "video/mp4"
    except Exception: pass
    if os.path.exists(in_path): os.remove(in_path)
    if os.path.exists(out_path): os.remove(out_path)
    return uploaded_file.getvalue(), uploaded_file.type or "video/mp4"

# -------------------- 【系統初始化】 --------------------
try: init_db()
except Exception as e: st.error(f"雲端資料庫初始化失敗: {e}")

if "user_info" not in st.session_state:
    st.session_state["user_info"] = None

# -------------------- 【側邊欄配置】 --------------------
st.sidebar.title("HURDEX 運動科技")
st.sidebar.markdown("---")

if st.session_state["user_info"] is not None:
    u = st.session_state["user_info"]
    role_name = {"player": "選手 Athlete", "coach": "教練 Coach", "trainer": "運動防護員 Trainer"}.get(u['role'], u['role'])
    st.sidebar.success(f"使用者：**{u['username']}**\n\n身分：**{role_name}**\n\n團隊碼：`{u['team_code']}`")
    if st.sidebar.button("登出系統 Logout", key="sidebar_logout"):
        st.session_state["user_info"] = None
        st.rerun()

st.sidebar.markdown("---")

nav_options = [
    "系統首頁 Home",
    "運動防護與傷勢評估 Injury & Defense Feedback",
    "比賽行事曆 Competition Calendar",
    "角度分析 Angle Analysis",
    "殘影軌跡 Ghosting Track",
    "雙影同步 Dual Video Sync",
    "跨期數據對比 Longitudinal Comparison",
    "課表紀錄 Training Plan",
    "歷史分析紀錄 History Records",
    "未來擴充規劃 Future Roadmap"
]

page = st.sidebar.radio("請選擇功能模組：", nav_options)

# ==================== 【頁面 1：系統首頁 Home】 ====================
if page == "系統首頁 Home":
    st.title("🏃 HURDEX 跨欄運動技術與防護分析系統")
    st.markdown("##### 結合運科視訊量測、防護日誌與訓練追蹤的跨欄專用分析平台")
    st.markdown("---")

    if st.session_state["user_info"] is None:
        tab_login, tab_reg, tab_reset = st.tabs(["🔐 帳號登入 Login", "📝 註冊帳號 Register", "🔑 重設密碼 Reset Password"])
        
        with tab_login:
            st.subheader("使用者登入")
            l_user = st.text_input("帳號 Username", key="l_user")
            l_pass = st.text_input("密碼 Password", type="password", key="l_pass")
            if st.button("登入系統 Login", key="btn_login", use_container_width=True):
                ok, res = check_login(l_user, l_pass)
                if ok:
                    st.session_state["user_info"] = res
                    st.success(f"歡迎回來，{res['username']}！")
                    st.rerun()
                else:
                    st.error(res)

        with tab_reg:
            st.subheader("註冊新帳號")
            r_user = st.text_input("帳號 Username", key="r_user")
            r_pass = st.text_input("密碼 Password", type="password", key="r_pass")
            r_email = st.text_input("電子信箱 Email", key="r_email")
            r_role = st.selectbox("身分 Role", ["player", "coach", "trainer"], format_func=lambda x: {"player": "選手 Athlete", "coach": "教練 Coach", "trainer": "運動防護員 Trainer"}[x], key="r_role")
            
            r_team = ""
            if r_role == "coach":
                gen_team = generate_random_team_code()
                c_code = st.text_input("請輸入教練專用邀請碼 (預設: 09290808)", key="r_ccode")
                if c_code == COACH_SECRET_CODE:
                    st.success(f"教練權限核可！系統自動為您生成新團隊碼：{gen_team}")
                    r_team = gen_team
                else:
                    st.warning("請輸入正確的教練邀請碼以生成團隊碼")
            elif r_role == "trainer":
                t_code = st.text_input("請輸入防護員專用邀請碼", key="r_tcode")
                r_team = st.text_input("請輸入要加入的團隊碼 Team Code", key="r_team_t")
            else:
                r_team = st.text_input("請輸入團隊碼 Team Code (由教練提供)", key="r_team_p")

            if st.button("確認註冊 Register", key="btn_reg", use_container_width=True):
                if r_role == "coach" and st.session_state.get("r_ccode") != COACH_SECRET_CODE:
                    st.error("教練邀請碼錯誤！")
                else:
                    ok, msg = register_user(r_user, r_pass, r_role, r_team, r_email)
                    if ok:
                        st.success(msg)
                    else:
                        st.error(msg)

        with tab_reset:
            st.subheader("忘記密碼 / 重設密碼")
            rst_user = st.text_input("請輸入帳號 Username", key="rst_user")
            if st.button("索取 6 位數驗證碼", key="btn_req_otp"):
                ok, msg = request_password_reset(rst_user)
                if ok: st.success(msg)
                else: st.error(msg)

            st.markdown("---")
            otp_code = st.text_input("請輸入驗證碼 OTP Code", key="rst_otp")
            new_pass = st.text_input("請輸入新密碼 New Password", type="password", key="rst_new_pass")
            if st.button("確認修改密碼 Reset Password", key="btn_confirm_rst"):
                ok, msg = verify_and_reset_password(rst_user, otp_code, new_pass)
                if ok: st.success(msg)
                else: st.error(msg)
    else:
        u = st.session_state["user_info"]
        st.success(f"🎉 歡迎使用 HURDEX 運科系統，{u['username']}！您的團隊碼為：`{u['team_code']}`")
        
        c1, c2, c3 = st.columns(3)
        with c1:
            st.info("📐 **角度分析**\n\n上傳攻欄影片，精準量測軀幹前傾角與關節角。")
        with c2:
            st.info("⏱️ **欄間秒數與速度**\n\n打點紀錄起跑至過欄耗時，自動計算分段速度。")
        with c3:
            st.info("🩺 **防護與訓練日誌**\n\n紀錄身體痛點、疲勞度 RPE 與防護員復健建議。")

# ==================== 【頁面 2：運動防護與傷勢評估】 ====================
elif page == "運動防護與傷勢評估 Injury & Defense Feedback":
    st.title("🩺 運動防護與傷勢報告 Injury & Defense Feedback")
    st.markdown("---")

    if st.session_state["user_info"] is None:
        st.warning("請先登入帳號")
    else:
        u = st.session_state["user_info"]
        team_code = u["team_code"]
        role = u["role"]

        if role in ["coach", "trainer"]:
            tab_tfb, tab_listfb = st.tabs(["✍️ 新增/開立防護建議報告", "📋 團隊防護建議歷史紀錄"])
            
            with tab_tfb:
                team_athletes = get_team_athletes(team_code)
                if not team_athletes:
                    st.warning("團隊目前尚無選手帳號")
                else:
                    with st.form("form_injury_feedback"):
                        sel_athlete = st.selectbox("選擇選手 Select Athlete", team_athletes)
                        fb_date = st.date_input("評估日期 Date", value=datetime.date.today())
                        body_part = st.text_input("傷痛部位 (例: 右大腿後側肌群 / 左腳踝跟腱)")
                        status_tag = st.selectbox("傷勢狀態評估", ["🔴 需暫停專項訓練 (Rest required)", "🟡 減量運科訓練 (Modified training)", "🟢 正常訓練兼防護觀察 (Full with monitoring)"])
                        treatment_advice = st.text_area("防護員處置與復健建議 Treatment & Rehab Advice", height=120)
                        
                        if st.form_submit_button("送出防護診斷報告", use_container_width=True):
                            if body_part.strip() and treatment_advice.strip():
                                save_injury_feedback(sel_athlete, u["username"], team_code, fb_date, body_part.strip(), status_tag, treatment_advice.strip())
                                st.success("防護建議報告已成功送出！")
                                st.rerun()
                            else:
                                st.warning("請填寫部位與建議內容")

            with tab_listfb:
                feedbacks = get_injury_feedbacks(team_code)
                if not feedbacks:
                    st.info("目前尚無防護回饋紀錄")
                else:
                    for fb in feedbacks:
                        fb_id, a_user, t_user, f_date, b_part, s_tag, advice, c_at = fb
                        with st.expander(f"📅 {f_date} ｜ 選手: {a_user} ｜ 部位: {b_part} ｜ {s_tag}"):
                            st.write(f"• **防護員**: {t_user}")
                            st.write(f"• **處置與建議**: \n```text\n{advice}\n```")
                            if st.button("🗑️ 刪除這筆報告", key=f"del_fb_{fb_id}"):
                                delete_injury_feedback(fb_id)
                                st.rerun()
        else:
            st.subheader("我的防護建議與傷勢診斷報告")
            my_feedbacks = get_injury_feedbacks(team_code, athlete_username=u["username"])
            if not my_feedbacks:
                st.info("尚無防護員開立的報告")
            else:
                for fb in my_feedbacks:
                    fb_id, a_user, t_user, f_date, b_part, s_tag, advice, c_at = fb
                    with st.expander(f"📅 {f_date} ｜ 部位: {b_part} ｜ 狀態: {s_tag}"):
                        st.write(f"• **評估防護員**: {t_user}")
                        st.write(f"• **復健處置與建議**: \n```text\n{advice}\n```")

# ==================== 【頁面 3：比賽行事曆 (賽事總覽依日期由近到遠排序)】 ====================
elif page == "比賽行事曆 Competition Calendar":
    st.title("🏆 團隊比賽行事曆與賽事倒數")
    st.markdown("---")

    if st.session_state["user_info"] is None:
        st.warning("請先登入帳號")
    else:
        u = st.session_state["user_info"]
        team_code = u["team_code"]
        role = u["role"]

        if role in ["coach", "trainer"]:
            team_athletes = get_team_athletes(team_code)
            selected_user = st.selectbox("檢視賽事範圍：", ["全團隊賽事"] + team_athletes) if team_athletes else "全團隊賽事"
            target_user = None if selected_user == "全團隊賽事" else selected_user
        else:
            target_user = u["username"]

        competitions = get_competitions(team_code, username=target_user)
        today = datetime.date.today()

        st.subheader("⏳ 近期與進行中賽事倒數")
        active_and_upcoming = []
        for comp in competitions:
            c_id, c_user, c_name, c_start_str, c_end_str = comp[0], comp[1], comp[2], comp[3], comp[4]
            c_notes = comp[6] if len(comp) > 6 else ""
            try:
                c_start = datetime.datetime.strptime(c_start_str, "%Y-%m-%d").date()
                c_end = datetime.datetime.strptime(c_end_str, "%Y-%m-%d").date() if c_end_str else c_start
                days_start = (c_start - today).days
                days_end = (c_end - today).days
                if days_end >= 0:
                    active_and_upcoming.append({
                        "id": c_id, "user": c_user, "name": c_name, "start_str": c_start_str, "end_str": c_end_str,
                        "notes": c_notes, "days_start": days_start, "days_end": days_end,
                        "start_date": c_start, "end_date": c_end,
                        "is_active": (days_start <= 0 <= days_end)
                    })
            except Exception: pass

        active_and_upcoming.sort(key=lambda x: (not x["is_active"], x["days_start"]))

        if active_and_upcoming:
            cols = st.columns(min(len(active_and_upcoming), 3))
            for idx, item in enumerate(active_and_upcoming[:3]):
                with cols[idx % 3]:
                    date_disp = item["start_str"] if item["start_str"] == item["end_str"] else f"{item['start_str']} ~ {item['end_str']}"
                    user_suffix = f" ({item['user']})" if role in ["coach", "trainer"] else ""

                    if item["is_active"]:
                        st.error(f"🔥 **比賽進行中！**\n\n**{item['name']}**{user_suffix}\n\n📅 日期：{date_disp}")
                    elif item["days_start"] <= 7:
                        st.warning(f"⏰ **倒數 {item['days_start']} 天**\n\n**{item['name']}**{user_suffix}\n\n📅 日期：{date_disp}")
                    else:
                        st.info(f"📅 **倒數 {item['days_start']} 天**\n\n**{item['name']}**{user_suffix}\n\n📅 日期：{date_disp}")

                    if role in ["coach", "trainer"] or item["user"] == u["username"]:
                        if st.button(f"🗑️ 刪除賽事", key=f"del_card_{item['id']}"):
                            delete_competition(item["id"])
                            st.rerun()
        else:
            st.info("目前尚無賽事")

        st.markdown("---")

        tab_cal_view, tab_add_comp, tab_list_comp = st.tabs([
            "📆 賽事月曆視圖 Calendar Grid",
            "➕ 新增賽事日程",
            "📋 賽事總覽清單"
        ])

        with tab_cal_view:
            import calendar
            c_y, c_m = st.columns([1, 1])
            with c_y:
                sel_year = st.number_input("選擇年份 Year", min_value=2024, max_value=2030, value=int(today.year), step=1, key="cal_sel_year")
            with c_m:
                sel_month = st.number_input("選擇月份 Month", min_value=1, max_value=12, value=int(today.month), step=1, key="cal_sel_month")

            cal_matrix = calendar.monthcalendar(int(sel_year), int(sel_month))

            parsed_comps = []
            for comp in competitions:
                c_user, c_name, c_start_str, c_end_str = comp[1], comp[2], comp[3], comp[4]
                try:
                    s_dt = datetime.datetime.strptime(c_start_str, "%Y-%m-%d").date()
                    e_dt = datetime.datetime.strptime(c_end_str, "%Y-%m-%d").date() if c_end_str else s_dt
                    parsed_comps.append({"name": c_name, "user": c_user, "start": s_dt, "end": e_dt})
                except Exception: pass

            html_cal = """
            <style>
                .cal-table { width: 100%; border-collapse: collapse; table-layout: fixed; background: #ffffff; border-radius: 8px; overflow: hidden; border: 1px solid #cbd5e1; }
                .cal-th { background: #f1f5f9; padding: 10px; text-align: center; color: #2563eb; font-weight: bold; border: 1px solid #e2e8f0; font-size: 14px; }
                .cal-td { height: 95px; vertical-align: top; padding: 6px; border: 1px solid #e2e8f0; background: #ffffff; box-sizing: border-box; }
                .cal-td-empty { background: #f8fafc; border: 1px solid #e2e8f0; }
                .cal-date-num { font-size: 13px; font-weight: bold; color: #334155; margin-bottom: 4px; }
                .cal-today-num { background: #2563eb; color: #ffffff; padding: 2px 7px; border-radius: 10px; font-size: 12px; display: inline-block; font-weight: bold; }
                .comp-tag { background: #2563eb; color: #ffffff; padding: 3px 6px; border-radius: 4px; font-size: 11px; margin-top: 3px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; display: block; font-weight: bold; box-shadow: 0 1px 2px rgba(0,0,0,0.1); }
                .comp-tag-active { background: #dc2626 !important; }
            </style>
            <table class="cal-table">
                <thead>
                    <tr>
                        <th class="cal-th">一 Mon</th>
                        <th class="cal-th">二 Tue</th>
                        <th class="cal-th">三 Wed</th>
                        <th class="cal-th">四 Thu</th>
                        <th class="cal-th">五 Fri</th>
                        <th class="cal-th">六 Sat</th>
                        <th class="cal-th">日 Sun</th>
                    </tr>
                </thead>
                <tbody>
            """

            for week in cal_matrix:
                html_cal += "<tr>"
                for day in week:
                    if day == 0:
                        html_cal += '<td class="cal-td-empty"></td>'
                    else:
                        curr_dt = datetime.date(int(sel_year), int(sel_month), day)
                        is_today = (curr_dt == today)
                        date_html = f'<span class="cal-today-num">{day}</span>' if is_today else f'<span class="cal-date-num">{day}</span>'

                        day_events_html = ""
                        for item in parsed_comps:
                            if item["start"] <= curr_dt <= item["end"]:
                                tag_class = "comp-tag-active" if item["start"] <= today <= item["end"] else ""
                                tag_label = f"{item['name']} ({item['user']})" if role in ["coach", "trainer"] else item["name"]
                                day_events_html += f'<div class="comp-tag {tag_class}" title="{tag_label}">{tag_label}</div>'

                        html_cal += f'<td class="cal-td">{date_html}{day_events_html}</td>'
                html_cal += "</tr>"

            html_cal += "</tbody></table>"
            st.markdown(html_cal, unsafe_allow_html=True)

        with tab_add_comp:
            with st.form("form_add_comp"):
                comp_name = st.text_input("賽事名稱 (例: 全國中等學校運動會)")
                c1, c2 = st.columns(2)
                with c1: start_date = st.date_input("開始日期", value=today)
                with c2: end_date = st.date_input("結束日期", value=today)
                comp_notes = st.text_area("賽事目標與備註")
                if st.form_submit_button("儲存賽事日程", use_container_width=True):
                    if comp_name.strip():
                        save_competition(u["username"], team_code, comp_name.strip(), start_date, end_date, "", comp_notes.strip())
                        st.success("賽事已新增！")
                        st.rerun()
                    else:
                        st.warning("請輸入賽事名稱")

        with tab_list_comp:
            def parse_comp_start(c):
                try:
                    return datetime.datetime.strptime(c[3], "%Y-%m-%d").date()
                except Exception:
                    return datetime.date.max

            sorted_competitions = sorted(competitions, key=parse_comp_start)

            for comp in sorted_competitions:
                c_id, c_user, c_name, c_start_str, c_end_str = comp[0], comp[1], comp[2], comp[3], comp[4]
                c_notes = comp[6] if len(comp) > 6 else ""
                date_range = c_start_str if c_start_str == c_end_str else f"{c_start_str} 至 {c_end_str}"
                creator_info = f" (建立者: {c_user})" if role in ["coach", "trainer"] else ""
                
                with st.expander(f"{date_range} ｜ {c_name}{creator_info}"):
                    if c_notes:
                        st.write(f"• **賽事備註**：\n```text\n{c_notes}\n```")
                    else:
                        st.write("• **賽事備註**：尚未填寫備註")

                    if role in ["coach", "trainer"] or c_user == u["username"]:
                        col_edit, col_del = st.columns([1, 1])
                        
                        with col_edit:
                            with st.popover("✏️ 編輯賽事與備註"):
                                with st.form(f"edit_comp_form_{c_id}"):
                                    st.subheader("編輯賽事資料")
                                    edit_name = st.text_input("賽事名稱", value=c_name)
                                    
                                    try:
                                        s_dt_val = datetime.datetime.strptime(c_start_str, "%Y-%m-%d").date()
                                        e_dt_val = datetime.datetime.strptime(c_end_str, "%Y-%m-%d").date() if c_end_str else s_dt_val
                                    except Exception:
                                        s_dt_val, e_dt_val = today, today

                                    ec1, ec2 = st.columns(2)
                                    with ec1: edit_start = st.date_input("開始日期", value=s_dt_val, key=f"es_{c_id}")
                                    with ec2: edit_end = st.date_input("結束日期", value=e_dt_val, key=f"ee_{c_id}")
                                    
                                    edit_notes = st.text_area("賽事目標與備註", value=c_notes, key=f"en_{c_id}")
                                    
                                    if st.form_submit_button("儲存修改", use_container_width=True):
                                        if edit_name.strip():
                                            update_competition(c_id, edit_name.strip(), edit_start, edit_end, edit_notes.strip())
                                            st.success("賽事修改成功！")
                                            st.rerun()
                                        else:
                                            st.warning("請輸入賽事名稱")

                        with col_del:
                            if st.button(f"🗑️ 刪除賽事", key=f"del_comp_{c_id}"):
                                delete_competition(c_id)
                                st.rerun()

# ==================== 【頁面 4：角度分析】 ====================
elif page == "角度分析 Angle Analysis":
    st.title("角度分析模組 Angle Analysis Module")
    st.markdown("---")

    uploaded_file = st.file_uploader("上傳動作影片 Upload Video MP4 MOV", type=["mp4", "mov", "avi"])

    if uploaded_file is not None:
        with st.spinner("正在優化手機影片格式 (H.264 MP4)..."):
            bytes_single, mime_single = process_and_convert_video(uploaded_file)
        b64_single = base64.b64encode(bytes_single).decode("utf-8")

        tab_play, tab_measure, tab_hurdle_timer = st.tabs([
            "影片流暢預覽",
            "關鍵影格角度量測",
            "欄間秒數及速度計算"
        ])

        with tab_play:
            st.video(bytes_single)

        with tab_measure:
            measure_html_template = """<!DOCTYPE html>
            <html>
            <head>
            <style>
                * { box-sizing: border-box; }
                body { font-family: -apple-system, sans-serif; background: transparent; color: #0f172a; margin: 0; padding: 0; }
                .control-bar { display: flex; align-items: center; gap: 8px; margin-bottom: 12px; background: #ffffff; padding: 10px 16px; border-radius: 8px; border: 1px solid #e2e8f0; flex-wrap: wrap; }
                .btn { background: #2563eb; color: #fff; border: none; padding: 7px 12px; border-radius: 6px; font-weight: bold; font-size: 13px; cursor: pointer; }
                .btn-secondary { background: #f1f5f9; color: #334155; border: 1px solid #cbd5e1; }
                .btn-t0 { background: #d97706; color: #fff; border: none; padding: 7px 12px; border-radius: 6px; font-weight: bold; font-size: 13px; cursor: pointer; }
                .btn-snap { background: #059669; color: #fff; border: none; padding: 7px 14px; border-radius: 6px; font-weight: bold; font-size: 13px; cursor: pointer; }
                .btn-mode { background: #f8fafc; color: #64748b; border: 1px solid #cbd5e1; padding: 6px 12px; border-radius: 20px; font-size: 13px; font-weight: bold; cursor: pointer; }
                .btn-mode.active { background: #2563eb; color: #fff; border-color: #2563eb; }
                .main-layout { display: flex; gap: 16px; width: 100%; align-items: flex-start; }
                .canvas-card { flex: 1.2; background: #ffffff; border-radius: 10px; border: 1px solid #e2e8f0; padding: 12px; display: flex; flex-direction: column; align-items: center; }
                .video-wrapper { position: relative; width: 100%; background: #0f172a; border-radius: 8px; overflow: hidden; display: flex; justify-content: center; align-items: center; }
                video { width: 100%; max-height: 400px; object-fit: contain; display: block; }
                canvas { position: absolute; top: 0; left: 0; cursor: crosshair; }
                .slider-box { width: 100%; margin-top: 10px; background: #f8fafc; padding: 8px 12px; border-radius: 8px; border: 1px solid #e2e8f0; }
                input[type=range] { width: 100%; height: 6px; border-radius: 3px; background: #cbd5e1; outline: none; cursor: pointer; }
                .data-box { flex: 0.8; background: #ffffff; border-radius: 10px; border: 1px solid #e2e8f0; padding: 16px; display: flex; flex-direction: column; max-height: 520px; overflow-y: auto; }
                .metric-card { background: #f8fafc; padding: 12px 16px; border-radius: 8px; margin-bottom: 12px; border: 1px solid #e2e8f0; border-left: 4px solid #2563eb; }
                .metric-title { font-size: 12px; color: #64748b; margin-bottom: 4px; }
                .metric-val { font-size: 22px; font-weight: bold; color: #2563eb; }
                select { background: #ffffff; color: #0f172a; border: 1px solid #cbd5e1; padding: 5px 8px; border-radius: 6px; font-weight: bold; }
            </style>
            </head>
            <body>
                <div class="control-bar">
                    <button id="btnMode3p" class="btn-mode active">三點關節角 3-Point Angle</button>
                    <button id="btnMode2p" class="btn-mode">兩點前傾角 2-Point Lean Angle</button>
                    <button id="btnUndo" class="btn btn-secondary">撤銷 Undo</button>
                    <button id="btnClear" class="btn btn-secondary">清空 Clear</button>
                    <button id="btnSetT0" class="btn-t0">設為動作起點 T0</button>
                    <select id="selPhaseTag">
                        <option value="攻欄起跳瞬間 Takeoff Phase">攻欄起跳瞬間 Takeoff Phase</option>
                        <option value="過欄最高點 Peak Clearance">過欄最高點 Peak Clearance</option>
                        <option value="攻欄腳著地瞬間 Landing Phase">攻欄腳著地瞬間 Landing Phase</option>
                        <option value="欄間跑步姿態 Inter-hurdle Run">欄間跑步姿態 Inter-hurdle Run</option>
                        <option value="自訂關鍵影格 Custom Keyframe" selected>自訂關鍵影格 Custom Keyframe</option>
                    </select>
                    <button id="btnSnapAngle" class="btn-snap" style="margin-left: auto;">擷取當前畫面與角度</button>
                </div>

                <div class="main-layout">
                    <div class="canvas-card">
                        <div class="video-wrapper">
                            <video id="vidMeas" playsinline muted></video>
                            <canvas id="canvasOverlay"></canvas>
                        </div>
                        <div class="slider-box">
                            <div style="display:flex; justify-content:space-between; margin-bottom:4px; font-size:12px; color:#64748b;">
                                <span>時間控制滑桿 Time Slider</span>
                                <span>影片時間: <b id="timeTextMeas" style="color:#0f172a;">0.00s</b> ｜ <b style="color:#d97706;">時間: <span id="relTimeTextMeas" style="color:#059669; font-size:14px; font-weight:bold;">0.00s</span></b></span>
                            </div>
                            <input type="range" id="sliderMeas" min="0" max="100" value="0" step="0.01">
                        </div>
                        <div style="display: flex; width: 100%; gap: 8px; margin-top: 10px; align-items: center; flex-wrap: wrap;">
                            <button id="btnPlayMeas" class="btn">播放 暫停 Play Pause</button>
                            <button id="btnStepBack" class="btn btn-secondary">上一幀 Frame Back</button>
                            <button id="btnStepForward" class="btn btn-secondary">下一幀 Frame Forward</button>
                            <span style="font-size:12px; color:#64748b;">抽幀:</span>
                            <select id="selFrameInterval">
                                <option value="1" selected>1 幀 (1/60s)</option>
                                <option value="3">3 幀 (0.05s)</option>
                                <option value="5">5 幀 (0.08s)</option>
                            </select>
                            <span style="font-size:12px; color:#64748b;">速度:</span>
                            <select id="selSpeedMeas">
                                <option value="0.25">0.25x</option>
                                <option value="0.5">0.5x</option>
                                <option value="1.0" selected>1.0x</option>
                            </select>
                        </div>
                    </div>
                    <div class="data-box">
                        <h4 style="margin: 0 0 12px 0; color:#2563eb;">當前影格角度數據 Angle Metrics</h4>
                        <div id="metricsContainer"></div>
                    </div>
                </div>

                <script>
                    const vidMeas = document.getElementById('vidMeas');
                    const canvas = document.getElementById('canvasOverlay');
                    const ctx = canvas.getContext('2d');
                    const sliderMeas = document.getElementById('sliderMeas');
                    const timeTextMeas = document.getElementById('timeTextMeas');
                    const relTimeTextMeas = document.getElementById('relTimeTextMeas');
                    const btnPlayMeas = document.getElementById('btnPlayMeas');
                    const btnStepBack = document.getElementById('btnStepBack');
                    const btnStepForward = document.getElementById('btnStepForward');
                    const btnSetT0 = document.getElementById('btnSetT0');
                    const selFrameInterval = document.getElementById('selFrameInterval');
                    const selSpeedMeas = document.getElementById('selSpeedMeas');
                    const selPhaseTag = document.getElementById('selPhaseTag');
                    const btnMode3p = document.getElementById('btnMode3p');
                    const btnMode2p = document.getElementById('btnMode2p');
                    const btnUndo = document.getElementById('btnUndo');
                    const btnClear = document.getElementById('btnClear');
                    const btnSnapAngle = document.getElementById('btnSnapAngle');
                    const metricsContainer = document.getElementById('metricsContainer');

                    let mode = '3p', pts2p = [], pts3pGroups = [], buf3p = [], currentAngleList = [], t0 = 0.0;

                    function syncToStreamlit(el, val) {
                        if (!el) return;
                        try {
                            const pWin = window.parent;
                            const nativeSetter = pWin.Object.getOwnPropertyDescriptor(pWin.HTMLTextAreaElement.prototype, "value").set;
                            nativeSetter.call(el, val);
                            if (el._valueTracker) el._valueTracker.setValue('');
                            el.focus(); el.dispatchEvent(new pWin.Event('input', { bubbles: true }));
                            el.dispatchEvent(new pWin.Event('change', { bubbles: true })); el.blur();
                        } catch(err) { el.value = val; el.dispatchEvent(new Event('input', { bubbles: true })); }
                    }

                    function b64ToBlob(b64Data, contentType) {
                        const byteCharacters = atob(b64Data);
                        const sliceSize = 1024 * 1024; const byteArrays = [];
                        for (let offset = 0; offset < byteCharacters.length; offset += sliceSize) {
                            const slice = byteCharacters.slice(offset, offset + sliceSize);
                            const byteNumbers = new Array(slice.length);
                            for (let i = 0; i < slice.length; i++) byteNumbers[i] = slice.charCodeAt(i);
                            byteArrays.push(new Uint8Array(byteNumbers));
                        }
                        return new Blob(byteArrays, {type: contentType});
                    }

                    try { vidMeas.src = URL.createObjectURL(b64ToBlob("___B64_SINGLE___", "___MIME_SINGLE___")); } catch(e){}

                    function syncCanvasSize() {
                        if (vidMeas.clientWidth > 0 && vidMeas.clientHeight > 0) { canvas.width = vidMeas.clientWidth; canvas.height = vidMeas.clientHeight; }
                        redraw();
                    }
                    vidMeas.addEventListener('loadedmetadata', () => { sliderMeas.max = vidMeas.duration; syncCanvasSize(); });
                    vidMeas.addEventListener('canplay', syncCanvasSize);
                    window.addEventListener('resize', syncCanvasSize);

                    function updateTimeDisplays() {
                        const absT = vidMeas.currentTime || 0;
                        const relT = Math.max(0, absT - t0);
                        timeTextMeas.innerText = absT.toFixed(2) + 's';
                        relTimeTextMeas.innerText = relT.toFixed(2) + 's';
                    }

                    vidMeas.addEventListener('timeupdate', () => {
                        updateTimeDisplays();
                        if (!vidMeas.paused) sliderMeas.value = vidMeas.currentTime;
                        redraw();
                    });

                    sliderMeas.addEventListener('input', (e) => { vidMeas.currentTime = parseFloat(e.target.value); updateTimeDisplays(); redraw(); });
                    btnSetT0.addEventListener('click', () => { t0 = vidMeas.currentTime; btnSetT0.innerText = `起點 T0: ${t0.toFixed(2)}s`; updateTimeDisplays(); });
                    btnPlayMeas.addEventListener('click', () => { if (vidMeas.paused) vidMeas.play(); else vidMeas.pause(); });

                    selSpeedMeas.addEventListener('change', (e) => { vidMeas.playbackRate = parseFloat(e.target.value); });

                    const BASE_FRAME_TIME = 1 / 60;
                    btnStepBack.addEventListener('click', () => {
                        vidMeas.pause();
                        vidMeas.currentTime = Math.max(0, vidMeas.currentTime - (BASE_FRAME_TIME * (parseInt(selFrameInterval.value)||1)));
                        sliderMeas.value = vidMeas.currentTime; updateTimeDisplays(); redraw();
                    });
                    btnStepForward.addEventListener('click', () => {
                        vidMeas.pause();
                        vidMeas.currentTime = Math.min(vidMeas.duration, vidMeas.currentTime + (BASE_FRAME_TIME * (parseInt(selFrameInterval.value)||1)));
                        sliderMeas.value = vidMeas.currentTime; updateTimeDisplays(); redraw();
                    });

                    btnMode3p.addEventListener('click', () => { mode = '3p'; btnMode3p.classList.add('active'); btnMode2p.classList.remove('active'); redraw(); });
                    btnMode2p.addEventListener('click', () => { mode = '2p'; btnMode2p.classList.add('active'); btnMode3p.classList.remove('active'); redraw(); });

                    canvas.addEventListener('click', (e) => {
                        const rect = canvas.getBoundingClientRect();
                        const x = e.clientX - rect.left, y = e.clientY - rect.top;
                        if (mode === '3p') {
                            buf3p.push({x, y});
                            if (buf3p.length === 3) { pts3pGroups.push([...buf3p]); buf3p = []; }
                        } else { if (pts2p.length < 2) pts2p.push({x, y}); }
                        redraw();
                    });

                    btnUndo.addEventListener('click', () => {
                        if (mode === '3p') { if (buf3p.length > 0) buf3p.pop(); else if (pts3pGroups.length > 0) pts3pGroups.pop(); }
                        else { if (pts2p.length > 0) pts2p.pop(); }
                        redraw();
                    });
                    btnClear.addEventListener('click', () => { pts2p = []; pts3pGroups = []; buf3p = []; redraw(); });

                    function calc2ptAngle(p1, p2) {
                        let dx = p1.x - p2.x, dy = p1.y - p2.y;
                        let dot = dy * (-1), len = Math.sqrt(dx*dx + dy*dy);
                        return len === 0 ? 0 : Math.acos(Math.max(-1, Math.min(1, dot / len))) * 180 / Math.PI;
                    }

                    function calc3ptAngle(A, B, C) {
                        let baX = A.x - B.x, baY = A.y - B.y, bcX = C.x - B.x, bcY = C.y - B.y;
                        let lenBA = Math.sqrt(baX*baX + baY*baY), lenBC = Math.sqrt(bcX*bcX + bcY*bcY);
                        return (lenBA === 0 || lenBC === 0) ? 0 : Math.acos(Math.max(-1, Math.min(1, (baX*bcX + baY*bcY) / (lenBA * lenBC)))) * 180 / Math.PI;
                    }

                    const colors3p = ['#d97706', '#2563eb', '#059669', '#7c3aed', '#dc2626'];

                    function redraw() {
                        ctx.clearRect(0, 0, canvas.width, canvas.height);
                        metricsContainer.innerHTML = ''; currentAngleList = [];

                        if (pts2p.length === 2) {
                            ctx.strokeStyle = '#2563eb'; ctx.lineWidth = 2;
                            ctx.beginPath(); ctx.moveTo(pts2p[0].x, pts2p[0].y); ctx.lineTo(pts2p[1].x, pts2p[1].y); ctx.stroke();
                            const leanAng = calc2ptAngle(pts2p[0], pts2p[1]);
                            currentAngleList.push({ name: "軀幹前傾角 Trunk Lean Angle", value: `${leanAng.toFixed(1)}°` });
                            metricsContainer.innerHTML += `<div class="metric-card"><div class="metric-title">軀幹前傾角 Trunk Lean Angle</div><div class="metric-val">${leanAng.toFixed(1)}°</div></div>`;
                        }

                        pts3pGroups.forEach((group, idx) => {
                            const color = colors3p[idx % colors3p.length];
                            const ang = calc3ptAngle(group[0], group[1], group[2]);
                            ctx.strokeStyle = color; ctx.lineWidth = 2;
                            ctx.beginPath(); ctx.moveTo(group[0].x, group[0].y); ctx.lineTo(group[1].x, group[1].y); ctx.lineTo(group[2].x, group[2].y); ctx.stroke();
                            currentAngleList.push({ name: `關節角度 Joint Angle #${idx+1}`, value: `${ang.toFixed(1)}°` });
                            metricsContainer.innerHTML += `<div class="metric-card" style="border-left-color: ${color};"><div class="metric-title">關節角度 Joint Angle #${idx+1}</div><div class="metric-val" style="color:${color};">${ang.toFixed(1)}°</div></div>`;
                        });
                    }

                    btnSnapAngle.addEventListener('click', () => {
                        const off = document.createElement('canvas');
                        off.width = vidMeas.videoWidth; off.height = vidMeas.videoHeight;
                        const offCtx = off.getContext('2d');
                        offCtx.drawImage(vidMeas, 0, 0, off.width, off.height);
                        offCtx.drawImage(canvas, 0, 0, off.width, off.height);

                        const textAreas = window.parent.document.querySelectorAll('textarea');
                        let targetImgTA = null, targetAngleTA = null;
                        textAreas.forEach(ta => {
                            const label = ta.getAttribute('aria-label') || '';
                            if (label.includes('angle_img_b64')) targetImgTA = ta;
                            if (label.includes('angle_data_json')) targetAngleTA = ta;
                        });

                        const absT = vidMeas.currentTime || 0;
                        const relT = Math.max(0, absT - t0);

                        if (targetImgTA && targetAngleTA) {
                            syncToStreamlit(targetImgTA, off.toDataURL('image/jpeg', 0.85));
                            syncToStreamlit(targetAngleTA, JSON.stringify({
                                time_sec: absT.toFixed(2), rel_time_sec: relT.toFixed(2), t0_sec: t0.toFixed(2),
                                phase_tag: selPhaseTag.value, angles: currentAngleList
                            }));
                            alert(`已記錄角度數據！相對時間: ${relT.toFixed(2)}s`);
                        }
                    });
                    redraw();
                </script>
            </body>
            </html>"""

            measure_html_code = measure_html_template.replace("___MIME_SINGLE___", mime_single).replace("___B64_SINGLE___", b64_single)
            components.html(measure_html_code, height=720, scrolling=False)

            st.markdown("---")
            captured_img = st.text_area("angle_img_b64", key="captured_angle_img", label_visibility="collapsed")
            captured_json = st.text_area("angle_data_json", key="captured_angle_json", label_visibility="collapsed")

            if captured_img and captured_json:
                st.success("已成功擷取影格與角度數據")
                col_prev1, col_prev2 = st.columns([1.2, 0.8])
                with col_prev1: st.image(captured_img, caption="角度量測合成圖", use_container_width=True)
                with col_prev2:
                    try:
                        p_data = json.loads(captured_json)
                        st.write(f"時間：`{p_data.get('rel_time_sec', '0.00')} 秒`")
                        st.write(f"階段：`{p_data.get('phase_tag')}`")
                        for item in p_data.get("angles", []): st.write(f"• **{item['name']}**：`{item['value']}`")
                    except Exception: pass

                if st.session_state["user_info"]:
                    with st.form("save_angle_form"):
                        rec_title_angle = st.text_input("紀錄名稱與備註", value=f"動作關鍵影格角度量測 {uploaded_file.name}")
                        if st.form_submit_button("儲存至團隊資料庫"):
                            u = st.session_state["user_info"]
                            save_record(u["username"], u["team_code"], "關鍵影格角度量測 Keyframe Angle Measurement", rec_title_angle, {
                                "video_filename": uploaded_file.name, "image_b64": captured_img, "angle_metrics": json.loads(captured_json), "saved_at": str(datetime.datetime.now())
                            })
                            st.success("儲存成功！")

        with tab_hurdle_timer:
            timer_html_template = """<!DOCTYPE html>
            <html>
            <head>
            <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
            <style>
                * { box-sizing: border-box; }
                body { font-family: -apple-system, sans-serif; background: transparent; color: #0f172a; margin: 0; padding: 0; }
                .control-bar { display: flex; gap: 8px; margin-bottom: 12px; background: #ffffff; padding: 10px; border-radius: 8px; border: 1px solid #e2e8f0; align-items: center; flex-wrap: wrap; }
                .btn { background: #2563eb; color: #fff; border: none; padding: 6px 12px; border-radius: 6px; font-weight: bold; cursor: pointer; }
                .btn-secondary { background: #f1f5f9; color: #334155; border: 1px solid #cbd5e1; padding: 6px 12px; border-radius: 6px; font-weight: bold; cursor: pointer; }
                .btn-t0 { background: #d97706; color: #fff; border: none; padding: 6px 12px; border-radius: 6px; font-weight: bold; cursor: pointer; }
                .btn-sync { background: #059669; color: #fff; font-weight: bold; border: none; padding: 8px 14px; border-radius: 6px; cursor: pointer; width: 100%; margin-top: 4px; }
                .btn-tap { background: #2563eb; color: #fff; font-size: 15px; padding: 12px; border-radius: 6px; border: none; font-weight: bold; cursor: pointer; width: 100%; margin-top: 8px; }
                .layout { display: flex; flex-direction: column; gap: 16px; width: 100%; }
                video { width: 100%; border-radius: 6px; background: #000; max-height: 420px; object-fit: contain; }
                table { width: 100%; border-collapse: collapse; margin-top: 8px; font-size: 13px; }
                th, td { padding: 8px 4px; text-align: center; border-bottom: 1px solid #e2e8f0; }
                th { color: #2563eb; background: #f8fafc; font-weight: bold; }
                select { background: #ffffff; color: #0f172a; border: 1px solid #cbd5e1; padding: 5px 8px; border-radius: 6px; font-weight: bold; }
            </style>
            </head>
            <body>
                <div class="control-bar">
                    <select id="selGender">
                        <option value="men">男子 110mH (13.72m / 9.14m)</option>
                        <option value="women">女子 100mH (13.00m / 8.50m)</option>
                    </select>
                    <button id="btnSetT0T" class="btn-t0">設為起點 T0</button>
                    <button id="btnPlayT" class="btn">播放/暫停</button>
                    <button id="btnStepBackT" class="btn btn-secondary">上一幀</button>
                    <button id="btnStepForwardT" class="btn btn-secondary">下一幀</button>
                    <select id="selSpeedT">
                        <option value="0.25">0.25x</option>
                        <option value="0.5">0.5x</option>
                        <option value="1.0" selected>1.0x</option>
                    </select>
                    <button id="btnUndoT" class="btn btn-secondary">撤銷</button>
                    <button id="btnClearT" class="btn btn-secondary">重置</button>
                    <button id="btnSyncT" class="btn btn-sync">同步數據 Sync Timing Data</button>
                </div>
                <div class="layout">
                    <div>
                        <video id="vidT" muted playsinline></video>
                        <input type="range" id="sliderT" min="0" max="100" value="0" step="0.01" style="width:100%; margin-top:8px;">
                        <button id="btnTap" class="btn-tap">1. 紀錄起跑與過欄瞬間 (按 Space 或 Enter)</button>
                    </div>
                    <div>
                        <table id="tb">
                            <thead><tr><th>階段 Phase</th><th>累積時間</th><th>單段耗時</th><th>平均速度</th></tr></thead>
                            <tbody></tbody>
                        </table>
                        <div style="height:220px; margin-top:14px;"><canvas id="chartV"></canvas></div>
                    </div>
                </div>
                <script>
                    const vidT = document.getElementById('vidT');
                    const sliderT = document.getElementById('sliderT');
                    const btnTap = document.getElementById('btnTap');
                    let taps = [], chartObj = null, t0_h = 0.0;

                    const segLabels = [
                        "01. 起跑->H1", "02. H1->H2", "03. H2->H3", "04. H3->H4", "05. H4->H5",
                        "06. H5->H6", "07. H6->H7", "08. H7->H8", "09. H8->H9", "10. H9->H10", "11. H10->終點"
                    ];

                    function syncToStreamlit(el, val) {
                        if (!el) return;
                        try {
                            const pWin = window.parent;
                            const nativeSetter = pWin.Object.getOwnPropertyDescriptor(pWin.HTMLTextAreaElement.prototype, "value").set;
                            nativeSetter.call(el, val);
                            if (el._valueTracker) el._valueTracker.setValue('');
                            el.focus(); el.dispatchEvent(new pWin.Event('input', { bubbles: true }));
                            el.dispatchEvent(new pWin.Event('change', { bubbles: true })); el.blur();
                        } catch(err) { el.value = val; el.dispatchEvent(new Event('input', { bubbles: true })); }
                    }

                    function b64ToBlob(b64Data, contentType) {
                        const byteCharacters = atob(b64Data); const sliceSize = 1024 * 1024; const byteArrays = [];
                        for (let offset = 0; offset < byteCharacters.length; offset += sliceSize) {
                            const slice = byteCharacters.slice(offset, offset + sliceSize);
                            const byteNumbers = new Array(slice.length);
                            for (let i = 0; i < slice.length; i++) byteNumbers[i] = slice.charCodeAt(i);
                            byteArrays.push(new Uint8Array(byteNumbers));
                        }
                        return new Blob(byteArrays, {type: contentType});
                    }

                    try { vidT.src = URL.createObjectURL(b64ToBlob("___B64_SINGLE___", "___MIME_SINGLE___")); } catch(e){}
                    vidT.addEventListener('loadedmetadata', () => sliderT.max = vidT.duration);
                    vidT.addEventListener('timeupdate', () => { if (!vidT.paused) sliderT.value = vidT.currentTime; });
                    sliderT.addEventListener('input', (e) => vidT.currentTime = parseFloat(e.target.value));
                    document.getElementById('btnPlayT').addEventListener('click', () => { if (vidT.paused) vidT.play(); else vidT.pause(); });
                    document.getElementById('selSpeedT').addEventListener('change', (e) => vidT.playbackRate = parseFloat(e.target.value));

                    const FRAME_TIME = 1 / 60;
                    document.getElementById('btnStepBackT').addEventListener('click', () => { vidT.pause(); vidT.currentTime = Math.max(0, vidT.currentTime - FRAME_TIME); });
                    document.getElementById('btnStepForwardT').addEventListener('click', () => { vidT.pause(); vidT.currentTime = Math.min(vidT.duration, vidT.currentTime + FRAME_TIME); });

                    document.getElementById('btnSetT0T').addEventListener('click', () => {
                        t0_h = vidT.currentTime;
                        if (taps.length === 0) taps.push(t0_h); else taps[0] = t0_h;
                        render();
                    });

                    const ctxChart = document.getElementById('chartV').getContext('2d');
                    chartObj = new Chart(ctxChart, {
                        type: 'bar',
                        data: { labels: [], datasets: [{ label: 'm/s', data: [], backgroundColor: 'rgba(37,99,235,0.2)', borderColor: '#2563eb', borderWidth: 2 }] },
                        options: { responsive: true, maintainAspectRatio: false }
                    });

                    function render() {
                        const tbody = document.querySelector('#tb tbody');
                        tbody.innerHTML = '';
                        const dists = document.getElementById('selGender').value === 'men' ? [13.72, 9.14, 9.14, 9.14, 9.14, 9.14, 9.14, 9.14, 9.14, 9.14, 14.02] : [13.00, 8.50, 8.50, 8.50, 8.50, 8.50, 8.50, 8.50, 8.50, 8.50, 10.50];
                        let labels = [], vels = [];

                        for (let i = 0; i < taps.length; i++) {
                            let cum = taps[i] - taps[0];
                            let lbl = i === 0 ? '起跑 T0' : segLabels[i - 1];
                            let dtStr = '-', spdStr = '-';
                            if (i > 0) {
                                let dt = taps[i] - taps[i-1];
                                let spd = dt > 0 ? (dists[i-1] / dt) : 0;
                                dtStr = dt.toFixed(2) + 's'; spdStr = spd.toFixed(2) + ' m/s';
                                labels.push(lbl); vels.push(spd.toFixed(2));
                            }
                            tbody.innerHTML += `<tr><td>${lbl}</td><td>${cum.toFixed(2)}s</td><td>${dtStr}</td><td>${spdStr}</td></tr>`;
                        }
                        chartObj.data.labels = labels; chartObj.data.datasets[0].data = vels; chartObj.update();
                    }

                    function recordTap() { if (taps.length < 12) { taps.push(vidT.currentTime); render(); } }
                    btnTap.addEventListener('click', recordTap);
                    document.getElementById('btnUndoT').addEventListener('click', () => { taps.pop(); render(); });
                    document.getElementById('btnClearT').addEventListener('click', () => { taps = []; render(); });
                    document.addEventListener('keydown', (e) => { if (e.code==='Space'||e.code==='Enter') { e.preventDefault(); recordTap(); } });

                    document.getElementById('btnSyncT').addEventListener('click', () => {
                        const dists = document.getElementById('selGender').value === 'men' ? [13.72, 9.14, 9.14, 9.14, 9.14, 9.14, 9.14, 9.14, 9.14, 9.14, 14.02] : [13.00, 8.50, 8.50, 8.50, 8.50, 8.50, 8.50, 8.50, 8.50, 8.50, 10.50];
                        let splits = [];
                        for (let i = 1; i < taps.length; i++) {
                            let dt = taps[i] - taps[i-1];
                            let spd = dt > 0 ? (dists[i-1] / dt) : 0;
                            splits.push({ segment: segLabels[i-1], cum_sec: (taps[i]-taps[0]).toFixed(2), split_sec: dt.toFixed(2), speed_ms: spd.toFixed(2) });
                        }
                        const textAreas = window.parent.document.querySelectorAll('textarea');
                        let targetTimerTA = null;
                        textAreas.forEach(ta => { if ((ta.getAttribute('aria-label') || '').includes('timer_data_json')) targetTimerTA = ta; });
                        if (targetTimerTA) { syncToStreamlit(targetTimerTA, JSON.stringify({ total_time: (taps[taps.length-1]-taps[0]).toFixed(2), splits: splits })); alert("數據已成功同步！"); }
                    });
                </script>
            </body>
            </html>"""

            timer_html_code = timer_html_template.replace("___MIME_SINGLE___", mime_single).replace("___B64_SINGLE___", b64_single)
            components.html(timer_html_code, height=980, scrolling=True)

            st.markdown("---")
            captured_timer_json = st.text_area("timer_data_json", key="captured_timer_json", label_visibility="collapsed")
            if captured_timer_json:
                try:
                    tm_data = json.loads(captured_timer_json)
                    st.success(f"打點數據接收成功！總累積耗時：`{tm_data.get('total_time')} 秒`")
                    if st.session_state["user_info"]:
                        with st.form("save_timer_form"):
                            rec_title_timer = st.text_input("紀錄名稱", value=f"欄間秒數及速度計算 {uploaded_file.name}")
                            if st.form_submit_button("儲存至團隊資料庫"):
                                u = st.session_state["user_info"]
                                save_record(u["username"], u["team_code"], "欄間秒數及速度計算 Hurdle Time & Speed Calculation", rec_title_timer, {"timer_metrics": tm_data, "saved_at": str(datetime.datetime.now())})
                                st.success("儲存成功！")
                except Exception: pass

# ==================== 【頁面 5：殘影軌跡】 ====================
elif page == "殘影軌跡 Ghosting Track":
    st.title("殘影軌跡模組 Ghosting Track Module")
    st.markdown("---")
    uploaded_strobe_file = st.file_uploader("上傳動作影片 Upload Video MP4 MOV", type=["mp4", "mov", "avi"], key="strobe_uploader")

    if uploaded_strobe_file is not None:
        with st.spinner("正在優化手機影片格式..."):
            bytes_strobe, mime_strobe = process_and_convert_video(uploaded_strobe_file)
        b64_strobe = base64.b64encode(bytes_strobe).decode("utf-8")

        strobe_html_template = """<!DOCTYPE html>
        <html>
        <head>
        <script src="https://cdn.jsdelivr.net/npm/@mediapipe/pose/pose.js" crossorigin="anonymous"></script>
        <style>
            * { box-sizing: border-box; }
            body { font-family: -apple-system, sans-serif; background: transparent; color: #0f172a; margin: 0; padding: 0; }
            .control-bar { display: flex; gap: 10px; margin-bottom: 10px; background: #ffffff; padding: 10px; border-radius: 8px; border: 1px solid #e2e8f0; align-items: center; flex-wrap: wrap; }
            .btn { background: #dc2626; color: #fff; border: none; padding: 7px 14px; border-radius: 6px; font-weight: bold; cursor: pointer; }
            .btn-secondary { background: #f1f5f9; color: #334155; border: 1px solid #cbd5e1; padding: 7px 12px; border-radius: 6px; font-weight: bold; cursor: pointer; }
            .btn-snap { background: #059669; color: #fff; font-weight: bold; padding: 7px 14px; border-radius: 6px; border: none; cursor: pointer; margin-left: auto; }
            .video-wrapper { position: relative; width: 100%; background: #000; border-radius: 6px; overflow: hidden; display: flex; justify-content: center; align-items: center; }
            video { width: 100%; max-height: 440px; display: block; object-fit: contain; }
            canvas { position: absolute; top: 0; left: 0; }
            select { background: #ffffff; color: #0f172a; border: 1px solid #cbd5e1; padding: 5px 8px; border-radius: 6px; font-weight: bold; }
        </style>
        </head>
        <body>
            <div class="control-bar">
                <select id="selGhostCount">
                    <option value="20">20 影格 20 Frames</option>
                    <option value="40" selected>40 影格 High Frame Rate</option>
                    <option value="60">60 影格 Full Motion</option>
                </select>
                <button id="btnCapGhost" class="btn">捕捉攻欄至落地殘影</button>
                <button id="btnClearG" class="btn btn-secondary">清空殘影 Clear</button>
                <button id="btnSnapGhostImg" class="btn-snap">擷取殘影畫面存檔</button>
            </div>
            <div class="video-wrapper">
                <video id="vidG" playsinline muted></video>
                <canvas id="cvsG"></canvas>
            </div>
            <script>
                const vidG = document.getElementById('vidG'), cvsG = document.getElementById('cvsG'), ctxG = cvsG.getContext('2d');
                let ghostHistory = [], currentPoseLM = null;

                function syncToStreamlit(el, val) {
                    if (!el) return;
                    try {
                        const pWin = window.parent;
                        const nativeSetter = pWin.Object.getOwnPropertyDescriptor(pWin.HTMLTextAreaElement.prototype, "value").set;
                        nativeSetter.call(el, val);
                        if (el._valueTracker) el._valueTracker.setValue('');
                        el.focus(); el.dispatchEvent(new pWin.Event('input', { bubbles: true }));
                        el.dispatchEvent(new pWin.Event('change', { bubbles: true })); el.blur();
                    } catch(err) { el.value = val; el.dispatchEvent(new Event('input', { bubbles: true })); }
                }

                function b64ToBlob(b64Data, contentType) {
                    const byteCharacters = atob(b64Data); const sliceSize = 1024 * 1024; const byteArrays = [];
                    for (let offset = 0; offset < byteCharacters.length; offset += sliceSize) {
                        const slice = byteCharacters.slice(offset, offset + sliceSize);
                        const byteNumbers = new Array(slice.length);
                        for (let i = 0; i < slice.length; i++) byteNumbers[i] = slice.charCodeAt(i);
                        byteArrays.push(new Uint8Array(byteNumbers));
                    }
                    return new Blob(byteArrays, {type: contentType});
                }
                try { vidG.src = URL.createObjectURL(b64ToBlob("___B64_STROBE___", "___MIME_STROBE___")); } catch(e){}

                function syncSize() { if (vidG.clientWidth>0 && vidG.clientHeight>0) { cvsG.width = vidG.clientWidth; cvsG.height = vidG.clientHeight; } }
                vidG.addEventListener('loadedmetadata', syncSize); vidG.addEventListener('canplay', syncSize);
                window.addEventListener('resize', syncSize);

                const pose = new Pose({ locateFile: (f) => `https://cdn.jsdelivr.net/npm/@mediapipe/pose/${f}` });
                pose.setOptions({ modelComplexity: 1, smoothLandmarks: true });
                pose.onResults((res) => { if (res.poseLandmarks) { currentPoseLM = res.poseLandmarks; draw(); } });

                vidG.addEventListener('timeupdate', async () => { if (vidG.readyState >= 2) await pose.send({ image: vidG }); });

                document.getElementById('btnCapGhost').addEventListener('click', async () => {
                    const cnt = parseInt(document.getElementById('selGhostCount').value);
                    ghostHistory = []; const startT = vidG.currentTime;
                    for (let i = 0; i < cnt; i++) {
                        const targetT = startT + (i * 0.033);
                        if (targetT > vidG.duration) break;
                        vidG.currentTime = targetT;
                        await pose.send({ image: vidG });
                        if (currentPoseLM) ghostHistory.push(currentPoseLM);
                        await new Promise(r => setTimeout(r, 10));
                    }
                    draw();
                });

                const POSE_LINES = [[11,12],[11,23],[12,24],[23,24],[11,13],[13,15],[12,14],[14,16],[23,25],[25,27],[24,26],[26,28]];
                function draw() {
                    ctxG.clearRect(0, 0, cvsG.width, cvsG.height);
                    const w = cvsG.width, h = cvsG.height;
                    ghostHistory.forEach((lm, idx) => {
                        ctxG.save(); ctxG.globalAlpha = 0.2 + (idx / ghostHistory.length) * 0.6;
                        ctxG.strokeStyle = '#2563eb'; ctxG.lineWidth = 2;
                        POSE_LINES.forEach(([i, j]) => { if (lm[i] && lm[j]) { ctxG.beginPath(); ctxG.moveTo(lm[i].x * w, lm[i].y * h); ctxG.lineTo(lm[j].x * w, lm[j].y * h); ctxG.stroke(); } });
                        ctxG.restore();
                    });
                    if (currentPoseLM) {
                        ctxG.strokeStyle = '#059669'; ctxG.lineWidth = 3;
                        POSE_LINES.forEach(([i, j]) => { if (currentPoseLM[i] && currentPoseLM[j]) { ctxG.beginPath(); ctxG.moveTo(currentPoseLM[i].x * w, currentPoseLM[i].y * h); ctxG.lineTo(currentPoseLM[j].x * w, currentPoseLM[j].y * h); ctxG.stroke(); } });
                    }
                }
                document.getElementById('btnClearG').addEventListener('click', () => { ghostHistory = []; draw(); });
                document.getElementById('btnSnapGhostImg').addEventListener('click', () => {
                    const off = document.createElement('canvas'); off.width = vidG.videoWidth; off.height = vidG.videoHeight;
                    const offCtx = off.getContext('2d'); offCtx.drawImage(vidG, 0, 0, off.width, off.height); offCtx.drawImage(cvsG, 0, 0, off.width, off.height);
                    const textAreas = window.parent.document.querySelectorAll('textarea');
                    let targetGhostTA = null;
                    textAreas.forEach(ta => { if ((ta.getAttribute('aria-label') || '').includes('ghost_img_b64')) targetGhostTA = ta; });
                    if (targetGhostTA) { syncToStreamlit(targetGhostTA, off.toDataURL('image/jpeg', 0.85)); alert("殘影畫面已成功擷取！"); }
                });
            </script>
        </body>
        </html>"""

        strobe_html_code = strobe_html_template.replace("___MIME_STROBE___", mime_strobe).replace("___B64_STROBE___", b64_strobe)
        components.html(strobe_html_code, height=720, scrolling=False)

        st.markdown("---")
        captured_ghost_img = st.text_area("ghost_img_b64", key="captured_ghost_img", label_visibility="collapsed")
        if captured_ghost_img:
            st.image(captured_ghost_img, caption="殘影畫面合成圖", use_container_width=True)
            if st.session_state["user_info"]:
                with st.form("save_ghost_form"):
                    rec_title_ghost = st.text_input("紀錄名稱", value=f"攻欄至落地殘影軌跡 {uploaded_strobe_file.name}")
                    if st.form_submit_button("儲存至團隊資料庫"):
                        u = st.session_state["user_info"]
                        save_record(u["username"], u["team_code"], "攻欄至落地殘影軌跡 Ghosting Track", rec_title_ghost, {"image_b64": captured_ghost_img, "saved_at": str(datetime.datetime.now())})
                        st.success("儲存成功！")

# ==================== 【頁面 6：雙影同步 Dual Video Sync】 ====================
elif page == "雙影同步 Dual Video Sync":
    st.title("🎬 雙影同步與關鍵影格角度分析 Dual Video Sync & Angle Analysis")
    st.markdown("---")

    col_f1, col_f2 = st.columns(2)
    with col_f1:
        file_a = st.file_uploader("上傳影片 A (如：左側 / 舊紀錄)", type=["mp4", "mov", "avi"], key="dual_v_a")
    with col_f2:
        file_b = st.file_uploader("上傳影片 B (如：右側 / 新紀錄)", type=["mp4", "mov", "avi"], key="dual_v_b")

    if file_a and file_b:
        with st.spinner("正在進行手機影片自動轉碼與雙影解碼 (H.264 MP4)..."):
            bytes_a, mime_a = process_and_convert_video(file_a)
            bytes_b, mime_b = process_and_convert_video(file_b)
            b64_a = base64.b64encode(bytes_a).decode("utf-8")
            b64_b = base64.b64encode(bytes_b).decode("utf-8")

        tab_sync_play, tab_dual_angle = st.tabs([
            "📹 雙影對齊與相對同步播放",
            "📐 雙影畫面擷取與角度分析"
        ])

        with tab_sync_play:
            dual_play_html_template = """<!DOCTYPE html>
            <html>
            <head>
                <style>
                    * { box-sizing: border-box; }
                    body { font-family: -apple-system, sans-serif; background: transparent; color: #0f172a; margin: 0; padding: 0; }
                    .main-slider-box { background: #ffffff; border: 2px solid #2563eb; padding: 12px 16px; border-radius: 10px; margin-bottom: 14px; box-shadow: 0 2px 4px rgba(37,99,235,0.1); }
                    .top-bar { display: flex; gap: 8px; margin-bottom: 12px; background: #ffffff; padding: 10px 14px; border-radius: 8px; border: 1px solid #e2e8f0; align-items: center; justify-content: center; flex-wrap: wrap; }
                    .btn-align-play { background: #059669; color: #fff; border: none; padding: 8px 16px; border-radius: 6px; font-weight: bold; cursor: pointer; font-size: 13px; }
                    .btn-align-jump { background: #d97706; color: #fff; border: none; padding: 8px 14px; border-radius: 6px; font-weight: bold; cursor: pointer; font-size: 13px; }
                    .btn-sync { background: #2563eb; color: #fff; border: none; padding: 8px 16px; border-radius: 6px; font-weight: bold; cursor: pointer; font-size: 13px; }
                    .btn-reset { background: #f1f5f9; color: #334155; border: 1px solid #cbd5e1; padding: 8px 14px; border-radius: 6px; font-weight: bold; cursor: pointer; font-size: 13px; }
                    .layout { display: flex; gap: 14px; width: 100%; }
                    .vid-card { flex: 1; background: #ffffff; border-radius: 10px; border: 1px solid #e2e8f0; padding: 12px; }
                    video { width: 100%; border-radius: 6px; max-height: 360px; object-fit: contain; background: #000; display: block; }
                    .ctrl-row { display: flex; gap: 6px; margin-top: 8px; align-items: center; flex-wrap: wrap; }
                    .btn-sm { background: #f1f5f9; color: #334155; border: 1px solid #cbd5e1; padding: 5px 8px; border-radius: 4px; font-size: 12px; font-weight: bold; cursor: pointer; }
                    .btn-set-align { background: #2563eb; color: #fff; border: none; padding: 6px 12px; border-radius: 4px; font-size: 12px; font-weight: bold; cursor: pointer; }
                    select { background: #ffffff; color: #0f172a; border: 1px solid #cbd5e1; padding: 4px; border-radius: 4px; font-size: 12px; font-weight: bold; }
                    input[type=range] { width: 100%; height: 6px; border-radius: 3px; background: #cbd5e1; outline: none; cursor: pointer; }
                </style>
            </head>
            <body>
                <div class="main-slider-box">
                    <div style="display:flex; justify-content:space-between; margin-bottom:6px; font-size:13px;">
                        <span style="color:#2563eb; font-weight:bold;">🔗 雙影片相對對齊同步控制滑桿 (Master Sync Slider)</span>
                        <span>相對對齊時間: <b id="txtMasterTime" style="color:#059669; font-size:15px;">0.00s</b></span>
                    </div>
                    <input type="range" id="sliderMaster" min="0" max="100" value="0" step="0.01">
                </div>

                <div class="top-bar">
                    <button class="btn-align-play" onclick="playFromAlign()">▶ 從對齊點同步播放</button>
                    <button class="btn-align-jump" onclick="jumpToAlign()">🎯 跳至各自對齊點</button>
                    <button class="btn-sync" onclick="togglePlayBoth()">⏯ 同步播放 / 暫停</button>
                    <button class="btn-reset" onclick="resetTimes()">↺ 時間歸零</button>
                </div>
                
                <div class="layout">
                    <div class="vid-card">
                        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
                            <h5 style="margin:0; color:#2563eb; font-size:14px;">影片 A</h5>
                            <span style="font-size:12px; color:#64748b;">時間: <b id="txtTimeA" style="color:#2563eb; font-size:13px;">0.00s</b></span>
                        </div>
                        <video id="vidA" muted playsinline controls></video>
                        <input type="range" id="sliderA" min="0" max="100" value="0" step="0.01" style="margin-top:6px;">
                        <div class="ctrl-row">
                            <button class="btn-set-align" onclick="setAlignA()">📌 設為 A 對齊點</button>
                            <button class="btn-sm" onclick="stepA(-0.033)">-1幀</button>
                            <button class="btn-sm" onclick="stepA(0.033)">+1幀</button>
                            <span style="font-size:11px; color:#64748b; margin-left:auto;">速度:</span>
                            <select id="selSpeedA" onchange="vidA.playbackRate = parseFloat(this.value)">
                                <option value="0.25">0.25x</option>
                                <option value="0.5">0.5x</option>
                                <option value="0.75">0.75x</option>
                                <option value="1.0" selected>1.0x</option>
                            </select>
                        </div>
                        <div id="txtAlignA" style="margin-top:8px; font-size:12px; color:#059669; font-weight:bold;">📍 影片 A 對齊點：未設定 (0.00s)</div>
                    </div>

                    <div class="vid-card">
                        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
                            <h5 style="margin:0; color:#d97706; font-size:14px;">影片 B</h5>
                            <span style="font-size:12px; color:#64748b;">時間: <b id="txtTimeB" style="color:#d97706; font-size:13px;">0.00s</b></span>
                        </div>
                        <video id="vidB" muted playsinline controls></video>
                        <input type="range" id="sliderB" min="0" max="100" value="0" step="0.01" style="margin-top:6px;">
                        <div class="ctrl-row">
                            <button class="btn-set-align" style="background:#d97706;" onclick="setAlignB()">📌 設為 B 對齊點</button>
                            <button class="btn-sm" onclick="stepB(-0.033)">-1幀</button>
                            <button class="btn-sm" onclick="stepB(0.033)">+1幀</button>
                            <span style="font-size:11px; color:#64748b; margin-left:auto;">速度:</span>
                            <select id="selSpeedB" onchange="vidB.playbackRate = parseFloat(this.value)">
                                <option value="0.25">0.25x</option>
                                <option value="0.5">0.5x</option>
                                <option value="0.75">0.75x</option>
                                <option value="1.0" selected>1.0x</option>
                            </select>
                        </div>
                        <div id="txtAlignB" style="margin-top:8px; font-size:12px; color:#059669; font-weight:bold;">📍 影片 B 對齊點：未設定 (0.00s)</div>
                    </div>
                </div>

                <script>
                    const vidA = document.getElementById('vidA'), vidB = document.getElementById('vidB');
                    const sliderA = document.getElementById('sliderA'), sliderB = document.getElementById('sliderB');
                    const sliderMaster = document.getElementById('sliderMaster');
                    const txtMasterTime = document.getElementById('txtMasterTime');
                    const txtTimeA = document.getElementById('txtTimeA'), txtTimeB = document.getElementById('txtTimeB');
                    const txtAlignA = document.getElementById('txtAlignA'), txtAlignB = document.getElementById('txtAlignB');

                    let alignTimeA = 0, alignTimeB = 0;

                    function b64ToBlob(b64Data, contentType) {
                        const byteCharacters = atob(b64Data);
                        const sliceSize = 1024 * 1024; const byteArrays = [];
                        for (let offset = 0; offset < byteCharacters.length; offset += sliceSize) {
                            const slice = byteCharacters.slice(offset, offset + sliceSize);
                            const byteNumbers = new Array(slice.length);
                            for (let i = 0; i < slice.length; i++) byteNumbers[i] = slice.charCodeAt(i);
                            byteArrays.push(new Uint8Array(byteNumbers));
                        }
                        return new Blob(byteArrays, {type: contentType});
                    }

                    try {
                        vidA.src = URL.createObjectURL(b64ToBlob("___B64_A___", "___MIME_A___"));
                        vidB.src = URL.createObjectURL(b64ToBlob("___B64_B___", "___MIME_B___"));
                    } catch(e) { console.error(e); }

                    function updateMasterSliderMax() {
                        const durA = (vidA.duration || 0) - alignTimeA;
                        const durB = (vidB.duration || 0) - alignTimeB;
                        const maxSyncDur = Math.max(0, Math.max(durA, durB));
                        sliderMaster.max = maxSyncDur > 0 ? maxSyncDur : 100;
                    }

                    vidA.addEventListener('loadedmetadata', () => { sliderA.max = vidA.duration; updateMasterSliderMax(); });
                    vidB.addEventListener('loadedmetadata', () => { sliderB.max = vidB.duration; updateMasterSliderMax(); });

                    vidA.addEventListener('timeupdate', () => {
                        txtTimeA.innerText = vidA.currentTime.toFixed(2) + 's';
                        if (!vidA.paused) {
                            sliderA.value = vidA.currentTime;
                            const relT = Math.max(0, vidA.currentTime - alignTimeA);
                            sliderMaster.value = relT;
                            txtMasterTime.innerText = relT.toFixed(2) + 's';
                        }
                    });

                    vidB.addEventListener('timeupdate', () => {
                        txtTimeB.innerText = vidB.currentTime.toFixed(2) + 's';
                        if (!vidB.paused) sliderB.value = vidB.currentTime;
                    });

                    sliderA.addEventListener('input', function() { vidA.currentTime = parseFloat(this.value); });
                    sliderB.addEventListener('input', function() { vidB.currentTime = parseFloat(this.value); });

                    sliderMaster.addEventListener('input', function() {
                        const relOffset = parseFloat(this.value);
                        txtMasterTime.innerText = relOffset.toFixed(2) + 's';
                        vidA.currentTime = Math.min(vidA.duration, Math.max(0, alignTimeA + relOffset));
                        vidB.currentTime = Math.min(vidB.duration, Math.max(0, alignTimeB + relOffset));
                        sliderA.value = vidA.currentTime;
                        sliderB.value = vidB.currentTime;
                    });

                    function setAlignA() {
                        alignTimeA = vidA.currentTime;
                        txtAlignA.innerText = `📍 影片 A 對齊點：${alignTimeA.toFixed(2)}s`;
                        updateMasterSliderMax();
                    }

                    function setAlignB() {
                        alignTimeB = vidB.currentTime;
                        txtAlignB.innerText = `📍 影片 B 對齊點：${alignTimeB.toFixed(2)}s`;
                        updateMasterSliderMax();
                    }

                    function stepA(val) { vidA.pause(); vidA.currentTime = Math.max(0, Math.min(vidA.duration, vidA.currentTime + val)); sliderA.value = vidA.currentTime; }
                    function stepB(val) { vidB.pause(); vidB.currentTime = Math.max(0, Math.min(vidB.duration, vidB.currentTime + val)); sliderB.value = vidB.currentTime; }

                    function playFromAlign() {
                        vidA.pause(); vidB.pause();
                        vidA.currentTime = alignTimeA;
                        vidB.currentTime = alignTimeB;
                        sliderMaster.value = 0;
                        txtMasterTime.innerText = '0.00s';
                        vidA.play(); vidB.play();
                    }

                    function jumpToAlign() {
                        vidA.pause(); vidB.pause();
                        vidA.currentTime = alignTimeA;
                        vidB.currentTime = alignTimeB;
                        sliderMaster.value = 0;
                        txtMasterTime.innerText = '0.00s';
                    }

                    function togglePlayBoth() {
                        if (vidA.paused || vidB.paused) { vidA.play(); vidB.play(); }
                        else { vidA.pause(); vidB.pause(); }
                    }

                    function resetTimes() {
                        vidA.pause(); vidB.pause();
                        vidA.currentTime = 0; vidB.currentTime = 0;
                        sliderMaster.value = 0;
                        txtMasterTime.innerText = '0.00s';
                    }
                </script>
            </body>
            </html>"""

            dual_play_html_code = (
                dual_play_html_template
                .replace("___B64_A___", b64_a).replace("___MIME_A___", mime_a)
                .replace("___B64_B___", b64_b).replace("___MIME_B___", mime_b)
            )
            components.html(dual_play_html_code, height=580, scrolling=False)

        with tab_dual_angle:
            st.info("💡 **操作指南**：在兩部影片播放器中各自暫停於關鍵動作點，點擊「📷 擷取畫面至畫布」，接著用滑鼠在畫布上連續點擊 3 點標記關節角度，最後點擊「🚀 合成兩圖與角度數據」。")
            
            dual_angle_html_template = """<!DOCTYPE html>
            <html>
            <head>
                <style>
                    * { box-sizing: border-box; }
                    body { font-family: -apple-system, sans-serif; background: transparent; color: #0f172a; margin: 0; padding: 0; }
                    .angle-container { display: flex; gap: 14px; width: 100%; }
                    .card { flex: 1; background: #ffffff; padding: 12px; border-radius: 10px; border: 1px solid #e2e8f0; text-align: center; }
                    video { width: 100%; max-height: 240px; background: #000; border-radius: 6px; object-fit: contain; }
                    canvas { width: 100%; background: #0f172a; border-radius: 6px; cursor: crosshair; margin-top: 8px; }
                    .btn-group { display: flex; gap: 6px; justify-content: center; margin-top: 6px; flex-wrap: wrap; }
                    button { padding: 6px 12px; border-radius: 4px; border: none; cursor: pointer; font-weight: bold; font-size: 12px; }
                    .btn-cap { background: #2563eb; color: white; }
                    .btn-clr { background: #ef4444; color: white; }
                    .btn-sm { background: #f1f5f9; color: #334155; border: 1px solid #cbd5e1; }
                    .btn-upload { background: #059669; color: white; font-size: 14px; padding: 10px 24px; margin-top: 14px; border-radius: 6px; cursor: pointer; border: none; font-weight: bold; }
                    .angle-info { background: #f8fafc; padding: 6px; border-radius: 4px; font-size: 12px; margin-top: 6px; color: #2563eb; font-weight: bold; border: 1px solid #cbd5e1; }
                </style>
            </head>
            <body>
                <div class="angle-container">
                    <div class="card">
                        <div style="font-weight:bold; font-size:13px; color:#2563eb; margin-bottom:4px;">影片 A 關鍵畫面</div>
                        <video id="vSrcA" controls playsinline></video>
                        <div class="btn-group">
                            <button class="btn-sm" onclick="stepSrc('A', -0.033)">-1幀</button>
                            <button class="btn-sm" onclick="stepSrc('A', 0.033)">+1幀</button>
                            <button class="btn-cap" onclick="capFrame('A')">📷 擷取當前畫面至畫布 A</button>
                            <button class="btn-clr" onclick="clearPts('A')">↺ 重置 A 角度</button>
                        </div>
                        <canvas id="canA" width="480" height="320"></canvas>
                        <div class="angle-info" id="infoA">角度：尚未量測 (請先擷取畫面並點擊 3 點)</div>
                    </div>

                    <div class="card">
                        <div style="font-weight:bold; font-size:13px; color:#d97706; margin-bottom:4px;">影片 B 關鍵畫面</div>
                        <video id="vSrcB" controls playsinline></video>
                        <div class="btn-group">
                            <button class="btn-sm" onclick="stepSrc('B', -0.033)">-1幀</button>
                            <button class="btn-sm" onclick="stepSrc('B', 0.033)">+1幀</button>
                            <button class="btn-cap" style="background:#d97706;" onclick="capFrame('B')">📷 擷取當前畫面至畫布 B</button>
                            <button class="btn-clr" onclick="clearPts('B')">↺ 重置 B 角度</button>
                        </div>
                        <canvas id="canB" width="480" height="320"></canvas>
                        <div class="angle-info" id="infoB">角度：尚未量測 (請先擷取畫面並點擊 3 點)</div>
                    </div>
                </div>

                <div style="text-align:center;">
                    <button class="btn-upload" onclick="mergeAndUpload()">🚀 合成兩圖與角度數據並傳送至系統</button>
                </div>

                <script>
                    const vSrcA = document.getElementById('vSrcA'), vSrcB = document.getElementById('vSrcB');
                    const canA = document.getElementById('canA'), ctxA = canA.getContext('2d');
                    const canB = document.getElementById('canB'), ctxB = canB.getContext('2d');

                    let ptsA = [], ptsB = [];
                    let angleA = null, angleB = null;
                    let imgDataA = null, imgDataB = null;

                    function b64ToBlob(b64Data, contentType) {
                        const byteCharacters = atob(b64Data);
                        const sliceSize = 1024 * 1024; const byteArrays = [];
                        for (let offset = 0; offset < byteCharacters.length; offset += sliceSize) {
                            const slice = byteCharacters.slice(offset, offset + sliceSize);
                            const byteNumbers = new Array(slice.length);
                            for (let i = 0; i < slice.length; i++) byteNumbers[i] = slice.charCodeAt(i);
                            byteArrays.push(new Uint8Array(byteNumbers));
                        }
                        return new Blob(byteArrays, {type: contentType});
                    }

                    try {
                        vSrcA.src = URL.createObjectURL(b64ToBlob("___B64_A___", "___MIME_A___"));
                        vSrcB.src = URL.createObjectURL(b64ToBlob("___B64_B___", "___MIME_B___"));
                    } catch(e) { console.error(e); }

                    function stepSrc(tag, val) {
                        const v = (tag === 'A') ? vSrcA : vSrcB;
                        v.pause(); v.currentTime = Math.max(0, Math.min(v.duration, v.currentTime + val));
                    }

                    function capFrame(tag) {
                        const v = (tag === 'A') ? vSrcA : vSrcB;
                        const c = (tag === 'A') ? canA : canB;
                        const ctx = (tag === 'A') ? ctxA : ctxB;
                        
                        ctx.fillStyle = '#0f172a';
                        ctx.fillRect(0, 0, c.width, c.height);
                        
                        if (v.videoWidth && v.videoHeight) {
                            const vAspect = v.videoWidth / v.videoHeight;
                            const cAspect = c.width / c.height;
                            let drawW = c.width, drawH = c.height, startX = 0, startY = 0;
                            if (vAspect > cAspect) {
                                drawH = c.width / vAspect;
                                startY = (c.height - drawH) / 2;
                            } else {
                                drawW = c.height * vAspect;
                                startX = (c.width - drawW) / 2;
                            }
                            ctx.drawImage(v, startX, startY, drawW, drawH);
                        } else {
                            ctx.drawImage(v, 0, 0, c.width, c.height);
                        }

                        if (tag === 'A') {
                            imgDataA = ctx.getImageData(0, 0, c.width, c.height);
                            ptsA = []; angleA = null;
                            document.getElementById('infoA').innerText = '已擷取畫面，請在畫布上連續點擊 3 點標記關節';
                        } else {
                            imgDataB = ctx.getImageData(0, 0, c.width, c.height);
                            ptsB = []; angleB = null;
                            document.getElementById('infoB').innerText = '已擷取畫面，請在畫布上連續點擊 3 點標記關節';
                        }
                    }

                    function clearPts(tag) {
                        const ctx = (tag === 'A') ? ctxA : ctxB;
                        const img = (tag === 'A') ? imgDataA : imgDataB;
                        if (img) ctx.putImageData(img, 0, 0);
                        if (tag === 'A') { ptsA = []; angleA = null; document.getElementById('infoA').innerText = '角度：尚未量測'; }
                        else { ptsB = []; angleB = null; document.getElementById('infoB').innerText = '角度：尚未量測'; }
                    }

                    function addClickEvent(can, tag) {
                        can.addEventListener('click', function(e) {
                            const rect = can.getBoundingClientRect();
                            const x = (e.clientX - rect.left) * (can.width / rect.width);
                            const y = (e.clientY - rect.top) * (can.height / rect.height);
                            const pts = (tag === 'A') ? ptsA : ptsB;

                            if (pts.length < 3) {
                                pts.push({x, y});
                                drawPts(tag);
                            }
                        });
                    }
                    addClickEvent(canA, 'A');
                    addClickEvent(canB, 'B');

                    function drawPts(tag) {
                        const c = (tag === 'A') ? canA : canB;
                        const ctx = (tag === 'A') ? ctxA : ctxB;
                        const img = (tag === 'A') ? imgDataA : imgDataB;
                        const pts = (tag === 'A') ? ptsA : ptsB;

                        if (img) ctx.putImageData(img, 0, 0);

                        ctx.strokeStyle = '#ef4444'; ctx.lineWidth = 3; ctx.fillStyle = '#f59e0b';
                        for(let i=0; i<pts.length; i++) {
                            ctx.beginPath(); ctx.arc(pts[i].x, pts[i].y, 5, 0, Math.PI*2); ctx.fill(); ctx.stroke();
                        }

                        if (pts.length === 2) {
                            ctx.beginPath(); ctx.moveTo(pts[0].x, pts[0].y); ctx.lineTo(pts[1].x, pts[1].y); ctx.stroke();
                        } else if (pts.length === 3) {
                            ctx.beginPath(); ctx.moveTo(pts[0].x, pts[0].y); ctx.lineTo(pts[1].x, pts[1].y); ctx.lineTo(pts[2].x, pts[2].y); ctx.stroke();

                            const rad1 = Math.atan2(pts[0].y - pts[1].y, pts[0].x - pts[1].x);
                            const rad2 = Math.atan2(pts[2].y - pts[1].y, pts[2].x - pts[1].x);
                            let deg = Math.abs((rad1 - rad2) * (180 / Math.PI));
                            if (deg > 180) deg = 360 - deg;
                            deg = Math.round(deg * 10) / 10;

                            if (tag === 'A') angleA = deg; else angleB = deg;

                            ctx.fillStyle = '#38bdf8'; ctx.font = 'bold 16px sans-serif';
                            ctx.fillText(deg + '°', pts[1].x + 10, pts[1].y - 10);
                            document.getElementById((tag==='A')?'infoA':'infoB').innerText = '關節夾角量測結果：' + deg + '°';
                        }
                    }

                    function syncVal(el, val) {
                        if (!el) return;
                        const pWin = window.parent;
                        try {
                            const nativeSetter = pWin.Object.getOwnPropertyDescriptor(pWin.HTMLTextAreaElement.prototype, "value").set;
                            nativeSetter.call(el, val);
                            if (el._valueTracker) el._valueTracker.setValue('');
                            el.focus();
                            el.dispatchEvent(new pWin.Event('input', { bubbles: true }));
                            el.dispatchEvent(new pWin.Event('change', { bubbles: true }));
                            el.blur();
                        } catch(err) {
                            el.value = val;
                            el.dispatchEvent(new Event('input', { bubbles: true }));
                            el.dispatchEvent(new Event('change', { bubbles: true }));
                        }
                    }

                    function mergeAndUpload() {
                        const outCan = document.createElement('canvas');
                        outCan.width = 960; outCan.height = 360;
                        const oCtx = outCan.getContext('2d');
                        oCtx.fillStyle = '#0f172a'; oCtx.fillRect(0,0,960,360);

                        oCtx.drawImage(canA, 0, 20, 470, 320);
                        oCtx.drawImage(canB, 490, 20, 470, 320);

                        oCtx.fillStyle = '#ffffff'; oCtx.font = 'bold 14px sans-serif';
                        oCtx.fillText('影片 A (時間: ' + vSrcA.currentTime.toFixed(2) + 's, 角度: ' + (angleA ? angleA + '°' : '未量測') + ')', 10, 15);
                        oCtx.fillText('影片 B (時間: ' + vSrcB.currentTime.toFixed(2) + 's, 角度: ' + (angleB ? angleB + '°' : '未量測') + ')', 500, 15);

                        const mergedB64 = outCan.toDataURL('image/png');
                        const jsonPayload = JSON.stringify({
                            time_A: vSrcA.currentTime.toFixed(2),
                            time_B: vSrcB.currentTime.toFixed(2),
                            angle_A: angleA,
                            angle_B: angleB,
                            timestamp: new Date().toISOString()
                        });

                        const textAreas = window.parent.document.querySelectorAll('textarea');
                        let targetImgBox = null, targetJsonBox = null;
                        textAreas.forEach(ta => {
                            const label = ta.getAttribute('aria-label') || '';
                            if (label.includes('dual_img_b64')) targetImgBox = ta;
                            if (label.includes('dual_data_json')) targetJsonBox = ta;
                        });

                        if (!targetImgBox && textAreas.length >= 2) targetImgBox = textAreas[0];
                        if (!targetJsonBox && textAreas.length >= 2) targetJsonBox = textAreas[1];

                        if (targetImgBox && targetJsonBox) {
                            syncVal(targetImgBox, mergedB64);
                            syncVal(targetJsonBox, jsonPayload);
                            alert("雙影對比畫面與角度數據已成功傳送至系統！");
                        } else {
                            alert("已擷取數據，請於下方預覽區確認！");
                        }
                    }
                </script>
            </body>
            </html>"""

            dual_angle_html_code = (
                dual_angle_html_template
                .replace("___B64_A___", b64_a).replace("___MIME_A___", mime_a)
                .replace("___B64_B___", b64_b).replace("___MIME_B___", mime_b)
            )
            components.html(dual_angle_html_code, height=760, scrolling=False)

        st.markdown("---")
        captured_dual_img = st.text_area("dual_img_b64", key="captured_dual_img", label_visibility="collapsed")
        captured_dual_json = st.text_area("dual_data_json", key="captured_dual_json", label_visibility="collapsed")

        if captured_dual_img and captured_dual_json:
            st.success("📸 已成功擷取並合成雙影角度分析畫面！")
            st.image(captured_dual_img, caption="雙影對比與角度量測合成圖", use_container_width=True)

            if st.session_state["user_info"]:
                with st.form("save_dual_angle_form"):
                    rec_title_dual = st.text_input("紀錄名稱", value=f"雙影角度對比 {file_a.name} vs {file_b.name}")
                    if st.form_submit_button("💾 儲存至團隊數據庫", use_container_width=True):
                        u = st.session_state["user_info"]
                        try:
                            parsed_metrics = json.loads(captured_dual_json)
                        except Exception:
                            parsed_metrics = {"raw_json": captured_dual_json}

                        save_record(
                            u["username"],
                            "雙影同步 Dual Video Sync",
                            rec_title_dual,
                            {
                                "image_b64": captured_dual_img, 
                                "angle_metrics": parsed_metrics, 
                                "saved_at": str(datetime.datetime.now())
                            }
                        )
                        st.success("成功儲存至團隊歷史數據庫！")
                        st.rerun()

# ==================== 【頁面 7：跨期數據對比 (精簡雙指標)】 ====================
elif page == "跨期數據對比 Longitudinal Comparison":
    st.title("跨期數據對比分析 Longitudinal Comparison Analysis")
    st.markdown("---")

    if st.session_state["user_info"] is None:
        st.warning("請先登入帳號")
    else:
        u = st.session_state["user_info"]
        team_code = u["team_code"]
        role = u["role"]

        if role in ["coach", "trainer"]:
            team_athletes = get_team_athletes(team_code)
            target_user = st.selectbox("請選擇選手 Select Athlete：", team_athletes) if team_athletes else None
        else:
            target_user = u["username"]

        if target_user:
            mod_choice = st.radio("請選擇對比項目：", ["欄間秒數及速度計算 Hurdle Time & Speed Calculation", "關鍵影格角度量測 Keyframe Angle Measurement"])
            records = get_user_records(team_code, username=target_user, module=mod_choice)

            if not records:
                st.warning(f"選手 {target_user} 在團隊中尚無 {mod_choice} 的歷史紀錄。")
            else:
                title_counts = {}
                rec_options = {}
                for r in records:
                    t = r[3]
                    if t in title_counts:
                        title_counts[t] += 1
                        disp_key = f"{t} ({title_counts[t]})"
                    else:
                        title_counts[t] = 1
                        disp_key = t
                    rec_options[disp_key] = r

                selected_keys = st.multiselect("請選擇 2 至 5 筆紀錄進行對照：", list(rec_options.keys()))

                if len(selected_keys) >= 2:
                    st.markdown("---")
                    st.subheader("多日數據對比明細")

                    if mod_choice == "欄間秒數及速度計算 Hurdle Time & Speed Calculation":
                        chart_metric = st.radio(
                            "選擇折線圖展示指標：", 
                            ["分段平均速度 (m/s)", "單段耗時 (秒 s)"], 
                            horizontal=True
                        )

                        ORDERED_SEGMENTS = [
                            "起跑->H1", "H1->H2", "H2->H3", "H3->H4", "H4->H5",
                            "H5->H6", "H6->H7", "H7->H8", "H8->H9", "H9->H10", "H10->終點"
                        ]

                        plot_dict = {}
                        table_data = {}
                        for k in selected_keys:
                            rec = rec_options[k]
                            tm = json.loads(rec[4]).get("timer_metrics", {})
                            splits = tm.get("splits", [])
                            rec_label = k

                            for idx, s in enumerate(splits):
                                if idx < len(ORDERED_SEGMENTS):
                                    seg = ORDERED_SEGMENTS[idx]
                                else:
                                    seg = s.get("segment", f"段落{idx+1}")

                                try: split_sec = float(s.get("split_sec", "0"))
                                except ValueError: split_sec = 0.0

                                try: speed_ms = float(s.get("speed_ms", "0"))
                                except ValueError: speed_ms = 0.0

                                if seg not in plot_dict: plot_dict[seg] = {}
                                if seg not in table_data: table_data[seg] = {}

                                if "分段平均速度" in chart_metric:
                                    plot_dict[seg][rec_label] = round(speed_ms, 2)
                                else:
                                    plot_dict[seg][rec_label] = round(split_sec, 3)

                                table_data[seg][rec_label] = f"速度 {speed_ms:.2f} m/s ｜ 單段 {s.get('split_sec')}s ｜ 累積 {s.get('cum_sec')}s"

                        unit_label = "m/s" if "分段平均速度" in chart_metric else "s"
                        st.markdown(f"##### 跨期分段速度與耗時變化趨勢圖 (單位：{unit_label})")
                        if plot_dict:
                            df_plot = pd.DataFrame.from_dict(plot_dict, orient='index')
                            
                            existing_indices = df_plot.index.tolist()
                            sorted_index = [seg for seg in ORDERED_SEGMENTS if seg in existing_indices] + [seg for seg in existing_indices if seg not in ORDERED_SEGMENTS]
                            df_plot = df_plot.reindex(sorted_index)

                            df_alt = df_plot.reset_index().rename(columns={'index': 'Segment'})
                            df_melt = df_alt.melt(id_vars=['Segment'], var_name='紀錄 Title', value_name=f'數值 ({unit_label})')

                            chart = alt.Chart(df_melt).mark_line(point=True).encode(
                                x=alt.X('Segment:N', sort=ORDERED_SEGMENTS, title="比賽分段 Segment"),
                                y=alt.Y(f'數值 ({unit_label}):Q', title=f"數值 ({unit_label})"),
                                color='紀錄 Title:N',
                                tooltip=['Segment', '紀錄 Title', f'數值 ({unit_label})']
                            ).properties(height=420)

                            st.altair_chart(chart, use_container_width=True)

                        st.markdown("##### 各段落耗時與平均速度對照表")
                        if table_data:
                            df_table = pd.DataFrame.from_dict(table_data, orient='index')
                            existing_table_indices = df_table.index.tolist()
                            sorted_table_index = [seg for seg in ORDERED_SEGMENTS if seg in existing_table_indices] + [seg for seg in existing_table_indices if seg not in ORDERED_SEGMENTS]
                            df_table = df_table.reindex(sorted_table_index)
                            st.dataframe(df_table, use_container_width=True)

                    elif mod_choice == "關鍵影格角度量測 Keyframe Angle Measurement":
                        plot_dict_angle = {}
                        for k in selected_keys:
                            rec = rec_options[k]
                            am = json.loads(rec[4]).get("angle_metrics", {})
                            rec_label = k
                            angles = am.get("angles", []) if isinstance(am, dict) else am
                            if isinstance(angles, list):
                                for a in angles:
                                    name = a.get("name", "未知名稱")
                                    val_str = str(a.get("value", "0")).replace("°", "").strip()
                                    try: val = float(val_str)
                                    except ValueError: val = 0.0
                                    if rec_label not in plot_dict_angle: plot_dict_angle[rec_label] = {}
                                    plot_dict_angle[rec_label][name] = val

                        st.markdown("##### 跨期角度變化趨勢折線圖 Angle Progression Line Chart")
                        if plot_dict_angle:
                            st.line_chart(pd.DataFrame.from_dict(plot_dict_angle, orient='index'))

                        st.markdown("##### 跨期畫面與角度數據明細 Comparison Cards")
                        col_arr = st.columns(len(selected_keys))
                        for idx, k in enumerate(selected_keys):
                            rec = rec_options[k]
                            details = json.loads(rec[4])
                            with col_arr[idx]:
                                st.markdown(f"**紀錄標題：{rec[3]}**")
                                if "image_b64" in details:
                                    st.image(details["image_b64"], use_container_width=True)
                                am = details.get("angle_metrics", {})
                                angles = am.get("angles", []) if isinstance(am, dict) else am
                                st.write(f"時間：`{am.get('rel_time_sec', '0.00')} 秒`")
                                st.write(f"階段：`{am.get('phase_tag', '關鍵點')}`")
                                if isinstance(angles, list):
                                    for a in angles:
                                        st.write(f"• {a.get('name')}：`{a.get('value')}`")

# ==================== 【頁面 8：課表紀錄】 ====================
elif page == "課表紀錄 Training Plan":
    st.title("課表與訓練日誌紀錄 Training Plan & Daily Log")
    st.markdown("---")

    if st.session_state["user_info"] is None:
        st.warning("請先在左側邊欄登入帳號 Please Login in Sidebar First")
    else:
        user = st.session_state["user_info"]
        is_coach = (user["role"] == "coach")

        tab_new_plan, tab_view_plan = st.tabs([
            "新增與編輯訓練日誌 Add & Edit Training Log",
            "歷次課表與日誌總覽 Training Log History"
        ])

        with tab_new_plan:
            st.subheader("填寫訓練日誌內容 Fill Training Log Details")
            
            if is_coach:
                conn = get_db_connection()
                c = conn.cursor()
                c.execute("SELECT DISTINCT username FROM users WHERE role='player'")
                player_list = [r[0] for r in c.fetchall()]
                c.close()
                conn.close()
                if not player_list: player_list = [user["username"]]
                target_athlete = st.selectbox("指定選手 Select Athlete", player_list, key="plan_sel_athlete")
            else:
                target_athlete = user["username"]

            if "dyn_injuries_user" not in st.session_state or st.session_state["dyn_injuries_user"] != target_athlete:
                try:
                    prev_plans = get_training_plans(target_athlete)
                except Exception:
                    try:
                        prev_plans = get_training_plans(username=target_athlete)
                    except Exception:
                        prev_plans = []

                carried_injuries = []
                if prev_plans:
                    last_plan = prev_plans[-1]
                    try:
                        parsed_last = json.loads(last_plan[6])
                        last_inj_list = parsed_last.get("injuries_list", [])
                        for inj in last_inj_list:
                            if inj.get("area"):
                                carried_injuries.append({
                                    "area": inj.get("area"),
                                    "level": inj.get("level", 0),
                                    "pain_types": inj.get("pain_types", []),
                                    "notes": inj.get("notes", "")
                                })
                    except Exception:
                        pass

                if not carried_injuries:
                    carried_injuries = [{"area": "右大腿股四頭肌 Right Quadriceps", "level": 0, "pain_types": [], "notes": ""}]

                st.session_state["dyn_injuries"] = carried_injuries
                st.session_state["dyn_injuries_user"] = target_athlete

            BODY_PART_OPTIONS = [
                "頭頸部 Head & Neck", "右肩/三角肌 Right Shoulder Deltoid", "左肩/三角肌 Left Shoulder Deltoid",
                "胸大肌 Pectoralis Major", "腹直肌/核心 Rectus Abdominis", "下背/腰椎肌群 Lower Back Lumbar",
                "臀大肌 Gluteus Maximus", "右大腿股四頭肌 Right Quadriceps", "左大腿股四頭肌 Left Quadriceps",
                "右大腿後側膕繩肌 Right Hamstrings", "左大腿後側膕繩肌 Left Hamstrings", "右膝/髕腱 Right Patellar Tendon",
                "左膝/髕腱 Left Patellar Tendon", "右小腿/跟腱 Right Calf & Achilles", "左小腿/跟腱 Left Calf & Achilles",
                "右腳踝 Right Ankle", "左腳踝 Left Ankle", "右腳底 Right Plantar/Sole", "左腳底 Left Plantar/Sole"
            ]

            PAIN_NATURE_OPTIONS = [
                "延遲性酸痛 (DOMS)", "肌肉緊繃/僵硬", "拉扯痛/牽扯感", "撕裂痛",
                "銳痛/刺痛", "鈍痛/深層隱痛", "關節卡痛/摩擦感", "按壓痛",
                "腫脹壓迫痛", "灼熱感/發炎痛", "脈動痛/跳痛",
                "麻木感/麻痛", "放射痛/電擊感"
            ]

            captured_body_map_img = st.text_area("body_map_img_b64", key="captured_body_map_img", label_visibility="collapsed")

            st.markdown("##### 1. 基本訓練設定 Basic Plan Info")
            c_p1, c_p2, c_p3 = st.columns(3)
            with c_p1:
                plan_date = st.date_input("訓練日期 Training Date", value=datetime.date.today(), key="log_plan_date")
                st.text_input("選手名稱 Athlete Name", value=target_athlete, disabled=True, key="log_athlete_name")
            with c_p2:
                category = st.selectbox("訓練主題與類別", ["欄架技術 Hurdle Technique", "爆發力/重量 Explosive/Strength", "速耐力/心肺 Speed Endurance", "恢復/伸展 Recovery"], key="log_category")
                duration_min = st.number_input("訓練總時長 (分鐘 min)", min_value=10, max_value=300, value=90, step=5, key="log_duration")
            with c_p3:
                rpe_feeling = st.select_slider("本體感覺 RPE", options=["1-2 非常輕鬆", "3-4 輕鬆", "5-6 中等", "7-8 艱難", "9-10 極限"], value="5-6 中等", key="log_rpe")
                weather_env = st.text_input("氣候與場地環境 Weather", value="晴天 室外跑道 Sunny Outdoor", key="log_weather")

            st.markdown("---")
            st.markdown("##### 2. 體能與恢復狀態 Recovery Status & Physiology")
            c_r1, c_r2, c_r3 = st.columns([1, 1.2, 1])
            with c_r1:
                fatigue_score = st.slider("身體疲勞指數 Fatigue (1-10)", 1, 10, 3, key="log_fatigue")
            with c_r2:
                st.write("**昨夜睡眠總時長 Sleep Duration**")
                cs_h, cs_m = st.columns(2)
                with cs_h: sleep_hours = st.number_input("小時 hr", min_value=0, max_value=24, value=7, step=1, key="log_sleep_h")
                with cs_m: sleep_mins = st.number_input("分鐘 min", min_value=0, max_value=59, value=30, step=5, key="log_sleep_m")
                total_sleep_hr = sleep_hours + (sleep_mins / 60.0)
            with c_r3:
                resting_hr = st.number_input("靜息心率 Heart Rate (bpm)", min_value=40, max_value=220, value=65, key="log_hr")

            st.markdown("<br>", unsafe_allow_html=True)
            st.markdown("###### 傷害追蹤")

            injuries_input_data = []
            to_delete_idx = None
            for idx, item in enumerate(st.session_state["dyn_injuries"]):
                c_sel1, c_sel2, c_sel3, c_sel4 = st.columns([1.5, 1.2, 1.6, 1.2])
                with c_sel1:
                    default_area_idx = BODY_PART_OPTIONS.index(item["area"]) if item["area"] in BODY_PART_OPTIONS else 0
                    selected_area = st.selectbox(f"痛點部位 #{idx+1}", BODY_PART_OPTIONS, index=default_area_idx, key=f"sel_area_{idx}")
                with c_sel2:
                    selected_lvl = st.slider(f"疼痛等級 (VAS 0-10)", 0, 10, item["level"], key=f"sel_lvl_{idx}")
                    st.markdown("<br>", unsafe_allow_html=True)
                    if st.button("刪除此痛點", key=f"del_inj_{idx}"):
                        to_delete_idx = idx
                with c_sel3:
                    default_pts = item.get("pain_types", [])
                    if not isinstance(default_pts, list): default_pts = []
                    selected_pain_types = st.multiselect(
                        f"疼痛性質 (可多選)", 
                        options=PAIN_NATURE_OPTIONS, 
                        default=[p for p in default_pts if p in PAIN_NATURE_OPTIONS],
                        key=f"sel_ptypes_{idx}"
                    )
                with c_sel4:
                    selected_note = st.text_input(f"備註說明", value=item.get("notes", ""), key=f"sel_note_{idx}")

                injuries_input_data.append({
                    "id": idx + 1,
                    "area": selected_area,
                    "level": selected_lvl,
                    "pain_types": selected_pain_types,
                    "notes": selected_note
                })

            if to_delete_idx is not None:
                st.session_state["dyn_injuries"].pop(to_delete_idx)
                st.rerun()

            if st.button("新增身體部位痛點 Add Muscle Region", key="add_more_inj_btn"):
                st.session_state["dyn_injuries"].append({"area": "右膝/髕腱 Right Patellar Tendon", "level": 0, "pain_types": [], "notes": ""})
                st.rerun()

            st.markdown("---")
            st.markdown("##### 3. 課表內容與自主備註 Plan Details & Notes")
            content = st.text_area("課表細節與組數規劃 Plan Details & Sets", height=120, key="log_content")
            user_notes = st.text_area("動作感覺與教練叮嚀 Notesif st.button("儲存訓練日誌 Save Training Log", key="btn_save_training_log", use_container_width=True):
                if content.strip():
                    extra_log_info = {
                        "duration_min": duration_min, 
                        "rpe_feeling": rpe_feeling, 
                        "weather_env": weather_env,
                        "fatigue_score": fatigue_score, 
                        "sleep_hours": sleep_hours, 
                        "sleep_mins": sleep_mins,
                        "total_sleep_hr": round(total_sleep_hr, 2), 
                        "resting_hr": resting_hr,
                        "injuries_list": injuries_input_data,
                        "body_map_img_b64": captured_body_map_img,
                        "user_notes": user_notes
                    }
                    save_training_plan(
                        target_athlete,
                        user["team_code"],
                        str(plan_date),
                        category,
                        content,
                        rpe_feeling,
                        json.dumps(extra_log_info, ensure_ascii=False)
                    )
                    
                    if "dyn_injuries_user" in st.session_state:
                        del st.session_state["dyn_injuries_user"]
                    if "dyn_injuries" in st.session_state:
                        del st.session_state["dyn_injuries"]

                    st.success(f"成功儲存 {target_athlete} 於 {plan_date} 的訓練與精準傷病日誌！")
                    st.rerun()
                else:
                    st.warning("請填寫課表細節與組數規劃 Please fill plan details!")

            st.markdown("---")
            st.markdown("##### 獨立人體圖釘點輔助工具（點擊圖面即可即時同步存檔截圖）")

            body_map_html = """<!DOCTYPE html>
            <html>
            <head>
            <style>
                * { box-sizing: border-box; }
                body { font-family: -apple-system, sans-serif; background: transparent; margin: 0; padding: 0; text-align: center; color: #0f172a; }
                .map-outer-container { display: flex; justify-content: center; gap: 24px; background: #ffffff; padding: 16px; border-radius: 12px; border: 1px solid #cbd5e1; flex-wrap: wrap; box-shadow: 0 1px 4px rgba(0,0,0,0.05); }
                .body-card { width: 220px; background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 10px; padding: 12px; display: flex; flex-direction: column; align-items: center; }
                .body-title { font-size: 14px; font-weight: 700; color: #2563eb; margin-bottom: 8px; }
                .svg-wrapper { position: relative; width: 190px; height: 380px; cursor: crosshair; user-select: none; background: #ffffff; border-radius: 8px; border: 1px solid #cbd5e1; }
                .svg-wrapper svg { width: 100%; height: 100%; display: block; }
                .body-shape { fill: #f1f5f9; stroke: #475569; stroke-width: 1.5; stroke-linecap: round; stroke-linejoin: round; }
                .body-line { fill: none; stroke: #94a3b8; stroke-width: 1; }
                .pin-overlay { position: absolute; top: 0; left: 0; width: 100%; height: 100%; pointer-events: none; }
                .pin-marker { position: absolute; transform: translate(-50%, -50%); width: 20px; height: 20px; border-radius: 50%; background: #ef4444; color: #ffffff; font-size: 11px; font-weight: bold; display: flex; align-items: center; justify-content: center; box-shadow: 0 2px 4px rgba(0,0,0,0.2); pointer-events: auto; }
            </style>
            </head>
            <body>
                <div class="map-outer-container">
                    <div class="body-card">
                        <div class="body-title">正面肌肉輪廓 Front</div>
                        <div class="svg-wrapper" id="wFront">
                            <svg viewBox="0 0 200 400" id="svgF">
                                <circle cx="100" cy="35" r="20" class="body-shape"/>
                                <path d="M92,55 L92,65 L108,65 L108,55" class="body-shape"/>
                                <path d="M55,75 C70,70 130,70 145,75 L155,120 L140,125 L135,180 L125,230 L75,230 L65,180 L60,125 L45,120 Z" class="body-shape"/>
                                <path d="M45,120 L30,180 L40,185 L52,130 Z" class="body-shape"/>
                                <path d="M155,120 L170,180 L160,185 L148,130 Z" class="body-shape"/>
                                <path d="M75,230 L70,320 L65,380 L85,380 L92,320 L96,230 Z" class="body-shape"/>
                                <path d="M125,230 L130,320 L135,380 L115,380 L108,320 L104,230 Z" class="body-shape"/>
                                <line x1="100" y1="70" x2="100" y2="230" class="body-line"/>
                            </svg>
                            <div class="pin-overlay" id="overlayFront"></div>
                        </div>
                    </div>
                    <div class="body-card">
                        <div class="body-title">背面肌肉輪廓 Back</div>
                        <div class="svg-wrapper" id="wBack">
                            <svg viewBox="0 0 200 400" id="svgB">
                                <circle cx="100" cy="35" r="20" class="body-shape"/>
                                <path d="M92,55 L92,65 L108,65 L108,55" class="body-shape"/>
                                <path d="M55,75 C70,70 130,70 145,75 L155,120 L140,125 L135,180 L125,230 L75,230 L65,180 L60,125 L45,120 Z" class="body-shape"/>
                                <path d="M45,120 L30,180 L40,185 L52,130 Z" class="body-shape"/>
                                <path d="M155,120 L170,180 L160,185 L148,130 Z" class="body-shape"/>
                                <path d="M75,230 L70,320 L65,380 L85,380 L92,320 L96,230 Z" class="body-shape"/>
                                <path d="M125,230 L130,320 L135,380 L115,380 L108,320 L104,230 Z" class="body-shape"/>
                                <line x1="100" y1="70" x2="100" y2="230" class="body-line"/>
                            </svg>
                            <div class="pin-overlay" id="overlayBack"></div>
                        </div>
                    </div>
                </div>
                <script>
                    let mapPins = [];
                    const wf = document.getElementById('wFront'), wb = document.getElementById('wBack');
                    const of = document.getElementById('overlayFront'), ob = document.getElementById('overlayBack');

                    function syncToStreamlit(el, val) {
                        if (!el) return;
                        try {
                            const pWin = window.parent;
                            const nativeSetter = pWin.Object.getOwnPropertyDescriptor(pWin.HTMLTextAreaElement.prototype, "value").set;
                            nativeSetter.call(el, val);
                            if (el._valueTracker) el._valueTracker.setValue('');
                            el.focus();
                            el.dispatchEvent(new pWin.Event('input', { bubbles: true }));
                            el.dispatchEvent(new pWin.Event('change', { bubbles: true }));
                            el.blur();
                        } catch(err) {
                            el.value = val;
                            el.dispatchEvent(new Event('input', { bubbles: true }));
                            el.dispatchEvent(new Event('change', { bubbles: true }));
                        }
                    }

                    function drawBodySilhouette(ctx, ox, oy) {
                        ctx.save();
                        ctx.translate(ox, oy);

                        ctx.fillStyle = "#f1f5f9";
                        ctx.strokeStyle = "#475569";
                        ctx.lineWidth = 1.5;

                        ctx.beginPath(); ctx.arc(100, 35, 20, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
                        ctx.beginPath(); ctx.moveTo(92, 55); ctx.lineTo(92, 65); ctx.lineTo(108, 65); ctx.lineTo(108, 55); ctx.closePath(); ctx.fill(); ctx.stroke();
                        ctx.beginPath();
                        ctx.moveTo(55, 75); ctx.bezierCurveTo(70, 70, 130, 70, 145, 75);
                        ctx.lineTo(155, 120); ctx.lineTo(140, 125); ctx.lineTo(135, 180);
                        ctx.lineTo(125, 230); ctx.lineTo(75, 230); ctx.lineTo(65, 180);
                        ctx.lineTo(60, 125); ctx.lineTo(45, 120);
                        ctx.closePath(); ctx.fill(); ctx.stroke();

                        ctx.beginPath(); ctx.moveTo(45, 120); ctx.lineTo(30, 180); ctx.lineTo(40, 185); ctx.lineTo(52, 130); ctx.closePath(); ctx.fill(); ctx.stroke();
                        ctx.beginPath(); ctx.moveTo(155, 120); ctx.lineTo(170, 180); ctx.lineTo(160, 185); ctx.lineTo(148, 130); ctx.closePath(); ctx.fill(); ctx.stroke();
                        ctx.beginPath(); ctx.moveTo(75, 230); ctx.lineTo(70, 320); ctx.lineTo(65, 380); ctx.lineTo(85, 380); ctx.lineTo(92, 320); ctx.lineTo(96, 230); ctx.closePath(); ctx.fill(); ctx.stroke();
                        ctx.beginPath(); ctx.moveTo(125, 230); ctx.lineTo(130, 320); ctx.lineTo(135, 380); ctx.lineTo(115, 380); ctx.lineTo(108, 320); ctx.lineTo(104, 230); ctx.closePath(); ctx.fill(); ctx.stroke();

                        ctx.strokeStyle = "#94a3b8"; ctx.lineWidth = 1;
                        ctx.beginPath(); ctx.moveTo(100, 70); ctx.lineTo(100, 230); ctx.stroke();

                        ctx.restore();
                    }

                    function generateSnapshot() {
                        const cvs = document.createElement('canvas');
                        cvs.width = 440; cvs.height = 400;
                        const ctx = cvs.getContext('2d');
                        ctx.fillStyle = "#ffffff"; ctx.fillRect(0, 0, 440, 400);

                        ctx.fillStyle = "#2563eb"; ctx.font = "bold 14px sans-serif"; ctx.textAlign = "left";
                        ctx.fillText("正面 Front", 70, 20); ctx.fillText("背面 Back", 290, 20);

                        drawBodySilhouette(ctx, 10, 0);
                        drawBodySilhouette(ctx, 230, 0);

                        mapPins.forEach((p, idx) => {
                            let px = p.v === 'f' ? (10 + (p.x / 100) * 200) : (230 + (p.x / 100) * 200);
                            let py = (p.y / 100) * 400;
                            ctx.fillStyle = "#ef4444"; ctx.beginPath(); ctx.arc(px, py, 10, 0, Math.PI * 2); ctx.fill();
                            ctx.fillStyle = "#ffffff"; ctx.font = "bold 11px sans-serif"; ctx.textAlign = "center";
                            ctx.fillText(idx + 1, px, py + 4);
                        });

                        const imgB64 = cvs.toDataURL('image/jpeg', 0.85);
                        const textAreas = window.parent.document.querySelectorAll('textarea');
                        let targetMapTA = null;
                        textAreas.forEach(ta => {
                            if ((ta.getAttribute('aria-label') || '').includes('body_map_img_b64')) targetMapTA = ta;
                        });
                        if (targetMapTA) syncToStreamlit(targetMapTA, imgB64);
                    }

                    function render() {
                        of.innerHTML = ''; ob.innerHTML = '';
                        mapPins.forEach((p, idx) => {
                            const d = document.createElement('div');
                            d.className = 'pin-marker'; d.innerText = idx + 1;
                            d.style.left = p.x + '%'; d.style.top = p.y + '%';
                            if (p.v === 'f') of.appendChild(d); else ob.appendChild(d);
                        });
                        generateSnapshot();
                    }

                    wf.addEventListener('click', (e) => {
                        const r = wf.getBoundingClientRect();
                        mapPins.push({ v: 'f', x: ((e.clientX - r.left)/r.width*100).toFixed(1), y: ((e.clientY - r.top)/r.height*100).toFixed(1) });
                        render();
                    });
                    wb.addEventListener('click', (e) => {
                        const r = wb.getBoundingClientRect();
                        mapPins.push({ v: 'b', x: ((e.clientX - r.left)/r.width*100).toFixed(1), y: ((e.clientY - r.top)/r.height*100).toFixed(1) });
                        render();
                    });
                </script>
            </body>
            </html>"""

            components.html(body_map_html, height=450, scrolling=False)

        with tab_view_plan:
            st.subheader("訓練課表與日誌歷史檔案 Training Log Archive")
            if is_coach:
                conn = get_db_connection()
                c = conn.cursor()
                c.execute("SELECT DISTINCT username FROM training_plans")
                plan_users = [r[0] for r in c.fetchall()]
                c.close()
                conn.close()
                filter_user = st.selectbox("篩選選手 Filter Athlete", ["全隊所有人 Entire Team"] + plan_users)
                query_user = None if filter_user == "全隊所有人 Entire Team" else filter_user
            else:
                query_user = user["username"]

            try:
                plans = get_training_plans(query_user)
            except Exception:
                try:
                    plans = get_training_plans(username=query_user)
                except Exception:
                    plans = []

            if not plans:
                st.warning("尚無訓練日誌紀錄 No Training Log Records")
            else:
                st.markdown("##### 部位疼痛指數復原趨勢圖 Pain Score Recovery Trend")

                pain_rows = []
                for p in plans:
                    p_date = p[2]
                    try:
                        parsed = json.loads(p[6])
                        inj_list = parsed.get("injuries_list", [])
                        for inj in inj_list:
                            area = inj.get("area", f"部位 #{inj.get('id',1)}")
                            lvl = inj.get("level", 0)
                            pain_rows.append({"Date": p_date, "BodyArea": area, "PainLevel": int(lvl)})
                    except Exception:
                        continue

                if pain_rows:
                    df_pain_raw = pd.DataFrame(pain_rows)
                    df_pivot = df_pain_raw.pivot_table(index='Date', columns='BodyArea', values='PainLevel', aggfunc='last').fillna(0)
                    df_pivot = df_pivot.sort_index()
                    st.line_chart(df_pivot)
                else:
                    st.caption("尚無傷病追蹤數據可繪製趨勢圖 No Injury Data for Line Chart")

                st.markdown("---")
                
                for p in reversed(plans):
                    p_id, p_user, p_date, p_cat, p_content, p_intensity, p_notes, p_created = p
                    parsed_notes = None
                    try: parsed_notes = json.loads(p_notes)
                    except Exception: parsed_notes = None

                    with st.expander(f"日期 Date: {p_date} ｜ 選手 Athlete: {p_user} ｜ 類別: {p_cat} ｜ RPE: {p_intensity}"):
                        if parsed_notes and isinstance(parsed_notes, dict):
                            tot_s = parsed_notes.get('total_sleep_hr', 0)
                            s_h = parsed_notes.get('sleep_hours', int(tot_s))
                            s_m = parsed_notes.get('sleep_mins', int((tot_s - int(tot_s))*60))
                            sleep_str = f"`{s_h}小時 {s_m}分`"

                            inj_list = parsed_notes.get("injuries_list", [])
                            st.markdown("**訓練與生理狀態：**")
                            c_d1, c_d2, c_d3 = st.columns(3)
                            with c_d1: st.write(f"• 訓練時長：`{parsed_notes.get('duration_min', '-')} 分鐘`")
                            with c_d2: st.write(f"• 疲勞指數：`{parsed_notes.get('fatigue_score', '-')}/10` ｜ 睡眠：{sleep_str}")
                            with c_d3: st.write(f"• 心率統計：`{parsed_notes.get('resting_hr', '-')} bpm`")

                            col_inj_text, col_inj_map = st.columns([1.2, 0.8])
                            with col_inj_text:
                                st.markdown("**痛點部位與疼痛性質紀錄 Injury Details：**")
                                if inj_list:
                                    for inj in inj_list:
                                        lvl = inj.get("level", 0)
                                        area = inj.get("area", f"痛點 #{inj.get('id',1)}")
                                        ptypes = inj.get("pain_types", [])
                                        ptypes_str = f" ｜ 性質: {', '.join(ptypes)}" if ptypes else ""
                                        n_str = inj.get("notes", "")
                                        n_fmt = f" ({n_str})" if n_str else ""
                                        
                                        if lvl >= 5: st.markdown(f"• :red[**{area}** ｜ 疼痛 VAS: {lvl}/10{ptypes_str}{n_fmt}]")
                                        elif lvl > 0: st.markdown(f"• :orange[**{area}** ｜ 疼痛 VAS: {lvl}/10 (復原中){ptypes_str}{n_fmt}]")
                                        else: st.markdown(f"• :green[**{area}** ｜ 疼痛 VAS: 0/10 (痊癒){ptypes_str}{n_fmt}]")
                                else: st.markdown(":green[良好無傷病 No Active Injury]")

                            with col_inj_map:
                                if "body_map_img_b64" in parsed_notes and parsed_notes["body_map_img_b64"]:
                                    st.image(parsed_notes["body_map_img_b64"], caption="當時人體標記圖 Body Map Snapshot", use_container_width=True)

                            st.markdown(f"**課表細節：**\n```text\n{p_content}\n```")
                            u_notes = parsed_notes.get("user_notes", "")
                            if u_notes:
                                st.markdown(f"**動作感覺與教練叮嚀：**\n```text\n{u_notes}\n```")
                        else:
                            st.markdown(f"**課表細節 Details：**\n```text\n{p_content}\n```")
                        
                        if is_coach or p_user == user["username"]:
                            if st.button(f"刪除日誌 Log ID {p_id}", key=f"del_plan_{p_id}"):
                                delete_training_plan(p_id)
                                st.rerun()

# ==================== 【頁面 9：歷史分析紀錄】 ====================
elif page == "歷史分析紀錄 History Records":
    st.title("歷史分析紀錄庫 History Records Repository")
    st.markdown("---")

    if st.session_state["user_info"] is None:
        st.warning("請先在左側邊欄登入帳號 Please Login in Sidebar First")
    else:
        user = st.session_state["user_info"]
        is_coach = (user["role"] == "coach")

        records = get_user_records(username=None if is_coach else user["username"])

        if not records:
            st.warning("尚無任何分析紀錄 No History Records Found")
        else:
            grouped_records = {}
            for rec in records:
                date_str = rec[5].split(" ")[0] if " " in rec[5] else rec[5]
                if date_str not in grouped_records: grouped_records[date_str] = []
                grouped_records[date_str].append(rec)

            for date_key in sorted(grouped_records.keys(), reverse=True):
                day_recs = grouped_records[date_key]
                with st.expander(f"📅 日期 Date：{date_key} （共 {len(day_recs)} 筆紀錄）"):
                    for rec in day_recs:
                        rec_id, rec_user, rec_module, rec_title, rec_details_str, rec_time = rec
                        try:
                            rec_details = json.loads(rec_details_str) if isinstance(rec_details_str, str) else rec_details_str
                        except Exception:
                            rec_details = {}

                        st.markdown(f"#### [{rec_module}] {rec_title}")

                        col_d1, col_d2 = st.columns([1, 1])

                        with col_d1:
                            if isinstance(rec_details, dict) and rec_details.get("image_b64"):
                                st.image(rec_details["image_b64"], caption="畫面截圖 Composite Image", use_container_width=True)

                            st.markdown("##### 📝 動作分析筆記與教練叮嚀 Analysis Notes")
                            current_notes = rec_details.get("notes", "") if isinstance(rec_details, dict) else ""
                            
                            note_input = st.text_area(
                                "輸入或修改本筆紀錄的檢討與建議 Notes & Comments",
                                value=current_notes,
                                height=110,
                                key=f"note_input_{rec_id}"
                            )
                            
                            col_b1, col_b2 = st.columns([1, 1])
                            with col_b1:
                                if st.button("💾 儲存筆記 Save Note", key=f"save_note_btn_{rec_id}", use_container_width=True):
                                    if isinstance(rec_details, dict):
                                        rec_details["notes"] = note_input
                                        update_record_details(rec_id, rec_details)
                                        st.success("筆記已成功更新至資料庫！")
                                        st.rerun()

                            if is_coach or rec_user == user["username"]:
                                with col_b2:
                                    if st.button(f"🗑️ 刪除紀錄 ID {rec_id}", key=f"del_btn_{rec_id}", use_container_width=True):
                                        delete_record(rec_id)
                                        st.rerun()

                        with col_d2:
                            st.markdown("##### 📊 數據明細 Data Details")
                            
                            if isinstance(rec_details, dict):
                                if "angle_metrics" in rec_details:
                                    am = rec_details["angle_metrics"]
                                    if isinstance(am, str):
                                        try: am = json.loads(am)
                                        except Exception: am = {}
                                    if isinstance(am, dict):
                                        rel_t = am.get("rel_time_sec", am.get("time_sec", "0.00"))
                                        st.write(f"時間 Time：`{rel_t} 秒` ｜ 階段 Phase：`{am.get('phase_tag', '未標註')}`")
                                        for item in am.get("angles", []):
                                            if isinstance(item, dict):
                                                st.write(f"• {item.get('name', '角度')}：`{item.get('value', '0°')}`")

                                tm = rec_details.get("timer_metrics", rec_details.get("timer_data", None))
                                if tm is None and ("splits" in rec_details or "total_time" in rec_details):
                                    tm = rec_details

                                if tm:
                                    if isinstance(tm, str):
                                        try: tm = json.loads(tm)
                                        except Exception: tm = {}

                                    if isinstance(tm, dict):
                                        total_t = tm.get("total_time", tm.get("total_sec", tm.get("total", "-")))
                                        t0_v = tm.get("t0_sec", tm.get("t0", "0.00"))
                                        st.write(f"**總時間 Total**：`{total_t} 秒` ｜ **T0 起點**：`{t0_v}s`")

                                        splits = tm.get("splits", rec_details.get("splits", []))
                                        if isinstance(splits, str):
                                            try: splits = json.loads(splits)
                                            except Exception: splits = []

                                        if isinstance(splits, list) and len(splits) > 0:
                                            table_rows = []
                                            for s in splits:
                                                if isinstance(s, dict):
                                                    seg_name = s.get("segment", s.get("phase", s.get("name", s.get("label", "-"))))
                                                    cum_t = s.get("cum_sec", s.get("cum_time", s.get("cumulative", "-")))
                                                    split_t = s.get("split_sec", s.get("split_time", s.get("dt", "-")))
                                                    spd_val = s.get("speed_ms", s.get("speed", s.get("spd", "-")))

                                                    table_rows.append({
                                                        "階段 Phase": seg_name,
                                                        "累積時間 Cumulative (s)": f"{cum_t}s" if str(cum_t).replace('.','',1).isdigit() else str(cum_t),
                                                        "單段 Split (s)": f"{split_t}s" if str(split_t).replace('.','',1).isdigit() else str(split_t),
                                                        "平均速度 Speed (m/s)": f"{spd_val} m/s" if str(spd_val).replace('.','',1).isdigit() else str(spd_val)
                                                    })
                                            if table_rows:
                                                st.dataframe(pd.DataFrame(table_rows), use_container_width=True, hide_index=True)
                                        else:
                                            st.caption("⚠️ 尚無分段數據明細 No split details found")

                        st.markdown("---")

# ==================== 【頁面 10：未來擴充規劃】 ====================
elif page == "未來擴充規劃 Future Roadmap":
    st.title("未來擴充規劃 Future Roadmap")
    st.markdown("---")
    st.markdown("""
    1. **AI 3D 骨架關節自動重建**：引入 MediaPipe 3D Pose 進行空間姿態還原。
    2. **過欄拋物線與起跳點預測**：結合物理解算，預測過欄拋物線最高點。
    3. **團隊傷病交叉分析警報**：防護員評估報告與體能 RPE 疲勞度連動發出預警。
    """)
