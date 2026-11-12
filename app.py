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
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL,
            team_code TEXT NOT NULL DEFAULT 'DEFAULT_TEAM',
            email TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT ''
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS reset_tokens (
            username TEXT PRIMARY KEY,
            otp_code TEXT NOT NULL,
            expires_at TEXT NOT NULL
        )
    """)
    c.execute("""
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
    """)
    c.execute("""
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
    """)
    c.execute("""
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
    """)
    c.execute("""
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
    """)

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

# 【異动說明】已依需求移除「殘影軌跡 Ghosting Track」選單項目
nav_options = [
    "系統首頁 Home",
    "運動防護與傷勢評估 Injury & Defense Feedback",
    "比賽行事曆 Competition Calendar",
    "角度分析 Angle Analysis",
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

# ==================== 【頁面 3：比賽行事曆】 ====================
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
                .btn-sync { background: #059669; color: #fff; font-weight: bold; border: none; padding:
