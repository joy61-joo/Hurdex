import base64
import datetime
import hashlib
import json
import os
import random
import sqlite3
import smtplib
import subprocess
import tempfile
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

# -------------------- 【頁面與全站 CSS 配置】 --------------------
st.set_page_config(page_title="生物力學動作分析系統 Biomechanics Motion Analysis System", layout="wide", initial_sidebar_state="expanded")

st.markdown("""
    <style>
    .stApp {
        background-color: #f8fafc;
        color: #0f172a;
    }
    section[data-testid="stSidebar"] {
        background-color: #f1f5f9 !important;
        border-right: 1px solid #e2e8f0;
    }
    h1, h2, h3, h4, h5, h6 {
        color: #0f172a !important;
        font-weight: 700 !important;
    }
    p, label, span, div {
        color: #334155;
    }
    .stButton>button {
        background-color: #2563eb !important;
        color: #ffffff !important;
        border-radius: 6px !important;
        border: none !important;
        font-weight: 600 !important;
        box-shadow: 0 1px 2px rgba(0, 0, 0, 0.05);
        transition: all 0.2s ease;
    }
    .stButton>button p, .stButton>button span, .stButton>button div {
        color: #ffffff !important;
        font-weight: 600 !important;
    }
    .stButton>button:hover {
        background-color: #1d4ed8 !important;
    }
    .stTextInput>div>div>input, .stSelectbox>div>div>div {
        background-color: #ffffff !important;
        color: #0f172a !important;
        border: 1px solid #cbd5e1 !important;
        border-radius: 6px !important;
    }
    div[data-testid="stExpander"] {
        background-color: #ffffff !important;
        border: 1px solid #e2e8f0 !important;
        border-radius: 8px !important;
    }
    div[data-testid="stTextArea"]:has(textarea[aria-label*="body_map_img_b64"]),
    div[data-testid="stTextArea"]:has(textarea[aria-label*="angle_img_b64"]),
    div[data-testid="stTextArea"]:has(textarea[aria-label*="angle_data_json"]),
    div[data-testid="stTextArea"]:has(textarea[aria-label*="timer_data_json"]),
    div[data-testid="stTextArea"]:has(textarea[aria-label*="ghost_img_b64"]),
    div[data-testid="stTextArea"]:has(textarea[aria-label*="dual_img_b64"]),
    div[data-testid="stTextArea"]:has(textarea[aria-label*="dual_data_json"]) {
        position: absolute !important;
        left: -9999px !important;
        top: -9999px !important;
        width: 1px !important;
        height: 1px !important;
        overflow: hidden !important;
    }
    </style>
""", unsafe_allow_html=True)

# -------------------- 【資料庫與系統驗證配置】 --------------------
COACH_SECRET_CODE = "09290808"
DB_NAME = "hurdle_analysis.db"

# SMTP 發信伺服器設定
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
SENDER_EMAIL = "your_system_email@gmail.com"
SENDER_PASSWORD = "your_app_password"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL,
            email TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        )
    ''')
    
    c.execute("PRAGMA table_info(users)")
    columns = [column[1] for column in c.fetchall()]
    if 'email' not in columns:
        try:
            c.execute("ALTER TABLE users ADD COLUMN email TEXT NOT NULL DEFAULT ''")
            conn.commit()
        except sqlite3.OperationalError:
            pass

    c.execute('''
        CREATE TABLE IF NOT EXISTS reset_tokens (
            username TEXT PRIMARY KEY,
            otp_code TEXT NOT NULL,
            expires_at TEXT NOT NULL
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            module TEXT NOT NULL,
            title TEXT NOT NULL,
            details TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            FOREIGN KEY (username) REFERENCES users (username)
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS training_plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            plan_date TEXT NOT NULL,
            category TEXT NOT NULL,
            content TEXT NOT NULL,
            intensity TEXT NOT NULL,
            notes TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    ''')
    conn.commit()
    conn.close()

# -------------------- 【伺服器端手機影片自動轉碼處理】 --------------------
def process_and_convert_video(uploaded_file):
    """將 iPhone (.MOV / H.265) 或手機影片自動透過 FFmpeg 轉碼為網頁通用 H.264 MP4"""
    if uploaded_file is None:
        return None, None
    suffix = os.path.splitext(uploaded_file.name)[1]
    if not suffix:
        suffix = ".mp4"
        
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp_in:
        tmp_in.write(uploaded_file.getvalue())
        in_path = tmp_in.name

    out_path = in_path + "_converted.mp4"

    cmd = [
        "ffmpeg", "-y", "-i", in_path,
        "-vcodec", "libx264", "-acodec", "aac",
        "-crf", "25", "-preset", "ultrafast",
        out_path
    ]
    
    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        if os.path.exists(out_path):
            with open(out_path, "rb") as f:
                converted_bytes = f.read()
            if os.path.exists(in_path): os.remove(in_path)
            if os.path.exists(out_path): os.remove(out_path)
            return converted_bytes, "video/mp4"
    except Exception:
        pass
        
    if os.path.exists(in_path): os.remove(in_path)
    if os.path.exists(out_path): os.remove(out_path)
    mime = uploaded_file.type if uploaded_file.type else "video/mp4"
    return uploaded_file.getvalue(), mime

def hash_password(password):
    return hashlib.sha256(password.encode('utf-8')).hexdigest()

def register_user(username, password, role, email):
    username = username.strip()
    if not username:
        return False, "使用者名稱不可為空 Username Cannot Be Empty"
    
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    c.execute("SELECT username FROM users WHERE username = ?", (username,))
    if c.fetchone():
        conn.close()
        return False, "該使用者名稱已被註冊 請更換其他名稱 Username Already Exists"

    try:
        pw_hash = hash_password(password)
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        c.execute("INSERT INTO users (username, password_hash, role, email, created_at) VALUES (?, ?, ?, ?, ?)",
                  (username, pw_hash, role, email, now_str))
        conn.commit()
        conn.close()
        return True, "註冊成功 請登入使用 Registration Successful Please Login"
    except sqlite3.IntegrityError:
        conn.close()
        return False, "該使用者名稱已存在 請更換名稱 Username Exists"

def check_login(username, password):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    pw_hash = hash_password(password)
    c.execute("SELECT username, role, email FROM users WHERE username = ? AND password_hash = ?", (username, pw_hash))
    user = c.fetchone()
    conn.close()
    if user:
        return True, {"username": user[0], "role": user[1], "email": user[2]}
    return False, "帳號或密碼錯誤 Account or Password Error"

def update_username(old_username, new_username):
    new_username = new_username.strip()
    if not new_username:
        return False, "新帳號名稱不可為空 Username Cannot Be Empty"
    if old_username == new_username:
        return False, "新舊帳號名稱相同，無需修改 Same Username"

    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT username FROM users WHERE username = ?", (new_username,))
    if c.fetchone():
        conn.close()
        return False, "該使用者名稱已存在 請更換其他名稱 Target Username Exists"

    try:
        c.execute("UPDATE users SET username = ? WHERE username = ?", (new_username, old_username))
        c.execute("UPDATE records SET username = ? WHERE username = ?", (new_username, old_username))
        c.execute("UPDATE training_plans SET username = ? WHERE username = ?", (new_username, old_username))
        c.execute("UPDATE reset_tokens SET username = ? WHERE username = ?", (new_username, old_username))
        conn.commit()
        conn.close()
        return True, "帳號名稱修改成功 Username Updated Successfully"
    except Exception as e:
        conn.close()
        return False, f"修改失敗 Failed: {str(e)}"

def update_password(username, old_password, new_password):
    if not old_password or not new_password:
        return False, "請輸入完整的舊密碼與新密碼 Please Fill All Password Fields"

    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    old_hash = hash_password(old_password)
    c.execute("SELECT username FROM users WHERE username = ? AND password_hash = ?", (username, old_hash))
    if not c.fetchone():
        conn.close()
        return False, "舊密碼不正確 Old Password Incorrect"

    new_hash = hash_password(new_password)
    c.execute("UPDATE users SET password_hash = ? WHERE username = ?", (new_hash, username))
    conn.commit()
    conn.close()
    return True, "密碼修改成功 Password Updated Successfully"

def send_reset_email(to_email, username, otp_code):
    try:
        msg = MIMEMultipart()
        msg['From'] = SENDER_EMAIL
        msg['To'] = to_email
        msg['Subject'] = "【生物力學動作分析系統】重設密碼驗證碼 Password Reset Verification Code"

        body = f"""您好 {username}：

您收到了重設密碼的請求。
您的 6 位數驗證碼為：{otp_code} (10分鐘內有效)

請在系統頁面輸入此驗證碼以完成密碼重設。
若非您本人操作，請忽略此郵件。

生物力學動作分析系統 Biomechanics Motion Analysis System
"""
        msg.attach(MIMEText(body, 'plain', 'utf-8'))

        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        server.send_message(msg)
        server.quit()
        return True, f"驗證碼已寄送至信箱 {to_email}，請查收信件 Verification Code Sent to Email!"
    except Exception as e:
        return False, f"信件發送失敗（發送端未設定 SMTP）：{str(e)}"

def request_password_reset(username):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT email FROM users WHERE username = ?", (username,))
    row = c.fetchone()
    if not row:
        conn.close()
        return False, "找不到該使用者名稱 Account Not Found"

    user_email = row[0]
    otp_code = f"{random.randint(100000, 999999)}"
    expires_at = (datetime.datetime.now() + datetime.timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S")

    c.execute("INSERT OR REPLACE INTO reset_tokens (username, otp_code, expires_at) VALUES (?, ?, ?)",
              (username, otp_code, expires_at))
    conn.commit()
    conn.close()

    ok, msg = send_reset_email(user_email, username, otp_code)
    if not ok:
        return True, f"【開發測試模式】發信未配置，您的驗證碼為：{otp_code} (信箱：{user_email})"
    return True, msg

def verify_and_reset_password(username, otp_code, new_password):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT otp_code, expires_at FROM reset_tokens WHERE username = ?", (username,))
    row = c.fetchone()

    if not row:
        conn.close()
        return False, "尚未索取驗證碼或驗證碼已無效 Please Request Verification Code First"

    db_otp, expires_at_str = row
    now_dt = datetime.datetime.now()
    expires_dt = datetime.datetime.strptime(expires_at_str, "%Y-%m-%d %H:%M:%S")

    if now_dt > expires_dt:
        conn.close()
        return False, "驗證碼已過期，請重新索取 Verification Code Expired"

    if otp_code.strip() != db_otp:
        conn.close()
        return False, "驗證碼錯誤 Verification Code Incorrect"

    pw_hash = hash_password(new_password)
    c.execute("UPDATE users SET password_hash = ? WHERE username = ?", (pw_hash, username))
    c.execute("DELETE FROM reset_tokens WHERE username = ?", (username,))
    conn.commit()
    conn.close()
    return True, "密碼重設成功 請使用新密碼登入 Password Reset Successful"

def save_record(username, module, title, details_dict):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    details_json = json.dumps(details_dict, ensure_ascii=False)
    c.execute("INSERT INTO records (username, module, title, details, timestamp) VALUES (?, ?, ?, ?, ?)",
              (username, module, title, details_json, now_str))
    conn.commit()
    conn.close()

def get_user_records(username=None, module=None):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    query = "SELECT id, username, module, title, details, timestamp FROM records"
    params = []
    where_clauses = []
    if username:
        where_clauses.append("username = ?")
        params.append(username)
    if module:
        where_clauses.append("module = ?")
        params.append(module)
    if where_clauses:
        query += " WHERE " + " AND ".join(where_clauses)
    query += " ORDER BY id DESC"
    c.execute(query, tuple(params))
    rows = c.fetchall()
    conn.close()
    return rows

def delete_record(record_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("DELETE FROM records WHERE id = ?", (record_id,))
    conn.commit()
    conn.close()

def save_training_plan(username, plan_date, category, content, intensity, notes):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    c.execute("INSERT INTO training_plans (username, plan_date, category, content, intensity, notes, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
              (username, plan_date, category, content, intensity, notes, now_str))
    conn.commit()
    conn.close()

def get_training_plans(username=None):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    query = "SELECT id, username, plan_date, category, content, intensity, notes, created_at FROM training_plans"
    params = []
    if username:
        query += " WHERE username = ?"
        params.append(username)
    query += " ORDER BY plan_date ASC, id ASC"
    c.execute(query, tuple(params))
    rows = c.fetchall()
    conn.close()
    return rows

def delete_training_plan(plan_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("DELETE FROM training_plans WHERE id = ?", (plan_id,))
    conn.commit()
    conn.close()

init_db()

if "user_info" not in st.session_state:
    st.session_state["user_info"] = None

# -------------------- 【側邊欄：導覽選單】 --------------------
st.sidebar.title("生物力學動作分析系統 Biomechanics Motion Analysis System")
st.sidebar.markdown("---")

if st.session_state["user_info"] is not None:
    u = st.session_state["user_info"]
    st.sidebar.success(f"當前登入 Current User：{u['username']} {'教練 Coach' if u['role']=='coach' else '選手 Athlete'}")
    if st.sidebar.button("登出系統 Logout System", key="sidebar_logout"):
        st.session_state["user_info"] = None
        st.rerun()

st.sidebar.markdown("---")

nav_options = [
    "系統首頁 Home",
    "角度分析 Angle Analysis",
    "殘影軌跡 Ghosting Track",
    "雙影同步 Dual Video Sync",
    "跨期數據對比 Longitudinal Comparison",
    "課表紀錄 Training Plan",
    "歷史分析紀錄 History Records",
    "未來擴充規劃 Future Roadmap"
]

page = st.sidebar.radio("請選擇功能模組 Select Feature Module：", nav_options)

# ==================== 【頁面 1：系統首頁】 ====================
if page == "系統首頁 Home":
    st.title("跨欄生物力學與動作分析系統 Hurdle Biomechanics Analysis System")
    st.markdown("---")

    if st.session_state["user_info"] is None:
        col1, col2, col3 = st.columns([1, 1.6, 1])
        with col2:
            st.subheader("使用者登入、註冊與密碼重設 User Account System")
            auth_mode = st.radio("選擇操作 Select Action：", ["登入帳號 Login Account", "註冊新帳號 Register New Account", "忘記/重設密碼 Reset Password Via Email"], key="main_auth_mode")
            
            if auth_mode == "登入帳號 Login Account":
                login_user = st.text_input("使用者名稱 Username", key="main_login_u")
                login_pw = st.text_input("密碼 Password", type="password", key="main_login_p")
                if st.button("登入 Login", key="main_login_btn", use_container_width=True):
                    if login_user and login_pw:
                        success, result = check_login(login_user, login_pw)
                        if success:
                            st.session_state["user_info"] = result
                            st.success(f"歡迎回來 Welcome Back {result['username']} {'教練 Coach' if result['role']=='coach' else '選手 Athlete'}！")
                            st.rerun()
                        else:
                            st.error(result)
                    else:
                        st.warning("請輸入帳號與密碼 Please Enter Username & Password")
            
            elif auth_mode == "註冊新帳號 Register New Account":
                reg_user = st.text_input("新使用者名稱 New Username", key="main_reg_u")
                reg_email = st.text_input("電子信箱 Email Address", key="main_reg_email")
                reg_pw = st.text_input("設定密碼 Set Password", type="password", key="main_reg_p")
                reg_role = st.selectbox("身份權限 Role", ["player", "coach"], format_func=lambda x: "選手 Athlete" if x=="player" else "教練 Coach", key="main_reg_role")
                
                coach_code_input = ""
                if reg_role == "coach":
                    coach_code_input = st.text_input("教練驗證碼 Coach Verification Code", type="password", key="main_reg_coach_code")

                if st.button("註冊 Register", key="main_reg_btn", use_container_width=True):
                    if reg_user and reg_pw and reg_email:
                        if "@" not in reg_email or "." not in reg_email:
                            st.error("請輸入正確格式的電子信箱 Invalid Email Format")
                        elif reg_role == "coach" and coach_code_input != COACH_SECRET_CODE:
                            st.error("教練驗證碼錯誤 Coach Code Error")
                        else:
                            ok, msg = register_user(reg_user, reg_pw, reg_role, reg_email)
                            if ok:
                                st.success(msg)
                            else:
                                st.error(msg)
                    else:
                        st.warning("請填寫完整的註冊資訊（含電子信箱） Please Fill All Registration Fields")
            
            elif auth_mode == "忘記/重設密碼 Reset Password Via Email":
                st.markdown("##### 步驟 1：索取 Email 驗證碼 Step 1: Request OTP")
                req_user = st.text_input("請輸入您的使用者名稱 Username", key="main_req_u")
                if st.button("發送驗證碼到信箱 Send Verification Code to Email", key="main_req_btn", use_container_width=True):
                    if req_user:
                        ok, msg = request_password_reset(req_user)
                        if ok:
                            st.info(msg)
                        else:
                            st.error(msg)
                    else:
                        st.warning("請輸入使用者名稱 Please Enter Username")

                st.markdown("---")
                st.markdown("##### 步驟 2：驗證並設定新密碼 Step 2: Reset Password")
                reset_user = st.text_input("使用者名稱 Username", key="main_reset_u")
                reset_otp = st.text_input("信箱收到的 6 位數驗證碼 6-Digit Email OTP", key="main_reset_otp")
                new_pw = st.text_input("設定新密碼 New Password", type="password", key="main_reset_np")
                confirm_pw = st.text_input("確認新密碼 Confirm New Password", type="password", key="main_reset_cp")
                
                if st.button("確認重設密碼 Confirm Reset Password", key="main_reset_btn", use_container_width=True):
                    if reset_user and reset_otp and new_pw and confirm_pw:
                        if new_pw != confirm_pw:
                            st.error("兩次輸入的新密碼不一致 Passwords Do Not Match")
                        else:
                            ok, msg = verify_and_reset_password(reset_user, reset_otp, new_pw)
                            if ok:
                                st.success(msg)
                            else:
                                st.error(msg)
                    else:
                        st.warning("請填寫所有必要欄位 Please Fill All Required Fields")

    else:
        u = st.session_state["user_info"]
        col1, col2, col3 = st.columns([1, 2, 1])
        with col2:
            st.success(f"當前登入使用者 Current User：**{u['username']}** ｜ 信箱 Email：**{u.get('email', '未紀錄')}** ｜ 身份權限 Role：**{'教練 Coach' if u['role']=='coach' else '選手 Athlete'}**")
            
            st.markdown("---")
            st.subheader("帳號個人設定 Account Settings")
            
            tab_edit_name, tab_edit_pw = st.tabs(["修改帳號名稱 Change Username", "修改密碼 Change Password"])
            
            with tab_edit_name:
                new_uname_input = st.text_input("新使用者名稱 New Username", value=u["username"], key="change_uname_input")
                if st.button("確認修改帳號名稱 Confirm Change Username", key="btn_change_uname", use_container_width=True):
                    ok, msg = update_username(u["username"], new_uname_input)
                    if ok:
                        st.session_state["user_info"]["username"] = new_uname_input.strip()
                        st.success(msg)
                        st.rerun()
                    else:
                        st.error(msg)

            with tab_edit_pw:
                old_pw_input = st.text_input("輸入舊密碼 Current Password", type="password", key="change_old_pw_input")
                new_pw_input = st.text_input("輸入新密碼 New Password", type="password", key="change_new_pw_input")
                confirm_new_pw = st.text_input("再次確認新密碼 Confirm New Password", type="password", key="change_confirm_pw_input")
                
                if st.button("確認修改密碼 Confirm Change Password", key="btn_change_pw", use_container_width=True):
                    if new_pw_input != confirm_new_pw:
                        st.error("兩次輸入的新密碼不一致 Passwords Do Not Match")
                    else:
                        ok, msg = update_password(u["username"], old_pw_input, new_pw_input)
                        if ok:
                            st.success(msg)
                        else:
                            st.error(msg)

            st.markdown("<br>", unsafe_allow_html=True)
            if st.button("登出系統 Logout System", key="main_dashboard_logout", use_container_width=True):
                st.session_state["user_info"] = None
                st.rerun()

# ==================== 【頁面 2：角度分析】 ====================
elif page == "角度分析 Angle Analysis":
    st.title("角度分析模組 Angle Analysis Module")
    st.markdown("---")

    uploaded_file = st.file_uploader("上傳動作影片 Upload Video MP4 MOV", type=["mp4", "mov", "avi"])

    if uploaded_file is not None:
        with st.spinner("正在優化手機影片格式 (H.264 MP4)..."):
            bytes_single, mime_single = process_and_convert_video(uploaded_file)
        b64_single = base64.b64encode(bytes_single).decode("utf-8")

        tab_play, tab_measure, tab_hurdle_timer = st.tabs([
            "影片流暢預覽 Video Preview",
            "關鍵影格角度量測 Keyframe Angle Measurement",
            "欄間秒數及速度計算 Hurdle Time & Speed Calculation"
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
                    <button id="btnMode2p" class="btn-mode">兩點前傾角 2-Point Lean Angle Shoulder to Hip</button>
                    <button id="btnUndo" class="btn btn-secondary">撤銷 Undo</button>
                    <button id="btnClear" class="btn btn-secondary">清空 Clear</button>
                    <button id="btnSetT0" class="btn-t0">設為動作起點 T0 Set Motion Start T0</button>
                    <span style="font-size: 12px; color: #64748b;">階段 Phase:</span>
                    <select id="selPhaseTag">
                        <option value="攻欄起跳瞬間 Takeoff Phase">攻欄起跳瞬間 Takeoff Phase</option>
                        <option value="過欄最高點 Peak Clearance">過欄最高點 Peak Clearance</option>
                        <option value="攻欄腳著地瞬間 Landing Phase">攻欄腳著地瞬間 Landing Phase</option>
                        <option value="欄間跑步姿態 Inter-hurdle Run">欄間跑步姿態 Inter-hurdle Run</option>
                        <option value="自訂關鍵影格 Custom Keyframe" selected>自訂關鍵影格 Custom Keyframe</option>
                    </select>
                    <button id="btnSnapAngle" class="btn-snap" style="margin-left: auto;">擷取當前畫面與角度 Capture Frame & Angles</button>
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
                                <span>
                                    影片時間 Video Time: <b id="timeTextMeas" style="color:#0f172a;">0.00s</b> ｜ 
                                    <b style="color:#d97706;">時間 Time: <span id="relTimeTextMeas" style="color:#059669; font-size:14px; font-weight:bold;">0.00s</span></b>
                                </span>
                            </div>
                            <input type="range" id="sliderMeas" min="0" max="100" value="0" step="0.01">
                        </div>

                        <div style="display: flex; width: 100%; gap: 8px; margin-top: 10px; align-items: center; flex-wrap: wrap;">
                            <button id="btnPlayMeas" class="btn">播放 暫停 Play Pause</button>
                            <button id="btnStepBack" class="btn btn-secondary">上一幀 Frame Back</button>
                            <button id="btnStepForward" class="btn btn-secondary">下一幀 Frame Forward</button>
                            
                            <span style="font-size:12px; color:#64748b; margin-left:4px;">抽幀間隔 Interval:</span>
                            <select id="selFrameInterval">
                                <option value="1" selected>1 幀 1 Frame 1/60s</option>
                                <option value="3">3 幀 3 Frames 0.05s</option>
                                <option value="5">5 幀 5 Frames 0.08s</option>
                                <option value="10">10 幀 10 Frames 0.16s</option>
                            </select>

                            <span style="font-size:12px; color:#64748b; margin-left:4px;">速度 Speed:</span>
                            <select id="selSpeedMeas">
                                <option value="0.25">0.25x 慢動作 Slow Motion</option>
                                <option value="0.5">0.5x</option>
                                <option value="0.75">0.75x</option>
                                <option value="1.0" selected>1.0x 正常 Normal</option>
                            </select>
                        </div>
                    </div>
                    <div class="data-box">
                        <h4 style="margin: 0 0 12px 0; color:#2563eb;">當前影格角度數據明細 Angle Metrics Detail</h4>
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

                    let mode = '3p', pts2p = [], pts3pGroups = [], buf3p = [], currentAngleList = [];
                    let t0 = 0.0;

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
                        const blob = b64ToBlob("___B64_SINGLE___", "___MIME_SINGLE___");
                        vidMeas.src = URL.createObjectURL(blob);
                    } catch(e) { console.error(e); }

                    function syncCanvasSize() {
                        const w = vidMeas.clientWidth, h = vidMeas.clientHeight;
                        if (w > 0 && h > 0) { canvas.width = w; canvas.height = h; }
                        redraw();
                    }

                    vidMeas.addEventListener('loadedmetadata', () => {
                        sliderMeas.max = vidMeas.duration;
                        syncCanvasSize();
                    });
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

                    sliderMeas.addEventListener('input', (e) => {
                        vidMeas.currentTime = parseFloat(e.target.value);
                        updateTimeDisplays(); redraw();
                    });

                    btnSetT0.addEventListener('click', () => {
                        t0 = vidMeas.currentTime;
                        btnSetT0.innerText = `起點 T0: ${t0.toFixed(2)}s`;
                        alert(`已成功將 ${t0.toFixed(2)}s 設為動作起點 Set Motion Start T0!`);
                        updateTimeDisplays();
                    });

                    btnPlayMeas.addEventListener('click', () => {
                        if (vidMeas.paused) { vidMeas.play(); btnPlayMeas.innerText = '暫停 Pause'; }
                        else { vidMeas.pause(); btnPlayMeas.innerText = '播放 Play'; }
                    });

                    selSpeedMeas.addEventListener('change', (e) => { vidMeas.playbackRate = parseFloat(e.target.value); });

                    const BASE_FRAME_TIME = 1 / 60;
                    btnStepBack.addEventListener('click', () => {
                        vidMeas.pause(); btnPlayMeas.innerText = '播放 Play';
                        const interval = parseInt(selFrameInterval.value) || 1;
                        vidMeas.currentTime = Math.max(0, vidMeas.currentTime - (BASE_FRAME_TIME * interval));
                        sliderMeas.value = vidMeas.currentTime; updateTimeDisplays(); redraw();
                    });

                    btnStepForward.addEventListener('click', () => {
                        vidMeas.pause(); btnPlayMeas.innerText = '播放 Play';
                        const interval = parseInt(selFrameInterval.value) || 1;
                        vidMeas.currentTime = Math.min(vidMeas.duration, vidMeas.currentTime + (BASE_FRAME_TIME * interval));
                        sliderMeas.value = vidMeas.currentTime; updateTimeDisplays(); redraw();
                    });

                    btnMode3p.addEventListener('click', () => {
                        mode = '3p'; btnMode3p.classList.add('active'); btnMode2p.classList.remove('active'); redraw();
                    });

                    btnMode2p.addEventListener('click', () => {
                        mode = '2p'; btnMode2p.classList.add('active'); btnMode3p.classList.remove('active'); redraw();
                    });

                    canvas.addEventListener('click', (e) => {
                        const rect = canvas.getBoundingClientRect();
                        const x = e.clientX - rect.left, y = e.clientY - rect.top;
                        if (mode === '3p') {
                            buf3p.push({x, y});
                            if (buf3p.length === 3) { pts3pGroups.push([...buf3p]); buf3p = []; }
                        } else {
                            if (pts2p.length < 2) pts2p.push({x, y});
                        }
                        redraw();
                    });

                    btnUndo.addEventListener('click', () => {
                        if (mode === '3p') {
                            if (buf3p.length > 0) buf3p.pop();
                            else if (pts3pGroups.length > 0) pts3pGroups.pop();
                        } else {
                            if (pts2p.length > 0) pts2p.pop();
                        }
                        redraw();
                    });

                    btnClear.addEventListener('click', () => {
                        pts2p = []; pts3pGroups = []; buf3p = []; redraw();
                    });

                    function calc2ptAngle(p1, p2) {
                        let dx = p1.x - p2.x, dy = p1.y - p2.y;
                        let dot = dy * (-1), len = Math.sqrt(dx*dx + dy*dy);
                        if (len === 0) return 0;
                        return Math.acos(Math.max(-1, Math.min(1, dot / len))) * 180 / Math.PI;
                    }

                    function calc3ptAngle(A, B, C) {
                        let baX = A.x - B.x, baY = A.y - B.y, bcX = C.x - B.x, bcY = C.y - B.y;
                        let lenBA = Math.sqrt(baX*baX + baY*baY), lenBC = Math.sqrt(bcX*bcX + bcY*bcY);
                        if (lenBA === 0 || lenBC === 0) return 0;
                        return Math.acos(Math.max(-1, Math.min(1, (baX*bcX + baY*bcY) / (lenBA * lenBC)))) * 180 / Math.PI;
                    }

                    const colors3p = ['#d97706', '#2563eb', '#059669', '#7c3aed', '#dc2626'];

                    function redraw() {
                        ctx.clearRect(0, 0, canvas.width, canvas.height);
                        metricsContainer.innerHTML = ''; currentAngleList = [];

                        if (pts2p.length >= 1) {
                            ctx.fillStyle = '#2563eb'; ctx.beginPath(); ctx.arc(pts2p[0].x, pts2p[0].y, 6, 0, Math.PI*2); ctx.fill();
                            ctx.font = '12px sans-serif'; ctx.fillText('肩 Shoulder', pts2p[0].x + 8, pts2p[0].y - 8);
                        }

                        if (pts2p.length === 2) {
                            ctx.fillStyle = '#2563eb'; ctx.beginPath(); ctx.arc(pts2p[1].x, pts2p[1].y, 6, 0, Math.PI*2); ctx.fill();
                            ctx.fillText('髖 Hip', pts2p[1].x + 8, pts2p[1].y - 8);
                            ctx.strokeStyle = '#2563eb'; ctx.lineWidth = 2;
                            ctx.beginPath(); ctx.moveTo(pts2p[0].x, pts2p[0].y); ctx.lineTo(pts2p[1].x, pts2p[1].y); ctx.stroke();
                            ctx.strokeStyle = '#64748b'; ctx.lineWidth = 1; ctx.setLineDash([4, 4]);
                            ctx.beginPath(); ctx.moveTo(pts2p[1].x, pts2p[1].y); ctx.lineTo(pts2p[1].x, Math.max(0, pts2p[1].y - 80)); ctx.stroke();
                            ctx.setLineDash([]);
                            const leanAng = calc2ptAngle(pts2p[0], pts2p[1]);
                            ctx.fillStyle = '#2563eb'; ctx.font = 'bold 14px sans-serif';
                            ctx.fillText(`Lean: ${leanAng.toFixed(1)}°`, pts2p[1].x + 10, pts2p[1].y - 10);
                            currentAngleList.push({ name: "軀幹前傾角 Trunk Lean Angle", value: `${leanAng.toFixed(1)}°` });
                            metricsContainer.innerHTML += `<div class="metric-card"><div class="metric-title">軀幹前傾角 Trunk Lean Angle</div><div class="metric-val">${leanAng.toFixed(1)}°</div></div>`;
                        }

                        pts3pGroups.forEach((group, idx) => {
                            const color = colors3p[idx % colors3p.length];
                            const pA = group[0], pB = group[1], pC = group[2];
                            const ang = calc3ptAngle(pA, pB, pC);
                            ctx.strokeStyle = color; ctx.lineWidth = 2;
                            ctx.beginPath(); ctx.moveTo(pA.x, pA.y); ctx.lineTo(pB.x, pB.y); ctx.lineTo(pC.x, pC.y); ctx.stroke();
                            ctx.fillStyle = color;
                            ctx.beginPath(); ctx.arc(pA.x, pA.y, 5, 0, Math.PI*2); ctx.fill();
                            ctx.beginPath(); ctx.arc(pC.x, pC.y, 5, 0, Math.PI*2); ctx.fill();
                            ctx.fillStyle = '#059669'; ctx.beginPath(); ctx.arc(pB.x, pB.y, 7, 0, Math.PI*2); ctx.fill();
                            ctx.fillStyle = color; ctx.font = 'bold 14px sans-serif';
                            ctx.fillText(`#${idx+1}: ${ang.toFixed(1)}°`, pB.x + 10, pB.y + 15);
                            currentAngleList.push({ name: `關節角度 Joint Angle #${idx+1}`, value: `${ang.toFixed(1)}°` });
                            metricsContainer.innerHTML += `<div class="metric-card" style="border-left-color: ${color};"><div class="metric-title">關節角度 Joint Angle #${idx+1}</div><div class="metric-val" style="color:${color};">${ang.toFixed(1)}°</div></div>`;
                        });

                        if (buf3p.length > 0) {
                            ctx.fillStyle = '#059669'; ctx.beginPath(); ctx.arc(buf3p[0].x, buf3p[0].y, 6, 0, Math.PI*2); ctx.fill();
                            if (buf3p.length === 2) {
                                ctx.beginPath(); ctx.arc(buf3p[1].x, buf3p[1].y, 7, 0, Math.PI*2); ctx.fill();
                                ctx.strokeStyle = '#059669'; ctx.lineWidth = 2;
                                ctx.beginPath(); ctx.moveTo(buf3p[0].x, buf3p[0].y); ctx.lineTo(buf3p[1].x, buf3p[1].y); ctx.stroke();
                            }
                        }
                    }

                    btnSnapAngle.addEventListener('click', () => {
                        if (!vidMeas.videoWidth || !vidMeas.videoHeight) return alert("影片尚未準備就緒 Video Not Ready!");
                        const off = document.createElement('canvas');
                        off.width = vidMeas.videoWidth; off.height = vidMeas.videoHeight;
                        const offCtx = off.getContext('2d');
                        offCtx.drawImage(vidMeas, 0, 0, off.width, off.height);
                        offCtx.drawImage(canvas, 0, 0, off.width, off.height);

                        const imgBase64 = off.toDataURL('image/jpeg', 0.85);
                        const textAreas = window.parent.document.querySelectorAll('textarea');
                        let targetImgTA = null, targetAngleTA = null;

                        textAreas.forEach(ta => {
                            const label = ta.getAttribute('aria-label') || '';
                            if (label.includes('angle_img_b64')) targetImgTA = ta;
                            if (label.includes('angle_data_json')) targetAngleTA = ta;
                        });

                        if (!targetImgTA && textAreas.length >= 2) targetImgTA = textAreas[0];
                        if (!targetAngleTA && textAreas.length >= 2) targetAngleTA = textAreas[1];

                        const absT = vidMeas.currentTime || 0;
                        const relT = Math.max(0, absT - t0);

                        if (targetImgTA && targetAngleTA) {
                            syncToStreamlit(targetImgTA, imgBase64);
                            syncToStreamlit(targetAngleTA, JSON.stringify({
                                time_sec: absT.toFixed(2),
                                rel_time_sec: relT.toFixed(2),
                                t0_sec: t0.toFixed(2),
                                phase_tag: selPhaseTag.value,
                                angles: currentAngleList
                            }));
                            alert(`已記錄【${selPhaseTag.value}】角度數據 Angle Data Captured! Time: ${relT.toFixed(2)}s`);
                        }
                    });

                    redraw();
                </script>
            </body>
            </html>"""

            measure_html_code = (
                measure_html_template
                .replace("___MIME_SINGLE___", mime_single)
                .replace("___B64_SINGLE___", b64_single)
            )
            components.html(measure_html_code, height=720, scrolling=False)

            st.markdown("---")
            st.subheader("儲存角度量測截圖與數據紀錄 Save Angle Measurement Record")

            captured_img = st.text_area("angle_img_b64", key="captured_angle_img", label_visibility="collapsed")
            captured_json = st.text_area("angle_data_json", key="captured_angle_json", label_visibility="collapsed")

            if captured_img and captured_json:
                st.success("已成功擷取影格與角度數據 Successfully Captured Frame & Angle Data")
                col_prev1, col_prev2 = st.columns([1.2, 0.8])
                with col_prev1:
                    st.image(captured_img, caption="合成畫面預覽圖 Composite Preview Image", use_container_width=True)
                with col_prev2:
                    st.markdown("##### 擷取角度明細 Angle Details：")
                    try:
                        parsed_data = json.loads(captured_json)
                        st.write(f"時間 Time：`{parsed_data.get('rel_time_sec', '0.00')} 秒 sec`")
                        st.write(f"動作階段 Phase：`{parsed_data.get('phase_tag', '自訂點 Custom')}`")
                        for item in parsed_data.get("angles", []):
                            st.write(f"• **{item['name']}**：`{item['value']}`")
                    except Exception:
                        pass

                if st.session_state["user_info"]:
                    with st.form("save_angle_form"):
                        rec_title_angle = st.text_input("紀錄名稱與訓練備註 Record Title & Notes", value=f"動作關鍵影格角度量測 Keyframe Angle Measurement {uploaded_file.name}")
                        if st.form_submit_button("儲存至資料庫 Save to Database"):
                            save_record(
                                username=st.session_state["user_info"]["username"],
                                module="關鍵影格角度量測 Keyframe Angle Measurement",
                                title=rec_title_angle,
                                details_dict={
                                    "video_filename": uploaded_file.name,
                                    "image_b64": captured_img,
                                    "angle_metrics": json.loads(captured_json),
                                    "saved_at": str(datetime.datetime.now())
                                }
                            )
                            st.success("儲存成功 Save Successful")

        with tab_hurdle_timer:
            timer_html_template = """<!DOCTYPE html>
            <html>
            <head>
            <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
            <style>
                * { box-sizing: border-box; }
                body { font-family: -apple-system, sans-serif; background: transparent; color: #0f172a; margin: 0; padding: 0; }
                .control-bar { display: flex; gap: 8px; margin-bottom: 10px; background: #ffffff; padding: 10px; border-radius: 8px; border: 1px solid #e2e8f0; align-items: center; flex-wrap: wrap; }
                .btn { background: #2563eb; color: #fff; border: none; padding: 6px 12px; border-radius: 6px; font-weight: bold; cursor: pointer; }
                .btn-secondary { background: #f1f5f9; color: #334155; border: 1px solid #cbd5e1; }
                .btn-t0 { background: #d97706; color: #fff; border: none; padding: 6px 12px; border-radius: 6px; font-weight: bold; cursor: pointer; }
                .btn-sync { background: #059669; color: #fff; font-weight: bold; margin-left: auto; border: none; padding: 6px 12px; border-radius: 6px; cursor: pointer; }
                .btn-tap { background: #2563eb; color: #fff; font-size: 14px; padding: 10px; border-radius: 6px; border: none; font-weight: bold; cursor: pointer; width: 100%; margin-top: 8px; }
                .layout { display: flex; gap: 15px; width: 100%; }
                .video-box { flex: 1.1; background: #ffffff; border-radius: 10px; border: 1px solid #e2e8f0; padding: 10px; }
                video { width: 100%; border-radius: 6px; background: #000; max-height: 380px; object-fit: contain; }
                .data-panel { flex: 0.9; background: #ffffff; border-radius: 10px; border: 1px solid #e2e8f0; padding: 12px; max-height: 500px; overflow-y: auto; }
                table { width: 100%; border-collapse: collapse; margin-top: 6px; font-size: 12px; }
                th, td { padding: 6px; text-align: center; border-bottom: 1px solid #e2e8f0; }
                th { color: #2563eb; background: #f8fafc; }
                select { background: #ffffff; color: #0f172a; border: 1px solid #cbd5e1; padding: 4px; border-radius: 4px; font-weight: bold; }
            </style>
            </head>
            <body>
                <div class="control-bar">
                    <span style="font-size:12px; color:#64748b;">項目 Item:</span>
                    <select id="selGender">
                        <option value="men">男子 110mH Men 110mH 13.72m / 9.14m</option>
                        <option value="women">女子 100mH Women 100mH 13.00m / 8.50m</option>
                    </select>
                    <button id="btnSetT0T" class="btn-t0">設為動作起點 T0 Set Motion Start T0</button>
                    <button id="btnPlayT" class="btn">播放 暫停 Play Pause</button>
                    <button id="btnStepBackT" class="btn btn-secondary">上一幀 Frame Back</button>
                    <button id="btnStepForwardT" class="btn btn-secondary">下一幀 Frame Forward</button>
                    <span style="font-size:12px; color:#64748b;">速度 Speed:</span>
                    <select id="selSpeedT">
                        <option value="0.25">0.25x 慢動作 Slow Motion</option>
                        <option value="0.5">0.5x</option>
                        <option value="0.75">0.75x</option>
                        <option value="1.0" selected>1.0x 正常 Normal</option>
                    </select>
                    <button id="btnUndoT" class="btn btn-secondary">撤銷 Undo</button>
                    <button id="btnClearT" class="btn btn-secondary">重置 Reset</button>
                    <button id="btnSyncT" class="btn btn-sync">同步打點數據 Sync Timing Data</button>
                </div>

                <div class="layout">
                    <div class="video-box">
                        <video id="vidT" muted playsinline></video>
                        <input type="range" id="sliderT" min="0" max="100" value="0" step="0.01" style="width:100%; margin-top:6px;">
                        
                        <div style="display:flex; justify-content:space-between; margin-top:4px; font-size:12px; color:#64748b;">
                            <span>影片時間 Video Time: <b id="txtAbsTimeT" style="color:#0f172a;">0.00s</b></span>
                            <span>時間 Time: <b id="txtRelTimeT" style="color:#059669; font-size:13px;">0.00s</b> T0: <span id="txtT0Val">未設定 Not Set</span></span>
                        </div>

                        <button id="btnTap" class="btn-tap">1. 紀錄起跑與起跳瞬間 Press Space or Enter 1. Record Start Takeoff</button>
                    </div>
                    <div class="data-panel">
                        <h4 style="margin:0 0 6px 0; color:#2563eb;">分段速度與累積時間數據 Split Speed & Cumulative Time Data</h4>
                        <table>
                            <thead><tr><th>階段 Phase</th><th>分段累積時間 Cumulative Time</th><th>單段耗時 Split Time</th><th>平均速度 Average Speed</th></tr></thead>
                            <tbody id="tb"></tbody>
                        </table>
                        <div style="height:180px; margin-top:10px;"><canvas id="chartV"></canvas></div>
                    </div>
                </div>

                <script>
                    const vidT = document.getElementById('vidT');
                    const sliderT = document.getElementById('sliderT');
                    const btnTap = document.getElementById('btnTap');
                    const tb = document.getElementById('tb');
                    const selGender = document.getElementById('selGender');
                    const selSpeedT = document.getElementById('selSpeedT');
                    const btnStepBackT = document.getElementById('btnStepBackT');
                    const btnStepForwardT = document.getElementById('btnStepForwardT');
                    const btnSetT0T = document.getElementById('btnSetT0T');
                    const txtAbsTimeT = document.getElementById('txtAbsTimeT');
                    const txtRelTimeT = document.getElementById('txtRelTimeT');
                    const txtT0Val = document.getElementById('txtT0Val');

                    let taps = [], chartObj = null, t0_hurdle = 0.0;

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
                        const blob = b64ToBlob("___B64_SINGLE___", "___MIME_SINGLE___");
                        vidT.src = URL.createObjectURL(blob);
                    } catch(e) { console.error(e); }

                    function updateTimeDisplays() {
                        const absT = vidT.currentTime || 0;
                        const relT = Math.max(0, absT - t0_hurdle);
                        txtAbsTimeT.innerText = absT.toFixed(2) + 's';
                        txtRelTimeT.innerText = relT.toFixed(2) + 's';
                    }

                    vidT.addEventListener('loadedmetadata', () => { sliderT.max = vidT.duration; });
                    vidT.addEventListener('timeupdate', () => {
                        updateTimeDisplays();
                        if (!vidT.paused) sliderT.value = vidT.currentTime;
                    });
                    sliderT.addEventListener('input', (e) => {
                        vidT.currentTime = parseFloat(e.target.value);
                        updateTimeDisplays();
                    });
                    document.getElementById('btnPlayT').addEventListener('click', () => { if (vidT.paused) vidT.play(); else vidT.pause(); });

                    selSpeedT.addEventListener('change', (e) => { vidT.playbackRate = parseFloat(e.target.value); });

                    const FRAME_TIME = 1 / 60;
                    btnStepBackT.addEventListener('click', () => {
                        vidT.pause(); vidT.currentTime = Math.max(0, vidT.currentTime - FRAME_TIME);
                        sliderT.value = vidT.currentTime; updateTimeDisplays();
                    });
                    btnStepForwardT.addEventListener('click', () => {
                        vidT.pause(); vidT.currentTime = Math.min(vidT.duration, vidT.currentTime + FRAME_TIME);
                        sliderT.value = vidT.currentTime; updateTimeDisplays();
                    });

                    btnSetT0T.addEventListener('click', () => {
                        t0_hurdle = vidT.currentTime;
                        txtT0Val.innerText = t0_hurdle.toFixed(2) + 's';
                        if (taps.length === 0) {
                            taps.push(t0_hurdle);
                        } else {
                            taps[0] = t0_hurdle;
                        }
                        render();
                        alert(`已成功將 ${t0_hurdle.toFixed(2)}s 設為起跑起跳點 T0 Set Start Point T0!`);
                    });

                    function initChart() {
                        const ctx = document.getElementById('chartV').getContext('2d');
                        chartObj = new Chart(ctx, {
                            type: 'bar',
                            data: { labels: [], datasets: [{ label: 'm/s', data: [], backgroundColor: 'rgba(37,99,235,0.2)', borderColor: '#2563eb', borderWidth: 2 }] },
                            options: { responsive: true, maintainAspectRatio: false, scales: { y: { min: 0, max: 11 } }, plugins: { legend: { display: false } } }
                        });
                    }
                    initChart();

                    function getDists() {
                        return selGender.value === 'men' ? [13.72, 9.14, 9.14, 9.14, 9.14, 9.14, 9.14, 9.14, 9.14, 9.14, 14.02] : [13.00, 8.50, 8.50, 8.50, 8.50, 8.50, 8.50, 8.50, 8.50, 8.50, 10.50];
                    }

                    function render() {
                        tb.innerHTML = '';
                        if (taps.length === 0) btnTap.innerText = '1. 紀錄起跑瞬間 Press Space 1. Record Start';
                        else btnTap.innerText = `${taps.length+1}. 紀錄 Record H${taps.length} / Finish`;

                        const dists = getDists();
                        let labels = [], vels = [];
                        for (let i = 0; i < taps.length; i++) {
                            let cum = taps[i] - taps[0];
                            let lbl = i === 0 ? '起跑 Start T0' : (i === 1 ? '起跑->H1 Start->H1' : `H${i-1}->H${i}`);
                            let dtStr = '-', spdStr = '-';
                            if (i > 0) {
                                let dt = taps[i] - taps[i-1];
                                let spd = dt > 0 ? (dists[i-1] / dt) : 0;
                                dtStr = dt.toFixed(2) + 's'; spdStr = spd.toFixed(2) + ' m/s';
                                labels.push(lbl); vels.push(spd.toFixed(2));
                            }
                            tb.innerHTML += `<tr><td style="color:#2563eb;font-weight:bold;">${lbl}</td><td style="color:#059669;font-weight:bold;">${cum.toFixed(2)}s</td><td style="color:#d97706;">${dtStr}</td><td style="color:#2563eb;font-weight:bold;">${spdStr}</td></tr>`;
                        }
                        chartObj.data.labels = labels; chartObj.data.datasets[0].data = vels; chartObj.update();
                    }

                    function recordTap() { if (taps.length < 12) { taps.push(vidT.currentTime); render(); } }
                    btnTap.addEventListener('click', recordTap);
                    document.getElementById('btnUndoT').addEventListener('click', () => { taps.pop(); render(); });
                    document.getElementById('btnClearT').addEventListener('click', () => { taps = []; t0_hurdle = 0; txtT0Val.innerText = "未設定 Not Set"; render(); });
                    document.addEventListener('keydown', (e) => { if (e.code==='Space'||e.code==='Enter') { e.preventDefault(); recordTap(); } });

                    document.getElementById('btnSyncT').addEventListener('click', () => {
                        if (taps.length < 2) return alert("請至少記錄起跑與 H1 秒數 Please Record Start & H1!");
                        const dists = getDists();
                        let splits = [];
                        for (let i = 1; i < taps.length; i++) {
                            let cum = taps[i] - taps[0];
                            let dt = taps[i] - taps[i-1];
                            let spd = dt > 0 ? (dists[i-1] / dt) : 0;
                            splits.push({
                                segment: i === 1 ? '起跑->H1 Start->H1' : `H${i-1}->H${i}`,
                                cum_sec: cum.toFixed(2),
                                split_sec: dt.toFixed(2),
                                speed_ms: spd.toFixed(2)
                            });
                        }
                        const payload = {
                            gender_mode: selGender.value,
                            t0_sec: t0_hurdle.toFixed(2),
                            total_time: (taps[taps.length-1] - taps[0]).toFixed(2),
                            splits: splits
                        };
                        const textAreas = window.parent.document.querySelectorAll('textarea');
                        let targetTimerTA = null;
                        textAreas.forEach(ta => {
                            if ((ta.getAttribute('aria-label') || '').includes('timer_data_json')) targetTimerTA = ta;
                        });
                        if (!targetTimerTA && textAreas.length >= 3) targetTimerTA = textAreas[2];
                        if (targetTimerTA) {
                            syncToStreamlit(targetTimerTA, JSON.stringify(payload));
                            alert("打點數據與分段累積時間已成功同步 Timing Data Synced Successfully!");
                        }
                    });
                </script>
            </body>
            </html>"""

            timer_html_code = (
                timer_html_template
                .replace("___MIME_SINGLE___", mime_single)
                .replace("___B64_SINGLE___", b64_single)
            )
            components.html(timer_html_code, height=680, scrolling=False)

            st.markdown("---")
            st.subheader("儲存欄間速度紀錄 Save Hurdle Speed Record")
            captured_timer_json = st.text_area("timer_data_json", key="captured_timer_json", label_visibility="collapsed")

            if captured_timer_json:
                try:
                    tm_data = json.loads(captured_timer_json)
                    st.success(f"打點數據接收成功 Data Received Successfully！總累積耗時 Total Time：`{tm_data.get('total_time')} 秒 sec`")
                    if st.session_state["user_info"]:
                        with st.form("save_timer_form"):
                            rec_title_timer = st.text_input("紀錄名稱 Record Title", value=f"欄間秒數及速度計算 Hurdle Time & Speed Calculation {uploaded_file.name}")
                            if st.form_submit_button("儲存至資料庫 Save to Database"):
                                save_record(
                                    username=st.session_state["user_info"]["username"],
                                    module="欄間秒數及速度計算 Hurdle Time & Speed Calculation",
                                    title=rec_title_timer,
                                    details_dict={"timer_metrics": tm_data, "saved_at": str(datetime.datetime.now())}
                                )
                                st.success("儲存成功 Save Successful")
                except Exception:
                    pass

# ==================== 【頁面 3：殘影軌跡】 ====================
elif page == "殘影軌跡 Ghosting Track":
    st.title("殘影軌跡模組 Ghosting Track Module")
    st.markdown("---")

    uploaded_strobe_file = st.file_uploader("上傳動作影片 Upload Video MP4 MOV", type=["mp4", "mov", "avi"], key="strobe_uploader")

    if uploaded_strobe_file is not None:
        with st.spinner("正在優化手機影片格式 (H.264 MP4)..."):
            bytes_strobe, mime_strobe = process_and_convert_video(uploaded_strobe_file)
        b64_strobe = base64.b64encode(bytes_strobe).decode("utf-8")

        strobe_html_template = """<!DOCTYPE html>
        <html>
        <head>
        <script src="https://cdn.jsdelivr.net/npm/@mediapipe/pose/pose.js" crossorigin="anonymous"></script>
        <style>
            * { box-sizing: border-box; }
            body { font-family: -apple-system, sans-serif; background: transparent; color: #0f172a; margin: 0; padding: 0; }
            .control-bar { display: flex; gap: 10px; margin-bottom: 10px; background: #ffffff; padding: 10px 18px; border-radius: 8px; border: 1px solid #e2e8f0; align-items: center; flex-wrap: wrap; }
            .btn { background: #dc2626; color: #fff; border: none; padding: 7px 14px; border-radius: 6px; font-weight: bold; cursor: pointer; }
            .btn-blue { background: #2563eb; color: #fff; border: none; padding: 7px 14px; border-radius: 6px; font-weight: bold; cursor: pointer; }
            .btn-secondary { background: #f1f5f9; color: #334155; border: 1px solid #cbd5e1; padding: 7px 12px; border-radius: 6px; font-weight: bold; cursor: pointer; }
            .btn-snap { background: #059669; color: #fff; font-weight: bold; padding: 7px 14px; border-radius: 6px; border: none; cursor: pointer; margin-left: auto; }
            .layout { display: flex; flex-direction: column; width: 100%; align-items: center; }
            .canvas-card { width: 100%; background: #ffffff; border-radius: 10px; border: 1px solid #e2e8f0; padding: 12px; }
            .video-wrapper { position: relative; width: 100%; background: #000; border-radius: 6px; overflow: hidden; display: flex; justify-content: center; align-items: center; }
            video { width: 100%; max-height: 440px; display: block; object-fit: contain; }
            canvas { position: absolute; top: 0; left: 0; }
            select { background: #ffffff; color: #0f172a; border: 1px solid #cbd5e1; padding: 5px 8px; border-radius: 6px; font-weight: bold; }
            input[type=range] { width: 100%; height: 6px; border-radius: 3px; background: #cbd5e1; outline: none; cursor: pointer; }
        </style>
        </head>
        <body>
            <div class="control-bar">
                <span style="font-size:13px; color:#64748b;">抽取幀數 Frame Extraction Count:</span>
                <select id="selGhostCount">
                    <option value="20">20 影格 20 Frames</option>
                    <option value="40" selected>40 影格 High Frame Rate 40 Frames</option>
                    <option value="60">60 影格 Full Motion 60 Frames</option>
                </select>
                <button id="btnCapGhost" class="btn">捕捉攻欄至落地殘影 Capture Ghosting Track</button>
                <button id="btnClearG" class="btn btn-secondary">清空殘影 Clear Ghosting</button>
                <button id="btnSnapGhostImg" class="btn-snap">擷取殘影畫面存檔 Capture Ghosting Frame</button>
            </div>

            <div class="layout">
                <div class="canvas-card">
                    <div class="video-wrapper">
                        <video id="vidG" playsinline muted></video>
                        <canvas id="cvsG"></canvas>
                    </div>
                    
                    <div style="width:100%; margin-top:8px; background:#f8fafc; padding:8px 12px; border-radius:8px; border:1px solid #e2e8f0;">
                        <div style="display:flex; justify-content:space-between; font-size:12px; color:#64748b; margin-bottom:4px;">
                            <span>影片進度 Video Progress</span>
                            <span id="txtTimeG" style="color:#2563eb; font-weight:bold; font-size:13px;">0.00s</span>
                        </div>
                        <input type="range" id="sliderG" min="0" max="100" value="0" step="0.01">
                    </div>

                    <div style="display:flex; gap:10px; margin-top:10px; width:100%; align-items:center; flex-wrap:wrap;">
                        <button id="btnPlayG" class="btn-blue">播放 暫停 Play Pause</button>
                        <button id="btnStepBackG" class="btn btn-secondary">上一幀 Frame Back</button>
                        <button id="btnStepForwardG" class="btn btn-secondary">下一幀 Frame Forward</button>
                        <span style="font-size:12px; color:#64748b; margin-left:6px;">播放速度 Speed:</span>
                        <select id="selSpeedG">
                            <option value="0.25">0.25x 慢動作 Slow Motion</option>
                            <option value="0.5">0.5x</option>
                            <option value="0.75">0.75x</option>
                            <option value="1.0" selected>1.0x 正常 Normal</option>
                        </select>
                    </div>
                </div>
            </div>

            <script>
                const vidG = document.getElementById('vidG');
                const cvsG = document.getElementById('cvsG');
                const ctxG = cvsG.getContext('2d');
                const sliderG = document.getElementById('sliderG');
                const txtTimeG = document.getElementById('txtTimeG');
                const btnPlayG = document.getElementById('btnPlayG');
                const btnStepBackG = document.getElementById('btnStepBackG');
                const btnStepForwardG = document.getElementById('btnStepForwardG');
                const selSpeedG = document.getElementById('selSpeedG');

                let ghostHistory = [], currentPoseLM = null;

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
                    const blob = b64ToBlob("___B64_STROBE___", "___MIME_STROBE___");
                    vidG.src = URL.createObjectURL(blob);
                } catch(e) { console.error(e); }

                function syncSize() {
                    if (vidG.clientWidth > 0 && vidG.clientHeight > 0) {
                        cvsG.width = vidG.clientWidth; cvsG.height = vidG.clientHeight;
                    }
                }
                vidG.addEventListener('loadedmetadata', () => { sliderG.max = vidG.duration; syncSize(); });
                vidG.addEventListener('canplay', syncSize);
                window.addEventListener('resize', syncSize);

                vidG.addEventListener('timeupdate', async () => {
                    txtTimeG.innerText = (vidG.currentTime || 0).toFixed(2) + 's';
                    if (!vidG.paused) sliderG.value = vidG.currentTime;
                    if (vidG.readyState >= 2) await pose.send({ image: vidG });
                });
                sliderG.addEventListener('input', (e) => { vidG.currentTime = parseFloat(e.target.value); });
                btnPlayG.addEventListener('click', () => {
                    if (vidG.paused) { vidG.play(); btnPlayG.innerText = '暫停 Pause'; }
                    else { vidG.pause(); btnPlayG.innerText = '播放 Play'; }
                });

                selSpeedG.addEventListener('change', (e) => { vidG.playbackRate = parseFloat(e.target.value); });

                const FRAME_TIME = 1 / 60;
                btnStepBackG.addEventListener('click', () => {
                    vidG.pause(); btnPlayG.innerText = '播放 Play';
                    vidG.currentTime = Math.max(0, vidG.currentTime - FRAME_TIME);
                    sliderG.value = vidG.currentTime;
                });
                btnStepForwardG.addEventListener('click', () => {
                    vidG.pause(); btnPlayG.innerText = '播放 Play';
                    vidG.currentTime = Math.min(vidG.duration, vidG.currentTime + FRAME_TIME);
                    sliderG.value = vidG.currentTime;
                });

                const pose = new Pose({ locateFile: (f) => `https://cdn.jsdelivr.net/npm/@mediapipe/pose/${f}` });
                pose.setOptions({ modelComplexity: 1, smoothLandmarks: true });
                pose.onResults((res) => {
                    if (res.poseLandmarks) { currentPoseLM = res.poseLandmarks; draw(); }
                });

                document.getElementById('btnCapGhost').addEventListener('click', async () => {
                    const cnt = parseInt(document.getElementById('selGhostCount').value);
                    ghostHistory = [];
                    const startT = vidG.currentTime;
                    for (let i = 0; i < cnt; i++) {
                        const targetT = startT + (i * 0.033);
                        if (targetT > vidG.duration) break;
                        vidG.currentTime = targetT;
                        await pose.send({ image: vidG });
                        if (currentPoseLM) { ghostHistory.push(currentPoseLM); }
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
                        POSE_LINES.forEach(([i, j]) => {
                            if (lm[i] && lm[j]) { ctxG.beginPath(); ctxG.moveTo(lm[i].x * w, lm[i].y * h); ctxG.lineTo(lm[j].x * w, lm[j].y * h); ctxG.stroke(); }
                        });
                        ctxG.restore();
                    });

                    if (currentPoseLM) {
                        ctxG.strokeStyle = '#059669'; ctxG.lineWidth = 3;
                        POSE_LINES.forEach(([i, j]) => {
                            if (currentPoseLM[i] && currentPoseLM[j]) { ctxG.beginPath(); ctxG.moveTo(currentPoseLM[i].x * w, currentPoseLM[i].y * h); ctxG.lineTo(currentPoseLM[j].x * w, currentPoseLM[j].y * h); ctxG.stroke(); }
                        });
                    }
                }

                document.getElementById('btnClearG').addEventListener('click', () => { ghostHistory = []; draw(); });
                document.getElementById('btnSnapGhostImg').addEventListener('click', () => {
                    if (ghostHistory.length === 0) return alert("請先點擊捕捉殘影 Capture Ghosting First!");
                    const off = document.createElement('canvas');
                    off.width = vidG.videoWidth; off.height = vidG.videoHeight;
                    const offCtx = off.getContext('2d');
                    offCtx.drawImage(vidG, 0, 0, off.width, off.height);
                    offCtx.drawImage(cvsG, 0, 0, off.width, off.height);
                    
                    const textAreas = window.parent.document.querySelectorAll('textarea');
                    let targetGhostTA = null;
                    textAreas.forEach(ta => {
                        if ((ta.getAttribute('aria-label') || '').includes('ghost_img_b64')) targetGhostTA = ta;
                    });
                    if (targetGhostTA) {
                        syncToStreamlit(targetGhostTA, off.toDataURL('image/jpeg', 0.85));
                        alert("殘影畫面已成功擷取 Ghosting Frame Captured Successfully!");
                    }
                });
            </script>
        </body>
        </html>"""

        strobe_html_code = (
            strobe_html_template
            .replace("___MIME_STROBE___", mime_strobe)
            .replace("___B64_STROBE___", b64_strobe)
        )
        components.html(strobe_html_code, height=720, scrolling=False)

        st.markdown("---")
        st.subheader("儲存攻欄落地殘影截圖紀錄 Save Ghosting Record")
        captured_ghost_img = st.text_area("ghost_img_b64", key="captured_ghost_img", label_visibility="collapsed")

        if captured_ghost_img:
            st.success("攻欄落地殘影畫面擷取成功 Ghosting Frame Captured Successfully！")
            st.image(captured_ghost_img, caption="殘影畫面合成圖 Composite Ghosting Image", use_container_width=True)
            if st.session_state["user_info"]:
                with st.form("save_ghost_form"):
                    rec_title_ghost = st.text_input("紀錄名稱 Record Title", value=f"攻欄至落地殘影軌跡 Ghosting Track {uploaded_strobe_file.name}")
                    if st.form_submit_button("儲存至資料庫 Save to Database"):
                        save_record(
                            username=st.session_state["user_info"]["username"],
                            module="攻欄至落地殘影軌跡 Ghosting Track",
                            title=rec_title_ghost,
                            details_dict={"image_b64": captured_ghost_img, "saved_at": str(datetime.datetime.now())}
                        )
                        st.success("殘影紀錄已成功存檔 Ghosting Record Saved Successfully！")

# ==================== 【頁面 4：雙影同步】 ====================
elif page == "雙影同步 Dual Video Sync":
    st.title("雙影同步模組 Dual Video Sync Module")
    st.markdown("---")

    col_a, col_b = st.columns(2)
    with col_a:
        st.subheader("影片一 Video 1")
        file_a = st.file_uploader("上傳影片一 Upload Video 1", type=["mp4", "mov", "avi"], key="file_a")
    with col_b:
        st.subheader("影片二 Video 2")
        file_b = st.file_uploader("上傳影片二 Upload Video 2", type=["mp4", "mov", "avi"], key="file_b")

    if file_a and file_b:
        with st.spinner("正在進行手機影片格式優化與雙影解碼 (H.264 MP4)..."):
            bytes_a, mime_a = process_and_convert_video(file_a)
            bytes_b, mime_b = process_and_convert_video(file_b)
            b64_a = base64.b64encode(bytes_a).decode("utf-8")
            b64_b = base64.b64encode(bytes_b).decode("utf-8")

        dual_html_template = """<!DOCTYPE html>
        <html>
        <head>
        <style>
            * { box-sizing: border-box; }
            body { font-family: -apple-system, sans-serif; background: transparent; color: #0f172a; margin: 0; padding: 0; }
            .top-bar { display: flex; gap: 10px; margin-bottom: 10px; background: #ffffff; padding: 12px 18px; border-radius: 8px; border: 1px solid #e2e8f0; align-items: center; justify-content: center; flex-wrap: wrap; }
            .main-slider-box { background: #ffffff; border: 1px solid #2563eb; padding: 10px 16px; border-radius: 8px; margin-bottom: 12px; }
            .btn-align-play { background: #059669; color: #fff; border: none; padding: 8px 16px; border-radius: 6px; font-weight: bold; cursor: pointer; font-size: 13px; }
            .btn-align-jump { background: #d97706; color: #fff; border: none; padding: 8px 14px; border-radius: 6px; font-weight: bold; cursor: pointer; font-size: 13px; }
            .btn-sync { background: #2563eb; color: #fff; border: none; padding: 8px 14px; border-radius: 6px; font-weight: bold; cursor: pointer; font-size: 13px; }
            .btn-reset { background: #f1f5f9; color: #334155; border: 1px solid #cbd5e1; padding: 8px 14px; border-radius: 6px; font-weight: bold; cursor: pointer; font-size: 13px; }
            .btn-snap { background: #059669; color: #fff; border: none; padding: 8px 16px; border-radius: 6px; font-weight: bold; cursor: pointer; font-size: 13px; margin-left: auto; }
            .layout { display: flex; gap: 15px; width: 100%; }
            .vid-card { flex: 1; background: #ffffff; border-radius: 10px; border: 1px solid #e2e8f0; padding: 12px; }
            video { width: 100%; border-radius: 6px; max-height: 360px; object-fit: contain; background: #000; display: block; }
            .ctrl-row { display: flex; gap: 6px; margin-top: 8px; align-items: center; flex-wrap: wrap; }
            .btn-sm { background: #f1f5f9; color: #334155; border: 1px solid #cbd5e1; padding: 5px 8px; border-radius: 4px; font-size: 12px; cursor: pointer; }
            .btn-set-align { background: #2563eb; color: #fff; border: none; padding: 5px 10px; border-radius: 4px; font-size: 12px; font-weight: bold; cursor: pointer; }
            select { background: #ffffff; color: #0f172a; border: 1px solid #cbd5e1; padding: 4px; border-radius: 4px; font-size: 12px; font-weight: bold; }
            input[type=range] { width: 100%; height: 6px; border-radius: 3px; background: #cbd5e1; outline: none; cursor: pointer; }
        </style>
        </head>
        <body>
            <div class="main-slider-box">
                <div style="display:flex; justify-content:space-between; margin-bottom:4px; font-size:12px; color:#64748b;">
                    <span style="color:#2563eb; font-weight:bold;">雙影片同步主控制滑桿 Master Sync Time Slider</span>
                    <span>相對同步時間 Relative Sync Time: <b id="txtMasterTime" style="color:#059669; font-size:14px;">0.00s</b></span>
                </div>
                <input type="range" id="sliderMaster" min="0" max="100" value="0" step="0.01">
            </div>

            <div class="top-bar">
                <button id="btnPlayAlign" class="btn-align-play">從對齊點同步播放 Sync Play from Alignment</button>
                <button id="btnJumpAlign" class="btn-align-jump">跳至對齊點 Jump to Alignment</button>
                <button id="btnPlayDual" class="btn-sync">同步暫停 播放 Sync Play Pause</button>
                <button id="btnResetDual" class="btn-reset">時間歸零 Reset Time</button>
                <button id="btnSnapDual" class="btn-snap">擷取雙影對比畫面 Capture Dual Frame</button>
            </div>
            
            <div class="layout">
                <div class="vid-card">
                    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
                        <h5 style="margin:0; color:#2563eb;">影片一 Video 1</h5>
                        <span id="txtTimeA" style="font-size:12px; color:#2563eb; font-weight:bold;">0.00s</span>
                    </div>
                    <video id="vidA" muted playsinline></video>
                    <input type="range" id="sliderA" min="0" max="100" value="0" step="0.01" style="width:100%; margin-top:6px;">
                    
                    <div class="ctrl-row">
                        <button id="btnSetAlignA" class="btn-set-align">設為影片一對齊點 Set Video 1 Alignment</button>
                        <button id="btnPlayA" class="btn-sm">播放 暫停 Play Pause</button>
                        <button id="btnStepBackA" class="btn-sm">上一幀 Frame Back</button>
                        <button id="btnStepForwardA" class="btn-sm">下一幀 Frame Forward</button>
                        <span style="font-size:11px; color:#64748b;">速度 Speed:</span>
                        <select id="selSpeedA">
                            <option value="0.25">0.25x</option>
                            <option value="0.5">0.5x</option>
                            <option value="0.75">0.75x</option>
                            <option value="1.0" selected>1.0x</option>
                        </select>
                    </div>
                    <div id="txtAlignA" style="margin-top:6px; font-size:12px; color:#059669; font-weight:bold;">影片一對齊點 Video 1 Alignment Point: 未設定 Not Set 0.00s</div>
                </div>

                <div class="vid-card">
                    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
                        <h5 style="margin:0; color:#d97706;">影片二 Video 2</h5>
                        <span id="txtTimeB" style="font-size:12px; color:#d97706; font-weight:bold;">0.00s</span>
                    </div>
                    <video id="vidB" muted playsinline></video>
                    <input type="range" id="sliderB" min="0" max="100" value="0" step="0.01" style="width:100%; margin-top:6px;">
                    
                    <div class="ctrl-row">
                        <button id="btnSetAlignB" class="btn-set-align" style="background:#d97706;">設為影片二對齊點 Set Video 2 Alignment</button>
                        <button id="btnPlayB" class="btn-sm">播放 暫停 Play Pause</button>
                        <button id="btnStepBackB" class="btn-sm">上一幀 Frame Back</button>
                        <button id="btnStepForwardB" class="btn-sm">下一幀 Frame Forward</button>
                        <span style="font-size:11px; color:#64748b;">速度 Speed:</span>
                        <select id="selSpeedB">
                            <option value="0.25">0.25x</option>
                            <option value="0.5">0.5x</option>
                            <option value="0.75">0.75x</option>
                            <option value="1.0" selected>1.0x</option>
                        </select>
                    </div>
                    <div id="txtAlignB" style="margin-top:6px; font-size:12px; color:#059669; font-weight:bold;">影片二對齊點 Video 2 Alignment Point: 未設定 Not Set 0.00s</div>
                </div>
            </div>

            <script>
                const vidA = document.getElementById('vidA');
                const vidB = document.getElementById('vidB');
                const sliderA = document.getElementById('sliderA');
                const sliderB = document.getElementById('sliderB');
                const sliderMaster = document.getElementById('sliderMaster');
                const txtMasterTime = document.getElementById('txtMasterTime');
                const txtTimeA = document.getElementById('txtTimeA');
                const txtTimeB = document.getElementById('txtTimeB');
                const txtAlignA = document.getElementById('txtAlignA');
                const txtAlignB = document.getElementById('txtAlignB');

                let alignTimeA = 0;
                let alignTimeB = 0;

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

                vidA.addEventListener('loadedmetadata', () => {
                    sliderA.max = vidA.duration;
                    updateMasterMax();
                });
                vidB.addEventListener('loadedmetadata', () => {
                    sliderB.max = vidB.duration;
                    updateMasterMax();
                });

                function updateMasterMax() {
                    const durA = (vidA.duration || 0) - alignTimeA;
                    const durB = (vidB.duration || 0) - alignTimeB;
                    const maxSyncDur = Math.max(0, Math.max(durA, durB));
                    sliderMaster.max = maxSyncDur > 0 ? maxSyncDur : 100;
                }

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

                sliderA.addEventListener('input', (e) => vidA.currentTime = parseFloat(e.target.value));
                sliderB.addEventListener('input', (e) => vidB.currentTime = parseFloat(e.target.value));

                sliderMaster.addEventListener('input', (e) => {
                    const relOffset = parseFloat(e.target.value);
                    txtMasterTime.innerText = relOffset.toFixed(2) + 's';
                    vidA.currentTime = Math.min(vidA.duration, Math.max(0, alignTimeA + relOffset));
                    vidB.currentTime = Math.min(vidB.duration, Math.max(0, alignTimeB + relOffset));
                    sliderA.value = vidA.currentTime;
                    sliderB.value = vidB.currentTime;
                });

                document.getElementById('btnSetAlignA').addEventListener('click', () => {
                    alignTimeA = vidA.currentTime;
                    txtAlignA.innerText = `影片一對齊點 Video 1 Alignment Point: ${alignTimeA.toFixed(2)}s`;
                    updateMasterMax();
                });
                document.getElementById('btnSetAlignB').addEventListener('click', () => {
                    alignTimeB = vidB.currentTime;
                    txtAlignB.innerText = `影片二對齊點 Video 2 Alignment Point: ${alignTimeB.toFixed(2)}s`;
                    updateMasterMax();
                });

                document.getElementById('btnPlayAlign').addEventListener('click', () => {
                    vidA.pause(); vidB.pause();
                    vidA.currentTime = alignTimeA;
                    vidB.currentTime = alignTimeB;
                    vidA.play(); vidB.play();
                });

                document.getElementById('btnJumpAlign').addEventListener('click', () => {
                    vidA.pause(); vidB.pause();
                    vidA.currentTime = alignTimeA;
                    vidB.currentTime = alignTimeB;
                });

                document.getElementById('btnPlayA').addEventListener('click', () => { if (vidA.paused) vidA.play(); else vidA.pause(); });
                document.getElementById('btnPlayB').addEventListener('click', () => { if (vidB.paused) vidB.play(); else vidB.pause(); });

                document.getElementById('selSpeedA').addEventListener('change', (e) => vidA.playbackRate = parseFloat(e.target.value));
                document.getElementById('selSpeedB').addEventListener('change', (e) => vidB.playbackRate = parseFloat(e.target.value));

                const FRAME_TIME = 1 / 60;
                document.getElementById('btnStepBackA').addEventListener('click', () => { vidA.pause(); vidA.currentTime = Math.max(0, vidA.currentTime - FRAME_TIME); });
                document.getElementById('btnStepForwardA').addEventListener('click', () => { vidA.pause(); vidA.currentTime = Math.min(vidA.duration, vidA.currentTime + FRAME_TIME); });
                document.getElementById('btnStepBackB').addEventListener('click', () => { vidB.pause(); vidB.currentTime = Math.max(0, vidB.currentTime - FRAME_TIME); });
                document.getElementById('btnStepForwardB').addEventListener('click', () => { vidB.pause(); vidB.currentTime = Math.min(vidB.duration, vidB.currentTime + FRAME_TIME); });

                document.getElementById('btnPlayDual').addEventListener('click', () => {
                    if (vidA.paused || vidB.paused) { vidA.play(); vidB.play(); }
                    else { vidA.pause(); vidB.pause(); }
                });

                document.getElementById('btnResetDual').addEventListener('click', () => {
                    vidA.pause(); vidB.pause();
                    vidA.currentTime = 0; vidB.currentTime = 0;
                    sliderMaster.value = 0;
                    txtMasterTime.innerText = '0.00s';
                });

                document.getElementById('btnSnapDual').addEventListener('click', () => {
                    if (!vidA.videoWidth || !vidB.videoWidth) return alert("影片尚未準備就緒 Video Not Ready!");
                    
                    const off = document.createElement('canvas');
                    const w1 = vidA.videoWidth, h1 = vidA.videoHeight;
                    const w2 = vidB.videoWidth, h2 = vidB.videoHeight;
                    const targetH = Math.max(h1, h2);
                    const targetW1 = Math.round(w1 * (targetH / h1));
                    const targetW2 = Math.round(w2 * (targetH / h2));
                    
                    off.width = targetW1 + targetW2 + 30;
                    off.height = targetH + 60;
                    
                    const offCtx = off.getContext('2d');
                    offCtx.fillStyle = "#f8fafc";
                    offCtx.fillRect(0, 0, off.width, off.height);
                    
                    offCtx.fillStyle = "#2563eb";
                    offCtx.font = "bold 18px sans-serif";
                    offCtx.fillText(`影片一 Video 1 (${vidA.currentTime.toFixed(2)}s) - 對齊 Align: ${alignTimeA.toFixed(2)}s`, 15, 35);
                    
                    offCtx.fillStyle = "#d97706";
                    offCtx.fillText(`影片二 Video 2 (${vidB.currentTime.toFixed(2)}s) - 對齊 Align: ${alignTimeB.toFixed(2)}s`, targetW1 + 35, 35);
                    
                    offCtx.drawImage(vidA, 10, 50, targetW1, targetH);
                    offCtx.drawImage(vidB, targetW1 + 20, 50, targetW2, targetH);

                    const imgBase64 = off.toDataURL('image/jpeg', 0.85);
                    const textAreas = window.parent.document.querySelectorAll('textarea');
                    let targetDualTA = null, targetDualDataTA = null;
                    textAreas.forEach(ta => {
                        const label = ta.getAttribute('aria-label') || '';
                        if (label.includes('dual_img_b64')) targetDualTA = ta;
                        if (label.includes('dual_data_json')) targetDualDataTA = ta;
                    });
                    if (targetDualTA && targetDualDataTA) {
                        syncToStreamlit(targetDualTA, imgBase64);
                        syncToStreamlit(targetDualDataTA, JSON.stringify({
                            time_a: vidA.currentTime.toFixed(2),
                            time_b: vidB.currentTime.toFixed(2),
                            align_a: alignTimeA.toFixed(2),
                            align_b: alignTimeB.toFixed(2)
                        }));
                        alert("雙影對比畫面已成功擷取 Dual Frame Captured Successfully!");
                    }
                });
            </script>
        </body>
        </html>"""

        dual_html_code = (
            dual_html_template
            .replace("___B64_A___", b64_a).replace("___MIME_A___", mime_a)
            .replace("___B64_B___", b64_b).replace("___MIME_B___", mime_b)
        )
        components.html(dual_html_code, height=720, scrolling=False)

        st.markdown("---")
        st.subheader("儲存雙影對比畫面與紀錄 Save Dual Sync Record")
        captured_dual_img = st.text_area("dual_img_b64", key="captured_dual_img", label_visibility="collapsed")
        captured_dual_json = st.text_area("dual_data_json", key="captured_dual_json", label_visibility="collapsed")

        if captured_dual_img and captured_dual_json:
            st.success("雙影對比畫面擷取成功 Dual Frame Captured Successfully！")
            st.image(captured_dual_img, caption="雙影對比畫面合成圖 Dual Sync Composite Image", use_container_width=True)
            if st.session_state["user_info"]:
                with st.form("save_dual_form"):
                    rec_title_dual = st.text_input("紀錄名稱 Record Title", value=f"雙影同步對比 Dual Sync {file_a.name} vs {file_b.name}")
                    if st.form_submit_button("儲存至資料庫 Save to Database"):
                        save_record(
                            username=st.session_state["user_info"]["username"],
                            module="雙影同步 Dual Video Sync",
                            title=rec_title_dual,
                            details_dict={
                                "image_b64": captured_dual_img,
                                "sync_data": json.loads(captured_dual_json),
                                "file_a": file_a.name,
                                "file_b": file_b.name,
                                "saved_at": str(datetime.datetime.now())
                            }
                        )
                        st.success("雙影同步紀錄已成功存檔 Save Successful！")

# ==================== 【頁面 5：跨期數據對比】 ====================
elif page == "跨期數據對比 Longitudinal Comparison":
    st.title("跨期數據對比分析 Longitudinal Comparison Analysis")
    st.markdown("---")

    if st.session_state["user_info"] is None:
        st.warning("請先在左側邊欄登入帳號 Please Login in Sidebar First")
    else:
        user = st.session_state["user_info"]
        is_coach = (user["role"] == "coach")

        if is_coach:
            conn = sqlite3.connect(DB_NAME)
            c = conn.cursor()
            c.execute("SELECT DISTINCT username FROM records")
            all_users = [r[0] for r in c.fetchall()]
            conn.close()
            target_user = st.selectbox("請選擇選手 Select Athlete：", all_users) if all_users else None
        else:
            target_user = user["username"]

        if target_user:
            mod_choice = st.radio("請選擇對比項目 Select Item：", ["欄間秒數及速度計算 Hurdle Time & Speed Calculation", "關鍵影格角度量測 Keyframe Angle Measurement"])
            records = get_user_records(username=target_user, module=mod_choice)

            if not records:
                st.warning(f"使用者 User {target_user} 在 {mod_choice} 尚無歷史紀錄 No History Records")
            else:
                rec_options = {f"ID:{r[0]} ｜ 標題 Title:{r[3]} ｜ 時間 Time:{r[5]}": r for r in records}
                selected_keys = st.multiselect("請選擇 2 至 5 筆紀錄進行對照 Select 2 to 5 Records：", list(rec_options.keys()))

                if len(selected_keys) >= 2:
                    st.markdown("---")
                    st.subheader("多日數據對比明細 Comparison Details")

                    selected_recs = sorted([rec_options[k] for k in selected_keys], key=lambda x: x[5])

                    if mod_choice == "欄間秒數及速度計算 Hurdle Time & Speed Calculation":
                        plot_dict_speed = {}
                        table_data = {}

                        for rec in selected_recs:
                            details = json.loads(rec[4])
                            tm = details.get("timer_metrics", {})
                            splits = tm.get("splits", [])
                            rec_label = f"{rec[5]} ({rec[3]})"

                            for s in splits:
                                seg = s.get("segment")
                                spd = s.get("speed_ms", "0")
                                split_t = s.get("split_sec", "-")
                                cum_t = s.get("cum_sec", "-")
                                
                                if seg not in plot_dict_speed:
                                    plot_dict_speed[seg] = {}
                                plot_dict_speed[seg][rec_label] = float(spd)

                                if seg not in table_data:
                                    table_data[seg] = {}
                                table_data[seg][rec_label] = f"單段 {split_t}s (累積 {cum_t}s) ｜ 速度 {spd} m/s"

                        st.markdown("##### 跨期分段速度變化趨勢折線圖 Speed Progression Line Chart")
                        if plot_dict_speed:
                            df_speed = pd.DataFrame.from_dict(plot_dict_speed, orient='index')

                            CANONICAL_SEGMENTS = [
                                '起跑->H1 Start->H1',
                                'H1->H2', 'H2->H3', 'H3->H4', 'H4->H5',
                                'H5->H6', 'H6->H7', 'H7->H8', 'H8->H9', 'H9->H10',
                                'H10->終點 H10->Finish'
                            ]
                            ordered_indices = [s for s in CANONICAL_SEGMENTS if s in df_speed.index]
                            for s in df_speed.index:
                                if s not in ordered_indices:
                                    ordered_indices.append(s)
                            
                            df_speed = df_speed.reindex(ordered_indices)
                            df_speed.index = pd.Categorical(df_speed.index, categories=ordered_indices, ordered=True)
                            df_speed = df_speed.sort_index()
                            st.line_chart(df_speed)

                        st.markdown("##### 各段落耗時與平均速度對照表 Speed & Time Comparison Table")
                        if table_data:
                            df_table = pd.DataFrame.from_dict(table_data, orient='index')
                            st.dataframe(df_table, use_container_width=True)

                    elif mod_choice == "關鍵影格角度量測 Keyframe Angle Measurement":
                        plot_dict_angle = {}

                        for rec in selected_recs:
                            details = json.loads(rec[4])
                            am = details.get("angle_metrics", {})
                            angles = am.get("angles", []) if isinstance(am, dict) else am
                            rec_label = f"{rec[5]} ({rec[3]})"

                            if isinstance(angles, list):
                                for a in angles:
                                    name = a.get("name", "未知名稱")
                                    val_str = str(a.get("value", "0")).replace("°", "").strip()
                                    try:
                                        val = float(val_str)
                                    except ValueError:
                                        val = 0.0
                                    
                                    if rec_label not in plot_dict_angle:
                                        plot_dict_angle[rec_label] = {}
                                    plot_dict_angle[rec_label][name] = val

                        st.markdown("##### 跨期角度變化趨勢折線圖 Angle Progression Line Chart")
                        if plot_dict_angle:
                            df_angle = pd.DataFrame.from_dict(plot_dict_angle, orient='index')
                            st.line_chart(df_angle)

                        st.markdown("##### 跨期畫面與角度數據明細 Comparison Cards")
                        col_arr = st.columns(len(selected_recs))
                        for idx, rec in enumerate(selected_recs):
                            details = json.loads(rec[4])
                            with col_arr[idx]:
                                st.markdown(f"**日期 Date：{rec[5]}**")
                                st.write(f"標題 Title：{rec[3]}")
                                if "image_b64" in details:
                                    st.image(details["image_b64"], use_container_width=True)
                                am = details.get("angle_metrics", {})
                                angles = am.get("angles", []) if isinstance(am, dict) else am
                                rel_t = am.get("rel_time_sec", am.get("time_sec", "0.00")) if isinstance(am, dict) else "0.00"
                                phase = am.get("phase_tag", "關鍵點 Keypoint") if isinstance(am, dict) else "關鍵點 Keypoint"
                                st.write(f"時間 Time：`{rel_t} 秒 sec`")
                                st.write(f"階段 Phase：`{phase}`")
                                if isinstance(angles, list):
                                    for a in angles:
                                        st.write(f"• {a.get('name')}：`{a.get('value')}`")

# ==================== 【頁面 6：課表紀錄】 ====================
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
                conn = sqlite3.connect(DB_NAME)
                c = conn.cursor()
                c.execute("SELECT DISTINCT username FROM users WHERE role='player'")
                player_list = [r[0] for r in c.fetchall()]
                conn.close()
                if not player_list: player_list = [user["username"]]
                target_athlete = st.selectbox("指定選手 Select Athlete", player_list, key="plan_sel_athlete")
            else:
                target_athlete = user["username"]

            if "dyn_injuries_user" not in st.session_state or st.session_state["dyn_injuries_user"] != target_athlete:
                prev_plans = get_training_plans(username=target_athlete)
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
                                    "notes": ""
                                })
                    except Exception:
                        pass

                if not carried_injuries:
                    carried_injuries = [{"area": "右大腿股四頭肌 Right Quadriceps", "level": 0, "notes": ""}]

                st.session_state["dyn_injuries"] = carried_injuries
                st.session_state["dyn_injuries_user"] = target_athlete

            BODY_PART_OPTIONS = [
                "頭頸部 Head & Neck", "右肩/三角肌 Right Shoulder Deltoid", "左肩/三角肌 Left Shoulder Deltoid",
                "胸大肌 Pectoralis Major", "腹直肌/核心 Rectus Abdominis", "下背/腰椎肌群 Lower Back Lumbar",
                "臀大肌 Gluteus Maximus", "右大腿股四頭肌 Right Quadriceps", "左大腿股四頭肌 Left Quadriceps",
                "右大腿後側膕繩肌 Right Hamstrings", "左大腿後側膕繩肌 Left Hamstrings", "右膝/髕腱 Right Patellar Tendon",
                "左膝/髕腱 Left Patellar Tendon", "右小腿/跟腱 Right Calf & Achilles", "左小腿/跟腱 Left Calf & Achilles",
                "右腳踝/足底 Right Ankle & Plantar", "左腳踝/足底 Left Ankle & Plantar"
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
            st.markdown("###### 部位疼痛評估與傷病復原追蹤 (前次傷病已自動載入)")

            injuries_input_data = []
            to_delete_idx = None
            for idx, item in enumerate(st.session_state["dyn_injuries"]):
                c_sel1, c_sel2, c_sel3, c_sel4 = st.columns([1.5, 1.2, 1.5, 0.4])
                with c_sel1:
                    default_idx = BODY_PART_OPTIONS.index(item["area"]) if item["area"] in BODY_PART_OPTIONS else 0
                    selected_area = st.selectbox(f"部位 #{idx+1}", BODY_PART_OPTIONS, index=default_idx, key=f"sel_area_{idx}")
                with c_sel2:
                    selected_lvl = st.slider(f"疼痛等級 (0-10)", 0, 10, item["level"], key=f"sel_lvl_{idx}")
                with c_sel3:
                    selected_note = st.text_input(f"備註說明", value=item.get("notes", ""), key=f"sel_note_{idx}")
                with c_sel4:
                    st.markdown("<br>", unsafe_allow_html=True)
                    if st.button("刪除", key=f"del_inj_{idx}"):
                        to_delete_idx = idx

                injuries_input_data.append({
                    "id": idx + 1,
                    "area": selected_area,
                    "level": selected_lvl,
                    "notes": selected_note
                })

            if to_delete_idx is not None:
                st.session_state["dyn_injuries"].pop(to_delete_idx)
                st.rerun()

            if st.button("新增身體部位痛點 Add Muscle Region", key="add_more_inj_btn"):
                st.session_state["dyn_injuries"].append({"area": "右膝/髕腱 Right Patellar Tendon", "level": 0, "notes": ""})
                st.rerun()

            st.markdown("---")
            st.markdown("##### 3. 課表內容與自主備註 Plan Details & Notes")
            content = st.text_area("課表細節與組數規劃 Plan Details & Sets", height=120, key="log_content")
            user_notes = st.text_area("動作感覺與教練叮嚀 Notes & Feedback", height=80, key="log_user_notes")

            if st.button("儲存訓練日誌 Save Training Log", key="btn_save_training_log", use_container_width=True):
                if content.strip():
                    extra_log_info = {
                        "duration_min": duration_min, "rpe_feeling": rpe_feeling, "weather_env": weather_env,
                        "fatigue_score": fatigue_score, "sleep_hours": sleep_hours, "sleep_mins": sleep_mins,
                        "total_sleep_hr": round(total_sleep_hr, 2), "resting_hr": resting_hr,
                        "injuries_list": injuries_input_data,
                        "body_map_img_b64": captured_body_map_img,
                        "user_notes": user_notes
                    }
                    save_training_plan(target_athlete, str(plan_date), category, content, rpe_feeling, json.dumps(extra_log_info, ensure_ascii=False))
                    
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
                conn = sqlite3.connect(DB_NAME)
                c = conn.cursor()
                c.execute("SELECT DISTINCT username FROM training_plans")
                plan_users = [r[0] for r in c.fetchall()]
                conn.close()
                filter_user = st.selectbox("篩選選手 Filter Athlete", ["全隊所有人 Entire Team"] + plan_users)
                query_user = None if filter_user == "全隊所有人 Entire Team" else filter_user
            else:
                query_user = user["username"]

            plans = get_training_plans(username=query_user)

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
                                st.markdown("**痛點選單與防護紀錄 Injury Details：**")
                                if inj_list:
                                    for inj in inj_list:
                                        lvl = inj.get("level", 0)
                                        area = inj.get("area", f"痛點 #{inj.get('id',1)}")
                                        n_str = inj.get("notes", "")
                                        n_fmt = f" ({n_str})" if n_str else ""
                                        if lvl >= 5: st.markdown(f"• :red[**{area}** ｜ 疼痛等級: {lvl}/10{n_fmt}]")
                                        elif lvl > 0: st.markdown(f"• :orange[**{area}** ｜ 疼痛等級: {lvl}/10 (持續復原中){n_fmt}]")
                                        else: st.markdown(f"• :green[**{area}** ｜ 疼痛等級: 0/10 (已痊癒結案){n_fmt}]")
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

# ==================== 【頁面 7：歷史分析紀錄】 ====================
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
                with st.expander(f"日期 Date：{date_key} （共 {len(day_recs)} 筆紀錄）"):
                    for rec in day_recs:
                        rec_id, rec_user, rec_module, rec_title, rec_details_str, rec_time = rec
                        rec_details = json.loads(rec_details_str)

                        st.markdown(f"#### [{rec_module}] {rec_title}")
                        st.write(f"建立者 Creator：`{rec_user}` ｜ 上傳時間 Upload Time：`{rec_time}`")

                        col_d1, col_d2 = st.columns([1.2, 0.8])
                        with col_d1:
                            if "image_b64" in rec_details:
                                st.image(rec_details["image_b64"], caption="畫面截圖 Composite Image", use_container_width=True)
                            else:
                                st.write("原始影片檔名: " + rec_details.get("video_filename", "未指定"))

                        with col_d2:
                            st.markdown("##### 數據明細 Data Details：")
                            if "angle_metrics" in rec_details:
                                am = rec_details["angle_metrics"]
                                if isinstance(am, dict):
                                    rel_t = am.get("rel_time_sec", am.get("time_sec", "0.00"))
                                    st.write(f"時間 Time：`{rel_t} 秒` ｜ 階段 Phase：`{am.get('phase_tag', '未標註')}`")
                                    for item in am.get("angles", []):
                                        st.write(f"• {item['name']}：`{item['value']}`")

                            if "timer_metrics" in rec_details:
                                tm = rec_details["timer_metrics"]
                                st.write(f"**總時間 Total**：`{tm.get('total_time')} 秒` ｜ **T0 起點**：`{tm.get('t0_sec', '0.00')}s`")
                                splits = tm.get("splits", [])
                                if splits:
                                    table_rows = [{"階段 Phase": s.get("segment"), "單段 (s)": f"{s.get('split_sec')}s", "速度 (m/s)": f"{s.get('speed_ms')} m/s"} for s in splits]
                                    st.dataframe(pd.DataFrame(table_rows), use_container_width=True, hide_index=True)

                        if is_coach or rec_user == user["username"]:
                            if st.button(f"刪除紀錄 ID {rec_id}", key=f"del_btn_{rec_id}"):
                                delete_record(rec_id)
                                st.rerun()
                        st.markdown("---")

# ==================== 【頁面 8：未來擴充規劃】 ====================
elif page == "未來擴充規劃 Future Roadmap":
    st.title("未來擴充規劃 Future Roadmap")
    st.markdown("---")
    st.subheader("規劃開發藍圖 Development Roadmap")
    st.markdown("""
    1. AI 3D 骨架關節重建 AI 3D Skeleton Reconstruction
    2. 過欄起跳點預測 Hurdle Takeoff Point Prediction
    3. 全隊長期體能與技術導向歷程庫 Long-term Team Performance Database
    """)
