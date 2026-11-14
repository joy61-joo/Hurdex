import datetime
import json
import os
from typing import Any, Dict, List, Optional, Tuple

from google import genai
from google.genai import types
import numpy as np
import pandas as pd
import streamlit as st


def _get_gemini_client() -> genai.Client:
    """優先從 Streamlit secrets 讀取 GEMINI_API_KEY，其次讀取系統環境變數"""
    api_key = None
    try:
        if "GEMINI_API_KEY" in st.secrets:
            api_key = st.secrets["GEMINI_API_KEY"]
        elif "gemini" in st.secrets and "api_key" in st.secrets["gemini"]:
            api_key = st.secrets["gemini"]["api_key"]
    except Exception:
        pass

    if not api_key:
        api_key = os.environ.get("GEMINI_API_KEY")

    if not api_key:
        raise ValueError("尚未設定 GEMINI_API_KEY，請於 .streamlit/secrets.toml 或環境變數中配置。")

    return genai.Client(api_key=api_key)


MODEL_ID = "gemini-3.8-flash"


def _call_gemini(system_instruction: str, user_prompt: str, temperature: float = 0.3) -> str:
    try:
        client = _get_gemini_client()
        response = client.models.generate_content(
            model=MODEL_ID,
            contents=user_prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                temperature=temperature,
            ),
        )
        return response.text or "⚠️ AI 未回傳內容，請稍後再試。"
    except Exception as e:
        return f"⚠️ AI 分析生成失敗：{str(e)}"


# =====================================================================
# 資料解析輔助函式（對應 app-2_2_3.py 的 PostgreSQL 查詢結果）
# =====================================================================
def _parse_rpe_number(rpe_str: Any) -> int:
    """將 '5 中等' 轉為整數 5"""
    try:
        return int(str(rpe_str).strip().split()[0])
    except Exception:
        return 5


def _parse_training_rows(plan_rows: List[Tuple]) -> pd.DataFrame:
    """
    解析 get_training_plans() 回傳的 tuple 列表：
    (id, username, plan_date, category, content, intensity, notes, created_at)
    """
    records = []
    for p in plan_rows:
        p_id, p_user, p_date, p_cat, p_content, p_intensity, p_notes, _ = p
        try:
            extra = json.loads(p_notes) if isinstance(p_notes, str) else (p_notes or {})
        except Exception:
            extra = {}
        if not isinstance(extra, dict):
            extra = {}

        dur = float(extra.get("duration_min", 60) or 60)
        rpe_num = _parse_rpe_number(p_intensity)
        srpe = dur * rpe_num

        raw_hr = extra.get("resting_hr", None)
        hr_val = float(raw_hr) if isinstance(raw_hr, (int, float)) else np.nan

        raw_rs = extra.get("restorative_sleep_pct", None)
        rs_val = float(raw_rs) if isinstance(raw_rs, (int, float)) else np.nan

        raw_sleep = extra.get("total_sleep_hr", None)
        sleep_val = float(raw_sleep) if isinstance(raw_sleep, (int, float)) else np.nan

        raw_fatigue = extra.get("fatigue_score", None)
        fatigue_val = float(raw_fatigue) if isinstance(raw_fatigue, (int, float)) else np.nan

        records.append({
            "plan_id": int(p_id),
            "username": p_user,
            "date": pd.to_datetime(str(p_date)[:10], errors="coerce"),
            "date_str": str(p_date)[:10],
            "category": p_cat,
            "rpe_text": p_intensity,
            "rpe_num": rpe_num,
            "duration_min": dur,
            "srpe": srpe,
            "fatigue_score": fatigue_val,
            "total_sleep_hr": sleep_val,
            "restorative_sleep_pct": rs_val,
            "resting_hr": hr_val,
            "menstrual_period": extra.get("menstrual_period", "無"),
            "injuries_list": extra.get("injuries_list", []),
            "user_notes": extra.get("user_notes", ""),
        })

    if not records:
        return pd.DataFrame()
    df = pd.DataFrame(records).dropna(subset=["date"]).sort_values("date")
    return df


def _infer_hurdle_event(splits: List[Dict[str, Any]]) -> str:
    """從第一段 (起跑->H1) 的 speed_ms * split_sec 自動判斷男子 110mH 或女子 100mH"""
    if not splits:
        return "跨欄項目"
    try:
        s0 = splits[0]
        est_dist = float(s0.get("speed_ms", 0)) * float(s0.get("split_sec", 0))
        if est_dist > 13.35:
            return "男子 110mH (起跑13.72m / 欄距9.14m)"
        elif est_dist > 10.0:
            return "女子 100mH (起跑13.00m / 欄距8.50m)"
    except Exception:
        pass
    return "跨欄項目"


def _compute_single_hurdle_summary(title: str, tm_dict: Dict[str, Any]) -> Dict[str, Any]:
    """預計算單趟欄間秒數的運動生物力學指標"""
    raw_splits = tm_dict.get("splits", [])
    cleaned = []
    for s in raw_splits:
        try:
            cleaned.append({
                "segment": s.get("segment", ""),
                "cum_sec": float(s.get("cum_sec", 0)),
                "split_sec": float(s.get("split_sec", 0)),
                "speed_ms": float(s.get("speed_ms", 0)),
            })
        except Exception:
            continue

    if len(cleaned) < 3:
        return {"標題": title, "總時間": tm_dict.get("total_time"), "狀態": "分段數不足 3 段"}

    df = pd.DataFrame(cleaned)
    # 排除起跑->H1，若有到終點則也排除最後衝刺段，專注純欄間 (H1->H10)
    hurdle_df = df.iloc[1:-1].copy() if len(df) >= 11 else df.iloc[1:].copy()

    peak_row = hurdle_df.loc[hurdle_df["speed_ms"].idxmax()]
    min_row = hurdle_df.loc[hurdle_df["speed_ms"].idxmin()]

    late_df = hurdle_df.tail(3)
    late_avg_spd = float(late_df["speed_ms"].mean())
    peak_spd = float(peak_row["speed_ms"])
    decay_pct = ((peak_spd - late_avg_spd) / peak_spd * 100.0) if peak_spd > 0 else 0.0

    # 偵測單欄突然掉速 / 耗時暴增 (>= 0.04s 常見於打欄或步點過擠)
    hurdle_df["dt_diff"] = hurdle_df["split_sec"].diff()
    glitches = hurdle_df[hurdle_df["dt_diff"] >= 0.04]["segment"].tolist()

    return {
        "紀錄標題": title,
        "推估項目": _infer_hurdle_event(cleaned),
        "總成績(s)": tm_dict.get("total_time"),
        "最高欄間速度": f"{peak_spd:.2f} m/s ({peak_row['segment']}, {peak_row['split_sec']:.2f}s)",
        "最慢欄間速度": f"{float(min_row['speed_ms']):.2f} m/s ({min_row['segment']}, {min_row['split_sec']:.2f}s)",
        "後段(末3欄)速度衰減率": f"{decay_pct:.1f}%",
        "疑似技術失誤或步點斷層區段": glitches if glitches else "無明顯單欄突變",
        "分段明細": cleaned,
    }


# =====================================================================
# 項目 3A：單趟欄間秒數與速度 AI 診斷（用於「角度分析 -> 欄間秒數計算」）
# =====================================================================
def analyze_hurdle_timer_with_history(
    current_title: str,
    current_tm: Dict[str, Any],
    historical_record_rows: Optional[List[Tuple]] = None,
) -> str:
    curr_summary = _compute_single_hurdle_summary(current_title, current_tm)

    hist_summaries = []
    if historical_record_rows:
        for r in historical_record_rows[:5]:  # 取最近 5 筆歷史紀錄作基準
            r_title = r[3]
            try:
                r_details = json.loads(r[4])
                r_tm = r_details.get("timer_metrics", {})
                if r_tm.get("splits"):
                    hist_summaries.append({
                        "歷史標題": r_title,
                        "日期時間": r[5],
                        "總成績(s)": r_tm.get("total_time"),
                    })
            except Exception:
                continue

    payload = {
        "本次測驗詳細分析": curr_summary,
        "近期歷史紀錄基準": hist_summaries if hist_summaries else "無歷史對比資料",
    }

    system_instruction = (
        "你是一位專精跨欄運動生物力學的國家隊田徑教練。"
        "請根據分段耗時與速度曲線，精準區分『專項速度耐力不足（後程線性掉速）』與『過欄技術／三步節奏斷層（單一欄間耗時突增）』。"
        "請用繁體中文條列輸出，包含：\n"
        "1. **速度曲線與三步節奏診斷**\n"
        "2. **關鍵瓶頸欄架與掉速成因分析**\n"
        "3. **下週課表微調建議**（給出具體的欄間距離縮短幾公分、欄高調整或速度耐力趟數建議）"
    )
    user_prompt = f"請分析以下跨欄分段計時數據：\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
    return _call_gemini(system_instruction, user_prompt, temperature=0.3)


# =====================================================================
# 項目 3B：多趟跨期欄間表現 AI 演變分析（用於「跨期數據對比」）
# =====================================================================
def analyze_longitudinal_hurdle_comparison(selected_record_rows: List[Tuple]) -> str:
    summaries = []
    for r in selected_record_rows:
        r_title = r[3]
        try:
            r_tm = json.loads(r[4]).get("timer_metrics", {})
            summaries.append(_compute_single_hurdle_summary(f"{r_title} ({r[5][:10]})", r_tm))
        except Exception:
            continue

    if len(summaries) < 2:
        return "⚠️ 有效分段紀錄不足 2 筆，無法進行跨期 AI 對比。"

    system_instruction = (
        "你是一位專精跨欄運動生物力學的國家隊田徑教練。"
        "請對比選手多趟測驗的欄間速度曲線變化，指出進步區段、退步或疲勞累積區段，並給予針對性的技術與體能調整方向。"
        "輸出格式需精煉、結構清晰（使用繁體中文）。"
    )
    user_prompt = f"請對比分析以下多筆跨欄分段紀錄：\n{json.dumps(summaries, ensure_ascii=False, indent=2)}"
    return _call_gemini(system_instruction, user_prompt, temperature=0.3)


# =====================================================================
# 項目 4A：28 日疲勞負荷 (ACWR) 與傷病拉傷預警
# =====================================================================
def analyze_athlete_fatigue_and_injury_risk(
    athlete_name: str,
    plan_rows: List[Tuple],
    feedback_rows: Optional[List[Tuple]] = None,
) -> str:
    df = _parse_training_rows(plan_rows)
    if df.empty:
        return f"⚠️ 選手 **{athlete_name}** 尚無訓練日誌紀錄，無法計算 ACWR 與疲勞趨勢。"

    latest_dt = df["date"].max()
    df_7d = df[df["date"] > latest_dt - pd.Timedelta(days=7)]
    df_28d = df[df["date"] > latest_dt - pd.Timedelta(days=28)]

    acute_7d = float(df_7d["srpe"].sum())
    chronic_28d_weekly = float(df_28d["srpe"].sum()) / 4.0
    acwr = round(acute_7d / chronic_28d_weekly, 2) if chronic_28d_weekly > 0 else 0.0

    # 收集近 14 天所有 VAS > 0 的痛點
    df_14d = df[df["date"] > latest_dt - pd.Timedelta(days=14)]
    recent_pains = []
    for _, row in df_14d.iterrows():
        for inj in row["injuries_list"]:
            lvl = int(inj.get("level", 0))
            if lvl > 0:
                recent_pains.append({
                    "日期": row["date_str"],
                    "訓練類別": row["category"],
                    "部位": inj.get("area", ""),
                    "VAS疼痛分數": lvl,
                    "疼痛性質": inj.get("pain_types", []),
                    "備註": inj.get("notes", ""),
                })

    avg_sleep_7d = df_7d["total_sleep_hr"].mean()
    avg_rs_7d = df_7d["restorative_sleep_pct"].mean()
    avg_fatigue_7d = df_7d["fatigue_score"].mean()
    rhr_7d = df_7d["resting_hr"].mean()
    rhr_28d = df_28d["resting_hr"].mean()
    rhr_delta = round(rhr_7d - rhr_28d, 1) if pd.notna(rhr_7d) and pd.notna(rhr_28d) else None
    has_period = "有" in df_7d["menstrual_period"].tolist()

    payload = {
        "選手": athlete_name,
        "最新訓練日期": latest_dt.strftime("%Y-%m-%d"),
        "訓練負荷指標": {
            "近7日累積負荷(Acute_sRPE)": int(acute_7d),
            "28日週均負荷(Chronic_sRPE)": int(chronic_28d_weekly),
            "急慢性負荷比(ACWR)": acwr,
            "ACWR區間判讀": (
                "🔴 高風險超負荷 (>1.5)"
                if acwr > 1.5
                else ("🟡 警戒區 (1.3~1.5)" if acwr > 1.3 else ("🔵 負荷偏低 (<0.8)" if acwr < 0.8 else "🟢 安全甜區 (0.8~1.3)"))
            ),
        },
        "近7日生理與睡眠恢復": {
            "平均睡眠時長(hr)": round(float(avg_sleep_7d), 1) if pd.notna(avg_sleep_7d) else "未紀錄",
            "平均恢復性睡眠(%)": round(float(avg_rs_7d), 1) if pd.notna(avg_rs_7d) else "未紀錄",
            "平均主觀疲勞(1-10)": round(float(avg_fatigue_7d), 1) if pd.notna(avg_fatigue_7d) else "未紀錄",
            "靜息心率較28日基準變化(bpm)": f"{rhr_delta:+.1f}" if rhr_delta is not None else "未紀錄",
            "近7日生理期狀態": "生理期中" if has_period else "無",
        },
        "近14日活躍痛點明細(VAS>0)": recent_pains if recent_pains else "無活躍痛點",
    }

    system_instruction = (
        "你是一位資深運動醫學防護員（ATC）與田徑體能教練。"
        "請針對跨欄運動員的生理特性（高度依賴股二頭肌離心收縮、髂腰肌、跟腱與起跳腳踝關節），"
        "結合 ACWR 負荷比、睡眠恢復度、靜息心率變化與痛點 VAS／疼痛性質（如『撕裂痛、銳痛』vs『延遲性酸痛 DOMS』），"
        "產出繁體中文預警報告：\n"
        "1. **過度訓練與拉傷風險等級評估**（明確給出綠燈／黃燈／紅燈判定與理由）\n"
        "2. **高風險肌群與代償鏈分析**（指出目前負荷下最容易惡化的部位）\n"
        "3. **未來 72 小時訓練負荷與恢復調整建議**"
    )
    user_prompt = f"請分析以下選手的負荷與傷病風險數據：\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
    return _call_gemini(system_instruction, user_prompt, temperature=0.3)


# =====================================================================
# 項目 4B：防護員單筆日誌批改輔助 (AI Co-pilot)
# =====================================================================
def generate_trainer_review_copilot(
    log_item: Dict[str, Any],
    recent_plan_rows: List[Tuple],
    status_options: List[str],
    checklist_options: List[str],
) -> str:
    """針對運動防護員正在批改的單筆傷病日誌，生成推薦狀態核定、處置勾選與批改評語草稿"""
    df = _parse_training_rows(recent_plan_rows)
    acwr_note = "無近期負荷資料"
    if not df.empty:
        latest_dt = df["date"].max()
        a7 = df[df["date"] > latest_dt - pd.Timedelta(days=7)]["srpe"].sum()
        c28 = df[df["date"] > latest_dt - pd.Timedelta(days=28)]["srpe"].sum() / 4.0
        if c28 > 0:
            acwr_note = f"ACWR = {a7 / c28:.2f} (近7日負荷 {int(a7)} AU)"

    payload = {
        "選手": log_item.get("athlete"),
        "日誌日期": log_item.get("date"),
        "當日訓練類別": log_item.get("category"),
        "當日RPE": log_item.get("intensity"),
        "疲勞指數": log_item.get("fatigue"),
        "近期負荷背景": acwr_note,
        "選手回報痛點清單": log_item.get("injuries", []),
        "選手訓練備註": log_item.get("user_notes", ""),
        "系統可選訓練狀態": status_options,
        "系統可選處置勾選項目": checklist_options,
    }

    system_instruction = (
        "你是一位資深運動防護員（ATC），正在協助團隊防護員撰寫『傷病日誌批改回饋』。"
        "請根據選手回報的疼痛部位、VAS 分數、疼痛性質（如撕裂痛、刺痛、按壓痛 vs DOMS）與近期訓練負荷，"
        "精準推薦最合適的系統選項與專業評語草稿。\n"
        "請嚴格依以下格式輸出（繁體中文）：\n"
        "- **建議訓練狀態核定**：（從系統可選狀態中挑出最合適的一項）\n"
        "- **建議勾選處置項目**：（從系統可選勾選項目中挑出 2～4 項）\n"
        "- **可直接複製使用的批改評語草稿**：（寫一段約 80～120 字、專業且具同理心的防護建議，包含具體禁忌動作與恢復指導）"
    )
    user_prompt = f"請為以下傷病日誌生成防護員批改草稿：\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
    return _call_gemini(system_instruction, user_prompt, temperature=0.25)


# =====================================================================
# 項目 5：賽前整合週報 (Pre-Competition Integrated Weekly Report)
# =====================================================================
def generate_pre_comp_integrated_report(
    athlete_name: str,
    competitions: List[Tuple],
    goal_row: Optional[Tuple],
    weight_eval: Optional[Dict[str, Any]],
    plan_rows: List[Tuple],
    hurdle_record_rows: List[Tuple],
) -> str:
    today = datetime.date.today()

    # 1. 找出最近一場未來或進行中賽事
    next_comp_info = "目前行事曆尚無未來賽事"
    days_left = None
    for c in competitions:
        # (id, username, comp_name, comp_date, end_date, location, notes, created_at)
        try:
            s_dt = datetime.datetime.strptime(str(c[3])[:10], "%Y-%m-%d").date()
            e_dt = datetime.datetime.strptime(str(c[4])[:10], "%Y-%m-%d").date() if c[4] else s_dt
            if e_dt >= today:
                days_left = (s_dt - today).days
                next_comp_info = {
                    "賽事名稱": c[2],
                    "開始日期": str(c[3])[:10],
                    "結束日期": str(c[4])[:10] if c[4] else str(c[3])[:10],
                    "倒數天數": "比賽進行中" if days_left <= 0 else f"D-{days_left}",
                    "備註": c[6] or "",
                }
                break
        except Exception:
            continue

    # 2. 整理體重與營養狀態（直接複用 app-2_2_3.py 的 evaluate_measurement_progress 結果）
    if goal_row and weight_eval:
        weight_info = {
            "起始體重": f"{goal_row[2]} kg",
            "目標體重": f"{goal_row[4]} kg (目標日 {goal_row[5]})",
            "最新測量體重": f"{weight_eval['latest_w']} kg",
            "7日移動平均(MA7)": f"{weight_eval['ma7_w']} kg",
            "今日理論軌跡偏差": f"{weight_eval['vs_expected']:+.2f} kg",
            "每週實際變化率": f"{weight_eval['actual_weekly_pct']:+.2f}%/週",
            "運動醫學狀態判讀": weight_eval["status_title"],
        }
    elif goal_row:
        weight_info = f"已設目標 {goal_row[2]}kg -> {goal_row[4]}kg，但尚無近期測量紀錄"
    else:
        weight_info = "尚未設定體重目標"

    # 3. 整理近 14 日訓練減量 (Tapering) 與活躍傷病
    df_plans = _parse_training_rows(plan_rows)
    taper_info = "尚無近期課表紀錄"
    active_injuries = []
    if not df_plans.empty:
        max_dt = df_plans["date"].max()
        w1_load = df_plans[df_plans["date"] > max_dt - pd.Timedelta(days=7)]["srpe"].sum()
        w2_load = df_plans[
            (df_plans["date"] <= max_dt - pd.Timedelta(days=7))
            & (df_plans["date"] > max_dt - pd.Timedelta(days=14))
        ]["srpe"].sum()
        vol_diff_pct = round(((w1_load - w2_load) / w2_load) * 100, 1) if w2_load > 0 else 0.0
        taper_info = {
            "近7日總訓練負荷(sRPE)": int(w1_load),
            "前7日總訓練負荷(sRPE)": int(w2_load),
            "週負荷變化率": f"{vol_diff_pct:+.1f}%",
            "近7日平均疲勞指數": round(float(df_plans[df_plans["date"] > max_dt - pd.Timedelta(days=7)]["fatigue_score"].mean()), 1)
            if df_plans[df_plans["date"] > max_dt - pd.Timedelta(days=7)]["fatigue_score"].notna().any()
            else "未紀錄",
        }
        # 取最新一筆日誌中的痛點
        last_injs = df_plans.iloc[-1]["injuries_list"]
        active_injuries = [i for i in last_injs if int(i.get("level", 0)) > 0]

    # 4. 整理最近一次欄間檢測
    hurdle_info = "近期無欄間秒數檢測紀錄"
    if hurdle_record_rows:
        latest_r = hurdle_record_rows[0]  # get_user_records 依 id DESC 排序，第一筆即最新
        try:
            tm = json.loads(latest_r[4]).get("timer_metrics", {})
            hurdle_info = _compute_single_hurdle_summary(f"{latest_r[3]} ({latest_r[5][:10]})", tm)
            # 精簡掉過長明細以聚焦重點
            hurdle_info.pop("分段明細", None)
        except Exception:
            pass

    integrated_payload = {
        "選手名稱": athlete_name,
        "今日日期": str(today),
        "下一場目標賽事": next_comp_info,
        "競技體重與營養軌跡": weight_info,
        "訓練量減量(Tapering)與疲勞狀態": taper_info,
        "當前活躍傷病痛點": active_injuries if active_injuries else "無活躍傷病",
        "最近一次欄間速度檢測": hurdle_info,
    }

    system_instruction = (
        "你是統籌田徑跨欄選手備戰的總教練與運動科學團隊負責人。"
        "請將賽事倒數天數、體重 MA7 軌跡、訓練負荷減量（Tapering）、傷病狀態與欄間速度整合成一份『賽前整合備戰週報』。"
        "請用繁體中文分四個明確板塊輸出：\n"
        "1. **【整體備戰狀態總評】**（一句話精準定調目前競技準備度與最大風險點）\n"
        "2. **【課表減量（Tapering）與技術微調建議】**（依據距離比賽天數，明確建議訓練量下調百分比、攻欄趟數與起跑反應重點）\n"
        "3. **【競技體重與賽前肝醣超補策略】**（依據當前 MA7 體重與目標差距，給出具體碳水與水分調整）\n"
        "4. **【傷病控管與賽前防護重點】**（確保選手無痛站上起跑線）"
    )
    user_prompt = f"請生成本週賽前整合分析週報：\n{json.dumps(integrated_payload, ensure_ascii=False, indent=2)}"
    return _call_gemini(system_instruction, user_prompt, temperature=0.35)
def evaluate_daily_meals(
    meal_date_str: str,
    cur_weight: float,
    goal_mode: str,
    training_load: str,
    b_txt: str,
    l_txt: str,
    d_txt: str,
    s_txt: str,
) -> Dict[str, Any]:
    """評估田徑跨欄運動員單日三餐與補給之熱量及三大營養素（含 503 自動重試與備援切換）"""
    import time

    system_instruction = (
        "你是一位專精於田徑跨欄與爆發力項目的運動營養師。"
        "請根據運動員的三餐與補給內容，客觀估算每餐與全日總熱量（kcal）及三大營養素（蛋白質、碳水化合物、脂肪），"
        "並結合當日訓練負荷與體重目標給予繁體中文營養調整建議。"
        "請嚴格只輸出合法的 JSON 物件，不要包含任何多餘說明文字。"
    )

    user_prompt = f"""
【運動員基本狀態】
- 日期：{meal_date_str}
- 目前體重：{cur_weight} kg
- 體重管理目標：{goal_mode}
- 今日訓練負荷：{training_load}

【今日飲食內容】
- 早餐：{b_txt.strip() if b_txt.strip() else "未進食 / 未填寫"}
- 午餐：{l_txt.strip() if l_txt.strip() else "未進食 / 未填寫"}
- 晚餐：{d_txt.strip() if d_txt.strip() else "未進食 / 未填寫"}
- 點心 / 訓練前後補給：{s_txt.strip() if s_txt.strip() else "無"}

請嚴格依照以下 JSON 結構回傳：
{{
  "total_calories": 2250,
  "total_protein_g": 135,
  "total_carbs_g": 280,
  "total_fat_g": 62,
  "meals_breakdown": [
    {{"meal": "早餐", "items_summary": "簡述食物", "calories": 550, "protein_g": 30, "carbs_g": 65, "fat_g": 18}},
    {{"meal": "午餐", "items_summary": "簡述食物", "calories": 750, "protein_g": 45, "carbs_g": 95, "fat_g": 20}},
    {{"meal": "晚餐", "items_summary": "簡述食物", "calories": 700, "protein_g": 40, "carbs_g": 85, "fat_g": 18}},
    {{"meal": "點心/補給", "items_summary": "簡述食物", "calories": 250, "protein_g": 20, "carbs_g": 35, "fat_g": 6}}
  ],
  "target_comparison": "說明今日熱量與蛋白質（每公斤體重攝取量）是否符合該訓練負荷與體重目標",
  "nutrition_advice": "給予2至3點具體可執行的下一餐或明日飲食微調建議（繁體中文）"
}}
"""
    client = _get_gemini_client()
    candidate_models = [MODEL_ID, MODEL_ID, "gemini-2.5-flash"]
    last_err = None
    raw_text = ""

    for attempt_idx, target_model in enumerate(candidate_models):
        try:
            response = client.models.generate_content(
                model=target_model,
                contents=user_prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    temperature=0.3,
                    response_mime_type="application/json",
                ),
            )
            raw_text = (response.text or "").strip()
            if raw_text:
                break
        except Exception as e:
            last_err = e
            err_str = str(e)
            if any(code in err_str for code in ["503", "429", "UNAVAILABLE", "RESOURCE_EXHAUSTED", "overloaded"]):
                if attempt_idx < len(candidate_models) - 1:
                    time.sleep(2)
                    continue
            raise

    if not raw_text:
        if last_err:
            raise last_err
        raise RuntimeError("AI 模型未回傳任何文字內容，請稍後再試。")

    start_idx = raw_text.find("{")
    end_idx = raw_text.rfind("}")
    if start_idx == -1 or end_idx == -1 or end_idx <= start_idx:
        raise ValueError(f"AI 回傳格式非預期 JSON：{raw_text[:200]}")

    return json.loads(raw_text[start_idx : end_idx + 1])
