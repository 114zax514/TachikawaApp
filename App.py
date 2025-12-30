import streamlit as st
import pandas as pd
import gspread
import json
from google.oauth2.service_account import Credentials
from datetime import datetime
import time

# --- ライブラリのインポート ---
# requirements.txt に "geopy", "folium", "streamlit-folium" を追加してください
try:
    from geopy.geocoders import Nominatim
    GEOPY_AVAILABLE = True
except ImportError:
    GEOPY_AVAILABLE = False

try:
    import folium
    from streamlit_folium import st_folium
    FOLIUM_AVAILABLE = True
except ImportError:
    FOLIUM_AVAILABLE = False

# --- 設定 ---
SHEET_NAME = "立川グルメ管理"
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

def get_connection():
    """GCP認証と接続"""
    if "gcp_service_account" not in st.secrets:
        st.error("Secretsの設定が見つかりません。")
        st.stop()
        
    if "info" not in st.secrets["gcp_service_account"]:
        st.error("Secretsに 'info' キーが見つかりません。")
        st.stop()

    secret_value = st.secrets["gcp_service_account"]["info"]
    creds_dict = None

    if isinstance(secret_value, dict):
        creds_dict = secret_value
    elif isinstance(secret_value, str):
        try:
            creds_dict = json.loads(secret_value, strict=False)
        except json.JSONDecodeError:
            st.error("JSON形式エラー。Secretsを確認してください。")
            st.stop()
            
    if creds_dict is None:
        st.stop()

    try:
        creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
        client = gspread.authorize(creds)
        return client
    except Exception as e:
        st.error(f"認証エラー: {e}")
        st.stop()

def main():
    st.set_page_config(page_title="立川グルメ", layout="centered")

    # --- 簡易ログイン ---
    if "authenticated" not in st.session_state:
        st.write("### 🔒 ログイン")
        password = st.text_input("パスワード", type="password")
        if "app_password" in st.secrets and password == st.secrets["app_password"]:
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            if password:
                st.error("パスワードが違います")
            st.stop()

    st.title("🍽️ 立川グルメマップ")

    # --- データ読み込み ---
    try:
        client = get_connection()
        sheet = client.open(SHEET_NAME).sheet1
        data = sheet.get_all_records()
        df = pd.DataFrame(data)
        
        # マスタのカラム順序を定義（スプレッドシートと合わせる）
        expected_columns = ["店名", "ジャンル", "エリア", "評価", "メモ", "住所", "登録日", "緯度", "経度"]

        if df.empty:
            df = pd.DataFrame(columns=expected_columns)

        for col in ["緯度", "経度", "住所"]:
            if col not in df.columns:
                df[col] = None

    except Exception as e:
        st.error(f"データ読み込みエラー: {e}")
        st.stop()

    # --- タブ構成 ---
    tab1, tab2 = st.tabs(["🗺️ マップ・一覧編集", "✏️ 新規登録"])

    # --- Tab 1: マップ表示 & 編集機能 ---
    with tab1:
        st.subheader("お店マップ")
        
        map_df = df.copy()
        map_df["緯度"] = pd.to_numeric(map_df["緯度"], errors='coerce')
        map_df["経度"] = pd.to_numeric(map_df["経度"], errors='coerce')
        map_df = map_df.dropna(subset=["緯度", "経度"])

        if FOLIUM_AVAILABLE and not map_df.empty:
            center_lat = map_df["緯度"].mean()
            center_lon = map_df["経度"].mean()
            
            m = folium.Map(location=[center_lat, center_lon], zoom_start=14)

            for _, row in map_df.iterrows():
                gmap_url = f"https://www.google.com/maps/search/?api=1&query={row['緯度']},{row['経度']}"
                
                popup_html = f"""
                <div style="font-family:sans-serif; min-width:150px;">
                    <b>{row['店名']}</b><br>
                    <span style="font-size:0.9em; color:gray;">{row['ジャンル']} / {row['エリア']}</span><br>
                    <br>
                    {str(row['メモ'])[:20]}...<br>
                    <a href="{gmap_url}" target="_blank" style="color:blue; text-decoration:underline;">Googleマップで見る</a>
                </div>
                """
                
                folium.Marker(
                    [row["緯度"], row["経度"]],
                    popup=folium.Popup(popup_html, max_width=200),
                    tooltip=row["店名"]
                ).add_to(m)

            st_folium(m, width="100%", height=400)
            
        elif not FOLIUM_AVAILABLE:
            st.warning("地図機能を使うには 'folium' と 'streamlit-folium' をインストールしてください。")
        else:
            st.info("📍 位置情報付きのデータがまだありません。")

        # --- 編集機能付き一覧リスト ---
        st.divider()
        st.subheader("お店リスト（編集・削除）")
        st.caption("表のセルを直接書き換えて修正できます。「削除」にチェックを入れて保存すると削除されます。")

        col1, col2 = st.columns([3, 1])
        with col1:
            search_query = st.text_input("キーワード検索", placeholder="店名・ジャンル・住所など")
        with col2:
            if st.button("データ再読み込み"):
                st.rerun()

        # 編集用のデータフレーム準備
        edit_df = df.copy()
        # 削除用チェックボックス列を先頭に追加
        edit_df.insert(0, "削除", False)
        
        # 検索フィルタリング
        if search_query:
            mask = edit_df.astype(str).apply(lambda x: x.str.contains(search_query, case=False)).any(axis=1)
            edit_df = edit_df[mask]

        # 編集用ウィジェットの表示
        edited_df = st.data_editor(
            edit_df,
            use_container_width=True,
            hide_index=True,
            num_rows="fixed", # 行の追加は新規登録タブで行う運用にする
            column_config={
                "削除": st.column_config.CheckboxColumn(
                    "削除",
                    help="チェックして保存ボタンを押すと削除されます",
                    default=False,
                ),
                "店名": st.column_config.TextColumn("店名", required=True),
                "ジャンル": st.column_config.SelectboxColumn(
                    "ジャンル",
                    options=["和食", "洋食", "中華", "イタリアン", "ラーメン", "カフェ", "居酒屋", "その他"]
                ),
                "エリア": st.column_config.SelectboxColumn(
                    "エリア",
                    options=["北口", "南口", "グリーンスプリングス", "ららぽーと", "駅ナカ", "その他"]
                ),
                "評価": st.column_config.NumberColumn("評価", min_value=1, max_value=5, format="%d"),
                "登録日": st.column_config.TextColumn("登録日", disabled=True), # 登録日は編集不可にする
                "緯度": st.column_config.NumberColumn("緯度", format="%.6f"),
                "経度": st.column_config.NumberColumn("経度", format="%.6f"),
            }
        )

        # 保存ボタン
        if st.button("変更を保存する", type="primary"):
            try:
                # 削除チェックがついている行を除外
                save_df = edited_df[~edited_df["削除"]].drop(columns=["削除"])
                
                # 検索中の編集かもしれないので、オリジナルのdfに対して更新をかける必要があるが、
                # 簡易実装として「現在表示されている全データ（検索絞り込み含む）」ではなく
                # 「検索で見えていないデータ」が消えないように注意が必要。
                # → Streamlitの仕様上、フィルタ後のedited_dfをそのまま保存するとフィルタ外のデータが消えるリスクがある。
                
                # 安全策: 
                # 1. 検索していない状態（全件表示）の時だけ保存を許可するか、
                # 2. ID管理をする必要がある。
                
                if search_query:
                    st.warning("⚠️ 検索絞り込み中は保存できません。検索ワードを空にして全件表示してから編集・保存してください。")
                else:
                    with st.spinner("スプレッドシートを更新中..."):
                        # マスタの列順序に合わせてデータを整理（予期せぬ列順序変更を防ぐ）
                        # 存在しない列があればNoneで埋めるなどが必要だが、基本はsave_dfを信じる
                        # ただし save_df の列順序が column_config 等で変わっている可能性も考慮し、
                        # expected_columns の順序で並べ直すのが安全
                        
                        final_save_df = save_df.reindex(columns=expected_columns)
                        
                        # NaNを空文字に置換（JSONシリアライズ対策）
                        final_save_df = final_save_df.fillna("")

                        # スプレッドシート更新（全データ書き換え）
                        # ヘッダー行 + データ行
                        update_values = [final_save_df.columns.tolist()] + final_save_df.values.tolist()
                        
                        sheet.clear()
                        sheet.update(range_name="A1", values=update_values)
                        
                        st.success("✅ 変更を保存しました！")
                        time.sleep(1)
                        st.rerun()

            except Exception as e:
                st.error(f"保存エラー: {e}")

    # --- Tab 2: 新規登録 ---
    with tab2:
        st.subheader("新しいお店を登録")
        
        with st.form("register_form", clear_on_submit=True):
            col_a, col_b = st.columns(2)
            with col_a:
                name = st.text_input("店名", placeholder="例：立川餃子センター")
                genre = st.selectbox("ジャンル", ["和食", "洋食", "中華", "イタリアン", "ラーメン", "カフェ", "居酒屋", "その他"])
            with col_b:
                area = st.selectbox("エリア", ["北口", "南口", "グリーンスプリングス", "ららぽーと", "駅ナカ", "その他"])
                rating = st.slider("評価", 1, 5, 3)
            
            comment = st.text_area("メモ", placeholder="おすすめメニューなど")
            
            st.markdown("---")
            st.write("📍 **位置情報**")
            
            address = st.text_input("住所 (またはキーワード)", placeholder="例: 立川市曙町2-1-1")

            with st.expander("詳細設定（緯度経度手動）"):
                col_lat, col_lon = st.columns(2)
                with col_lat: lat_input = st.text_input("緯度")
                with col_lon: lon_input = st.text_input("経度")
            
            submitted = st.form_submit_button("登録する", use_container_width=True)
            
            if submitted:
                if not name:
                    st.warning("店名は必須です！")
                else:
                    try:
                        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
                        lat_val = lat_input
                        lon_val = lon_input
                        
                        # 住所から緯度経度検索（改良版）
                        if GEOPY_AVAILABLE and not lat_val and address:
                            with st.spinner(f"「{address}」を検索中..."):
                                try:
                                    geolocator = Nominatim(user_agent="tachikawa_app")
                                    
                                    # 1回目：そのまま検索
                                    loc = geolocator.geocode(address)
                                    
                                    # 2回目：見つからない場合、「東京都立川市」を付与して再検索
                                    if not loc:
                                         # すでに含まれている場合は重複しないように
                                        search_word = address
                                        if "立川" not in search_word:
                                            search_word = "東京都立川市 " + search_word
                                        elif "東京都" not in search_word:
                                            search_word = "東京都 " + search_word
                                            
                                        if search_word != address:
                                            loc = geolocator.geocode(search_word)

                                    if loc:
                                        lat_val = loc.latitude
                                        lon_val = loc.longitude
                                        st.success(f"📍 位置が見つかりました: {loc.address}")
                                        time.sleep(1)
                                    else:
                                        st.warning("⚠️ 位置情報が見つかりませんでした。住所が正しいか確認するか、'立川駅'のようなランドマークを入力してみてください。")
                                except Exception as geo_err:
                                    st.error(f"位置検索エラー: {geo_err}")

                        # None対策
                        lat_val = lat_val if lat_val else ""
                        lon_val = lon_val if lon_val else ""
                        
                        # マスタの列順に合わせて追加
                        new_row = [name, genre, area, rating, comment, address, timestamp, lat_val, lon_val]
                        # カラム順序の不整合を防ぐため、新規登録時はdfのカラム定義を見るのがベストだが、
                        # ここでは expected_columns に合わせる
                        # ["店名", "ジャンル", "エリア", "評価", "メモ", "住所", "登録日", "緯度", "経度"]
                        
                        new_row_ordered = [
                            name, genre, area, rating, comment, address, timestamp, lat_val, lon_val
                        ]
                        
                        sheet.append_row(new_row_ordered)
                        
                        st.success(f"「{name}」を登録しました！")
                        st.balloons()
                    except Exception as e:
                        st.error(f"登録エラー: {e}")

if __name__ == "__main__":
    main()