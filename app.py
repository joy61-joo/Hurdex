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

try:
    from google import genai
    from google.genai import types
    HAS_GENAI = True
except ImportError:
    HAS_GENAI = False

def get_base64_video(uploaded_file):
    """將 Streamlit 上傳的影片檔轉換為 Base64 字串"""
    bytes_data = uploaded_file.getvalue()
    return base64.b64encode(bytes_data).decode('utf-8')

def get_mime_type(filename):
    """取得影片檔的 MIME 類型"""
    mime_type, _ = mimetypes.guess_type(filename)
    return mime_type if mime_type else "video/mp4"

def calc_sleep_duration(bed_t, wake_t):
    """依睡覺時間與起床時間自動計算睡眠時長（支援跨夜）"""
    bed_m = bed_t.hour * 60 + bed_t.minute
    wake_m = wake_t.hour * 60 + wake_t.minute
    if wake_m < bed_m:
        diff_m = (24 * 60 - bed_m) + wake_m
    else:
        diff_m = wake_m - bed_m
    return diff_m // 60, diff_m % 60, round(diff_m / 60.0, 2)
    
def analyze_hurdle_action_with_ai(image_b64_str, metrics_dict, mode="single"):
    """呼叫 Google Gemini API 進行跨欄動作截圖與關節角度生物力學診斷（含 503 擁塞自動重試與備援切換）"""
    import time

    if not HAS_GENAI:
        return False, "系統尚未安裝 `google-genai` 套件，請先於 `requirements.txt` 加入 `google-genai` 並重新部署。"

    try:
        api_key = st.secrets["gemini"]["api_key"]
    except Exception:
        return False, "尚未在 Streamlit Secrets 中設定 `[gemini]` 與 `api_key`，請先完成設定。"

    if not api_key or not str(api_key).strip():
        return False, "Gemini API Key 為空，請檢查 Streamlit Secrets 設定。"

    try:
        mime_type = "image/jpeg"
        if image_b64_str.startswith("data:") and ";base64," in image_b64_str:
            header, raw_b64 = image_b64_str.split(";base64,", 1)
            mime_type = header.replace("data:", "").strip() or "image/jpeg"
        else:
            raw_b64 = image_b64_str.split(",")[-1]

        img_bytes = base64.b64decode(raw_b64)
        client = genai.Client(api_key=str(api_key).strip())

        if mode == "dual":
            prompt = (
                "你是一位具備運動生物力學背景的專業田徑跨欄教練。請根據附圖（左為影片 A、右為影片 B）的選手跨欄動作標記、網上文獻數據與以下量測數據（已去識別化），"
                "以繁體中文提供精準、具體且可執行的教練分析報告：\n"
                "1. **雙影動作與角度差異對比**：比較影片 A 與影片 B 在軀幹前傾、攻欄腿/擺動腿摺疊、起跳/過欄/著地姿態與角度數值上的關鍵差異。\n"
                "2. **生物力學優缺點診斷**：指出目前動作中影響過欄效率、騰空時間或水平速度維持的核心問題。\n"
                f"量測數據：{json.dumps(metrics_dict, ensure_ascii=False)}"
            )
        else:
            prompt = (
                "你是一位具備運動生物力學背景的專業田徑跨欄教練。請根據附圖的選手跨欄關鍵影格標記、網上文獻數據與以下量測數據（已去識別化），"
                "以繁體中文提供精準、具體且可執行的教練分析報告：\n"
                "1. **動作階段與角度判讀**：結合所選動作階段（如起跳、過欄最高點、著地或欄間跑）與圖中關節角度/前傾角數值，評估身體重心與肢段排列是否理想。\n"
                "2. **技術亮點與修正重點**：明確列出 1～2 項做得好的地方，以及 1～2 項最需要優先修正的動作細節。\n"
                f"量測數據：{json.dumps(metrics_dict, ensure_ascii=False)}"
            )

        candidate_models = ["gemini-3.8-flash", "gemini-3.5-flash-lite"]
        last_err = None

        def _is_retryable_error(err_obj):
            err_low = str(err_obj).lower()
            retry_keywords = (
                "404", "not found", "no longer available",
                "503", "unavailable", "high demand", "overloaded",
                "500", "502", "504", "internal", "deadline"
            )
            return any(k in err_low for k in retry_keywords)

        def _try_generate_with_retry(m_name, max_retries=2):
            nonlocal last_err
            for attempt in range(max_retries):
                try:
                    resp = client.models.generate_content(
                        model=m_name,
                        contents=[
                            types.Part.from_bytes(data=img_bytes, mime_type=mime_type),
                            prompt
                        ]
                    )
                    if resp and getattr(resp, "text", None):
                        return resp.text.strip()
                except Exception as inner_e:
                    last_err = inner_e
                    err_low = str(inner_e).lower()
                    # 若模型已下架(404)，直接跳下一個模型，不浪費時間重試
                    if "404" in err_low or "not found" in err_low or "no longer available" in err_low:
                        return None
                    # 若為 503 尖峰擁塞或暫時性錯誤，等待 1.5 秒後重試，若仍失敗則切換備援模型
                    if _is_retryable_error(inner_e):
                        if attempt < max_retries - 1:
                            time.sleep(1.5)
                            continue
                        return None
                    raise inner_e
            return None

        # 依序嘗試主力與輕量模型（各自最多重試 2 次）
        for model_name in candidate_models:
            res_text = _try_generate_with_retry(model_name, max_retries=2)
            if res_text:
                return True, res_text

        # 動態查詢目前帳號可用的其他 Flash 模型作為最終備援
        try:
            excluded_kw = ("tts", "image", "live", "audio", "cyber", "embedding")
            for m in client.models.list():
                m_id = getattr(m, "name", "")
                actions = getattr(m, "supported_actions", None) or []
                if "generateContent" in actions and "flash" in m_id.lower() and not any(k in m_id.lower() for k in excluded_kw):
                    clean_id = m_id.replace("models/", "")
                    if clean_id not in candidate_models:
                        res_text = _try_generate_with_retry(clean_id, max_retries=1)
                        if res_text:
                            return True, res_text
        except Exception as list_e:
            last_err = list_e

        if last_err and ("503" in str(last_err) or "UNAVAILABLE" in str(last_err)):
            return False, "Google Gemini 伺服器目前處於尖峰高負載狀態（503），系統已自動重試所有備援模型仍暫時滿載，請稍候 10～20 秒後再點擊一次診斷按鈕。"

        return False, f"AI 模型呼叫失敗：{str(last_err)}"
    except Exception as e:
        err_str = str(e)
        if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
            return False, "已達 Gemini 免費方案每分鐘或每日呼叫上限，請稍候 1 分鐘後再試。"
        if "API_KEY_INVALID" in err_str or "400" in err_str or "403" in err_str:
            return False, f"API Key 驗證失敗，請確認 Google AI Studio 金鑰是否正確或已啟用（錯誤訊息：{err_str}）。"
        return False, f"AI 分析發生錯誤：{err_str}"
# -------------------- 【運動醫學體重監控評估演算法】 --------------------
MEASURE_TIMING_OPTIONS = [
    "🌅 晨起空腹如廁後 (最準確基準)",
    "🏋️ 訓練前 Pre-Training",
    "💦 訓練後 Post-Training",
    "🌙 睡前 Before Bed"
]

def evaluate_weight_goal_plan(start_w, target_w, start_date_str, target_date_str):
    """依據 IOC REDs (2023)、Garthe et al. (2011)、ISSN (2017) 與 World Athletics (2019) 評估目標計畫合理性"""
    try:
        s_dt = datetime.datetime.strptime(str(start_date_str), "%Y-%m-%d").date()
        t_dt = datetime.datetime.strptime(str(target_date_str), "%Y-%m-%d").date()
    except Exception:
        s_dt = datetime.date.today()
        t_dt = s_dt + datetime.timedelta(days=28)

    total_days = max((t_dt - s_dt).days, 1)
    total_weeks = total_days / 7.0
    delta_w = round(float(target_w) - float(start_w), 2)
    weekly_kg = delta_w / total_weeks
    weekly_pct = (weekly_kg / float(start_w)) * 100.0 if start_w > 0 else 0.0

    if delta_w < -0.2:
        goal_type = "loss"
        abs_pct = abs(weekly_pct)
        safe_weeks = abs(delta_w) / (float(start_w) * 0.0065)
        rec_date = s_dt + datetime.timedelta(days=int(round(safe_weeks * 7)))

        if abs_pct <= 0.7:
            level = "success"
            badge = "🟢 符合運動醫學黃金減重速率 (0.5%–0.7% BW/週)"
            summary = (
                f"計畫每週減少 **{abs(weekly_kg):.2f} kg（{abs_pct:.2f}% 體重/週）**。"
                f"根據 *Garthe et al. (2011)* 與 *World Athletics (2019)* 共識，此速率能最大化保留瘦體組織（肌肉量）並維持跨欄爆發力與功率輸出。"
            )
        elif abs_pct <= 1.0:
            level = "warning"
            badge = "🟡 接近生理安全上限 (0.71%–1.0% BW/週)"
            summary = (
                f"計畫每週減少 **{abs(weekly_kg):.2f} kg（{abs_pct:.2f}% 體重/週）**。"
                f"已達 *Garthe et al. (2011)* 建議上限，需密切留意訓練疲勞指數與肌力維持；若感覺恢復變慢，建議將目標日延後至 **{rec_date}**。"
            )
        else:
            level = "error"
            badge = "🔴 減重速率過快 — 肌肉流失與 REDs 高風險 (> 1.0% BW/週)"
            summary = (
                f"計畫每週減少 **{abs(weekly_kg):.2f} kg（{abs_pct:.2f}% 體重/週）**，超過運動醫學安全上限。"
                f"依據 *IOC REDs 共識聲明 (Mountjoy et al., 2023)* 與 *Garthe et al. (2011)*，快速減重（>1.0%/週）極易造成瘦體組織流失、無氧爆發力下降與疲勞性傷害。"
                f"依安全速率（約 0.65% 體重/週）推算，建議將目標日期調整至 **{rec_date}** 左右。"
            )
    elif delta_w > 0.2:
        goal_type = "gain"
        abs_pct = abs(weekly_pct)
        safe_weeks = abs(delta_w) / (float(start_w) * 0.004)
        rec_date = s_dt + datetime.timedelta(days=int(round(safe_weeks * 7)))

        if abs_pct <= 0.5:
            level = "success"
            badge = "🟢 符合運動醫學理想增肌速率 (0.25%–0.5% BW/週)"
            summary = (
                f"計畫每週增加 **{weekly_kg:.2f} kg（{abs_pct:.2f}% 體重/週）**。"
                f"依據 *ISSN 身體組成立場聲明 (Aragon et al., 2017)* 與 *Slater et al. (2019)*，此漸進速率有利於促進骨骼肌合成並將脂肪堆積降至最低，維持優異的跨欄功率體重比。"
            )
        else:
            level = "warning"
            badge = "🟡 增重速率偏快 — 脂肪堆積風險 (> 0.5% BW/週)"
            summary = (
                f"計畫每週增加 **{weekly_kg:.2f} kg（{abs_pct:.2f}% 體重/週）**。"
                f"依據 *Aragon et al. (2017)*，受訓運動員每週增重超過 0.5% 體重時，多餘重量較易轉化為非功能性脂肪，可能增加過欄垂直負荷；建議將目標日延至 **{rec_date}**。"
            )
    else:
        goal_type = "maintain"
        level = "info"
        badge = "🔵 競技體重維持模式 (±0.5%–1.0% 正常生理波動)"
        summary = (
            "目前設定為維持目標體重。依據 *World Athletics (2019)* 共識，田徑選手因肝醣儲備（1g 肝醣結合約 3g 水分）與流汗影響，"
            "每日體重出現 **±0.5%～1.5%** 的小幅波動屬正常生理現象，重點在於觀察 **7 日移動平均線** 是否維持穩定。"
        )

    return {
        "goal_type": goal_type,
        "total_days": total_days,
        "total_weeks": round(total_weeks, 1),
        "delta_w": delta_w,
        "weekly_kg": round(weekly_kg, 2),
        "weekly_pct": round(weekly_pct, 2),
        "level": level,
        "badge": badge,
        "summary": summary
    }

def evaluate_measurement_progress(goal_row, logs_rows):
    """每次測量後，結合目標軌跡與運動醫學文獻判讀使用者是否正在往目標前進"""
    if not goal_row or not logs_rows:
        return None

    _, _, start_w, start_date_str, target_w, target_date_str, _, _ = goal_row
    start_w = float(start_w)
    target_w = float(target_w)

    try:
        s_dt = datetime.datetime.strptime(str(start_date_str), "%Y-%m-%d").date()
        t_dt = datetime.datetime.strptime(str(target_date_str), "%Y-%m-%d").date()
    except Exception:
        return None

    sorted_logs = sorted(logs_rows, key=lambda x: (x[2], x[0]))
    latest = sorted_logs[-1]
    latest_w = float(latest[3])
    try:
        latest_dt = datetime.datetime.strptime(str(latest[2]), "%Y-%m-%d").date()
    except Exception:
        latest_dt = datetime.date.today()

    recent_7 = sorted_logs[-7:]
    ma7_w = round(sum(float(x[3]) for x in recent_7) / len(recent_7), 2)
    prev_w = float(sorted_logs[-2][3]) if len(sorted_logs) >= 2 else start_w
    daily_diff = round(latest_w - prev_w, 2)
    daily_diff_pct = (daily_diff / prev_w * 100.0) if prev_w > 0 else 0.0

    total_goal_diff = target_w - start_w
    current_diff = latest_w - start_w
    remaining_w = round(target_w - latest_w, 2)
    days_left = (t_dt - latest_dt).days
    weeks_left = max(days_left / 7.0, 1.0 / 7.0)

    if abs(total_goal_diff) > 0.05:
        progress_pct = round(max(0.0, min(100.0, (current_diff / total_goal_diff) * 100.0)), 1)
    else:
        progress_pct = 100.0 if abs(latest_w - target_w) <= 0.5 else 0.0

    total_span_days = max((t_dt - s_dt).days, 1)
    elapsed_days = max((latest_dt - s_dt).days, 0)
    ratio_time = min(1.0, elapsed_days / total_span_days)
    expected_today_w = round(start_w + total_goal_diff * ratio_time, 2)
    vs_expected = round(latest_w - expected_today_w, 2)

    try:
        first_dt = datetime.datetime.strptime(str(sorted_logs[0][2]), "%Y-%m-%d").date()
    except Exception:
        first_dt = s_dt
    anchor_dt = min(s_dt, first_dt)
    elapsed_weeks = max((latest_dt - anchor_dt).days / 7.0, 0.5)
    actual_weekly_kg = (latest_w - start_w) / elapsed_weeks
    actual_weekly_pct = (actual_weekly_kg / start_w) * 100.0 if start_w > 0 else 0.0

    req_rem_weekly_kg = remaining_w / weeks_left
    req_rem_weekly_pct = (req_rem_weekly_kg / latest_w) * 100.0 if latest_w > 0 else 0.0

    is_loss_goal = total_goal_diff < -0.2
    is_gain_goal = total_goal_diff > 0.2

    if abs(remaining_w) <= 0.25:
        status_level = "success"
        status_title = "🎉 已達成目標體重！進入競技狀態維持期"
        feedback_msg = (
            f"目前測量體重 **{latest_w:.1f} kg**（7日均線 **{ma7_w:.1f} kg**）已精準抵達目標 **{target_w:.1f} kg**。"
            f"依據 *World Athletics (2019)* 共識，接下來請維持穩定的碳水與蛋白質攝取，只要每日波動在 **±0.5%～1.0%（約 ±{latest_w*0.008:.1f} kg）** 內皆屬正常肝醣與水分變化。"
        )
    elif is_loss_goal:
        if latest_w > start_w + 0.3 and ma7_w > start_w + 0.2:
            status_level = "error"
            status_title = "⚠️ 目前趨勢暫時偏離減重目標方向"
            feedback_msg = (
                f"目前體重 **{latest_w:.1f} kg**（7日均線 **{ma7_w:.1f} kg**）高於起始體重 **{start_w:.1f} kg**，距離目標尚差 **{abs(remaining_w):.1f} kg**。"
                f"若要於 **{target_date_str}** 達標，接下來每週需減少 **{abs(req_rem_weekly_kg):.2f} kg（{abs(req_rem_weekly_pct):.2f}% 體重/週）**。"
                f"建議檢視訓練後恢復與飲食能量平衡，但切勿採取超過 **1.0% 體重/週** 的極端節食。"
            )
        elif abs(actual_weekly_pct) > 1.0 and elapsed_days >= 5:
            status_level = "warning"
            status_title = "🟠 正在往目標前進，但下降速度過快（警戒肌肉流失 / REDs）"
            feedback_msg = (
                f"目前已累積減少 **{abs(current_diff):.1f} kg**（平均每週 **{abs(actual_weekly_pct):.2f}% 體重**），進度超前理論軌跡 **{abs(vs_expected):.1f} kg**。"
                f"依據 *Garthe et al. (2011)* 與 *IOC REDs 共識 (2023)*，每週減重超過 **1.0% 體重** 會顯著增加瘦體組織流失與疲勞風險；"
                f"若伴隨單日大幅下降（本次較前次 `{daily_diff:+.1f} kg`），請優先確認是否為訓練流汗脫水，並確保足夠蛋白質與碳水補充。"
            )
        elif latest_w <= expected_today_w + 0.35:
            status_level = "success"
            status_title = "✅ 穩健往目標前進中！完全符合運動醫學安全減重軌跡"
            feedback_msg = (
                f"目前體重 **{latest_w:.1f} kg**（7日均線 **{ma7_w:.1f} kg**），與今日理論目標軌跡（**{expected_today_w:.1f} kg**）高度吻合，"
                f"平均每週下降 **{abs(actual_weekly_pct):.2f}% 體重**，落在 *Garthe et al. (2011)* 建議的 **0.5%～0.7% 體重/週** 黃金安全區間，"
                f"能有效保留下肢肌力與過欄速度。距離目標還差 **{abs(remaining_w):.1f} kg**（剩餘 **{max(days_left, 0)} 天**）。"
            )
        else:
            status_level = "info"
            status_title = "📈 方向正確、正在往目標前進，但目前速率略慢於原定計畫"
            safe_note = (
                f"仍在 *Garthe et al. (2011)* 建議的安全區間（≤1.0%/週）內，可按此步調微調。"
                if abs(req_rem_weekly_pct) <= 1.0
                else f"已超過 **1.0% 體重/週** 安全上限，依據 *IOC REDs (2023)* 建議適度延後目標日期以保護運動表現。"
            )
            feedback_msg = (
                f"體重已較起始下降 **{abs(current_diff):.1f} kg**（7日均線 **{ma7_w:.1f} kg**），確實朝目標前進中，"
                f"但目前比今日理論軌跡（**{expected_today_w:.1f} kg**）慢約 **{vs_expected:.1f} kg**。"
                f"若要在 **{target_date_str}** 準時達標，剩餘期間需維持每週減少 **{abs(req_rem_weekly_kg):.2f} kg（{abs(req_rem_weekly_pct):.2f}% 體重/週）**，{safe_note}"
            )
    elif is_gain_goal:
        if latest_w < start_w - 0.3 and ma7_w < start_w - 0.2:
            status_level = "error"
            status_title = "⚠️ 目前趨勢暫時偏離增重目標方向"
            feedback_msg = (
                f"目前體重 **{latest_w:.1f} kg**（7日均線 **{ma7_w:.1f} kg**）低於起始體重 **{start_w:.1f} kg**，距離目標尚差 **{remaining_w:.1f} kg**。"
                f"依據 *IOC REDs (2023)* 與 *Slater et al. (2019)*，高強度跨欄訓練若能量攝取不足會抑制肌肉合成；"
                f"接下來每週需穩定增加 **{req_rem_weekly_kg:.2f} kg（{req_rem_weekly_pct:.2f}% 體重/週）**。"
            )
        elif actual_weekly_pct > 0.65 and elapsed_days >= 5:
            status_level = "warning"
            status_title = "🟠 正在往目標前進，但增重速度偏快（注意體脂比例）"
            feedback_msg = (
                f"目前已增加 **{current_diff:.1f} kg**（平均每週 **+{actual_weekly_pct:.2f}% 體重**），高於 *ISSN (Aragon et al., 2017)* 建議的 **0.25%～0.5% 體重/週** 理想增肌速率。"
                f"建議搭配體脂率或皮脂觀察，避免增加過多非功能性脂肪而影響起跑與過欄敏捷度。"
            )
        elif latest_w >= expected_today_w - 0.35:
            status_level = "success"
            status_title = "✅ 穩健往目標前進中！符合運動醫學理想增肌速率"
            feedback_msg = (
                f"目前體重 **{latest_w:.1f} kg**（7日均線 **{ma7_w:.1f} kg**）已達今日理論目標軌跡（**{expected_today_w:.1f} kg**），"
                f"平均每週增加 **+{actual_weekly_pct:.2f}% 體重**，符合 *Aragon et al. (2017)* 與 *Slater et al. (2019)* 的高品質瘦體組織成長區間。"
                f"距離目標還差 **{remaining_w:.1f} kg**（剩餘 **{max(days_left, 0)} 天**）。"
            )
        else:
            status_level = "info"
            status_title = "📈 方向正確、正在往目標前進，但增重速率略慢於計畫"
            feedback_msg = (
                f"體重已較起始增加 **+{current_diff:.1f} kg**（7日均線 **{ma7_w:.1f} kg**），正穩定朝目標推進，"
                f"但目前比今日理論軌跡（**{expected_today_w:.1f} kg**）略低 **{abs(vs_expected):.1f} kg**。"
                f"剩餘期間建議維持每週增加 **+{req_rem_weekly_kg:.2f} kg（{req_rem_weekly_pct:.2f}% 體重/週）**。"
            )
    else:
        fluct_pct = abs(latest_w - target_w) / target_w * 100.0 if target_w > 0 else 0.0
        if fluct_pct <= 1.2:
            status_level = "success"
            status_title = "✅ 體重穩定維持於理想競技區間"
            feedback_msg = (
                f"目前體重 **{latest_w:.1f} kg**（7日均線 **{ma7_w:.1f} kg**），與目標 **{target_w:.1f} kg** 差距僅 **{fluct_pct:.2f}%**。"
                f"依據 *World Athletics (2019)* 共識，**±0.5%～1.5%** 內的波動屬於肝醣與水分正常調節，代表能量平衡穩定。"
            )
        else:
            status_level = "warning"
            status_title = "🟡 體重波動幅度超過日常水分範圍（> 1.2%）"
            feedback_msg = (
                f"目前體重 **{latest_w:.1f} kg** 與目標 **{target_w:.1f} kg** 相差 **{latest_w - target_w:+.1f} kg（{fluct_pct:.2f}%）**。"
                f"若為連續多日（7日均線 **{ma7_w:.1f} kg**）偏移，建議檢視近期訓練量與碳水／水分補充是否失衡。"
            )

    water_note = ""
    if len(sorted_logs) >= 2 and abs(daily_diff_pct) >= 1.5:
        water_note = (
            f"💧 **單日水分波動提醒（World Athletics, 2019）**：本次測量較前次變化 **{daily_diff:+.1f} kg（{daily_diff_pct:+.1f}%）**，"
            f"單日超過 1.5% 的劇烈變化通常反映訓練流汗脫水、肝醣耗竭或高鈉飲食儲水，請以 **7 日移動平均線（{ma7_w:.1f} kg）** 作為真實進度依據。"
        )

    return {
        "latest_w": latest_w,
        "latest_date": str(latest[2]),
        "ma7_w": ma7_w,
        "daily_diff": daily_diff,
        "remaining_w": remaining_w,
        "days_left": days_left,
        "progress_pct": progress_pct,
        "expected_today_w": expected_today_w,
        "vs_expected": vs_expected,
        "actual_weekly_kg": round(actual_weekly_kg, 2),
        "actual_weekly_pct": round(actual_weekly_pct, 2),
        "req_rem_weekly_kg": round(req_rem_weekly_kg, 2),
        "req_rem_weekly_pct": round(req_rem_weekly_pct, 2),
        "status_level": status_level,
        "status_title": status_title,
        "feedback_msg": feedback_msg,
        "water_note": water_note
    }

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

    /* 疼痛性質顯示標籤：強制紅底純白字 */
    div[data-testid="stMarkdownContainer"] span.pain-tag-badge,
    div[data-testid="stMarkdownContainer"] p span.pain-tag-badge,
    span.pain-tag-badge {
        background-color: #dc2626 !important;
        color: #ffffff !important;
        -webkit-text-fill-color: #ffffff !important;
        padding: 2px 8px !important;
        border-radius: 5px !important;
        font-size: 12px !important;
        font-weight: 600 !important;
        display: inline-block !important;
        margin: 1px 2px !important;
        line-height: 1.4 !important;
    }

    [data-testid="stElementContainer"]:has(.pain-nature-anchor),
    .element-container:has(.pain-nature-anchor) {
        display: none !important;
    }
    div[data-testid="stAppViewContainer"] [data-testid="stColumn"]:has(.pain-nature-anchor) [data-testid="stMultiSelect"] [data-baseweb="tag"],
    div[data-testid="stAppViewContainer"] [data-testid="column"]:has(.pain-nature-anchor) [data-testid="stMultiSelect"] [data-baseweb="tag"],
    div[data-testid="stAppViewContainer"] [data-testid="stVerticalBlock"]:has(> [data-testid="stElementContainer"] .pain-nature-anchor) [data-testid="stMultiSelect"] [data-baseweb="tag"],
    div[data-testid="stAppViewContainer"] [data-testid="stVerticalBlock"]:has(> .element-container .pain-nature-anchor) [data-testid="stMultiSelect"] [data-baseweb="tag"],
    div[data-testid="stAppViewContainer"] [data-testid="stElementContainer"]:has(.pain-nature-anchor) + [data-testid="stElementContainer"] [data-baseweb="tag"],
    div[data-testid="stAppViewContainer"] .element-container:has(.pain-nature-anchor) + .element-container [data-baseweb="tag"] {
        background-color: #dc2626 !important;
        background: #dc2626 !important;
        color: #ffffff !important;
        -webkit-text-fill-color: #ffffff !important;
        border-radius: 6px !important;
        border: none !important;
    }
    div[data-testid="stAppViewContainer"] [data-testid="stColumn"]:has(.pain-nature-anchor) [data-testid="stMultiSelect"] [data-baseweb="tag"] *,
    div[data-testid="stAppViewContainer"] [data-testid="stColumn"]:has(.pain-nature-anchor) [data-testid="stMultiSelect"] [data-baseweb="tag"] span,
    div[data-testid="stAppViewContainer"] [data-testid="column"]:has(.pain-nature-anchor) [data-testid="stMultiSelect"] [data-baseweb="tag"] *,
    div[data-testid="stAppViewContainer"] [data-testid="column"]:has(.pain-nature-anchor) [data-testid="stMultiSelect"] [data-baseweb="tag"] span,
    div[data-testid="stAppViewContainer"] [data-testid="stVerticalBlock"]:has(> [data-testid="stElementContainer"] .pain-nature-anchor) [data-testid="stMultiSelect"] [data-baseweb="tag"] *,
    div[data-testid="stAppViewContainer"] [data-testid="stVerticalBlock"]:has(> [data-testid="stElementContainer"] .pain-nature-anchor) [data-testid="stMultiSelect"] [data-baseweb="tag"] span,
    div[data-testid="stAppViewContainer"] [data-testid="stVerticalBlock"]:has(> .element-container .pain-nature-anchor) [data-testid="stMultiSelect"] [data-baseweb="tag"] *,
    div[data-testid="stAppViewContainer"] [data-testid="stVerticalBlock"]:has(> .element-container .pain-nature-anchor) [data-testid="stMultiSelect"] [data-baseweb="tag"] span,
    div[data-testid="stAppViewContainer"] [data-testid="stElementContainer"]:has(.pain-nature-anchor) + [data-testid="stElementContainer"] [data-baseweb="tag"] *,
    div[data-testid="stAppViewContainer"] .element-container:has(.pain-nature-anchor) + .element-container [data-baseweb="tag"] * {
        color: #ffffff !important;
        -webkit-text-fill-color: #ffffff !important;
        fill: #ffffff !important;
    }
    div[data-testid="stTextArea"]:has(textarea[aria-label*="body_map_img_b64"]),
    div[data-testid="stTextArea"]:has(textarea[aria-label*="angle_img_b64"]),
    div[data-testid="stTextArea"]:has(textarea[aria-label*="angle_data_json"]),
    div[data-testid="stTextArea"]:has(textarea[aria-label*="timer_data_json"]),
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

ROLE_LABELS = {
    "player": "選手 Athlete",
    "coach": "教練 Coach",
    "trainer": "運動防護員 Trainer"
}

TREATMENT_CHECKLIST_OPTIONS = [
    "🧊 訓練後冰敷 (15-20分鐘)",
    "🧘 伸展與滾筒筋膜放鬆",
    "🩹 運動貼紮 / 護具固定",
    "⚡ 物理治療 / 儀器處置",
    "🏋️ 離心 / 穩定度復健訓練",
    "🏥 建議就醫 / 影像檢查"
]

STATUS_TAG_OPTIONS = [
    "🟢 正常訓練兼防護觀察 (Full with monitoring)",
    "🟡 減量 / 調整課表訓練 (Modified training)",
    "🔴 暫停專項訓練 (Rest required)"
]

def get_db_connection():
    return psycopg2.connect(st.secrets["postgres"]["url"])

def generate_random_team_code(length=6):
    prefix = "HRD-"
    rand_str = ''.join(random.choices(string.ascii_uppercase + string.digits, k=length-2))
    return f"{prefix}{rand_str}"

def _create_table_safe(c, table_name, create_sql):
    c.execute("SELECT to_regclass(%s)", (f"public.{table_name}",))
    if c.fetchone()[0] is None:
        c.execute(f"DROP TYPE IF EXISTS public.{table_name} CASCADE;")
        c.execute(create_sql)

@st.cache_resource
def init_db():
    conn = get_db_connection()
    try:
        c = conn.cursor()
        c.execute("SELECT pg_advisory_xact_lock(20261113);")

        tables = [
            ("users", """
                CREATE TABLE IF NOT EXISTS users (
                    username TEXT PRIMARY KEY,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL,
                    team_code TEXT NOT NULL DEFAULT 'DEFAULT_TEAM',
                    email TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT ''
                )
            """),
            ("reset_tokens", """
                CREATE TABLE IF NOT EXISTS reset_tokens (
                    username TEXT PRIMARY KEY,
                    otp_code TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                )
            """),
            ("records", """
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
            """),
            ("training_plans", """
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
            """),
            ("competitions", """
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
            """),
            ("injury_feedbacks", """
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
            """),
            ("injury_feedback_replies", """
                CREATE TABLE IF NOT EXISTS injury_feedback_replies (
                    id SERIAL PRIMARY KEY,
                    feedback_id INTEGER NOT NULL,
                    sender_username TEXT NOT NULL,
                    sender_role TEXT NOT NULL,
                    reply_text TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """),
            ("weight_goals", """
                CREATE TABLE IF NOT EXISTS weight_goals (
                    username TEXT PRIMARY KEY,
                    team_code TEXT NOT NULL DEFAULT 'DEFAULT_TEAM',
                    start_weight REAL NOT NULL,
                    start_date TEXT NOT NULL,
                    target_weight REAL NOT NULL,
                    target_date TEXT NOT NULL,
                    goal_notes TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL
                )
            """),
            ("weight_logs", """
                CREATE TABLE IF NOT EXISTS weight_logs (
                    id SERIAL PRIMARY KEY,
                    username TEXT NOT NULL,
                    team_code TEXT NOT NULL DEFAULT 'DEFAULT_TEAM',
                    log_date TEXT NOT NULL,
                    weight_kg REAL NOT NULL,
                    body_fat_pct REAL,
                    measure_timing TEXT NOT NULL DEFAULT '',
                    notes TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                )
            """)
        ]

        for t_name, t_sql in tables:
            _create_table_safe(c, t_name, t_sql)

        migration_sqls = [
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS team_code TEXT NOT NULL DEFAULT 'DEFAULT_TEAM'",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS email TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS created_at TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE records ADD COLUMN IF NOT EXISTS team_code TEXT NOT NULL DEFAULT 'DEFAULT_TEAM'",
            "ALTER TABLE training_plans ADD COLUMN IF NOT EXISTS team_code TEXT NOT NULL DEFAULT 'DEFAULT_TEAM'",
            "ALTER TABLE competitions ADD COLUMN IF NOT EXISTS team_code TEXT NOT NULL DEFAULT 'DEFAULT_TEAM'",
            "ALTER TABLE competitions ADD COLUMN IF NOT EXISTS end_date TEXT DEFAULT ''",
            "ALTER TABLE injury_feedbacks ADD COLUMN IF NOT EXISTS plan_id INTEGER DEFAULT NULL",
            "ALTER TABLE injury_feedbacks ADD COLUMN IF NOT EXISTS checked_items TEXT DEFAULT '[]'"
        ]
        for q in migration_sqls:
            c.execute(q)

        conn.commit()
        c.close()
        return True
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()

# -------------------- 【帳號與發訊認證 Logic】 --------------------
def hash_password(password):
    return hashlib.sha256(password.encode('utf-8')).hexdigest()

def delete_user_account(username, password=None):
    conn = get_db_connection()
    c = conn.cursor()
    if password is not None:
        c.execute("SELECT username FROM users WHERE username = %s AND password_hash = %s", (username, hash_password(password)))
        if not c.fetchone():
            c.close(); conn.close()
            return False, "密碼驗證錯誤，無法註銷帳號"

    try:
        c.execute("DELETE FROM records WHERE username = %s", (username,))
        c.execute("DELETE FROM training_plans WHERE username = %s", (username,))
        c.execute("DELETE FROM weight_logs WHERE username = %s", (username,))
        c.execute("DELETE FROM weight_goals WHERE username = %s", (username,))
        c.execute("DELETE FROM reset_tokens WHERE username = %s", (username,))
        c.execute("DELETE FROM competitions WHERE username = %s", (username,))
        c.execute("DELETE FROM injury_feedback_replies WHERE sender_username = %s", (username,))
        c.execute("DELETE FROM injury_feedback_replies WHERE feedback_id IN (SELECT id FROM injury_feedbacks WHERE athlete_username = %s OR trainer_username = %s)", (username, username))
        c.execute("DELETE FROM injury_feedbacks WHERE athlete_username = %s OR trainer_username = %s", (username, username))
        c.execute("DELETE FROM users WHERE username = %s", (username,))
        conn.commit()
        c.close(); conn.close()
        return True, "帳號及相關資料已永久註銷"
    except Exception as e:
        c.close(); conn.close()
        return False, f"註銷失敗: {str(e)}"

def register_user(username, password, role, team_code, email, is_new_team=False):
    username = username.strip()
    team_code = team_code.strip().upper()
    if not username: return False, "使用者名稱不可為空"
    if not password: return False, "密碼不可為空"
    if not team_code or team_code == "UNASSIGNED": return False, "團隊碼不可為空或為無效代碼"

    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT username FROM users WHERE username = %s", (username,))
    if c.fetchone():
        c.close(); conn.close()
        return False, "該使用者名稱已被註冊"

    if not is_new_team:
        c.execute("SELECT 1 FROM users WHERE team_code = %s LIMIT 1", (team_code,))
        if not c.fetchone():
            c.close(); conn.close()
            return False, f"找不到團隊碼「{team_code}」，請確認是否輸入正確，或改選「建立新團隊」"

    try:
        pw_hash = hash_password(password)
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        c.execute("INSERT INTO users (username, password_hash, role, team_code, email, created_at) VALUES (%s, %s, %s, %s, %s, %s)",
                  (username, pw_hash, role, team_code, email, now_str))
        conn.commit()
        c.close(); conn.close()
        action_text = "已建立並加入新團隊" if is_new_team else "已成功加入團隊"
        return True, f"註冊成功！{action_text}：{team_code}"
    except Exception as e:
        c.close(); conn.close()
        return False, f"註冊失敗: {str(e)}"

def update_user_team_code(username, new_team_code, is_new_team=False):
    new_team_code = new_team_code.strip().upper()
    if not new_team_code or new_team_code == "UNASSIGNED":
        return False, "團隊碼不可為空或為無效代碼"

    conn = get_db_connection()
    c = conn.cursor()
    if not is_new_team:
        c.execute("SELECT 1 FROM users WHERE team_code = %s LIMIT 1", (new_team_code,))
        if not c.fetchone():
            c.close(); conn.close()
            return False, f"找不到團隊碼「{new_team_code}」，請確認是否輸入正確"

    try:
        c.execute("UPDATE users SET team_code = %s WHERE username = %s", (new_team_code, username))
        conn.commit()
        c.close(); conn.close()
        return True, new_team_code
    except Exception as e:
        c.close(); conn.close()
        return False, f"更新失敗: {str(e)}"

def kick_team_member(team_code, target_username):
    conn = get_db_connection()
    c = conn.cursor()
    try:
        c.execute("SELECT username FROM users WHERE username = %s AND team_code = %s", (target_username, team_code))
        if not c.fetchone():
            c.close(); conn.close()
            return False, "該成員已不在此團隊中"
        c.execute("UPDATE users SET team_code = 'UNASSIGNED' WHERE username = %s AND team_code = %s", (target_username, team_code))
        conn.commit()
        c.close(); conn.close()
        return True, f"已將成員「{target_username}」移出團隊"
    except Exception as e:
        c.close(); conn.close()
        return False, f"移出失敗: {str(e)}"

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
        c.execute("UPDATE weight_logs SET username = %s WHERE username = %s", (new_username, old_username))
        c.execute("UPDATE weight_goals SET username = %s WHERE username = %s", (new_username, old_username))
        c.execute("UPDATE reset_tokens SET username = %s WHERE username = %s", (new_username, old_username))
        c.execute("UPDATE competitions SET username = %s WHERE username = %s", (new_username, old_username))
        c.execute("UPDATE injury_feedbacks SET athlete_username = %s WHERE athlete_username = %s", (new_username, old_username))
        c.execute("UPDATE injury_feedbacks SET trainer_username = %s WHERE trainer_username = %s", (new_username, old_username))
        c.execute("UPDATE injury_feedback_replies SET sender_username = %s WHERE sender_username = %s", (new_username, old_username))
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
    if not team_code or team_code == "UNASSIGNED":
        return []
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT username FROM users WHERE team_code = %s AND role = 'player' ORDER BY username ASC", (team_code,))
    rows = c.fetchall()
    c.close(); conn.close()
    return [r[0] for r in rows]

def get_team_members(team_code):
    if not team_code or team_code == "UNASSIGNED":
        return []
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("""
        SELECT username, role
        FROM users
        WHERE team_code = %s
        ORDER BY
            CASE role
                WHEN 'coach' THEN 1
                WHEN 'trainer' THEN 2
                WHEN 'player' THEN 3
                ELSE 4
            END,
            username ASC
    """, (team_code,))
    rows = c.fetchall()
    c.close(); conn.close()
    return rows

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

    team_code = kwargs.get("team_code", "DEFAULT_TEAM")
    if len(args) == 5:
        username, team_code, module, title, details_dict = args
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
        "INSERT INTO records (username, team_code, module, title, details, timestamp) VALUES (%s, %s, %s, %s, %s, %s)",
        (username, team_code, module, title, details_json, now_str)
    )
    conn.commit()
    c.close()
    conn.close()

def get_user_records(*args, **kwargs):
    conn = get_db_connection()
    c = conn.cursor()

    team_code = kwargs.get("team_code", args[0] if len(args) > 0 else None)
    username = kwargs.get("username", None)
    module = kwargs.get("module", kwargs.get("category", None))

    if module is None and len(args) > 2:
        module = args[2]

    query = "SELECT id, username, module, title, details, timestamp FROM records"
    params = []
    where_clauses = []

    if team_code and str(team_code).strip() and str(team_code) != "None":
        where_clauses.append("(team_code = %s OR username IN (SELECT username FROM users WHERE team_code = %s))")
        params.extend([str(team_code), str(team_code)])
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

def update_training_plan(plan_id, plan_date, category, content, intensity, notes):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("""
        UPDATE training_plans
        SET plan_date = %s, category = %s, content = %s, intensity = %s, notes = %s
        WHERE id = %s
    """, (str(plan_date), category, content, intensity, notes, plan_id))
    conn.commit()
    c.close(); conn.close()

def get_training_plans(*args, **kwargs):
    conn = get_db_connection()
    c = conn.cursor()
    
    username = kwargs.get("username", None)
    team_code = kwargs.get("team_code", None)
    if username is None and len(args) > 0:
        if len(args) == 1:
            username = args[0]
        elif len(args) >= 2:
            username = args[1]
            
    query = "SELECT id, username, plan_date, category, content, intensity, notes, created_at FROM training_plans"
    params = []
    where_clauses = []
    if username and str(username).strip() and str(username) != "None":
        where_clauses.append("username = %s")
        params.append(str(username))
    if team_code and str(team_code).strip() and str(team_code) != "None":
        where_clauses.append("team_code = %s")
        params.append(str(team_code))
    if where_clauses:
        query += " WHERE " + " AND ".join(where_clauses)
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

# -------------------- 【體重監控與目標資料庫操作】 --------------------
def upsert_weight_goal(username, team_code, start_weight, start_date, target_weight, target_date, goal_notes=""):
    conn = get_db_connection()
    c = conn.cursor()
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    c.execute("""
        INSERT INTO weight_goals (username, team_code, start_weight, start_date, target_weight, target_date, goal_notes, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (username) DO UPDATE SET
            team_code = EXCLUDED.team_code,
            start_weight = EXCLUDED.start_weight,
            start_date = EXCLUDED.start_date,
            target_weight = EXCLUDED.target_weight,
            target_date = EXCLUDED.target_date,
            goal_notes = EXCLUDED.goal_notes,
            updated_at = EXCLUDED.updated_at
    """, (username, team_code, float(start_weight), str(start_date), float(target_weight), str(target_date), goal_notes, now_str))
    conn.commit()
    c.close(); conn.close()

def get_weight_goal(username):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("""
        SELECT username, team_code, start_weight, start_date, target_weight, target_date, goal_notes, updated_at
        FROM weight_goals WHERE username = %s LIMIT 1
    """, (username,))
    row = c.fetchone()
    c.close(); conn.close()
    return row

def upsert_weight_log(username, team_code, log_date, weight_kg, body_fat_pct, measure_timing, notes=""):
    conn = get_db_connection()
    c = conn.cursor()
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    c.execute("SELECT id FROM weight_logs WHERE username = %s AND log_date = %s LIMIT 1", (username, str(log_date)))
    existing = c.fetchone()
    if existing:
        c.execute("""
            UPDATE weight_logs
            SET team_code = %s, weight_kg = %s, body_fat_pct = %s, measure_timing = %s, notes = %s, created_at = %s
            WHERE id = %s
        """, (team_code, float(weight_kg), body_fat_pct, measure_timing, notes, now_str, existing[0]))
    else:
        c.execute("""
            INSERT INTO weight_logs (username, team_code, log_date, weight_kg, body_fat_pct, measure_timing, notes, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (username, team_code, str(log_date), float(weight_kg), body_fat_pct, measure_timing, notes, now_str))
    conn.commit()
    c.close(); conn.close()

def get_weight_logs(username=None, team_code=None):
    conn = get_db_connection()
    c = conn.cursor()
    query = """
        SELECT id, username, log_date, weight_kg, body_fat_pct, measure_timing, notes, created_at
        FROM weight_logs
    """
    params = []
    clauses = []
    if username:
        clauses.append("username = %s")
        params.append(username)
    if team_code:
        clauses.append("team_code = %s")
        params.append(team_code)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY log_date ASC, id ASC"
    c.execute(query, tuple(params))
    rows = c.fetchall()
    c.close(); conn.close()
    return rows

def delete_weight_log(log_id):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("DELETE FROM weight_logs WHERE id = %s", (log_id,))
    conn.commit()
    c.close(); conn.close()

def save_injury_feedback(athlete_username, trainer_username, team_code, feedback_date, body_part, status_tag, advice, plan_id=None, checked_items=None):
    conn = get_db_connection()
    c = conn.cursor()
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    checked_json = json.dumps(checked_items if checked_items else [], ensure_ascii=False)

    if plan_id is not None:
        c.execute("SELECT id FROM injury_feedbacks WHERE plan_id = %s LIMIT 1", (int(plan_id),))
        existing = c.fetchone()
        if existing:
            c.execute("""
                UPDATE injury_feedbacks
                SET trainer_username = %s, body_part = %s, status_tag = %s, checked_items = %s, treatment_advice = %s, created_at = %s
                WHERE id = %s
            """, (trainer_username, body_part, status_tag, checked_json, advice, now_str, existing[0]))
            conn.commit()
            c.close(); conn.close()
            return existing[0]

    c.execute("""
        INSERT INTO injury_feedbacks 
        (athlete_username, trainer_username, team_code, feedback_date, body_part, status_tag, treatment_advice, created_at, plan_id, checked_items)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
    """, (athlete_username, trainer_username, team_code, str(feedback_date), body_part, status_tag, advice, now_str, plan_id, checked_json))
    new_id = c.fetchone()[0]
    conn.commit()
    c.close(); conn.close()
    return new_id

def get_injury_feedbacks(team_code, athlete_username=None):
    conn = get_db_connection()
    c = conn.cursor()
    query = """
        SELECT id, athlete_username, trainer_username, feedback_date, body_part, status_tag, treatment_advice, created_at, plan_id, checked_items
        FROM injury_feedbacks
        WHERE team_code = %s
    """
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
    c.execute("DELETE FROM injury_feedback_replies WHERE feedback_id = %s", (fb_id,))
    c.execute("DELETE FROM injury_feedbacks WHERE id = %s", (fb_id,))
    conn.commit()
    c.close(); conn.close()

def save_injury_feedback_reply(feedback_id, sender_username, sender_role, reply_text):
    conn = get_db_connection()
    c = conn.cursor()
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    c.execute("""
        INSERT INTO injury_feedback_replies
        (feedback_id, sender_username, sender_role, reply_text, created_at)
        VALUES (%s, %s, %s, %s, %s)
    """, (feedback_id, sender_username, sender_role, reply_text, now_str))
    conn.commit()
    c.close(); conn.close()

def get_replies_by_feedback_ids(fb_ids):
    if not fb_ids:
        return {}
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("""
        SELECT id, feedback_id, sender_username, sender_role, reply_text, created_at
        FROM injury_feedback_replies
        WHERE feedback_id = ANY(%s)
        ORDER BY id ASC
    """, ([int(x) for x in fb_ids],))
    rows = c.fetchall()
    c.close(); conn.close()
    grouped = {}
    for r in rows:
        grouped.setdefault(r[1], []).append(r)
    return grouped

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
    role_name = ROLE_LABELS.get(u['role'], u['role'])
    tc_disp = "尚未加入團隊" if u['team_code'] == "UNASSIGNED" else u['team_code']
    st.sidebar.success(f"使用者：**{u['username']}**\n\n身分：**{role_name}**\n\n團隊碼：`{tc_disp}`")
    if st.sidebar.button("登出系統 Logout", key="sidebar_logout"):
        st.session_state["user_info"] = None
        st.rerun()

st.sidebar.markdown("---")

nav_options = [
    "系統首頁 Home",
    "運動防護 Injury Care",
    "比賽行事曆 Competition Calendar",
    "角度分析 Angle Analysis",
    "雙影同步 Dual Video Sync",
    "跨期數據對比 Longitudinal Comparison",
    "課表紀錄 Training Plan",
    "體重監控 Weight Monitoring",
    "歷史分析紀錄 History Records"
]

page = st.sidebar.radio("請選擇功能模組：", nav_options)

# ==================== 【頁面 1：系統首頁 Home】 ====================
if page == "系統首頁 Home":
    st.title("🏃 HURDEX 跨欄運動技術與防護分析系統")
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
            r_role = st.selectbox("身分 Role", ["player", "coach", "trainer"], format_func=lambda x: ROLE_LABELS[x], key="r_role")

            role_verified = True
            if r_role == "coach":
                c_code = st.text_input("請輸入教練專用邀請碼 Coach Invitation Code", type="password", key="r_ccode")
                if c_code == COACH_SECRET_CODE:
                    st.success("教練權限核可！")
                else:
                    role_verified = False
                    st.warning("請輸入正確的教練邀請碼以驗證教練身分")
            elif r_role == "trainer":
                t_code = st.text_input("請輸入防護員專用邀請碼 Trainer Invitation Code", type="password", key="r_tcode")
                if t_code == TRAINER_SECRET_CODE:
                    st.success("運動防護員權限核可！")
                else:
                    role_verified = False
                    st.warning("請輸入正確的防護員邀請碼以驗證防護員身分")

            st.markdown("---")
            st.markdown("##### 團隊設定 Team Setup")
            team_mode = st.radio(
                "請選擇團隊加入方式（三方皆可加入既有團隊或建立新團隊）：",
                ["加入既有團隊 (輸入團隊碼)", "建立新團隊 (自動產生團隊碼)"],
                horizontal=True,
                key="r_team_mode"
            )

            is_new_team = (team_mode == "建立新團隊 (自動產生團隊碼)")
            r_team = ""

            if not is_new_team:
                r_team = st.text_input("請輸入團隊碼 Team Code (由團隊建立者提供，例如 HRD-XXXX)", key="r_team_join")
            else:
                if "reg_new_team_code" not in st.session_state:
                    st.session_state["reg_new_team_code"] = generate_random_team_code()

                col_tc1, col_tc2 = st.columns([3, 1])
                with col_tc1:
                    st.info(f"系統已為您配置專屬新團隊碼：**`{st.session_state['reg_new_team_code']}`**（註冊後可分享給團隊成員加入）")
                with col_tc2:
                    if st.button("🔄 換一組代碼", key="btn_refresh_team_code", use_container_width=True):
                        st.session_state["reg_new_team_code"] = generate_random_team_code()
                        st.rerun()

                r_team = st.session_state["reg_new_team_code"]

            if st.button("確認註冊 Register", key="btn_reg", use_container_width=True):
                if not role_verified:
                    if r_role == "coach":
                        st.error("教練邀請碼錯誤！")
                    elif r_role == "trainer":
                        st.error("防護員邀請碼錯誤！")
                else:
                    ok, msg = register_user(r_user, r_pass, r_role, r_team, r_email, is_new_team=is_new_team)
                    if ok:
                        if is_new_team and "reg_new_team_code" in st.session_state:
                            del st.session_state["reg_new_team_code"]
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
        if u["team_code"] == "UNASSIGNED":
            st.warning("⚠️ 您目前尚未所屬團隊（或已被教練移出原團隊），請在下方「帳號設定與團隊管理」加入既有團隊或建立新團隊。")
        else:
            st.success(f"🎉 歡迎回來，{u['username']}！目前所屬團隊碼：`{u['team_code']}`")

        st.subheader(f"👥 團隊成員名單 Team Roster（團隊碼：`{u['team_code']}`）")
        team_members = get_team_members(u["team_code"])
        if team_members:
            coach_cnt = sum(1 for m in team_members if m[1] == "coach")
            trainer_cnt = sum(1 for m in team_members if m[1] == "trainer")
            player_cnt = sum(1 for m in team_members if m[1] == "player")

            mc1, mc2, mc3, mc4 = st.columns(4)
            mc1.metric("團隊總人數", f"{len(team_members)} 人")
            mc2.metric("教練 Coach", f"{coach_cnt} 人")
            mc3.metric("運動防護員 Trainer", f"{trainer_cnt} 人")
            mc4.metric("選手 Athlete", f"{player_cnt} 人")

            roster_rows = []
            for m_user, m_role in team_members:
                is_me = "（目前帳號）" if m_user == u["username"] else ""
                roster_rows.append({
                    "成員帳號 Username": f"{m_user} {is_me}".strip(),
                    "團隊身分 Role": ROLE_LABELS.get(m_role, m_role)
                })
            st.dataframe(pd.DataFrame(roster_rows), use_container_width=True, hide_index=True)

            if u["role"] == "coach":
                kickable_members = [(m_user, m_role) for m_user, m_role in team_members if m_user != u["username"]]
                if kickable_members:
                    with st.expander("🚫 教練團隊成員管理（移出團隊成員）"):
                        member_map = {f"{m_user} ({ROLE_LABELS.get(m_role, m_role)})": m_user for m_user, m_role in kickable_members}
                        sel_kick_label = st.selectbox("選擇要移出團隊的成員：", list(member_map.keys()), key="sel_kick_member")
                        confirm_kick = st.checkbox("我確認要將此成員移出本團隊", key="chk_confirm_kick")
                        if st.button("確認踢出團隊成員 Remove Member", key="btn_kick_member"):
                            if not confirm_kick:
                                st.warning("請先勾選確認移出方塊")
                            else:
                                target_kick_user = member_map[sel_kick_label]
                                ok, msg = kick_team_member(u["team_code"], target_kick_user)
                                if ok:
                                    st.success(msg)
                                    st.rerun()
                                else:
                                    st.error(msg)
        else:
            st.info("目前團隊尚無成員資料")

        st.markdown("---")
        with st.expander("⚙️ 帳號設定與團隊管理 Account & Team Settings", expanded=(u["team_code"] == "UNASSIGNED")):
            tab_team, tab_chg_pw, tab_del_acc = st.tabs([
                "👥 加入 / 切換團隊 Team Settings",
                "🔑 修改密碼 Change Password",
                "⚠️ 註銷帳號 Delete Account"
            ])

            with tab_team:
                tc_display = "尚未加入團隊" if u["team_code"] == "UNASSIGNED" else u["team_code"]
                st.write(f"目前所屬團隊碼：**`{tc_display}`**")
                switch_mode = st.radio(
                    "選擇團隊操作方式：",
                    ["加入既有團隊 (輸入團隊碼)", "建立新團隊 (產生新團隊碼)"],
                    horizontal=True,
                    key="switch_team_mode"
                )
                if switch_mode == "加入既有團隊 (輸入團隊碼)":
                    input_new_tc = st.text_input("請輸入要加入的團隊碼 Team Code", key="input_switch_tc")
                    if st.button("確認加入團隊 Join Team", key="btn_join_team"):
                        ok, res_tc = update_user_team_code(u["username"], input_new_tc, is_new_team=False)
                        if ok:
                            st.session_state["user_info"]["team_code"] = res_tc
                            st.success(f"已成功加入團隊：{res_tc}")
                            st.rerun()
                        else:
                            st.error(res_tc)
                else:
                    if "switch_new_team_code" not in st.session_state:
                        st.session_state["switch_new_team_code"] = generate_random_team_code()

                    sc1, sc2 = st.columns([3, 1])
                    with sc1:
                        st.info(f"新團隊碼：**`{st.session_state['switch_new_team_code']}`**")
                    with sc2:
                        if st.button("🔄 換一組", key="btn_refresh_switch_tc", use_container_width=True):
                            st.session_state["switch_new_team_code"] = generate_random_team_code()
                            st.rerun()

                    if st.button("確認建立並切換至此團隊 Create & Switch Team", key="btn_create_switch_team"):
                        ok, res_tc = update_user_team_code(u["username"], st.session_state["switch_new_team_code"], is_new_team=True)
                        if ok:
                            st.session_state["user_info"]["team_code"] = res_tc
                            del st.session_state["switch_new_team_code"]
                            st.success(f"已建立並切換至新團隊：{res_tc}")
                            st.rerun()
                        else:
                            st.error(res_tc)

            with tab_chg_pw:
                with st.form("form_change_pw"):
                    old_pw_input = st.text_input("請輸入目前密碼 Current Password", type="password")
                    new_pw_input = st.text_input("請輸入新密碼 New Password", type="password")
                    new_pw_confirm = st.text_input("再次確認新密碼 Confirm New Password", type="password")
                    if st.form_submit_button("更新密碼 Update Password", use_container_width=True):
                        if new_pw_input != new_pw_confirm:
                            st.error("兩次輸入的新密碼不一致")
                        else:
                            ok, msg = update_password(u["username"], old_pw_input, new_pw_input)
                            if ok:
                                st.success(msg)
                            else:
                                st.error(msg)

            with tab_del_acc:
                st.warning("⚠️ **危險操作提醒**：註銷帳號後，您的個人帳號資料、所有訓練課表日誌與歷史分析紀錄將會永久刪除且無法復原。")
                del_pw_confirm = st.text_input("請輸入帳號密碼以確認身分 Password Confirmation", type="password", key="del_acc_pw")
                del_chk = st.checkbox("我已了解註銷後資料將永久刪除，確認要註銷此帳號", key="del_acc_chk")
                if st.button("🗑️ 永久註銷帳號 Permanently Delete Account", key="btn_delete_account"):
                    if not del_chk:
                        st.error("請先勾選確認註銷方塊")
                    elif not del_pw_confirm:
                        st.error("請輸入密碼以驗證身分")
                    else:
                        ok, msg = delete_user_account(u["username"], password=del_pw_confirm)
                        if ok:
                            st.session_state["user_info"] = None
                            st.success(msg)
                            st.rerun()
                        else:
                            st.error(msg)

# ==================== 【頁面 2：運動防護 Injury Care】 ====================
elif page == "運動防護 Injury Care":
    st.title("🩺 運動防護與傷病批改回饋 Injury Care")
    st.markdown("---")

    if st.session_state["user_info"] is None:
        st.warning("請先登入帳號")
    else:
        u = st.session_state["user_info"]
        team_code = u["team_code"]
        role = u["role"]

        raw_plans = get_training_plans(team_code=team_code) if role in ["trainer", "coach"] else get_training_plans(username=u["username"], team_code=team_code)
        all_feedbacks = get_injury_feedbacks(team_code) if role in ["trainer", "coach"] else get_injury_feedbacks(team_code, athlete_username=u["username"])

        feedback_by_plan = {}
        standalone_feedbacks = []
        for fb in all_feedbacks:
            fb_plan_id = fb[8]
            if fb_plan_id is not None:
                feedback_by_plan[int(fb_plan_id)] = fb
            else:
                standalone_feedbacks.append(fb)

        organized_injury_logs = []
        for p in reversed(raw_plans):
            p_id, p_user, p_date, p_cat, p_content, p_intensity, p_notes, p_created = p
            try:
                parsed = json.loads(p_notes) if isinstance(p_notes, str) else {}
            except Exception:
                parsed = {}

            if not isinstance(parsed, dict):
                continue

            inj_list = parsed.get("injuries_list", [])
            if inj_list and isinstance(inj_list, list):
                max_vas = max([int(item.get("level", 0)) for item in inj_list])
                parts_summary = "、".join([item.get("area", "未標註部位").split(" ")[0] for item in inj_list])
                existing_fb = feedback_by_plan.get(int(p_id))
                organized_injury_logs.append({
                    "plan_id": int(p_id),
                    "athlete": p_user,
                    "date": p_date,
                    "category": p_cat,
                    "intensity": p_intensity,
                    "fatigue": parsed.get("fatigue_score", "-"),
                    "user_notes": parsed.get("user_notes", ""),
                    "injuries": inj_list,
                    "body_map_b64": parsed.get("body_map_img_b64", ""),
                    "max_vas": max_vas,
                    "parts_summary": parts_summary,
                    "feedback": existing_fb
                })

        fb_ids_all = [fb[0] for fb in all_feedbacks]
        replies_map = get_replies_by_feedback_ids(fb_ids_all)

        def render_reply_thread(fb_id):
            fb_replies = replies_map.get(fb_id, [])
            st.markdown("##### 💬 團隊回覆與追蹤討論")
            if fb_replies:
                for rep in fb_replies:
                    _, _, s_user, s_role, rep_txt, rep_at = rep
                    r_label = ROLE_LABELS.get(s_role, s_role)
                    badge_bg = "#2563eb" if s_role == "coach" else ("#059669" if s_role == "trainer" else "#d97706")
                    st.markdown(f"""
                        <div style="background:#f8fafc; border:1px solid #e2e8f0; border-left:4px solid {badge_bg}; border-radius:6px; padding:10px 12px; margin-bottom:8px;">
                            <div style="font-size:12px; color:#64748b; margin-bottom:4px;">
                                <span style="background:{badge_bg}; color:#ffffff !important; -webkit-text-fill-color:#ffffff !important; padding:2px 6px; border-radius:4px; font-weight:bold; margin-right:6px;">{r_label}</span>
                                <b style="color:#0f172a;">{s_user}</b> · {rep_at}
                            </div>
                            <div style="font-size:14px; color:#1e293b; white-space:pre-wrap;">{rep_txt}</div>
                        </div>
                    """, unsafe_allow_html=True)
            else:
                st.caption("尚無回覆留言，教練、選手或防護員皆可在下方留言溝通。")

            rep_c1, rep_c2 = st.columns([4, 1])
            with rep_c1:
                reply_txt = st.text_input("輸入回覆留言...", key=f"reply_inp_{fb_id}", label_visibility="collapsed", placeholder="輸入訓練調整回饋、疼痛變化或執行疑問...")
            with rep_c2:
                if st.button("💬 送出回覆", key=f"btn_send_rep_{fb_id}", use_container_width=True):
                    if reply_txt.strip():
                        save_injury_feedback_reply(fb_id, u["username"], role, reply_txt.strip())
                        st.rerun()
                    else:
                        st.warning("請輸入留言內容")

        def render_athlete_injury_details(log_item):
            st.markdown(f"**訓練項目**：`{log_item['category']}` ｜ **RPE**：`{log_item['intensity']}` ｜ **疲勞指數**：`{log_item['fatigue']}/10`")
            for idx, inj in enumerate(log_item["injuries"]):
                lvl = int(inj.get("level", 0))
                area = inj.get("area", f"痛點 #{idx+1}")
                ptypes = inj.get("pain_types", [])
                n_str = inj.get("notes", "")

                badges_html = ""
                if ptypes:
                    badges_html = " " + " ".join([
                        f'<span class="pain-tag-badge" style="background-color:#dc2626 !important; color:#ffffff !important; -webkit-text-fill-color:#ffffff !important; padding:2px 8px; border-radius:5px; font-size:12px; font-weight:600;">{pt}</span>'
                        for pt in ptypes
                    ])

                if lvl >= 5:
                    st.markdown(f"• :red[**{area}** ｜ 疼痛 VAS: {lvl}/10]{badges_html}", unsafe_allow_html=True)
                elif lvl > 0:
                    st.markdown(f"• :orange[**{area}** ｜ 疼痛 VAS: {lvl}/10]{badges_html}", unsafe_allow_html=True)
                else:
                    st.markdown(f"• :green[**{area}** ｜ 疼痛 VAS: 0/10 (追蹤/痊癒)]{badges_html}", unsafe_allow_html=True)

                if n_str:
                    st.caption(f"└ 選手備註：{n_str}")

            if log_item["user_notes"]:
                st.caption(f"📝 訓練感覺備註：{log_item['user_notes']}")

            if log_item["body_map_b64"]:
                st.image(log_item["body_map_b64"], caption="選手標記痛點圖", width=240)

        if role == "trainer":
            tab_review, tab_manual = st.tabs(["📋 選手傷病日誌批改區", "➕ 獨立開立防護報告"])

            with tab_review:
                total_cnt = len(organized_injury_logs)
                reviewed_cnt = sum(1 for item in organized_injury_logs if item["feedback"] is not None)
                pending_cnt = total_cnt - reviewed_cnt
                high_pain_cnt = sum(1 for item in organized_injury_logs if item["max_vas"] >= 5)

                m1, m2, m3, m4 = st.columns(4)
                m1.metric("總傷病日誌數", f"{total_cnt} 筆")
                m2.metric("⏳ 待批改", f"{pending_cnt} 筆")
                m3.metric("✅ 已批改", f"{reviewed_cnt} 筆")
                m4.metric("🔴 中重度疼痛 (VAS≥5)", f"{high_pain_cnt} 筆")

                st.markdown("---")
                f_c1, f_c2, f_c3 = st.columns(3)
                with f_c1:
                    status_filter = st.selectbox("批改狀態篩選：", ["全部紀錄", "⏳ 僅顯示待批改", "✅ 僅顯示已批改"], key="tr_filter_status")
                with f_c2:
                    team_athletes = get_team_athletes(team_code)
                    ath_filter = st.selectbox("選手篩選：", ["全團隊選手"] + team_athletes, key="tr_filter_ath")
                with f_c3:
                    pain_filter = st.selectbox("疼痛篩選：", ["全部 (含 VAS 0 追蹤)", "僅顯示有疼痛 (VAS > 0)"], key="tr_filter_pain")

                filtered_logs = []
                for item in organized_injury_logs:
                    if status_filter == "⏳ 僅顯示待批改" and item["feedback"] is not None:
                        continue
                    if status_filter == "✅ 僅顯示已批改" and item["feedback"] is None:
                        continue
                    if ath_filter != "全團隊選手" and item["athlete"] != ath_filter:
                        continue
                    if pain_filter == "僅顯示有疼痛 (VAS > 0)" and item["max_vas"] == 0:
                        continue
                    filtered_logs.append(item)

                if not filtered_logs:
                    st.info("目前篩選條件下尚無選手傷病日誌紀錄。")
                else:
                    for item in filtered_logs:
                        p_id = item["plan_id"]
                        fb = item["feedback"]
                        state_badge = "✅ 已批改" if fb is not None else "⏳ 待批改"
                        vas_badge = f"🔴 VAS {item['max_vas']}/10" if item["max_vas"] >= 5 else (f"🟠 VAS {item['max_vas']}/10" if item["max_vas"] > 0 else "🟢 VAS 0/10")
                        exp_title = f"{state_badge} ｜ 📅 {item['date']} ｜ 選手：{item['athlete']} ｜ {item['parts_summary']} ｜ {vas_badge}"

                        with st.expander(exp_title, expanded=(fb is None)):
                            col_left, col_right = st.columns([1.05, 1.15], gap="large")

                            with col_left:
                                st.markdown("##### 📄 選手日誌傷病回報內容")
                                render_athlete_injury_details(item)

                            with col_right:
                                st.markdown("##### 🩺 防護員批改勾選與回饋")
                                init_status = fb[5] if fb else STATUS_TAG_OPTIONS[1]
                                if init_status not in STATUS_TAG_OPTIONS:
                                    init_status = STATUS_TAG_OPTIONS[0]

                                init_checked = []
                                if fb and fb[9]:
                                    try:
                                        init_checked = json.loads(fb[9])
                                    except Exception:
                                        init_checked = []

                                init_advice = fb[6] if fb else ""

                                sel_status = st.radio(
                                    "1. 訓練狀態核定：",
                                    STATUS_TAG_OPTIONS,
                                    index=STATUS_TAG_OPTIONS.index(init_status),
                                    key=f"tr_status_{p_id}"
                                )

                                st.markdown("**2. 勾選建議防護與處置項目：**")
                                chk_cols = st.columns(2)
                                selected_checks = []
                                for c_idx, opt in enumerate(TREATMENT_CHECKLIST_OPTIONS):
                                    with chk_cols[c_idx % 2]:
                                        is_chk = st.checkbox(opt, value=(opt in init_checked), key=f"tr_chk_{p_id}_{c_idx}")
                                        if is_chk:
                                            selected_checks.append(opt)

                                advice_txt = st.text_area(
                                    "3. 防護員批改評語與復健建議：",
                                    value=init_advice,
                                    height=105,
                                    placeholder="請輸入針對該部位的處置提醒、課表調整建議或觀察重點...",
                                    key=f"tr_adv_{p_id}"
                                )

                                btn_label = "💾 更新批改內容 Update Review" if fb else "✅ 完成批改並送出回饋 Submit Review"
                                if st.button(btn_label, key=f"tr_save_{p_id}", use_container_width=True):
                                    if not selected_checks and not advice_txt.strip():
                                        st.warning("請至少勾選一項防護處置或填寫回饋建議！")
                                    else:
                                        save_injury_feedback(
                                            athlete_username=item["athlete"],
                                            trainer_username=u["username"],
                                            team_code=team_code,
                                            feedback_date=item["date"],
                                            body_part=item["parts_summary"],
                                            status_tag=sel_status,
                                            advice=advice_txt.strip(),
                                            plan_id=p_id,
                                            checked_items=selected_checks
                                        )
                                        st.success(f"已完成批改並同步回饋給選手 {item['athlete']}！")
                                        st.rerun()

                            if fb is not None:
                                st.markdown("---")
                                render_reply_thread(fb[0])

            with tab_manual:
                st.caption("若需針對未填寫訓練日誌的日期獨立建立防護紀錄，可由此新增。")
                team_athletes = get_team_athletes(team_code)
                if not team_athletes:
                    st.warning("團隊目前尚無選手帳號")
                else:
                    with st.form("form_manual_injury_feedback"):
                        sel_athlete = st.selectbox("選擇選手 Select Athlete", team_athletes)
                        fb_date = st.date_input("評估日期 Date", value=datetime.date.today())
                        body_part = st.text_input("傷痛部位 (例: 右大腿後側肌群 / 左腳踝跟腱)")
                        status_tag = st.selectbox("傷勢狀態評估", STATUS_TAG_OPTIONS)
                        checked_manual = st.multiselect("防護處置勾選項目", TREATMENT_CHECKLIST_OPTIONS)
                        treatment_advice = st.text_area("防護員處置與復健建議 Treatment & Rehab Advice", height=110)

                        if st.form_submit_button("送出獨立防護報告", use_container_width=True):
                            if body_part.strip() and (treatment_advice.strip() or checked_manual):
                                save_injury_feedback(
                                    sel_athlete, u["username"], team_code, fb_date,
                                    body_part.strip(), status_tag, treatment_advice.strip(),
                                    plan_id=None, checked_items=checked_manual
                                )
                                st.success("防護建議報告已成功送出！")
                                st.rerun()
                            else:
                                st.warning("請填寫部位與處置建議")

        else:
            if role == "coach":
                st.subheader("📋 團隊選手傷病追蹤與防護員批改總覽")
                team_athletes = get_team_athletes(team_code)
                filter_ath = st.selectbox("篩選選手 Select Athlete：", ["全團隊選手"] + team_athletes) if team_athletes else "全團隊選手"
                view_logs = [x for x in organized_injury_logs if filter_ath == "全團隊選手" or x["athlete"] == filter_ath]
                view_standalone = [fb for fb in standalone_feedbacks if filter_ath == "全團隊選手" or fb[1] == filter_ath]
            else:
                st.subheader("📋 我的傷病日誌與防護員批改回饋")
                view_logs = organized_injury_logs
                view_standalone = standalone_feedbacks

            if not view_logs and not view_standalone:
                st.info("目前尚無傷病追蹤與防護批改紀錄。")
            else:
                for item in view_logs:
                    fb = item["feedback"]
                    state_badge = "✅ 防護員已批改" if fb is not None else "⏳ 等待防護員批改"
                    vas_badge = f"🔴 VAS {item['max_vas']}/10" if item["max_vas"] >= 5 else (f"🟠 VAS {item['max_vas']}/10" if item["max_vas"] > 0 else "🟢 VAS 0/10")
                    ath_prefix = f"選手：{item['athlete']} ｜ " if role == "coach" else ""
                    exp_title = f"{state_badge} ｜ 📅 {item['date']} ｜ {ath_prefix}{item['parts_summary']} ｜ {vas_badge}"

                    with st.expander(exp_title, expanded=(fb is not None)):
                        c_a, c_b = st.columns([1, 1.1], gap="large")
                        with c_a:
                            st.markdown("##### 📄 訓練日誌傷病紀錄")
                            render_athlete_injury_details(item)

                        with c_b:
                            st.markdown("##### 🩺 防護員批改結果與處置建議")
                            if fb is None:
                                st.info("⏳ 防護員尚未批改此筆傷病日誌，批改完成後將即時顯示於此處。")
                            else:
                                _, _, t_user, _, _, s_tag, advice, c_at, _, chk_json = fb
                                try:
                                    chk_list = json.loads(chk_json) if chk_json else []
                                except Exception:
                                    chk_list = []

                                st.write(f"• **批改防護員**：`{t_user}`（更新時間：{c_at}）")
                                st.write(f"• **訓練狀態核定**：**{s_tag}**")
                                if chk_list:
                                    st.markdown("• **防護處置勾選清單**：")
                                    for c_item in chk_list:
                                        st.markdown(f"  - ☑️ **{c_item}**")
                                if advice:
                                    st.markdown(f"• **防護員評語與建議**：\n```text\n{advice}\n```")

                        if fb is not None:
                            st.markdown("---")
                            render_reply_thread(fb[0])

                if view_standalone:
                    st.markdown("---")
                    st.markdown("##### 📌 防護員獨立開立報告")
                    for fb in view_standalone:
                        fb_id, a_user, t_user, f_date, b_part, s_tag, advice, c_at, _, chk_json = fb
                        try:
                            chk_list = json.loads(chk_json) if chk_json else []
                        except Exception:
                            chk_list = []
                        with st.expander(f"📅 {f_date} ｜ 選手: {a_user} ｜ 部位: {b_part} ｜ {s_tag}"):
                            st.write(f"• **開立運動防護員**：`{t_user}`（建立時間：{c_at}）")
                            if chk_list:
                                for c_item in chk_list:
                                    st.markdown(f"- ☑️ **{c_item}**")
                            if advice:
                                st.write(f"• **防護員處置與復健建議**：\n```text\n{advice}\n```")
                            st.markdown("---")
                            render_reply_thread(fb_id)

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
                parsed_angle_metrics = {}
                col_prev1, col_prev2 = st.columns([1.2, 0.8])
                with col_prev1: st.image(captured_img, caption="角度量測合成圖", use_container_width=True)
                with col_prev2:
                    try:
                        parsed_angle_metrics = json.loads(captured_json)
                        st.write(f"時間：`{parsed_angle_metrics.get('rel_time_sec', '0.00')} 秒`")
                        st.write(f"階段：`{parsed_angle_metrics.get('phase_tag')}`")
                        for item in parsed_angle_metrics.get("angles", []): st.write(f"• **{item['name']}**：`{item['value']}`")
                    except Exception: pass

                if st.button("🤖 AI 跨欄動作生物力學一鍵診斷", key="btn_ai_single_angle", use_container_width=True):
                    with st.spinner("Gemini AI 教練正在判讀關鍵影格姿態與關節角度..."):
                        ok_ai, ai_res = analyze_hurdle_action_with_ai(captured_img, parsed_angle_metrics, mode="single")
                        if ok_ai:
                            st.session_state["ai_single_diag"] = ai_res
                        else:
                            st.error(ai_res)

                if st.session_state.get("ai_single_diag"):
                    with st.container(border=True):
                        st.markdown("##### 🤖 Gemini AI 跨欄生物力學診斷報告")
                        st.markdown(st.session_state["ai_single_diag"])

                if st.session_state["user_info"]:
                    with st.form("save_angle_form"):
                        rec_title_angle = st.text_input("紀錄名稱", value=f"動作關鍵影格角度量測 {uploaded_file.name}")
                        rec_note_angle = st.text_area("動作分析筆記 (選填)", placeholder="可記錄起跳角度觀察、身體重心調整重點...", height=80)
                        if st.form_submit_button("儲存至團隊資料庫"):
                            u = st.session_state["user_info"]
                            save_record(u["username"], u["team_code"], "關鍵影格角度量測 Keyframe Angle Measurement", rec_title_angle, {
                                "video_filename": uploaded_file.name,
                                "image_b64": captured_img,
                                "angle_metrics": json.loads(captured_json),
                                "ai_diagnosis": st.session_state.get("ai_single_diag", ""),
                                "user_notes": rec_note_angle.strip(),
                                "saved_at": str(datetime.datetime.now())
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
                            rec_note_timer = st.text_area("欄間節奏分析筆記 (選填)", placeholder="可記錄各欄間步頻變化、後段速度維持情況...", height=80)
                            if st.form_submit_button("儲存至團隊資料庫"):
                                u = st.session_state["user_info"]
                                save_record(u["username"], u["team_code"], "欄間秒數及速度計算 Hurdle Time & Speed Calculation", rec_title_timer, {
                                    "timer_metrics": tm_data,
                                    "user_notes": rec_note_timer.strip(),
                                    "saved_at": str(datetime.datetime.now())
                                })
                                st.success("儲存成功！")
                except Exception: pass

# ==================== 【頁面 5：雙影同步 Dual Video Sync】 ====================
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

                        const mergedB64 = outCan.toDataURL('image/jpeg', 0.85);
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

            try:
                parsed_dual_metrics = json.loads(captured_dual_json)
            except Exception:
                parsed_dual_metrics = {"raw_json": captured_dual_json}

            if st.button("🤖 AI 雙影動作差異與生物力學一鍵診斷", key="btn_ai_dual_angle", use_container_width=True):
                with st.spinner("Gemini AI 教練正在比對左右兩部影片的動作與關節角度差異..."):
                    ok_dual_ai, dual_ai_res = analyze_hurdle_action_with_ai(captured_dual_img, parsed_dual_metrics, mode="dual")
                    if ok_dual_ai:
                        st.session_state["ai_dual_diag"] = dual_ai_res
                    else:
                        st.error(dual_ai_res)

            if st.session_state.get("ai_dual_diag"):
                with st.container(border=True):
                    st.markdown("##### 🤖 Gemini AI 雙影對比生物力學診斷報告")
                    st.markdown(st.session_state["ai_dual_diag"])

            if st.session_state["user_info"]:
                with st.form("save_dual_angle_form"):
                    rec_title_dual = st.text_input("紀錄名稱", value=f"雙影角度對比 {file_a.name} vs {file_b.name}")
                    rec_note_dual = st.text_area("雙影對比筆記 (選填)", placeholder="可記錄左右動作差異、改善重點...", height=80)
                    if st.form_submit_button("💾 儲存至團隊數據庫", use_container_width=True):
                        u = st.session_state["user_info"]
                        save_record(
                            u["username"],
                            u["team_code"],
                            "雙影同步 Dual Video Sync",
                            rec_title_dual,
                            {
                                "image_b64": captured_dual_img, 
                                "angle_metrics": parsed_dual_metrics,
                                "ai_diagnosis": st.session_state.get("ai_dual_diag", ""),
                                "user_notes": rec_note_dual.strip(),
                                "saved_at": str(datetime.datetime.now())
                            }
                        )
                        st.success("成功儲存至團隊歷史數據庫！")
                        st.rerun()

# ==================== 【頁面 6：跨期數據對比】 ====================
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

# ==================== 【頁面 7：課表紀錄】 ====================
elif page == "課表紀錄 Training Plan":
    st.title("課表與訓練日誌紀錄 Training Plan & Daily Log")
    st.markdown("---")

    if st.session_state["user_info"] is None:
        st.warning("請先在左側邊欄登入帳號 Please Login in Sidebar First")
    else:
        user = st.session_state["user_info"]
        role = user["role"]
        is_staff = (role in ["coach", "trainer"])

        BODY_PART_OPTIONS = [
            "頭頸部 Head & Neck",
            "右肩內側(前側) Right Medial/Anterior Shoulder",
            "右肩外側 Right Lateral Shoulder",
            "左肩內側(前側) Left Medial/Anterior Shoulder",
            "左肩外側 Left Lateral Shoulder",
            "胸大肌 Pectoralis Major",
            "腹直肌/核心 Rectus Abdominis",
            "下背/腰椎肌群 Lower Back Lumbar",
            "右臀肌 Right Gluteus",
            "左臀肌 Left Gluteus",
            "右髖內側(內收肌群) Right Medial Hip / Adductors",
            "右髖外側 Right Lateral Hip",
            "左髖內側(內收肌群) Left Medial Hip / Adductors",
            "左髖外側 Left Lateral Hip",
            "右大腿前側(股四頭肌) Right Quadriceps",
            "左大腿前側(股四頭肌) Left Quadriceps",
            "右大腿內側 Right Medial Thigh",
            "右大腿外側(髂脛束) Right Lateral Thigh / ITB",
            "左大腿內側 Left Medial Thigh",
            "左大腿外側(髂脛束) Left Lateral Thigh / ITB",
            "右大腿後側內側(半腱/半膜肌) Right Medial Hamstrings",
            "右大腿後側外側(股二頭肌) Right Lateral Hamstrings",
            "左大腿後側內側(半腱/半膜肌) Left Medial Hamstrings",
            "左大腿後側外側(股二頭肌) Left Lateral Hamstrings",
            "右膝前側(髕腱) Right Anterior Knee",
            "右膝內側 Right Medial Knee",
            "右膝外側 Right Lateral Knee",
            "左膝前側(髕腱) Left Anterior Knee",
            "左膝內側 Left Medial Knee",
            "左膝外側 Left Lateral Knee",
            "右小腿內側 Right Medial Calf",
            "右小腿外側 Right Lateral Calf",
            "右跟腱 Right Achilles Tendon",
            "左小腿內側 Left Medial Calf",
            "左小腿外側 Left Lateral Calf",
            "左跟腱 Left Achilles Tendon",
            "右腳踝內側 Right Medial Ankle",
            "右腳踝外側 Right Lateral Ankle",
            "左腳踝內側 Left Medial Ankle",
            "左腳踝外側 Left Lateral Ankle",
            "右腳底內側(足弓) Right Medial Plantar",
            "右腳底外側 Right Lateral Plantar",
            "左腳底內側(足弓) Left Medial Plantar",
            "左腳底外側 Left Lateral Plantar"
        ]

        PAIN_NATURE_OPTIONS = [
            "延遲性酸痛 (DOMS)", "肌肉緊繃/僵硬", "拉扯痛/牽扯感", "撕裂痛",
            "銳痛/刺痛", "鈍痛/深層隱痛", "關節卡痛/摩擦感", "按壓痛",
            "腫脹壓迫痛", "灼熱感/發炎痛", "脈動痛/跳痛",
            "麻木感/麻痛", "放射痛/電擊感"
        ]

        RPE_OPTIONS = [
            "1 非常輕鬆",
            "2 很輕鬆",
            "3 輕鬆",
            "4 稍微吃力",
            "5 中等",
            "6 稍微艱難",
            "7 艱難",
            "8 非常艱難",
            "9 接近極限",
            "10 極限"
        ]

        CATEGORY_OPTIONS = [
            "欄架技術 Hurdle Technique",
            "欄架基本／輔助 Hurdle Drills & Auxiliary",
            "起跑 Start",
            "欄間節奏與步頻 Inter-Hurdle Rhythm & Cadence",
            "最大速度 / 短衝衝刺 Max Velocity / Sprint",
            "專項速度耐力 Speed Endurance",
            "無氧 / 乳酸耐力 Anaerobic / Lactate Tolerance",
            "間歇 Interval Training",
            "阻力 Resistance Training",
            "重量訓練 Weight Training",
            "增強式訓練 / 彈跳 Plyometrics",
            "核心與動力鏈穩定 Core & Kinetic Chain Stability",
            "跨欄柔軟度 / 髖關節活動度 Hurdle Mobility & Flexibility",
            "敏捷協調 / 敏捷梯訓練 Agility & Coordination",
            "動態恢復 / 伸展放鬆 Active Recovery & Stretching",
            "物理治療 / 傷後復健 Rehab & Prehab",
            "測驗與模擬賽 Time Trial / Competition Simulation"
        ]

        def render_training_log_history():
            st.subheader("團隊訓練課表與日誌歷史檔案 Team Training Log Archive" if is_staff else "訓練課表與日誌歷史檔案 Training Log Archive")
            if is_staff:
                team_athletes = get_team_athletes(user["team_code"])
                filter_user = st.selectbox("篩選選手 Filter Athlete", ["全隊所有人 Entire Team"] + team_athletes)
                query_user = None if filter_user == "全隊所有人 Entire Team" else filter_user
                try:
                    plans = get_training_plans(username=query_user, team_code=user["team_code"])
                except Exception:
                    plans = []
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
                    df_pain_agg = df_pain_raw.groupby(['Date', 'BodyArea'], as_index=False)['PainLevel'].last()
                    pain_chart = alt.Chart(df_pain_agg).mark_line(point=True).encode(
                        x=alt.X('Date:N', title='訓練日期 Date', sort='ascending'),
                        y=alt.Y('PainLevel:Q', title='疼痛 VAS', scale=alt.Scale(domain=[0, 10])),
                        color=alt.Color('BodyArea:N', title='疼痛部位'),
                        tooltip=[
                            alt.Tooltip('Date:N', title='日期'),
                            alt.Tooltip('BodyArea:N', title='部位'),
                            alt.Tooltip('PainLevel:Q', title='疼痛 VAS')
                        ]
                    ).properties(height=340)
                    st.altair_chart(pain_chart, use_container_width=True)
                else:
                    st.caption("尚無傷病追蹤數據可繪製趨勢圖 No Injury Data for Line Chart")

                st.markdown("---")
                
                for p in reversed(plans):
                    p_id, p_user, p_date, p_cat, p_content, p_intensity, p_notes, p_created = p
                    parsed_notes = None
                    try: parsed_notes = json.loads(p_notes)
                    except Exception: parsed_notes = None

                    if is_staff:
                        expander_title = f"日期 Date: {p_date} ｜ 選手 Athlete: {p_user} ｜ 類別: {p_cat} ｜ RPE: {p_intensity}"
                    else:
                        expander_title = f"日期 Date: {p_date} ｜ {p_cat}"

                    with st.expander(expander_title):
                        edit_mode_key = f"editing_plan_{p_id}"
                        edit_inj_key = f"edit_injuries_{p_id}"
                        is_editing = st.session_state.get(edit_mode_key, False)
                        can_modify = (is_staff or p_user == user["username"])

                        if not is_editing:
                            if not is_staff:
                                st.write(f"• **訓練類別 Category**：`{p_cat}` ｜ **本體感覺 RPE**：`{p_intensity}`")

                            if parsed_notes and isinstance(parsed_notes, dict):
                                tot_s = parsed_notes.get('total_sleep_hr', 0)
                                s_h = parsed_notes.get('sleep_hours', int(tot_s))
                                s_m = parsed_notes.get('sleep_mins', int((tot_s - int(tot_s))*60))
                                sleep_str = f"`{s_h}小時 {s_m}分`"

                                bedtime_str = parsed_notes.get('bedtime', '-')
                                waketime_str = parsed_notes.get('waketime', '-')
                                restorative_pct = parsed_notes.get('restorative_sleep_pct', None)
                                if restorative_pct in [None, "", "未紀錄"]:
                                    restorative_str = "`未紀錄`"
                                else:
                                    restorative_str = f"`{restorative_pct}%`"

                                raw_hr = parsed_notes.get('resting_hr', '-')
                                if raw_hr in [None, "", "未紀錄"]:
                                    hr_str = "`未紀錄`"
                                else:
                                    hr_str = f"`{raw_hr} bpm`"

                                period_str = parsed_notes.get('menstrual_period', '-')

                                inj_list = parsed_notes.get("injuries_list", [])
                                st.markdown("**訓練與生理狀態：**")
                                c_d1, c_d2, c_d3 = st.columns(3)
                                with c_d1:
                                    st.write(f"• 訓練時長：`{parsed_notes.get('duration_min', '-')} 分鐘`")
                                    st.write(f"• 睡覺 / 起床時間：`{bedtime_str}` ～ `{waketime_str}`")
                                with c_d2:
                                    st.write(f"• 疲勞指數：`{parsed_notes.get('fatigue_score', '-')}/10`")
                                    st.write(f"• 睡眠總時長：{sleep_str} ｜ 恢復性睡眠：{restorative_str}")
                                with c_d3:
                                    st.write(f"• 靜息心率：{hr_str}")
                                    st.write(f"• 生理期紀錄：`{period_str}`")

                                col_inj_text, col_inj_map = st.columns([1.2, 0.8])
                                with col_inj_text:
                                    st.markdown("**痛點部位與疼痛性質紀錄 Injury Details：**")
                                    if inj_list:
                                        for inj in inj_list:
                                            lvl = inj.get("level", 0)
                                            area = inj.get("area", f"痛點 #{inj.get('id',1)}")
                                            ptypes = inj.get("pain_types", [])
                                            ptypes_badges = ""
                                            if ptypes:
                                                badges_html = " ".join([
                                                    f'<span class="pain-tag-badge" style="background-color:#dc2626 !important; color:#ffffff !important; -webkit-text-fill-color:#ffffff !important; padding:2px 8px; border-radius:5px; font-size:12px; font-weight:600;">{pt}</span>'
                                                    for pt in ptypes
                                                ])
                                                ptypes_badges = f" ｜ 性質：{badges_html}"
                                            n_str = inj.get("notes", "")
                                            n_fmt = f" ({n_str})" if n_str else ""
                                            
                                            if lvl >= 5:
                                                st.markdown(f"• :red[**{area}** ｜ 疼痛 VAS: {lvl}/10]{ptypes_badges}{n_fmt}", unsafe_allow_html=True)
                                            elif lvl > 0:
                                                st.markdown(f"• :orange[**{area}** ｜ 疼痛 VAS: {lvl}/10 (復原中)]{ptypes_badges}{n_fmt}", unsafe_allow_html=True)
                                            else:
                                                st.markdown(f"• :green[**{area}** ｜ 疼痛 VAS: 0/10 (痊癒)]{ptypes_badges}{n_fmt}", unsafe_allow_html=True)
                                    else:
                                        st.markdown(":green[良好無傷病 No Active Injury]")

                                with col_inj_map:
                                    if "body_map_img_b64" in parsed_notes and parsed_notes["body_map_img_b64"]:
                                        st.image(parsed_notes["body_map_img_b64"], caption="當時人體標記圖 Body Map Snapshot", use_container_width=True)

                                st.markdown(f"**課表細節：**\n```text\n{p_content}\n```")
                                u_notes = parsed_notes.get("user_notes", "")
                                if u_notes:
                                    st.markdown(f"**動作感覺與教練叮嚀：**\n```text\n{u_notes}\n```")
                            else:
                                st.markdown(f"**課表細節 Details：**\n```text\n{p_content}\n```")

                            if can_modify:
                                st.markdown("---")
                                act_c1, act_c2, _ = st.columns([1.1, 1.1, 2.8])
                                with act_c1:
                                    if st.button("✏️ 編輯日誌 Edit Log", key=f"btn_edit_plan_{p_id}", use_container_width=True):
                                        st.session_state[edit_mode_key] = True
                                        orig_inj = parsed_notes.get("injuries_list", []) if isinstance(parsed_notes, dict) else []
                                        st.session_state[edit_inj_key] = [
                                            {
                                                "area": item.get("area", BODY_PART_OPTIONS[0]),
                                                "level": int(item.get("level", 0)),
                                                "pain_types": list(item.get("pain_types", [])),
                                                "notes": item.get("notes", "")
                                            }
                                            for item in orig_inj
                                        ]
                                        st.rerun()
                                with act_c2:
                                    if st.button("🗑️ 刪除日誌 Delete Log", key=f"del_plan_{p_id}", use_container_width=True):
                                        delete_training_plan(p_id)
                                        st.rerun()
                        else:
                            st.info(f"✏️ 正在修改 **{p_date}** 的訓練日誌（修改完成後請點擊下方「💾 儲存修改」）")

                            p_dict = parsed_notes if isinstance(parsed_notes, dict) else {}
                            try:
                                init_date = datetime.datetime.strptime(p_date, "%Y-%m-%d").date()
                            except Exception:
                                init_date = datetime.date.today()

                            try:
                                init_bed = datetime.datetime.strptime(p_dict.get("bedtime", "23:00"), "%H:%M").time()
                            except Exception:
                                init_bed = datetime.time(23, 0)

                            try:
                                init_wake = datetime.datetime.strptime(p_dict.get("waketime", "06:30"), "%H:%M").time()
                            except Exception:
                                init_wake = datetime.time(6, 30)

                            cat_opts = CATEGORY_OPTIONS if p_cat in CATEGORY_OPTIONS else [p_cat] + CATEGORY_OPTIONS
                            rpe_opts = RPE_OPTIONS if p_intensity in RPE_OPTIONS else [p_intensity] + RPE_OPTIONS

                            st.markdown("##### 1. 基本訓練設定 Basic Plan Info")
                            ec_p1, ec_p2 = st.columns(2, gap="medium")
                            with ec_p1:
                                edit_date = st.date_input("訓練日期 Training Date", value=init_date, key=f"edit_date_{p_id}")
                                edit_dur = st.number_input("訓練總時長 (分鐘 min)", min_value=10, max_value=300, value=int(p_dict.get("duration_min", 90)), step=5, key=f"edit_dur_{p_id}")
                            with ec_p2:
                                edit_cat = st.selectbox("訓練主題與類別", cat_opts, index=cat_opts.index(p_cat), key=f"edit_cat_{p_id}")
                                edit_rpe = st.select_slider("本體感覺 RPE", options=rpe_opts, value=p_intensity, key=f"edit_rpe_{p_id}")

                            st.markdown("##### 2. 體能與恢復狀態 Recovery Status & Physiology")
                            raw_edit_hr = p_dict.get("resting_hr", 65)
                            init_hr_unrec = (raw_edit_hr in [None, "", "未紀錄"])
                            init_hr_num = int(raw_edit_hr) if isinstance(raw_edit_hr, (int, float)) else 65

                            ec_r1, ec_r2, ec_r3 = st.columns(3, gap="medium")
                            with ec_r1:
                                edit_fatigue = st.slider("身體疲勞指數 Fatigue (1–10)", 1, 10, int(p_dict.get("fatigue_score", 3)), key=f"edit_fatigue_{p_id}")
                            with ec_r2:
                                edit_hr_unrec = st.toggle("靜息心率：未紀錄（未戴手錶）", value=init_hr_unrec, key=f"edit_hr_unrec_{p_id}")
                                edit_hr_val = st.number_input("靜息心率 Resting HR (bpm)", min_value=40, max_value=220, value=init_hr_num, disabled=edit_hr_unrec, key=f"edit_hr_{p_id}")
                                edit_hr = "未紀錄" if edit_hr_unrec else edit_hr_val
                            with ec_r3:
                                init_period = p_dict.get("menstrual_period", "無")
                                period_idx = 1 if init_period == "有" else 0
                                edit_period = st.radio("生理期紀錄 Menstrual Period", ["無", "有"], index=period_idx, horizontal=True, key=f"edit_period_{p_id}")

                            ec_t1, ec_t2 = st.columns(2, gap="medium")
                            with ec_t1:
                                edit_bedtime = st.time_input("睡覺時間 Bedtime", value=init_bed, key=f"edit_bed_{p_id}")
                            with ec_t2:
                                edit_waketime = st.time_input("起床時間 Wake-up Time", value=init_wake, key=f"edit_wake_{p_id}")

                            edit_sleep_h, edit_sleep_m, edit_total_sleep = calc_sleep_duration(edit_bedtime, edit_waketime)

                            raw_edit_rs = p_dict.get("restorative_sleep_pct", 45)
                            init_rs_unrec = (raw_edit_rs in [None, "", "未紀錄"])
                            init_rs_num = int(raw_edit_rs) if isinstance(raw_edit_rs, (int, float)) else 45

                            ec_s1, ec_s2 = st.columns(2, gap="medium")
                            with ec_s1:
                                st.caption("昨夜睡眠總時長 Sleep Duration（依睡覺與起床時間自動計算）")
                                st.info(f"🛌 自動計算結果：**{edit_sleep_h} 小時 {edit_sleep_m} 分**（共 {edit_total_sleep} 小時）")
                            with ec_s2:
                                st.caption("深層 / REM 恢復性睡眠比例（手錶偵測）")
                                edit_rs_unrec = st.toggle("恢復性睡眠：未紀錄（未戴手錶）", value=init_rs_unrec, key=f"edit_rs_unrec_{p_id}")
                                edit_rest_sleep_val = st.number_input("恢復性睡眠 Restorative Sleep (%)", min_value=0, max_value=100, value=init_rs_num, step=1, disabled=edit_rs_unrec, key=f"edit_rs_{p_id}")
                                edit_rest_sleep = "未紀錄" if edit_rs_unrec else edit_rest_sleep_val

                            st.markdown("###### 傷害追蹤修正 Injury Tracking")
                            edit_injuries_list = st.session_state.get(edit_inj_key, [])
                            updated_injuries_data = []
                            edit_del_idx = None

                            for e_idx, e_item in enumerate(edit_injuries_list):
                                with st.container(border=True):
                                    esel1, esel2, esel3 = st.columns([1.5, 1.1, 1.6], gap="medium")
                                    with esel1:
                                        area_opts = BODY_PART_OPTIONS if e_item["area"] in BODY_PART_OPTIONS else [e_item["area"]] + BODY_PART_OPTIONS
                                        e_area = st.selectbox(f"痛點部位 #{e_idx+1}", area_opts, index=area_opts.index(e_item["area"]), key=f"edit_area_{p_id}_{e_idx}")
                                    with esel2:
                                        e_lvl = st.slider(f"疼痛等級 (VAS 0-10)", 0, 10, int(e_item.get("level", 0)), key=f"edit_lvl_{p_id}_{e_idx}")
                                        if st.button("刪除此痛點", key=f"edit_del_inj_{p_id}_{e_idx}"):
                                            edit_del_idx = e_idx
                                    with esel3:
                                        e_default_pts = e_item.get("pain_types", [])
                                        if not isinstance(e_default_pts, list):
                                            e_default_pts = []
                                        st.markdown('<span class="pain-nature-anchor"></span>', unsafe_allow_html=True)
                                        e_ptypes = st.multiselect(
                                            f"疼痛性質 (可多選)",
                                            options=PAIN_NATURE_OPTIONS,
                                            default=[pt for pt in e_default_pts if pt in PAIN_NATURE_OPTIONS],
                                            key=f"edit_ptypes_{p_id}_{e_idx}"
                                        )
                                    e_note = st.text_area(
                                        f"備註說明 #{e_idx+1}",
                                        value=e_item.get("notes", ""),
                                        height=95,
                                        placeholder="請輸入疼痛觸發動作、痠痛程度變化、冰敷/伸展處置等詳細說明...",
                                        key=f"edit_note_{p_id}_{e_idx}"
                                    )

                                    updated_injuries_data.append({
                                        "id": e_idx + 1,
                                        "area": e_area,
                                        "level": e_lvl,
                                        "pain_types": e_ptypes,
                                        "notes": e_note
                                    })

                            if edit_del_idx is not None:
                                st.session_state[edit_inj_key].pop(edit_del_idx)
                                st.rerun()

                            if st.button("➕ 新增身體部位痛點", key=f"edit_add_inj_{p_id}"):
                                st.session_state[edit_inj_key].append({"area": "右膝內側 Right Medial Knee", "level": 0, "pain_types": [], "notes": ""})
                                st.rerun()

                            st.markdown("##### 3. 課表內容與自主備註 Plan Details & Notes")
                            edit_content = st.text_area("課表細節與組數規劃 Plan Details & Sets", value=p_content, height=120, key=f"edit_content_{p_id}")
                            init_u_notes = p_dict.get("user_notes", "") if parsed_notes is not None else (p_notes or "")
                            edit_u_notes = st.text_area("動作感覺與教練叮嚀 Notes & Feedback", value=init_u_notes, height=85, key=f"edit_unotes_{p_id}")

                            sb_c1, sb_c2, _ = st.columns([1.2, 1.2, 2.6])
                            with sb_c1:
                                if st.button("💾 儲存修改 Save Changes", key=f"btn_save_edit_{p_id}", use_container_width=True):
                                    if edit_content.strip():
                                        updated_extra_info = {
                                            "duration_min": edit_dur,
                                            "rpe_feeling": edit_rpe,
                                            "fatigue_score": edit_fatigue,
                                            "bedtime": edit_bedtime.strftime("%H:%M"),
                                            "waketime": edit_waketime.strftime("%H:%M"),
                                            "sleep_hours": edit_sleep_h,
                                            "sleep_mins": edit_sleep_m,
                                            "total_sleep_hr": round(edit_total_sleep, 2),
                                            "restorative_sleep_pct": edit_rest_sleep,
                                            "resting_hr": edit_hr,
                                            "menstrual_period": edit_period,
                                            "injuries_list": updated_injuries_data,
                                            "body_map_img_b64": p_dict.get("body_map_img_b64", ""),
                                            "user_notes": edit_u_notes
                                        }
                                        update_training_plan(
                                            p_id,
                                            str(edit_date),
                                            edit_cat,
                                            edit_content,
                                            edit_rpe,
                                            json.dumps(updated_extra_info, ensure_ascii=False)
                                        )
                                        if edit_mode_key in st.session_state:
                                            del st.session_state[edit_mode_key]
                                        if edit_inj_key in st.session_state:
                                            del st.session_state[edit_inj_key]
                                        st.success("日誌修改已成功儲存！")
                                        st.rerun()
                                    else:
                                        st.warning("課表細節不可為空！")
                            with sb_c2:
                                if st.button("❌ 取消編輯 Cancel", key=f"btn_cancel_edit_{p_id}", use_container_width=True):
                                    if edit_mode_key in st.session_state:
                                        del st.session_state[edit_mode_key]
                                    if edit_inj_key in st.session_state:
                                        del st.session_state[edit_inj_key]
                                    st.rerun()

        if is_staff:
            render_training_log_history()
        else:
            tab_new_plan, tab_view_plan = st.tabs([
                "新增與編輯訓練日誌 Add & Edit Training Log",
                "歷次課表與日誌總覽 Training Log History"
            ])

            with tab_new_plan:
                st.subheader("填寫訓練日誌內容 Fill Training Log Details")
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
                        carried_injuries = [{"area": "右大腿前側(股四頭肌) Right Quadriceps", "level": 0, "pain_types": [], "notes": ""}]

                    st.session_state["dyn_injuries"] = carried_injuries
                    st.session_state["dyn_injuries_user"] = target_athlete

                captured_body_map_img = st.text_area("body_map_img_b64", key="captured_body_map_img", label_visibility="collapsed")

                st.markdown("##### 1. 基本訓練設定 Basic Plan Info")
                c_p1, c_p2 = st.columns(2, gap="medium")
                with c_p1:
                    plan_date = st.date_input("訓練日期 Training Date", value=datetime.date.today(), key="log_plan_date")
                    duration_min = st.number_input("訓練總時長 (分鐘 min)", min_value=10, max_value=300, value=90, step=5, key="log_duration")
                with c_p2:
                    category = st.selectbox("訓練主題與類別", CATEGORY_OPTIONS, key="log_category")
                    rpe_feeling = st.select_slider("本體感覺 RPE", options=RPE_OPTIONS, value="5 中等", key="log_rpe")

                st.markdown("---")
                st.markdown("##### 2. 體能與恢復狀態 Recovery Status & Physiology")

                c_r1, c_r2, c_r3 = st.columns(3, gap="medium")
                with c_r1:
                    fatigue_score = st.slider("身體疲勞指數 Fatigue (1–10)", 1, 10, 3, key="log_fatigue")
                with c_r2:
                    hr_unrecorded = st.toggle("靜息心率：未紀錄（未戴手錶）", value=False, key="log_hr_unrec")
                    resting_hr_val = st.number_input("靜息心率 Resting HR (bpm)", min_value=40, max_value=220, value=65, disabled=hr_unrecorded, key="log_hr")
                    resting_hr = "未紀錄" if hr_unrecorded else resting_hr_val
                with c_r3:
                    menstrual_period = st.radio("生理期紀錄 Menstrual Period", ["無", "有"], horizontal=True, key="log_menstrual")

                c_t1, c_t2 = st.columns(2, gap="medium")
                with c_t1:
                    bedtime = st.time_input("睡覺時間 Bedtime", value=datetime.time(23, 0), key="log_bedtime")
                with c_t2:
                    waketime = st.time_input("起床時間 Wake-up Time", value=datetime.time(6, 30), key="log_waketime")

                sleep_hours, sleep_mins, total_sleep_hr = calc_sleep_duration(bedtime, waketime)

                c_s1, c_s2 = st.columns(2, gap="medium")
                with c_s1:
                    st.caption("昨夜睡眠總時長 Sleep Duration（依睡覺與起床時間自動計算）")
                    st.info(f"🛌 自動計算結果：**{sleep_hours} 小時 {sleep_mins} 分**（共 {total_sleep_hr} 小時）")
                with c_s2:
                    st.caption("深層 / REM 恢復性睡眠比例（手錶偵測）")
                    rs_unrecorded = st.toggle("恢復性睡眠：未紀錄（未戴手錶）", value=False, key="log_rs_unrec")
                    restorative_sleep_val = st.number_input("恢復性睡眠 Restorative Sleep (%)", min_value=0, max_value=100, value=45, step=1, disabled=rs_unrecorded, key="log_restorative_sleep")
                    restorative_sleep_pct = "未紀錄" if rs_unrecorded else restorative_sleep_val

                st.markdown("<br>", unsafe_allow_html=True)
                st.markdown("###### 傷害追蹤")

                injuries_input_data = []
                to_delete_idx = None
                for idx, item in enumerate(st.session_state["dyn_injuries"]):
                    with st.container(border=True):
                        c_sel1, c_sel2, c_sel3 = st.columns([1.5, 1.1, 1.6], gap="medium")
                        with c_sel1:
                            default_area_idx = BODY_PART_OPTIONS.index(item["area"]) if item["area"] in BODY_PART_OPTIONS else 0
                            selected_area = st.selectbox(f"痛點部位 #{idx+1}", BODY_PART_OPTIONS, index=default_area_idx, key=f"sel_area_{idx}")
                        with c_sel2:
                            selected_lvl = st.slider(f"疼痛等級 (VAS 0-10)", 0, 10, item["level"], key=f"sel_lvl_{idx}")
                            if st.button("刪除此痛點", key=f"del_inj_{idx}"):
                                to_delete_idx = idx
                        with c_sel3:
                            default_pts = item.get("pain_types", [])
                            if not isinstance(default_pts, list): default_pts = []
                            st.markdown('<span class="pain-nature-anchor"></span>', unsafe_allow_html=True)
                            selected_pain_types = st.multiselect(
                                f"疼痛性質 (可多選)", 
                                options=PAIN_NATURE_OPTIONS, 
                                default=[p for p in default_pts if p in PAIN_NATURE_OPTIONS],
                                key=f"sel_ptypes_{idx}"
                            )
                        selected_note = st.text_area(
                            f"備註說明 #{idx+1}",
                            value=item.get("notes", ""),
                            height=95,
                            placeholder="請輸入疼痛觸發動作、痠痛程度變化、冰敷/伸展處置等詳細說明...",
                            key=f"sel_note_{idx}"
                        )

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
                    st.session_state["dyn_injuries"].append({"area": "右膝內側 Right Medial Knee", "level": 0, "pain_types": [], "notes": ""})
                    st.rerun()

                st.markdown("---")
                st.markdown("##### 3. 課表內容與自主備註 Plan Details & Notes")
                content = st.text_area("課表細節與組數規劃 Plan Details & Sets", height=120, key="log_content")
                user_notes = st.text_area("動作感覺與教練叮嚀 Notes & Feedback", height=80, key="log_user_notes")

                if st.button("儲存訓練日誌 Save Training Log", key="btn_save_training_log", use_container_width=True):
                    if content.strip():
                        extra_log_info = {
                            "duration_min": duration_min, 
                            "rpe_feeling": rpe_feeling, 
                            "fatigue_score": fatigue_score, 
                            "bedtime": bedtime.strftime("%H:%M"),
                            "waketime": waketime.strftime("%H:%M"),
                            "sleep_hours": sleep_hours, 
                            "sleep_mins": sleep_mins,
                            "total_sleep_hr": round(total_sleep_hr, 2), 
                            "restorative_sleep_pct": restorative_sleep_pct,
                            "resting_hr": resting_hr,
                            "menstrual_period": menstrual_period,
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

                        st.success(f"成功儲存 {plan_date} 的訓練與精準傷病日誌！")
                        st.rerun()
                    else:
                        st.warning("請填寫課表細節與組數規劃 Please fill plan details!")

                st.markdown("---")
                st.markdown("##### 獨立人體圖釘點輔助工具（點擊圖面可新增標記，點擊標記或下方按鈕可移除）")

                body_map_html = """<!DOCTYPE html>
                <html>
                <head>
                <style>
                    * { box-sizing: border-box; }
                    body { font-family: -apple-system, sans-serif; background: transparent; margin: 0; padding: 0; text-align: center; color: #0f172a; }
                    .map-wrapper-box { background: #ffffff; padding: 14px 16px 18px 16px; border-radius: 12px; border: 1px solid #cbd5e1; box-shadow: 0 1px 4px rgba(0,0,0,0.05); }
                    .map-toolbar { display: flex; justify-content: center; gap: 10px; margin-bottom: 14px; flex-wrap: wrap; align-items: center; }
                    .btn-pin-undo { background: #f1f5f9; color: #334155; border: 1px solid #cbd5e1; padding: 6px 14px; border-radius: 6px; font-size: 13px; font-weight: 600; cursor: pointer; }
                    .btn-pin-undo:hover { background: #e2e8f0; }
                    .btn-pin-clear { background: #ef4444; color: #ffffff; border: none; padding: 6px 14px; border-radius: 6px; font-size: 13px; font-weight: 600; cursor: pointer; }
                    .btn-pin-clear:hover { background: #dc2626; }
                    .pin-count-badge { font-size: 12px; color: #64748b; background: #f8fafc; padding: 5px 10px; border-radius: 6px; border: 1px solid #e2e8f0; }
                    .map-outer-container { display: flex; justify-content: center; gap: 24px; flex-wrap: wrap; }
                    .body-card { width: 220px; background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 10px; padding: 12px; display: flex; flex-direction: column; align-items: center; }
                    .body-title { font-size: 14px; font-weight: 700; color: #2563eb; margin-bottom: 8px; }
                    .svg-wrapper { position: relative; width: 190px; height: 380px; cursor: crosshair; user-select: none; background: #ffffff; border-radius: 8px; border: 1px solid #cbd5e1; }
                    .svg-wrapper svg { width: 100%; height: 100%; display: block; }
                    .body-shape { fill: #f1f5f9; stroke: #475569; stroke-width: 1.5; stroke-linecap: round; stroke-linejoin: round; }
                    .body-line { fill: none; stroke: #94a3b8; stroke-width: 1; }
                    .pin-overlay { position: absolute; top: 0; left: 0; width: 100%; height: 100%; pointer-events: none; }
                    .pin-marker { position: absolute; transform: translate(-50%, -50%); width: 22px; height: 22px; border-radius: 50%; background: #ef4444; color: #ffffff; font-size: 11px; font-weight: bold; display: flex; align-items: center; justify-content: center; box-shadow: 0 2px 4px rgba(0,0,0,0.25); pointer-events: auto; cursor: pointer; border: 1.5px solid #ffffff; }
                    .pin-marker:hover { background: #b91c1c; transform: translate(-50%, -50%) scale(1.15); }
                </style>
                </head>
                <body>
                    <div class="map-wrapper-box">
                        <div class="map-toolbar">
                            <button type="button" class="btn-pin-undo" id="btnUndoPin">↩ 移除上一個釘點</button>
                            <button type="button" class="btn-pin-clear" id="btnClearPins">🗑️ 清空全部釘點</button>
                            <span class="pin-count-badge" id="pinCountText">目前標記數：0（點擊標記點可直接移除）</span>
                        </div>
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
                                        <path d="M75,230 L70,310 L82,315 L92,240 Z" class="body-shape"/>
                                        <path d="M125,230 L130,310 L118,315 L108,240 Z" class="body-shape"/>
                                        <path d="M70,310 L68,380 L80,382 L82,315 Z" class="body-shape"/>
                                        <path d="M130,310 L132,380 L120,382 L118,315 Z" class="body-shape"/>
                                        <path d="M100,75 L100,220 M70,115 Q100,125 130,115 M75,155 L125,155" class="body-line"/>
                                    </svg>
                                    <div class="pin-overlay" id="oFront"></div>
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
                                        <path d="M75,230 L70,310 L82,315 L92,240 Z" class="body-shape"/>
                                        <path d="M125,230 L130,310 L118,315 L108,240 Z" class="body-shape"/>
                                        <path d="M70,310 L68,380 L80,382 L82,315 Z" class="body-shape"/>
                                        <path d="M130,310 L132,380 L120,382 L118,315 Z" class="body-shape"/>
                                        <path d="M100,65 L100,225 M70,105 L100,135 L130,105 M72,210 Q100,225 128,210" class="body-line"/>
                                    </svg>
                                    <div class="pin-overlay" id="oBack"></div>
                                </div>
                            </div>
                        </div>
                    </div>
                    <script>
                        const wF = document.getElementById('wFront'), wB = document.getElementById('wBack');
                        const oF = document.getElementById('oFront'), oB = document.getElementById('oBack');
                        const pinCountText = document.getElementById('pinCountText');
                        let pins = [];

                        function syncMapToStreamlit() {
                            const textAreas = window.parent.document.querySelectorAll('textarea');
                            let targetTA = null;
                            textAreas.forEach(ta => {
                                if ((ta.getAttribute('aria-label') || '').includes('body_map_img_b64')) targetTA = ta;
                            });
                            if (!targetTA) return;

                            if (pins.length === 0) {
                                try {
                                    const pWin = window.parent;
                                    const nativeSetter = pWin.Object.getOwnPropertyDescriptor(pWin.HTMLTextAreaElement.prototype, "value").set;
                                    nativeSetter.call(targetTA, "");
                                    if (targetTA._valueTracker) targetTA._valueTracker.setValue('');
                                    targetTA.dispatchEvent(new pWin.Event('input', { bubbles: true }));
                                    targetTA.dispatchEvent(new pWin.Event('change', { bubbles: true }));
                                } catch(e) { targetTA.value = ""; }
                                return;
                            }

                            const off = document.createElement('canvas');
                            off.width = 440; off.height = 420;
                            const ctx = off.getContext('2d');
                            ctx.fillStyle = '#ffffff'; ctx.fillRect(0, 0, off.width, off.height);

                            const svgFStr = new XMLSerializer().serializeToString(document.getElementById('svgF'));
                            const svgBStr = new XMLSerializer().serializeToString(document.getElementById('svgB'));
                            const imgF = new Image(), imgB = new Image();
                            let loaded = 0;

                            function finishDraw() {
                                loaded++;
                                if (loaded < 2) return;
                                ctx.drawImage(imgF, 15, 25, 190, 380);
                                ctx.drawImage(imgB, 235, 25, 190, 380);

                                ctx.fillStyle = '#2563eb'; ctx.font = 'bold 14px sans-serif'; ctx.textAlign = 'center';
                                ctx.fillText('正面 Front', 110, 18);
                                ctx.fillText('背面 Back', 330, 18);

                                pins.forEach((p, idx) => {
                                    const baseX = p.side === 'front' ? 15 : 235;
                                    const px = baseX + (p.xPct / 100) * 190;
                                    const py = 25 + (p.yPct / 100) * 380;

                                    ctx.beginPath();
                                    ctx.arc(px, py, 10, 0, Math.PI * 2);
                                    ctx.fillStyle = '#ef4444';
                                    ctx.fill();
                                    ctx.lineWidth = 2;
                                    ctx.strokeStyle = '#ffffff';
                                    ctx.stroke();

                                    ctx.fillStyle = '#ffffff';
                                    ctx.font = 'bold 11px sans-serif';
                                    ctx.textAlign = 'center';
                                    ctx.textBaseline = 'middle';
                                    ctx.fillText(String(idx + 1), px, py);
                                });

                                const b64Url = off.toDataURL('image/png');
                                try {
                                    const pWin = window.parent;
                                    const nativeSetter = pWin.Object.getOwnPropertyDescriptor(pWin.HTMLTextAreaElement.prototype, "value").set;
                                    nativeSetter.call(targetTA, b64Url);
                                    if (targetTA._valueTracker) targetTA._valueTracker.setValue('');
                                    targetTA.dispatchEvent(new pWin.Event('input', { bubbles: true }));
                                    targetTA.dispatchEvent(new pWin.Event('change', { bubbles: true }));
                                } catch(e) { targetTA.value = b64Url; }
                            }

                            imgF.onload = finishDraw;
                            imgB.onload = finishDraw;
                            imgF.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svgFStr);
                            imgB.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svgBStr);
                        }

                        function renderPins() {
                            oF.innerHTML = ''; oB.innerHTML = '';
                            pinCountText.innerText = `目前標記數：${pins.length}（點擊標記點可直接移除）`;
                            pins.forEach((p, i) => {
                                const el = document.createElement('div');
                                el.className = 'pin-marker';
                                el.style.left = p.xPct + '%'; el.style.top = p.yPct + '%';
                                el.innerText = i + 1;
                                el.title = `點擊移除標記 #${i + 1}`;
                                el.addEventListener('click', (ev) => {
                                    ev.stopPropagation();
                                    pins.splice(i, 1);
                                    renderPins();
                                    syncMapToStreamlit();
                                });
                                if (p.side === 'front') oF.appendChild(el); else oB.appendChild(el);
                            });
                        }

                        wF.addEventListener('click', (e) => {
                            const r = wF.getBoundingClientRect();
                            pins.push({side:'front', xPct:((e.clientX-r.left)/r.width)*100, yPct:((e.clientY-r.top)/r.height)*100});
                            renderPins();
                            syncMapToStreamlit();
                        });
                        wB.addEventListener('click', (e) => {
                            const r = wB.getBoundingClientRect();
                            pins.push({side:'back', xPct:((e.clientX-r.left)/r.width)*100, yPct:((e.clientY-r.top)/r.height)*100});
                            renderPins();
                            syncMapToStreamlit();
                        });

                        document.getElementById('btnUndoPin').addEventListener('click', () => {
                            if (pins.length > 0) {
                                pins.pop();
                                renderPins();
                                syncMapToStreamlit();
                            }
                        });
                        document.getElementById('btnClearPins').addEventListener('click', () => {
                            pins = [];
                            renderPins();
                            syncMapToStreamlit();
                        });
                    </script>
                </body>
                </html>"""
                components.html(body_map_html, height=500, scrolling=False)

            with tab_view_plan:
                render_training_log_history()

# ==================== 【頁面 8：體重監控 Weight Monitoring】 ====================
elif page == "體重監控 Weight Monitoring":
    st.title("⚖️ 體重監控與運動醫學目標追蹤 Weight Monitoring")
    st.markdown("---")

    if st.session_state["user_info"] is None:
        st.warning("請先在左側邊欄登入帳號 Please Login in Sidebar First")
    else:
        u = st.session_state["user_info"]
        role = u["role"]
        team_code = u["team_code"]
        is_staff = (role in ["coach", "trainer"])

        if is_staff:
            team_athletes = get_team_athletes(team_code)
            if not team_athletes:
                st.info("目前團隊尚無選手帳號，請先請選手加入團隊。")
                target_w_user = None
            else:
                target_w_user = st.selectbox("請選擇檢視／設定體重監控的選手 Select Athlete：", team_athletes, key="wm_sel_athlete")
        else:
            target_w_user = u["username"]

        if target_w_user:
            goal_row = get_weight_goal(target_w_user)
            logs_rows = get_weight_logs(username=target_w_user)
            today_dt = datetime.date.today()

            if is_staff:
                st.subheader(f"📊 選手「{target_w_user}」體重監控總覽與目標管理")
                tab_wm_dash, tab_wm_goal = st.tabs([
                    "📈 體重變化折線圖與進度評估",
                    "🎯 檢視 / 調整選手體重目標"
                ])
            else:
                tab_wm_dash, tab_wm_goal, tab_wm_log = st.tabs([
                    "📈 體重變化折線圖與進度評估",
                    "🎯 體重目標設定 Weight Goal",
                    "📝 每日體重紀錄 Daily Log"
                ])

            # -------------------- Tab 1：折線圖與每次測量醫學評估 --------------------
            with tab_wm_dash:
                if not goal_row and not logs_rows:
                    st.info("尚未設定體重目標且尚無測量紀錄！請先至「🎯 體重目標設定」建立目標，再於「📝 每日體重紀錄」輸入今日體重。")
                else:
                    prog_eval = evaluate_measurement_progress(goal_row, logs_rows) if (goal_row and logs_rows) else None

                    if prog_eval:
                        m1, m2, m3, m4 = st.columns(4)
                        m1.metric(
                            "最新測量體重",
                            f"{prog_eval['latest_w']:.1f} kg",
                            delta=f"{prog_eval['daily_diff']:+.1f} kg (較前次)" if len(logs_rows) >= 2 else None,
                            delta_color="off"
                        )
                        m2.metric("7 日移動平均 (排除水分波動)", f"{prog_eval['ma7_w']:.1f} kg")
                        m3.metric(
                            "目標體重 / 尚差",
                            f"{float(goal_row[4]):.1f} kg",
                            delta=f"距目標 {prog_eval['remaining_w']:+.1f} kg",
                            delta_color="off"
                        )
                        m4.metric(
                            "目標達成進度",
                            f"{prog_eval['progress_pct']:.1f}%",
                            delta=f"剩餘 {max(prog_eval['days_left'], 0)} 天"
                        )

                        st.markdown("##### 🩺 運動醫學測量進度自動判讀")
                        if prog_eval["status_level"] == "success":
                            st.success(f"**{prog_eval['status_title']}**\n\n{prog_eval['feedback_msg']}")
                        elif prog_eval["status_level"] == "warning":
                            st.warning(f"**{prog_eval['status_title']}**\n\n{prog_eval['feedback_msg']}")
                        elif prog_eval["status_level"] == "error":
                            st.error(f"**{prog_eval['status_title']}**\n\n{prog_eval['feedback_msg']}")
                        else:
                            st.info(f"**{prog_eval['status_title']}**\n\n{prog_eval['feedback_msg']}")

                        if prog_eval.get("water_note"):
                            st.info(prog_eval["water_note"])

                    elif goal_row and not logs_rows:
                        st.info(f"已設定目標體重 **{float(goal_row[4]):.1f} kg**（目標日：`{goal_row[5]}`），請新增第一筆體重測量紀錄以啟動即時軌跡分析！")
                    elif logs_rows and not goal_row:
                        latest_only = logs_rows[-1]
                        st.info(f"目前最新體重為 **{float(latest_only[3]):.1f} kg**（日期：`{latest_only[2]}`）。建議前往「🎯 體重目標設定」建立目標體重與日期，系統即可自動評估是否符合運動醫學安全速率。")

                    st.markdown("---")
                    st.markdown("##### 📈 體重變化與理論目標軌跡折線圖 Weight Progression Chart")

                    chart_points = []
                    if logs_rows:
                        df_logs_chart = pd.DataFrame(
                            logs_rows,
                            columns=["id", "username", "log_date", "weight_kg", "body_fat_pct", "measure_timing", "notes", "created_at"]
                        )
                        df_logs_chart["weight_kg"] = df_logs_chart["weight_kg"].astype(float)
                        df_logs_chart["ma7"] = df_logs_chart["weight_kg"].rolling(window=7, min_periods=1).mean().round(2)

                        for _, r in df_logs_chart.iterrows():
                            chart_points.append({
                                "日期 Date": str(r["log_date"]),
                                "數值 (kg)": round(float(r["weight_kg"]), 2),
                                "曲線類型": "實際測量體重 (kg)",
                                "備註": r["measure_timing"] if r["measure_timing"] else "實際測量"
                            })
                            if len(df_logs_chart) >= 2:
                                chart_points.append({
                                    "日期 Date": str(r["log_date"]),
                                    "數值 (kg)": float(r["ma7"]),
                                    "曲線類型": "7日移動平均線 (排除水分波動)",
                                    "備註": "7日趨勢均線"
                                })

                    if goal_row:
                        _, _, g_sw, g_sd, g_tw, g_td, _, _ = goal_row
                        chart_points.append({
                            "日期 Date": str(g_sd),
                            "數值 (kg)": round(float(g_sw), 2),
                            "曲線類型": "理想線性目標軌跡",
                            "備註": f"起始基準：{float(g_sw):.1f} kg"
                        })
                        chart_points.append({
                            "日期 Date": str(g_td),
                            "數值 (kg)": round(float(g_tw), 2),
                            "曲線類型": "理想線性目標軌跡",
                            "備註": f"目標終點：{float(g_tw):.1f} kg"
                        })

                    if chart_points:
                        df_w_chart = pd.DataFrame(chart_points)
                        all_vals = df_w_chart["數值 (kg)"].tolist()
                        y_min = max(30.0, min(all_vals) - 2.0)
                        y_max = max(all_vals) + 2.0

                        color_scale = alt.Scale(
                            domain=["實際測量體重 (kg)", "7日移動平均線 (排除水分波動)", "理想線性目標軌跡"],
                            range=["#2563eb", "#059669", "#dc2626"]
                        )
                        dash_scale = alt.Scale(
                            domain=["實際測量體重 (kg)", "7日移動平均線 (排除水分波動)", "理想線性目標軌跡"],
                            range=[[1, 0], [1, 0], [6, 4]]
                        )

                        w_line_chart = alt.Chart(df_w_chart).mark_line(point=True, strokeWidth=2.5).encode(
                            x=alt.X("日期 Date:T", title="測量日期 Date"),
                            y=alt.Y("數值 (kg):Q", title="體重 (kg)", scale=alt.Scale(domain=[y_min, y_max])),
                            color=alt.Color("曲線類型:N", scale=color_scale, title="圖例 Legend"),
                            strokeDash=alt.StrokeDash("曲線類型:N", scale=dash_scale, legend=None),
                            tooltip=[
                                alt.Tooltip("日期 Date:T", title="日期", format="%Y-%m-%d"),
                                alt.Tooltip("曲線類型:N", title="項目"),
                                alt.Tooltip("數值 (kg):Q", title="體重 (kg)"),
                                alt.Tooltip("備註:N", title="說明")
                            ]
                        ).properties(height=380)

                        st.altair_chart(w_line_chart, use_container_width=True)

                    if logs_rows:
                        st.markdown("##### 📋 歷次體重測量明細表")
                        table_rows = []
                        sorted_rev = list(reversed(logs_rows))
                        for idx_r, lr in enumerate(sorted_rev):
                            l_id, _, l_dt, l_w, l_bf, l_tm, l_nt, _ = lr
                            prev_val = float(sorted_rev[idx_r + 1][3]) if idx_r + 1 < len(sorted_rev) else None
                            diff_str = f"{float(l_w) - prev_val:+.1f} kg" if prev_val is not None else "起始紀錄"
                            table_rows.append({
                                "日期 Date": l_dt,
                                "體重 (kg)": f"{float(l_w):.1f}",
                                "較前次變化": diff_str,
                                "體脂率 (%)": f"{float(l_bf):.1f}%" if l_bf is not None else "-",
                                "測量時機": l_tm if l_tm else "-",
                                "備註": l_nt if l_nt else "-"
                            })
                        st.dataframe(pd.DataFrame(table_rows), use_container_width=True, hide_index=True)

                        if not is_staff:
                            with st.expander("🗑️ 管理 / 刪除單筆體重紀錄"):
                                del_options = {f"{r[2]} ｜ {float(r[3]):.1f} kg ({r[5] or '未標註時機'})": r[0] for r in reversed(logs_rows)}
                                sel_del_label = st.selectbox("選擇要刪除的測量紀錄：", list(del_options.keys()), key="sel_del_wlog")
                                if st.button("確認刪除此筆體重紀錄", key="btn_del_wlog"):
                                    delete_weight_log(del_options[sel_del_label])
                                    st.success("已刪除該筆體重紀錄")
                                    st.rerun()

                with st.expander("📚 運動醫學體重管理參考文獻依據 (Sports Medicine References)"):
                    st.markdown("""
                    - **Garthe, I., Raastad, T., Refsnes, P. E., Koivisto, A., & Sundgot-Borgen, J. (2011).** *Effect of two different weight-loss rates on body composition and strength and power-related performance in elite athletes.* International Journal of Sport Nutrition and Exercise Metabolism, 21(2), 97–104.（證實運動員每週減重 **0.5%–0.7% 體重** 可保留瘦體組織並維持下肢爆發力；超過 **1.0%/週** 則顯著損害功率表現。）
                    - **Mountjoy, M., et al. (2023).** *2023 International Olympic Committee's (IOC) consensus statement on Relative Energy Deficiency in Sport (REDs).* British Journal of Sports Medicine, 57(17), 1073–1097.（警示快速減重與低能量可用性對骨骼健康、內分泌與運動表現之風險。）
                    - **Aragon, A. A., et al. (2017).** *International Society of Sports Nutrition position stand: diets and body composition.* Journal of the International Society of Sports Nutrition, 14, 16.（受訓運動員理想增肌速率約為每週 **0.25%–0.5% 體重**，避免過快增重導致非功能性體脂增加。）
                    - **Burke, L. M., et al. (2019).** *International Association of Athletics Federations (World Athletics) Consensus Statement 2019: Nutrition for Athletics.* International Journal of Sport Nutrition and Exercise Metabolism, 29(2), 73–84.（說明田徑選手因肝醣與水分儲備造成之每日體重 ±0.5%–1.5% 生理波動及晨起標準化測量原則。）
                    """)

            # -------------------- Tab 2：體重目標設定與運動醫學合理性評估 --------------------
            with tab_wm_goal:
                st.subheader("🎯 設定理想目標體重與達成日期")
                st.caption("系統將依據國際奧會（IOC REDs）、ISSN 與世界田徑總會（World Athletics）運動醫學文獻，即時評估您的目標速率是否安全且有助於跨欄表現。")

                default_start_w = float(goal_row[2]) if goal_row else (float(logs_rows[-1][3]) if logs_rows else 65.0)
                default_target_w = float(goal_row[4]) if goal_row else round(default_start_w - 1.5, 1)
                try:
                    default_start_d = datetime.datetime.strptime(str(goal_row[3]), "%Y-%m-%d").date() if goal_row else today_dt
                except Exception:
                    default_start_d = today_dt
                try:
                    default_target_d = datetime.datetime.strptime(str(goal_row[5]), "%Y-%m-%d").date() if goal_row else (today_dt + datetime.timedelta(days=28))
                except Exception:
                    default_target_d = today_dt + datetime.timedelta(days=28)
                default_g_notes = goal_row[6] if goal_row else ""

                gc1, gc2 = st.columns(2)
                with gc1:
                    g_start_w = st.number_input("起始基準體重 Starting Weight (kg)", min_value=30.0, max_value=180.0, value=default_start_w, step=0.1, format="%.1f", key="wm_g_sw")
                    g_start_d = st.date_input("計畫起始日期 Start Date", value=default_start_d, key="wm_g_sd")
                with gc2:
                    g_target_w = st.number_input("目標理想體重 Target Weight (kg)", min_value=30.0, max_value=180.0, value=default_target_w, step=0.1, format="%.1f", key="wm_g_tw")
                    g_target_d = st.date_input("預計達成日期 Target Date", value=max(default_target_d, g_start_d + datetime.timedelta(days=1)), min_value=g_start_d + datetime.timedelta(days=1), key="wm_g_td")

                plan_eval = evaluate_weight_goal_plan(g_start_w, g_target_w, g_start_d, g_target_d)

                st.markdown("##### 🩺 運動醫學目標計畫合理性即時評估")
                pm1, pm2, pm3 = st.columns(3)
                pm1.metric("預計總體重變化", f"{plan_eval['delta_w']:+.1f} kg")
                pm2.metric("計畫週數", f"{plan_eval['total_weeks']} 週 ({plan_eval['total_days']} 天)")
                pm3.metric("平均每週變化率", f"{plan_eval['weekly_kg']:+.2f} kg/週 ({plan_eval['weekly_pct']:+.2f}%)")

                if plan_eval["level"] == "success":
                    st.success(f"**{plan_eval['badge']}**\n\n{plan_eval['summary']}")
                elif plan_eval["level"] == "warning":
                    st.warning(f"**{plan_eval['badge']}**\n\n{plan_eval['summary']}")
                elif plan_eval["level"] == "error":
                    st.error(f"**{plan_eval['badge']}**\n\n{plan_eval['summary']}")
                else:
                    st.info(f"**{plan_eval['badge']}**\n\n{plan_eval['summary']}")

                g_notes = st.text_area("階段目標備註（例：全大運賽前體重調整、增肌期功率維持）", value=default_g_notes, height=80, key="wm_g_notes")

                if st.button("💾 儲存體重目標設定 Save Weight Goal", key="btn_save_wgoal", use_container_width=True):
                    upsert_weight_goal(target_w_user, team_code, g_start_w, g_start_d, g_target_w, g_target_d, g_notes.strip())
                    st.success("體重目標計畫已儲存並同步更新至折線圖！")
                    st.rerun()

            # -------------------- Tab 3：選手每日體重紀錄與即時回饋 --------------------
            if not is_staff:
                with tab_wm_log:
                    st.subheader("📝 登記今日體重測量紀錄")
                    st.info("💡 **標準化測量建議（World Athletics, 2019）**：建議固定於 **每日晨起空腹、排尿後、著輕便衣物** 測量，可最大程度排除水分與食物重量干擾。")

                    last_w_default = float(logs_rows[-1][3]) if logs_rows else (float(goal_row[2]) if goal_row else 65.0)

                    with st.form("form_add_weight_log"):
                        lc1, lc2 = st.columns(2)
                        with lc1:
                            log_dt = st.date_input("測量日期 Measurement Date", value=today_dt)
                            log_w = st.number_input("測量體重 Weight (kg)", min_value=30.0, max_value=180.0, value=last_w_default, step=0.1, format="%.1f")
                        with lc2:
                            log_timing = st.selectbox("測量時機 Timing", MEASURE_TIMING_OPTIONS)
                            rec_bf = st.checkbox("同時紀錄體脂率 Body Fat % (選填)", value=False)
                            log_bf = st.number_input("體脂率 Body Fat (%)", min_value=3.0, max_value=50.0, value=14.0, step=0.1, format="%.1f")

                        log_notes = st.text_input("當日狀態備註（例：高強度間歇訓練後、生理期前、補水充足）")

                        if st.form_submit_button("✅ 儲存體重紀錄並進行醫學進度評估", use_container_width=True):
                            bf_val = float(log_bf) if rec_bf else None
                            upsert_weight_log(target_w_user, team_code, log_dt, log_w, bf_val, log_timing, log_notes.strip())

                            updated_goal = get_weight_goal(target_w_user)
                            updated_logs = get_weight_logs(username=target_w_user)
                            if updated_goal and updated_logs:
                                st.session_state["last_weight_eval"] = evaluate_measurement_progress(updated_goal, updated_logs)
                            else:
                                st.session_state["last_weight_eval"] = None

                            st.success(f"已成功儲存 {log_dt} 體重紀錄（{log_w:.1f} kg）！")
                            st.rerun()

                    if st.session_state.get("last_weight_eval"):
                        ev = st.session_state["last_weight_eval"]
                        st.markdown("---")
                        st.markdown("##### 🩺 本次測量即時運動醫學回饋")
                        if ev["status_level"] == "success":
                            st.success(f"**{ev['status_title']}**\n\n{ev['feedback_msg']}")
                        elif ev["status_level"] == "warning":
                            st.warning(f"**{ev['status_title']}**\n\n{ev['feedback_msg']}")
                        elif ev["status_level"] == "error":
                            st.error(f"**{ev['status_title']}**\n\n{ev['feedback_msg']}")
                        else:
                            st.info(f"**{ev['status_title']}**\n\n{ev['feedback_msg']}")

                        if ev.get("water_note"):
                            st.info(ev["water_note"])
# ==================== 【頁面 9：歷史分析紀錄】 ====================
elif page == "歷史分析紀錄 History Records":
    if st.session_state["user_info"] is None:
        st.title("歷史分析資料庫 Historical Analysis Records")
        st.markdown("---")
        st.warning("請先登入帳號")
    else:
        u = st.session_state["user_info"]
        team_code = u["team_code"]
        role = u["role"]

        if role in ["coach", "trainer"]:
            st.title("團隊歷史分析資料庫 Team Historical Analysis Records")
        else:
            st.title("歷史分析資料庫 Historical Analysis Records")
        st.markdown("---")

        if role in ["coach", "trainer"]:
            team_athletes = get_team_athletes(team_code)
            filter_user = st.selectbox("篩選團隊選手：", ["全團隊紀錄"] + team_athletes)
            target_user = None if filter_user == "全團隊紀錄" else filter_user
        else:
            target_user = u["username"]

        records = get_user_records(team_code, username=target_user)
        if not records:
            st.info("目前無任何紀錄")
        else:
            for r in records:
                r_id, r_user, r_mod, r_title, r_details_json, r_time = r
                with st.expander(f"{r_mod} - {r_title}"):
                    try:
                        details = json.loads(r_details_json)

                        if "image_b64" in details:
                            st.image(details["image_b64"], use_container_width=True)

                        if "angle_metrics" in details:
                            am = details["angle_metrics"]
                            if isinstance(am, dict):
                                if "time_A" in am and "time_B" in am:
                                    st.markdown("**雙影時間與角度量測數據：**")
                                    c1, c2 = st.columns(2)
                                    c1.write(f"• **影片 A 時間**：`{am.get('time_A')}s` ｜ **關節角度**：`{am.get('angle_A') if am.get('angle_A') is not None else '未量測'}°`")
                                    c2.write(f"• **影片 B 時間**：`{am.get('time_B')}s` ｜ **關節角度**：`{am.get('angle_B') if am.get('angle_B') is not None else '未量測'}°`")
                                else:
                                    st.write(f"• **時間**：`{am.get('rel_time_sec', '0.00')} 秒` ｜ **階段**：`{am.get('phase_tag', '關鍵點')}`")
                                    angles = am.get("angles", [])
                                    if isinstance(angles, list):
                                        for a in angles:
                                            st.write(f"  - **{a.get('name')}**：`{a.get('value')}`")

                        if "timer_metrics" in details:
                            tm = details["timer_metrics"]
                            st.write(f"• **總累積耗時**：`{tm.get('total_time')} 秒`")
                            splits_data = tm.get("splits", [])
                            if splits_data:
                                df_splits = pd.DataFrame(splits_data).rename(columns={
                                    "segment": "階段",
                                    "cum_sec": "累積時間 (秒)",
                                    "split_sec": "單段耗時 (秒)",
                                    "speed_ms": "平均速度 (m/s)"
                                })
                                col_order = ["階段", "累積時間 (秒)", "單段耗時 (秒)", "平均速度 (m/s)"]
                                existing_cols = [c for c in col_order if c in df_splits.columns]
                                other_cols = [c for c in df_splits.columns if c not in existing_cols]
                                st.table(df_splits[existing_cols + other_cols])

                        if details.get("ai_diagnosis"):
                            st.markdown("**🤖 AI 跨欄動作生物力學診斷建議：**")
                            st.info(details["ai_diagnosis"])

                        current_note = details.get("user_notes", "")
                        if current_note:
                            st.markdown(f"**📝 動作分析筆記 Analysis Notes：**\n```text\n{current_note}\n```")

                        with st.popover("✏️ 編輯動作分析筆記"):
                            with st.form(f"edit_rec_note_form_{r_id}"):
                                new_note = st.text_area("分析筆記內容", value=current_note, height=100)
                                if st.form_submit_button("💾 更新筆記", use_container_width=True):
                                    details["user_notes"] = new_note.strip()
                                    update_record_details(r_id, details)
                                    st.success("筆記已成功更新！")
                                    st.rerun()

                    except Exception:
                        st.write("原始數據讀取異常")

                    if role in ["coach", "trainer"] or r_user == u["username"]:
                        if st.button(f"🗑️ 刪除此筆紀錄", key=f"del_rec_{r_id}"):
                            delete_record(r_id)
                            st.rerun()
