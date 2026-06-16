import streamlit as st
import pandas as pd
import numpy as np
import datetime
import json
import os
import requests
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# --- アプリケーション設定 ---
st.set_page_config(
    page_title="複数人対応型・減量進捗管理アプリ | AI Trainer Pro",
    page_icon="🏋️‍♂️",
    layout="wide",
    initial_sidebar_state="expanded"
)

# 保存先パス設定 (ローカル用)
DATA_DIR = os.path.dirname(os.path.abspath(__file__))
USERS_CSV_PATH = os.path.join(DATA_DIR, "users.csv")

def get_data_csv_path(user_id):
    safe_user_id = "".join([c for c in str(user_id) if c.isalnum() or c in ('_', '-')]).strip()
    return os.path.join(DATA_DIR, f"data_{safe_user_id}.csv")

# =====================================================================
# 🌐 クラウド接続の自動判定と遅延初期化（GAS / GSheetsConnection / CSV）
# =====================================================================

is_gsheet_active = False
connection_type = "csv" # "csv", "gsheets", "gas"
gsheet_error_msg = ""
conn = None

# GAS 連携設定
gas_url = ""
gas_token = ""

def check_secrets_safe():
    """Secretsが安全に利用できるかを事前検証する"""
    # ローカルの.streamlit/secrets.toml存在確認
    path1 = os.path.expanduser("~/.streamlit/secrets.toml")
    path2 = os.path.join(DATA_DIR, ".streamlit", "secrets.toml")
    if os.path.exists(path1) or os.path.exists(path2):
        return True
    # Streamlit Cloud環境、または環境変数
    if "STREAMLIT_SERVER_PORT" in os.environ or os.environ.get("IS_STREAMLIT_CLOUD") == "true":
        return True
    return False

# 起動時に自動チェック
try:
    if check_secrets_safe() and st.secrets:
        # 1. Google Apps Script (GAS) 方式の優先チェック (GCP/JSON不要)
        if "gas_url" in st.secrets and "gas_token" in st.secrets:
            gas_url = st.secrets["gas_url"]
            gas_token = st.secrets["gas_token"]
            if gas_url.strip() != "" and gas_token.strip() != "":
                # 疎通確認のためのテスト呼び出し (タイムアウトは短めに設定)
                try:
                    r = requests.get(gas_url, params={"action": "get_users", "token": gas_token}, timeout=5)
                    if r.status_code == 200:
                        is_gsheet_active = True
                        connection_type = "gas"
                    else:
                        gsheet_error_msg = f"GAS疎通失敗: HTTP {r.status_code}"
                except Exception as e:
                    gsheet_error_msg = f"GASアクセス例外: {e}"

        # 2. 従来の GSheetsConnection 方式のチェック (GASが動いていない場合のみ)
        if not is_gsheet_active and "connections" in st.secrets and "gsheets" in st.secrets["connections"]:
            from streamlit_gsheets import GSheetsConnection
            # コネクションの確立
            conn = st.connection("gsheets", type=GSheetsConnection)
            # テスト読込で有効性をチェック
            _ = conn.read(worksheet="users", nrows=1)
            is_gsheet_active = True
            connection_type = "gsheets"
except BaseException as e:
    gsheet_error_msg = str(e)


# --- カスタムCSSの適用（Oracle Red Bull Racingデザイン） ---
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;700&family=Noto+Sans+JP:wght@300;400;700&display=swap');
    
    html, body, [class*="css"] {
        font-family: 'Outfit', 'Noto Sans JP', sans-serif;
    }
    
    .stApp {
        background-color: #04112b;
        color: #f1f5f9;
    }
    
    section[data-testid="stSidebar"] {
        background-color: #0b1e3f !important;
        border-right: 1px solid #1c365d;
    }
    
    div.stButton > button:first-child {
        background: linear-gradient(135deg, #ff002b 0%, #a8001c 100%);
        color: white;
        border: none;
        border-radius: 8px;
        padding: 0.5rem 2rem;
        font-weight: 600;
        transition: all 0.3s ease;
        box-shadow: 0 4px 6px -1px rgba(255, 0, 43, 0.2);
    }
    div.stButton > button:first-child:hover {
        background: linear-gradient(135deg, #ff3355 0%, #ff002b 100%);
        transform: translateY(-2px);
        box-shadow: 0 10px 15px -3px rgba(255, 0, 43, 0.3);
    }
    
    .report-card {
        background: rgba(11, 30, 63, 0.7);
        border: 1px solid #1c365d;
        border-radius: 16px;
        padding: 1.5rem;
        margin: 1rem 0;
        backdrop-filter: blur(10px);
    }
    
    .ai-feedback {
        background: linear-gradient(135deg, rgba(11, 30, 63, 0.9) 0%, rgba(4, 17, 43, 0.9) 100%);
        border-left: 5px solid #feb80a;
        border-radius: 12px;
        padding: 1.5rem;
        margin-top: 1.5rem;
        box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.3);
    }
    
    .ai-feedback-header {
        font-size: 1.25rem;
        font-weight: 700;
        color: #feb80a;
        margin-bottom: 0.75rem;
        display: flex;
        align-items: center;
        gap: 0.5rem;
    }
    
    .ai-feedback-body {
        font-size: 1rem;
        line-height: 1.7;
        color: #f1f5f9;
    }
</style>
""", unsafe_allow_html=True)


# =====================================================================
# 🗄️ データアクセス抽象化レイヤー（ハイブリッド対応版）
# =====================================================================

def init_db():
    """ローカルCSVデータベースの初期化 (CSVモード時のみ有効)"""
    if is_gsheet_active:
        return
        
    if not os.path.exists(DATA_DIR):
        os.makedirs(DATA_DIR)
        
    if not os.path.exists(USERS_CSV_PATH):
        df_users = pd.DataFrame(columns=["user_id", "name", "gender", "age", "height", "target_weight"])
        df_users.to_csv(USERS_CSV_PATH, index=False, encoding="utf-8")

def get_users():
    """全ユーザーのマスターリストを取得する"""
    if is_gsheet_active:
        if connection_type == "gas":
            try:
                r = requests.get(gas_url, params={"action": "get_users", "token": gas_token}, timeout=10)
                if r.status_code == 200:
                    res_json = r.json()
                    if isinstance(res_json, dict) and "error" in res_json:
                        st.error(f"GASエラー: {res_json['error']}")
                        return pd.DataFrame(columns=["user_id", "name", "gender", "age", "height", "target_weight"])
                    df = pd.DataFrame(res_json)
                    if df.empty:
                        df = pd.DataFrame(columns=["user_id", "name", "gender", "age", "height", "target_weight"])
                    else:
                        cols = ["user_id", "name", "gender", "age", "height", "target_weight"]
                        for col in cols:
                            if col not in df.columns:
                                df[col] = None
                        df = df[cols]
                    return df
                else:
                    st.error(f"GAS接続失敗: HTTP {r.status_code}")
                    return pd.DataFrame(columns=["user_id", "name", "gender", "age", "height", "target_weight"])
            except Exception as e:
                st.error(f"GASからのユーザー取得に失敗しました: {e}")
                return pd.DataFrame(columns=["user_id", "name", "gender", "age", "height", "target_weight"])
        elif connection_type == "gsheets" and conn is not None:
            try:
                try:
                    df = conn.read(worksheet="users")
                    if df.empty:
                        df = pd.DataFrame(columns=["user_id", "name", "gender", "age", "height", "target_weight"])
                    return df
                except Exception:
                    # ワークシートが存在しない場合は動的作成
                    client = conn.client
                    url = st.secrets["connections"]["gsheets"]["spreadsheet"]
                    sheet = client.open_by_url(url)
                    ws = sheet.add_worksheet(title="users", rows="100", cols="10")
                    ws.update('A1', [["user_id", "name", "gender", "age", "height", "target_weight"]])
                    return pd.DataFrame(columns=["user_id", "name", "gender", "age", "height", "target_weight"])
            except Exception as e:
                st.error(f"スプレッドシートからのユーザー取得に失敗しました: {e}")
                return pd.DataFrame(columns=["user_id", "name", "gender", "age", "height", "target_weight"])
    else:
        # CSVモード
        init_db()
        try:
            df = pd.read_csv(USERS_CSV_PATH)
            return df
        except Exception as e:
            st.error(f"ユーザーマスターCSVの読み込みに失敗しました: {e}")
            return pd.DataFrame(columns=["user_id", "name", "gender", "age", "height", "target_weight"])

def save_users(df):
    """ユーザーマスターを保存する"""
    if is_gsheet_active:
        if connection_type == "gas":
            try:
                df_clean = df.copy()
                df_clean = df_clean.replace({np.nan: None})
                payload = {
                    "action": "save_users",
                    "token": gas_token,
                    "data": df_clean.to_dict(orient="records")
                }
                r = requests.post(gas_url, json=payload, timeout=10)
                if r.status_code == 200:
                    res = r.json()
                    if res.get("success"):
                        return True
                    else:
                        st.error(f"GAS保存エラー: {res.get('error')}")
                        return False
                else:
                    st.error(f"GAS書き込み失敗: HTTP {r.status_code}")
                    return False
            except Exception as e:
                st.error(f"GASへのユーザー保存に失敗しました: {e}")
                return False
        elif connection_type == "gsheets" and conn is not None:
            try:
                conn.update(worksheet="users", data=df)
                return True
            except Exception as e:
                st.error(f"スプレッドシートへのユーザー保存に失敗しました: {e}")
                return False
    else:
        # CSVモード
        init_db()
        try:
            df.to_csv(USERS_CSV_PATH, index=False, encoding="utf-8")
            return True
        except Exception as e:
            st.error(f"ユーザーマスターCSVの保存に失敗しました: {e}")
            return False

def get_user_by_id(user_id):
    """ユーザーIDからプロフィールを取得する"""
    df = get_users()
    if df.empty:
        return None
    df['user_id'] = df['user_id'].astype(str)
    user_row = df[df['user_id'] == str(user_id)]
    if not user_row.empty:
        return user_row.iloc[0].to_dict()
    return None

def register_user(user_id, name, gender, age, height, target_weight):
    """新規ユーザーを登録する (IDの重複チェックあり)"""
    df_users = get_users()
    if not df_users.empty:
        df_users['user_id'] = df_users['user_id'].astype(str)
        if str(user_id) in df_users['user_id'].values:
            return False, "このユーザーIDはすでに登録されています。"
        
    new_user = {
        "user_id": str(user_id),
        "name": name,
        "gender": gender,
        "age": int(age),
        "height": float(height),
        "target_weight": float(target_weight)
    }
    
    df_updated = pd.concat([df_users, pd.DataFrame([new_user])], ignore_index=True)
    if save_users(df_updated):
        # 個別データの初期化
        if is_gsheet_active:
            if connection_type == "gas":
                try:
                    payload = {
                        "action": "create_user_sheet",
                        "user_id": user_id,
                        "token": gas_token
                    }
                    requests.post(gas_url, json=payload, timeout=5)
                except Exception as e:
                    st.error(f"個別ワークシートの自動作成に失敗しました: {e}")
            elif connection_type == "gsheets" and conn is not None:
                try:
                    sheet_title = f"data_{user_id}"
                    try:
                        conn.read(worksheet=sheet_title)
                    except Exception:
                        client = conn.client
                        url = st.secrets["connections"]["gsheets"]["spreadsheet"]
                        sheet = client.open_by_url(url)
                        ws = sheet.add_worksheet(title=sheet_title, rows="200", cols="10")
                        ws.update('A1', [["date", "weight", "intake_kcal", "activity_level", "bmr", "tdee"]])
                except Exception as e:
                    st.error(f"個別ワークシートの自動作成に失敗しました: {e}")
        else:
            # CSVモード
            csv_path = get_data_csv_path(user_id)
            if not os.path.exists(csv_path):
                df_empty = pd.DataFrame(columns=["date", "weight", "intake_kcal", "activity_level", "bmr", "tdee"])
                df_empty.to_csv(csv_path, index=False, encoding="utf-8")
        return True, "ユーザーを正常に登録しました。"
    return False, "ユーザーの登録書き込みに失敗しました。"

def get_user_data(user_id):
    """ユーザー個別のログデータを取得する"""
    if is_gsheet_active:
        if connection_type == "gas":
            try:
                r = requests.get(gas_url, params={"action": "get_user_data", "user_id": user_id, "token": gas_token}, timeout=10)
                if r.status_code == 200:
                    res_json = r.json()
                    if isinstance(res_json, dict) and "error" in res_json:
                        st.error(f"GASエラー: {res_json['error']}")
                        return pd.DataFrame(columns=["date", "weight", "intake_kcal", "activity_level", "bmr", "tdee"])
                    df = pd.DataFrame(res_json)
                    if df.empty:
                        df = pd.DataFrame(columns=["date", "weight", "intake_kcal", "activity_level", "bmr", "tdee"])
                    else:
                        df['date'] = pd.to_datetime(df['date']).dt.date
                        df = df.sort_values('date').reset_index(drop=True)
                    return df
                else:
                    st.error(f"GAS接続失敗: HTTP {r.status_code}")
                    return pd.DataFrame(columns=["date", "weight", "intake_kcal", "activity_level", "bmr", "tdee"])
            except Exception as e:
                st.error(f"GASからの個別データ取得に失敗しました: {e}")
                return pd.DataFrame(columns=["date", "weight", "intake_kcal", "activity_level", "bmr", "tdee"])
        elif connection_type == "gsheets" and conn is not None:
            try:
                sheet_title = f"data_{user_id}"
                try:
                    df = conn.read(worksheet=sheet_title)
                    if df.empty:
                        df = pd.DataFrame(columns=["date", "weight", "intake_kcal", "activity_level", "bmr", "tdee"])
                    else:
                        df['date'] = pd.to_datetime(df['date']).dt.date
                        df = df.sort_values('date').reset_index(drop=True)
                    return df
                except Exception:
                    # ワークシートが存在しない場合は自動作成
                    client = conn.client
                    url = st.secrets["connections"]["gsheets"]["spreadsheet"]
                    sheet = client.open_by_url(url)
                    ws = sheet.add_worksheet(title=sheet_title, rows="200", cols="10")
                    ws.update('A1', [["date", "weight", "intake_kcal", "activity_level", "bmr", "tdee"]])
                    return pd.DataFrame(columns=["date", "weight", "intake_kcal", "activity_level", "bmr", "tdee"])
            except Exception as e:
                st.error(f"スプレッドシートからの個別データ取得に失敗しました: {e}")
                return pd.DataFrame(columns=["date", "weight", "intake_kcal", "activity_level", "bmr", "tdee"])
    else:
        # CSVモード
        init_db()
        csv_path = get_data_csv_path(user_id)
        if os.path.exists(csv_path):
            try:
                df = pd.read_csv(csv_path)
                df['date'] = pd.to_datetime(df['date']).dt.date
                df = df.sort_values('date').reset_index(drop=True)
                return df
            except Exception as e:
                st.error(f"個別CSVの読み込みに失敗しました: {e}")
        return pd.DataFrame(columns=["date", "weight", "intake_kcal", "activity_level", "bmr", "tdee"])

def save_user_data(user_id, df):
    """ユーザー個別のログデータを保存する"""
    df_save = df.copy()
    df_save['date'] = df_save['date'].apply(lambda d: d.strftime("%Y-%m-%d") if isinstance(d, (datetime.date, datetime.datetime)) else str(d))
    df_save = df_save.replace({np.nan: None})
    
    if is_gsheet_active:
        if connection_type == "gas":
            try:
                payload = {
                    "action": "save_user_data",
                    "user_id": user_id,
                    "token": gas_token,
                    "data": df_save.to_dict(orient="records")
                }
                r = requests.post(gas_url, json=payload, timeout=10)
                if r.status_code == 200:
                    res = r.json()
                    if res.get("success"):
                        return True
                    else:
                        st.error(f"GAS個別データ保存エラー: {res.get('error')}")
                        return False
                else:
                    st.error(f"GAS個別データ書き込み失敗: HTTP {r.status_code}")
                    return False
            except Exception as e:
                st.error(f"GASへの個別データ保存に失敗しました: {e}")
                return False
        elif connection_type == "gsheets" and conn is not None:
            try:
                sheet_title = f"data_{user_id}"
                conn.update(worksheet=sheet_title, data=df_save)
                return True
            except Exception as e:
                st.error(f"スプレッドシートへの個別データ保存に失敗しました: {e}")
                return False
    else:
        # CSVモード
        init_db()
        csv_path = get_data_csv_path(user_id)
        try:
            df_save.to_csv(csv_path, index=False, encoding="utf-8")
            return True
        except Exception as e:
            st.error(f"個別CSV의 保存に失敗しました: {e}")
            return False


# =====================================================================
# 🧠 共通ロジック：自動計算と週比較AIアドバイス
# =====================================================================

def calculate_bmr(weight, height, age, gender):
    if gender == "男性":
        return 66.4730 + 13.7516 * weight + 5.0033 * height - 6.7550 * age
    else:
        return 655.0955 + 9.5634 * weight + 1.8496 * height - 4.6756 * age

def generate_weekly_report(df_user_data, user_profile):
    n_records = len(df_user_data)
    if n_records < 7:
        return None
        
    df_sorted = df_user_data.sort_values('date').reset_index(drop=True)
    is_two_weeks = n_records >= 14
    this_week = df_sorted.tail(7)
    
    avg_weight_this = this_week['weight'].mean()
    avg_intake_this = this_week['intake_kcal'].mean()
    avg_tdee_this = this_week['tdee'].mean()
    avg_net_this = avg_intake_this - avg_tdee_this
    
    if is_two_weeks:
        last_week = df_sorted.iloc[-14:-7]
        avg_weight_last = last_week['weight'].mean()
        compare_mode = "前週比較 (直近2週間の週平均値比較)"
    else:
        avg_weight_last = df_sorted.iloc[0]['weight']
        compare_mode = "初期比較 (初日の体重値 vs 直近7日間の週平均値比較)"
        
    delta_weight = avg_weight_this - avg_weight_last
    
    is_under_calorie = avg_net_this < 0
    is_weight_decreasing = delta_weight < 0
    
    feedback_pattern = ""
    feedback_message = ""
    
    if is_under_calorie and not is_weight_decreasing:
        feedback_pattern = "パターンA: 停滞期フェーズ（水分貯留）"
        feedback_message = (
            "データを見る限り、カロリー収支は完全にマイナスで順調です！"
            "今、体重が横ばい、あるいは増えているように見えるのは脂肪ではなく、"
            "女性の周期や塩分摂取による『水分貯留（むくみ）』のフェーズです。"
            "ここで焦って食事を極端に減らさず、今のベースを信じて維持しましょう！"
        )
    elif not is_under_calorie and is_weight_decreasing:
        feedback_pattern = "パターンB: 一時的減少フェーズ（水分抜けのタイムラグ）"
        feedback_message = (
            "体重は落ちていますが、エネルギー収支はややオーバー気味です。"
            "これは一時的な水分抜け（タイムラグ）による減少の可能性が高いので、"
            "明日から元の食事ペースに戻して調整していきましょう！"
        )
    elif is_under_calorie and is_weight_decreasing:
        feedback_pattern = "パターンC: 順調・減量進行フェーズ"
        feedback_message = (
            "素晴らしいです！エネルギー収支も体重のトレンドも、計算通り完全に右肩下がりです。"
            "日々の細かなブレに惑わされず、この調子で淡々とログを重ねていきましょう！"
        )
    else:
        feedback_pattern = "その他: 調整フェーズ"
        feedback_message = (
            "エネルギー収支がプラスで、体重も横ばい〜増加傾向にあります。"
            "これは摂取カロリーが消費カロリーを上回っていることを示しています。"
            "焦る必要はありません。まずは食事量を適切に戻すか、日常の歩数を少し増やして消費を稼ぎましょう！"
        )
        
    return {
        "compare_mode": compare_mode,
        "avg_weight_this": avg_weight_this,
        "avg_weight_last": avg_weight_last,
        "delta_weight": delta_weight,
        "avg_intake_this": avg_intake_this,
        "avg_tdee_this": avg_tdee_this,
        "avg_net_this": avg_net_this,
        "feedback_pattern": feedback_pattern,
        "feedback_message": feedback_message
    }


# =====================================================================
# 💻 UI描画: サイドバーコントロール
# =====================================================================

st.sidebar.markdown("# 🏋️‍♂️ AI Trainer App")
st.sidebar.caption("複数人対応・減量進捗管理システム")

# 接続状態インジケータ
if is_gsheet_active:
    if connection_type == "gas":
        st.sidebar.success("🟢 クラウド保存（GAS経由）稼働中")
    else:
        st.sidebar.success("🟢 クラウド保存（Google Sheets）稼働中")
elif check_secrets_safe() and (("gas_url" in st.secrets) or (st.secrets and "connections" in st.secrets and "gsheets" in st.secrets["connections"])):
    st.sidebar.error(f"🔴 クラウド接続エラー\n(ローカルCSVで代替動作中)\nエラー詳細: {gsheet_error_msg[:100]}...")
else:
    st.sidebar.info("🟡 ローカル保存（CSV）稼働中")

st.sidebar.markdown("---")
app_mode = st.sidebar.radio("モード選択", ["👤 利用者ログイン", "🔑 管理者モード"])


# =====================================================================
# 👤 利用者ログインモード
# =====================================================================

if app_mode == "👤 利用者ログイン":
    st.markdown("## 👤 利用者ログイン")
    
    if "user_logged_in" not in st.session_state:
        st.session_state.user_logged_in = False
        st.session_state.current_user_id = None
        
    if not st.session_state.user_logged_in:
        with st.form("login_form"):
            user_id_input = st.text_input("ユーザーID（識別番号）を入力してください")
            login_btn = st.form_submit_button("ログイン")
            
            if login_btn:
                if user_id_input.strip() == "":
                    st.error("ユーザーIDを入力してください。")
                else:
                    user_profile = get_user_by_id(user_id_input.strip())
                    if user_profile:
                        st.session_state.user_logged_in = True
                        st.session_state.current_user_id = user_profile["user_id"]
                        st.success(f"ログイン成功: {user_profile['name']} さん")
                        st.rerun()
                    else:
                        st.error("入力されたユーザーIDは存在しません。トレーナーへご確認ください。")
    else:
        user_profile = get_user_by_id(st.session_state.current_user_id)
        if not user_profile:
            st.session_state.user_logged_in = False
            st.session_state.current_user_id = None
            st.rerun()
            
        st.markdown(f"### 👋 ようこそ、**{user_profile['name']}** さん")
        st.caption(f"ID: {user_profile['user_id']} | 性別: {user_profile['gender']} | 年齢: {user_profile['age']}歳 | 身長: {user_profile['height']}cm | 目標体重: {user_profile['target_weight']}kg")
        
        if st.sidebar.button("ログアウト"):
            st.session_state.user_logged_in = False
            st.session_state.current_user_id = None
            st.rerun()
            
        tab_input, tab_report, tab_history = st.tabs(["📝 本日の記録", "📊 週刊分析レポート", "📅 日ごとの履歴"])
        
        # --- タブ1：本日の記録 ---
        with tab_input:
            st.markdown("#### 今日の記録を追加・更新")
            st.caption("Apple Watchなどの活動消費カロリーを直接入力できます。未入力の場合は、今日の活動レベルから自動計算されます。")
            
            df_user_data = get_user_data(user_profile["user_id"])
            last_weight_val = user_profile["target_weight"] + 5.0
            if not df_user_data.empty:
                last_weight_val = df_user_data.sort_values('date').iloc[-1]['weight']
                
            with st.form("daily_record_form"):
                col1, col2 = st.columns(2)
                with col1:
                    log_date = st.date_input("日付", value=datetime.date.today())
                    weight_val = st.number_input("体重 (kg) *", min_value=10.0, max_value=300.0, value=float(last_weight_val), step=0.1)
                    calories_val = st.number_input("摂取カロリー (kcal) *", min_value=0, max_value=15000, value=1800, step=50)
                
                with col2:
                    active_cal_input = st.number_input("活動消費カロリー (kcal) - Apple Watch等の値", min_value=0, max_value=5000, value=0, step=10, help="Apple Watchのアクティブエネルギー(ムーブ)などを直接入力します。入力すると活動レベルの計算より優先されます。")
                    act_level = st.radio(
                        "今日の活動レベル (活動消費カロリーが未入力の場合に適用されます) *",
                        ["低（デスクワーク中心） : 係数 1.2", "中（よく歩いた・軽い運動） : 係数 1.4", "高（筋トレ・激しい運動） : 係数 1.65"],
                        index=1
                    )
                    bmr_input = st.text_input("基礎代謝 (kcal) - 未入力で自動計算", placeholder="例: 1500")
                    
                submit_record = st.form_submit_button("記録を保存")
                
                if submit_record:
                    try:
                        bmr_val = float(bmr_input) if bmr_input.strip() != "" else 0.0
                    except ValueError:
                        bmr_val = 0.0
                        
                    if "低" in act_level:
                        coef = 1.2
                        act_str = "低"
                    elif "高" in act_level:
                        coef = 1.65
                        act_str = "高"
                    else:
                        coef = 1.4
                        act_str = "中"
                        
                    if bmr_val <= 0.0:
                        bmr_val = calculate_bmr(
                            weight_val,
                            user_profile["height"],
                            user_profile["age"],
                            user_profile["gender"]
                        )
                        
                    # TDEEの算出ロジック
                    if active_cal_input > 0:
                        tdee_val = bmr_val + active_cal_input
                        # 直接入力された場合は活動レベルの文字列を更新
                        act_str = f"{act_str} (直入力: {active_cal_input}kcal)"
                    else:
                        tdee_val = bmr_val * coef
                        
                    new_log = {
                        "date": log_date.strftime("%Y-%m-%d"),
                        "weight": round(weight_val, 2),
                        "intake_kcal": round(calories_val, 0),
                        "activity_level": act_str,
                        "bmr": round(bmr_val, 0),
                        "tdee": round(tdee_val, 0)
                    }
                    
                    df_user_data = get_user_data(user_profile["user_id"])
                    
                    if not df_user_data.empty:
                        dup_mask = df_user_data['date'] == log_date
                        if dup_mask.any():
                            idx_to_update = df_user_data[dup_mask].index[0]
                            for key, val in new_log.items():
                                df_user_data.at[idx_to_update, key] = val
                            df_updated = df_user_data
                        else:
                            df_updated = pd.concat([df_user_data, pd.DataFrame([new_log])], ignore_index=True)
                    else:
                        df_updated = pd.DataFrame([new_log])
                        
                    df_updated['date'] = pd.to_datetime(df_updated['date']).dt.date
                    df_updated = df_updated.sort_values('date').reset_index(drop=True)
                    
                    if save_user_data(user_profile["user_id"], df_updated):
                        total_logs = len(df_updated)
                        rem_days = 7 - (total_logs % 7)
                        
                        if rem_days == 7:
                            st.balloons()
                            st.success(f"🎉 本日の記録が完了しました！確実にデータが蓄積されています。これで合計 {total_logs} 日分のデータとなりました。")
                        else:
                            st.success(f"💾 本日の記録が完了しました！確実にデータが蓄積されています。次のAI週刊分析レポートの更新まで、あと {rem_days} 日分のログが必要です。")
                        st.rerun()

        # --- タブ3：日ごとの履歴・編集 ---
        with tab_history:
            st.markdown("#### 📅 日ごとの記録・履歴管理")
            
            df_user_data = get_user_data(user_profile["user_id"])
            
            if df_user_data.empty:
                st.info("登録されている履歴データがまだありません。「本日の記録」から入力を始めてください。")
            else:
                # タイムライン順（降順）ソート
                df_history = df_user_data.sort_values('date', ascending=False).reset_index(drop=True)
                
                # 詳細表示・編集セクション
                st.markdown("##### 📝 記録の確認・修正・削除")
                st.caption("修正または削除したい日付を選択し、値を変更して保存、または削除ボタンを押してください。")
                
                # 日付選択用リスト
                date_list = df_history['date'].tolist()
                selected_date = st.selectbox("確認・修正する日付を選択", date_list)
                
                if selected_date:
                    # 選択された日付のレコード抽出
                    record = df_history[df_history['date'] == selected_date].iloc[0].to_dict()
                    
                    # 活動カロリーの逆算（TDEE - BMR）
                    bmr_val = float(record.get('bmr', 0))
                    tdee_val = float(record.get('tdee', 0))
                    active_cal_calc = max(0.0, tdee_val - bmr_val)
                    
                    with st.form("edit_record_form"):
                        col_e1, col_e2 = st.columns(2)
                        with col_e1:
                            edit_weight = st.number_input("体重 (kg)", min_value=10.0, max_value=300.0, value=float(record.get('weight', 0.0)), step=0.1)
                            edit_intake = st.number_input("摂取カロリー (kcal)", min_value=0, max_value=15000, value=int(record.get('intake_kcal', 0)), step=50)
                        
                        with col_e2:
                            edit_active = st.number_input("活動消費カロリー (kcal)", min_value=0, max_value=5000, value=int(active_cal_calc), step=10, help="Apple Watchなどのムーブエネルギーの値")
                            edit_bmr = st.number_input("基礎代謝 (kcal)", min_value=0.0, max_value=5000.0, value=bmr_val, step=10.0)
                            
                        # ボタン配置用のレイアウト
                        col_btn1, col_btn2 = st.columns([1, 1])
                        with col_btn1:
                            submit_edit = st.form_submit_button("🎨 変更内容を保存")
                        with col_btn2:
                            submit_delete = st.form_submit_button("🗑️ この日の記録を削除")
                            
                        if submit_edit:
                            # TDEEの再計算
                            new_tdee = edit_bmr + edit_active
                            
                            # データの更新
                            idx_to_update = df_user_data[df_user_data['date'] == selected_date].index[0]
                            df_user_data.at[idx_to_update, 'weight'] = round(edit_weight, 2)
                            df_user_data.at[idx_to_update, 'intake_kcal'] = round(edit_intake, 0)
                            df_user_data.at[idx_to_update, 'bmr'] = round(edit_bmr, 0)
                            df_user_data.at[idx_to_update, 'tdee'] = round(new_tdee, 0)
                            
                            # 日付のソートと保存
                            df_user_data['date'] = pd.to_datetime(df_user_data['date']).dt.date
                            df_user_data = df_user_data.sort_values('date').reset_index(drop=True)
                            
                            if save_user_data(user_profile["user_id"], df_user_data):
                                st.success(f"💾 {selected_date} の記録を修正しました。")
                                st.rerun()
                                
                        if submit_delete:
                            # データの削除
                            df_deleted = df_user_data[df_user_data['date'] != selected_date]
                            df_deleted['date'] = pd.to_datetime(df_deleted['date']).dt.date
                            df_deleted = df_deleted.sort_values('date').reset_index(drop=True)
                            
                            if save_user_data(user_profile["user_id"], df_deleted):
                                st.success(f"🗑️ {selected_date} の記録を削除しました。")
                                st.rerun()
                
                st.markdown("---")
                st.markdown("##### 📜 過去の全履歴一覧")
                
                # タイムライン風のアコーディオン表示
                for idx, row in df_history.iterrows():
                    date_str = row['date'].strftime("%Y年 %m月 %d日")
                    weight_val = row['weight']
                    intake_val = row['intake_kcal']
                    bmr_val = row['bmr']
                    tdee_val = row['tdee']
                    active_val = max(0, tdee_val - bmr_val)
                    net_val = intake_val - tdee_val
                    
                    # 収支によって色分けやマークを変える
                    net_color = "#ff002b" if net_val > 0 else "#22c55e"
                    net_sign = "+" if net_val > 0 else ""
                    
                    title = f"📅 {date_str}  |  体重: {weight_val:.2f} kg  |  収支: {net_sign}{int(net_val)} kcal"
                    
                    with st.expander(title):
                        col_h1, col_h2, col_h3 = st.columns(3)
                        with col_h1:
                            st.metric(label="体重", value=f"{weight_val:.2f} kg")
                        with col_h2:
                            st.metric(label="摂取カロリー", value=f"{int(intake_val)} kcal")
                        with col_h3:
                            st.metric(
                                label="消費カロリー (TDEE)", 
                                value=f"{int(tdee_val)} kcal",
                                delta=f"内、活動: {int(active_val)} kcal"
                            )

        # --- タブ2：週刊分析レポート ---
        with tab_report:
            st.markdown("#### 📊 週刊分析レポート (AIトレーナーアドバイス)")
            
            df_user_data = get_user_data(user_profile["user_id"])
            n_records = len(df_user_data)
            
            if n_records < 7:
                st.warning(f"現在、登録されているデータは {n_records} 日分です。週平均分析レポートを作成するには、あと {7 - n_records} 日分のデータが必要です。")
                st.info("💡 毎日の記録タブから入力を行ってください。")
            else:
                report = generate_weekly_report(df_user_data, user_profile)
                if report:
                    st.caption(f"分析モード: {report['compare_mode']}")
                    
                    col_met1, col_met2, col_met3 = st.columns(3)
                    with col_met1:
                        st.metric(
                            label="今週の平均体重",
                            value=f"{report['avg_weight_this']:.2f} kg",
                            delta=f"{report['delta_weight']:+.2f} kg",
                            delta_color="inverse"
                        )
                    with col_met2:
                        st.metric(
                            label="今週の平均摂取カロリー",
                            value=f"{int(report['avg_intake_this'])} kcal"
                        )
                    with col_met3:
                        st.metric(
                            label="今週の平均消費カロリー",
                            value=f"{int(report['avg_tdee_this'])} kcal"
                        )
                        
                    st.markdown(f"""
                    <div class="ai-feedback">
                        <div class="ai-feedback-header">
                            <span>🧠 AI Trainer Advisor ({report['feedback_pattern']})</span>
                        </div>
                        <div class="ai-feedback-body">
                            {report['feedback_message']}
                        </div>
                    </div>
                    """, unsafe_allow_html=True)
                    
                    st.markdown("---")
                    st.markdown("#### 📈 体重推移 (全期間)")
                    df_chart = df_user_data.copy()
                    df_chart['date_str'] = df_chart['date'].apply(lambda d: d.strftime("%m/%d"))
                    st.line_chart(df_chart.set_index('date_str')['weight'])
                    
                    st.markdown("#### 📋 直近10日分の記録履歴")
                    df_sorted_desc = df_user_data.sort_values('date', ascending=False).head(10)
                    st.dataframe(
                        df_sorted_desc.rename(columns={
                            "date": "日付",
                            "weight": "体重(kg)",
                            "intake_kcal": "摂取(kcal)",
                            "activity_level": "活動レベル",
                            "bmr": "基礎代謝(kcal)",
                            "tdee": "消費TDEE(kcal)"
                        }),
                        use_container_width=True
                    )


# =====================================================================
# 🔑 管理者モード
# =====================================================================

elif app_mode == "🔑 管理者モード":
    st.markdown("## 🔑 管理者（トレーナー）ダッシュボード")
    
    # 秘密情報管理からのパスワード取得
    real_password = None
    try:
        if check_secrets_safe() and st.secrets and "admin_password" in st.secrets:
            real_password = st.secrets["admin_password"]
    except BaseException:
        pass
        
    if "admin_authenticated" not in st.session_state:
        st.session_state.admin_authenticated = False
        
    if not st.session_state.admin_authenticated:
        # パスワードが Secrets に設定されていない場合は警告を表示しログイン不可にする
        if real_password is None or str(real_password).strip() == "":
            st.error("⚠️ セキュリティ警告: 設定ファイル(secrets.toml)に管理者パスワード(admin_password)が設定されていないため、安全上、管理者ログインは現在無効化されています。")
        else:
            admin_pass_input = st.text_input("管理者パスワードを入力してください", type="password")
            login_admin = st.button("管理者ログイン")
            
            if login_admin:
                if admin_pass_input == real_password:
                    st.session_state.admin_authenticated = True
                    st.success("管理者認証に成功しました！")
                    st.rerun()
                else:
                    st.error("パスワードが正しくありません。")
    else:
        if st.sidebar.button("管理者ログアウト"):
            st.session_state.admin_authenticated = False
            st.rerun()
            
        st.markdown("### クライアント（利用者）の一括管理・監視")
        
        tab_reg, tab_monitor, tab_demo = st.tabs([
            "🆕 クライアント新規登録",
            "🔍 進捗監視ダッシュボード",
            "🧪 評価・デモデータ生成"
        ])
        
        # --- サブタブ1：クライアント新規登録 ---
        with tab_reg:
            st.markdown("#### クライアント新規追加")
            with st.form("new_user_form", clear_on_submit=True):
                col_u1, col_u2 = st.columns(2)
                with col_u1:
                    new_id = st.text_input("ユーザーID (一意の識別子・英数字を推奨)", placeholder="例: client_tanaka")
                    new_name = st.text_input("氏名", placeholder="例: 田中 太郎")
                    new_gender = st.radio("性別", ["男性", "女性"])
                with col_u2:
                    new_age = st.number_input("年齢 (歳)", min_value=1, max_value=120, value=30)
                    new_height = st.number_input("身長 (cm)", min_value=50.0, max_value=250.0, value=170.0, step=0.1)
                    new_target_weight = st.number_input("目標体重 (kg)", min_value=10.0, max_value=300.0, value=65.0, step=0.1)
                    
                reg_submit = st.form_submit_button("新規ユーザー登録")
                
                if reg_submit:
                    if new_id.strip() == "" or new_name.strip() == "":
                        st.error("ユーザーIDと氏名は必須項目です。")
                    else:
                        success, msg = register_user(
                            new_id.strip(),
                            new_name.strip(),
                            new_gender,
                            new_age,
                            new_height,
                            new_target_weight
                        )
                        if success:
                            st.success(msg)
                        else:
                            st.error(msg)
                            
        # --- サブタブ2：進捗監視ダッシュボード ---
        with tab_monitor:
            st.markdown("#### 登録クライアント一覧")
            df_users = get_users()
            
            if df_users.empty:
                st.info("登録されているユーザーがまだいません。「新規登録」タブから登録を行ってください。")
            else:
                st.dataframe(
                    df_users.rename(columns={
                        "user_id": "ユーザーID",
                        "name": "氏名",
                        "gender": "性別",
                        "age": "年齢(歳)",
                        "height": "身長(cm)",
                        "target_weight": "目標体重(kg)"
                    }),
                    use_container_width=True
                )
                
                st.markdown("---")
                st.markdown("#### クライアントの個別進捗監視")
                
                user_options = df_users.apply(lambda row: f"{row['name']} ({row['user_id']})", axis=1).tolist()
                selected_user_str = st.selectbox("進捗を確認するクライアントを選択", user_options)
                
                if selected_user_str:
                    selected_id = selected_user_str.split(" (")[-1][:-1]
                    sel_profile = get_user_by_id(selected_id)
                    
                    st.markdown(f"### 進捗詳細: **{sel_profile['name']}** さん (ID: {sel_profile['user_id']})")
                    
                    df_sel_data = get_user_data(selected_id)
                    
                    if df_sel_data.empty:
                        st.warning("このユーザーのデータはまだ記録されていません。")
                    else:
                        st.markdown("##### 📈 体重推移グラフ")
                        df_sel_chart = df_sel_data.copy()
                        df_sel_chart['date_str'] = df_sel_chart['date'].apply(lambda d: d.strftime("%m/%d"))
                        
                        fig = go.Figure()
                        fig.add_trace(
                            go.Scatter(
                                x=df_sel_chart['date_str'],
                                y=df_sel_chart['weight'],
                                name="体重",
                                line=dict(color="#feb80a", width=3), # レッドブルイエロー
                                mode="lines+markers"
                            )
                        )
                        fig.add_hline(
                            y=sel_profile["target_weight"],
                            line_dash="dash",
                            line_color="#ff002b", # レッドブルレッド
                            annotation_text=f"目標体重: {sel_profile['target_weight']} kg",
                            annotation_position="top left"
                        )
                        fig.update_layout(
                            paper_bgcolor="rgba(0,0,0,0)",
                            plot_bgcolor="rgba(0,0,0,0)",
                            font_color="#e2e8f0",
                            margin=dict(l=20, r=20, t=30, b=20)
                        )
                        fig.update_xaxes(gridcolor='#334155')
                        fig.update_yaxes(gridcolor='#334155')
                        st.plotly_chart(fig, use_container_width=True)
                        
                        st.markdown("##### 📋 直近10日分の記録履歴")
                        st.dataframe(
                            df_sel_data.sort_values('date', ascending=False).head(10).rename(columns={
                                "date": "日付",
                                "weight": "体重(kg)",
                                "intake_kcal": "摂取(kcal)",
                                "activity_level": "活動レベル",
                                "bmr": "基礎代謝(kcal)",
                                "tdee": "消費TDEE(kcal)"
                            }),
                            use_container_width=True
                        )
                        
                        st.markdown("##### 🧠 クライアント向け最新AIアドバイス（身代わり確認）")
                        st.caption("現在このユーザーの画面に自動的に表示されているフィードバック文面です。")
                        
                        n_logs = len(df_sel_data)
                        if n_logs < 7:
                            st.info(f"データ数が足りないため（現在 {n_logs}/7 日分）、クライアント画面にAIアドバイスはまだ表示されていません。")
                        else:
                            report = generate_weekly_report(df_sel_data, sel_profile)
                            if report:
                                st.markdown(f"""
                                <div class="ai-feedback">
                                    <div class="ai-feedback-header">
                                        <span>🧠 AI Trainer Advisor ({report['feedback_pattern']})</span>
                                    </div>
                                    <div class="ai-feedback-body">
                                        <b>分析モード:</b> {report['compare_mode']}<br>
                                        <b>体重推移:</b> {report['avg_weight_last']:.2f} kg → {report['avg_weight_this']:.2f} kg ({report['delta_weight']:+.2f} kg)<br>
                                        <b>平均摂取:</b> {int(report['avg_intake_this'])} kcal ｜ <b>平均消費(TDEE):</b> {int(report['avg_tdee_this'])} kcal (差分収支: {int(report['avg_net_this']):+,} kcal)<br>
                                        <hr style="border-top: 1px solid #1c365d; margin: 0.5rem 0;">
                                        <b>【アドバイス本文】</b><br>
                                        {report['feedback_message']}
                                    </div>
                                </div>
                                """, unsafe_allow_html=True)

        # --- サブタブ3：評価・デモデータ生成 ---
        with tab_demo:
            st.markdown("#### テスト・評価用デモデータの自動生成")
            st.caption("評価検証がスムーズに行えるよう、ワンクリックでデモユーザーと各AI判定パターンの14日分のログデータを自動生成します。")
            
            col_d1, col_d2, col_d3 = st.columns(3)
            
            with col_d1:
                st.markdown("**📁 パターンA (むくみ・停滞期)**")
                st.caption("アンダーカロリーなのに、水分貯留によって体重が落ちていない（微増）シミュレーション。")
                if st.button("デモユーザーAを生成"):
                    uid = "demo_pattern_a"
                    register_user(uid, "佐藤 優子", "女性", 28, 158.0, 50.0)
                    
                    today = datetime.date.today()
                    records = []
                    for i in range(13, -1, -1):
                        d = today - datetime.timedelta(days=i)
                        w = 55.0 - (13-i)*0.08 if i > 7 else 54.5 + (7-i)*0.10
                        intake = 1350
                        bmr = calculate_bmr(w, 158.0, 28, "女性")
                        tdee = bmr * 1.4
                        records.append({
                            "date": d.strftime("%Y-%m-%d"),
                            "weight": round(w, 2),
                            "intake_kcal": intake,
                            "activity_level": "中",
                            "bmr": round(bmr, 0),
                            "tdee": round(tdee, 0)
                        })
                    save_user_data(uid, pd.DataFrame(records))
                    st.success("デモユーザー 'demo_pattern_a' を生成しました！")
                    st.rerun()
                    
            with col_d2:
                st.markdown("**📁 パターンB (タイムラグ水分抜け)**")
                st.caption("オーバーカロリー状態なのに、一時的な水分抜けにより体重が減っているシミュレーション。")
                if st.button("デモユーザーBを生成"):
                    uid = "demo_pattern_b"
                    register_user(uid, "鈴木 一郎", "男性", 35, 175.0, 72.0)
                    
                    today = datetime.date.today()
                    records = []
                    for i in range(13, -1, -1):
                        d = today - datetime.timedelta(days=i)
                        w = 80.0 - (13-i)*0.18
                        intake = 2600
                        bmr = calculate_bmr(w, 175.0, 35, "男性")
                        tdee = bmr * 1.2
                        records.append({
                            "date": d.strftime("%Y-%m-%d"),
                            "weight": round(w, 2),
                            "intake_kcal": intake,
                            "activity_level": "低",
                            "bmr": round(bmr, 0),
                            "tdee": round(tdee, 0)
                        })
                    save_user_data(uid, pd.DataFrame(records))
                    st.success("デモユーザー 'demo_pattern_b' を生成しました！")
                    st.rerun()
                    
            with col_d3:
                st.markdown("**📁 パターンC (順調・計算通り)**")
                st.caption("アンダーカロリーで、体重も予測通りきれいに右肩下がりに減っている減量成功シミュレーション。")
                if st.button("デモユーザーCを生成"):
                    uid = "demo_pattern_c"
                    register_user(uid, "高橋 健太", "男性", 30, 180.0, 75.0)
                    
                    today = datetime.date.today()
                    records = []
                    for i in range(13, -1, -1):
                        d = today - datetime.timedelta(days=i)
                        w = 85.0 - (13-i)*0.20
                        intake = 1600
                        bmr = calculate_bmr(w, 180.0, 30, "男性")
                        tdee = bmr * 1.4
                        records.append({
                            "date": d.strftime("%Y-%m-%d"),
                            "weight": round(w, 2),
                            "intake_kcal": intake,
                            "activity_level": "中",
                            "bmr": round(bmr, 0),
                            "tdee": round(tdee, 0)
                        })
                    save_user_data(uid, pd.DataFrame(records))
                    st.success("デモユーザー 'demo_pattern_c' を生成しました！")
                    st.rerun()
                    
            st.markdown("---")
            if st.button("全データを完全に初期化する"):
                if not is_gsheet_active:
                    # CSVモード
                    if os.path.exists(USERS_CSV_PATH):
                        os.remove(USERS_CSV_PATH)
                    for f in os.listdir(DATA_DIR):
                        if f.startswith("data_") and f.endswith(".csv"):
                            os.remove(os.path.join(DATA_DIR, f))
                else:
                    # GSheetモード
                    try:
                        _, sheet = get_gsheet_client_and_sheet()
                        # usersシートをクリア
                        try:
                            ws_u = sheet.worksheet("users")
                            ws_u.clear()
                            ws_u.update('A1', [["user_id", "name", "gender", "age", "height", "target_weight"]])
                        except Exception:
                            pass
                        
                        # 個別のワークシートをすべて削除
                        for ws in sheet.worksheets():
                            if ws.title.startswith("data_"):
                                sheet.del_worksheet(ws)
                    except Exception as e:
                        st.error(f"スプレッドシートの初期化に失敗しました: {e}")
                        
                st.warning("すべてのデータを全削除して初期化しました。")
                st.rerun()
