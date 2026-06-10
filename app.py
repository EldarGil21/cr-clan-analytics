import streamlit as st
import requests
import pandas as pd
import datetime
import sqlite3

# Пытаемся импортировать pymongo для работы с облачной MongoDB Atlas
try:
    import pymongo
    HAS_MONGO = True
except ImportError:
    HAS_MONGO = False

# Обязательно первым Streamlit-командой в коде! (Фиксирует широкий полноэкранный формат)
st.set_page_config(page_title="Clash Royale Clan Analytics", layout="wide")

# =========================================================================
# 1. ОБЪЯВЛЕНИЕ ГЛОБАЛЬНЫХ ПЕРЕМЕННЫХ (Для предотвращения ошибок Pylance)
# =========================================================================
API_KEY = ""
CLAN_TAG = ""
ROLE_TRANSLATION = {
    "leader": "Лидер",
    "coLeader": "Соруководитель",
    "elder": "Старейшина",
    "member": "Участник"
}

# =========================================================================
# 2. ВСЕ ОПРЕДЕЛЕНИЯ ФУНКЦИЙ И КЭШ-ДЕКОРАТОРОВ
# =========================================================================

# Конвертация относительных уровней API в абсолютные (игровые) уровни 1-16
def get_absolute_level(card):
    max_level_api = card.get("maxLevel", 15)
    current_level_api = card.get("level", 15)
    old_system_max_levels = [15, 13, 10, 7, 5]
    if max_level_api in old_system_max_levels:
        return 15 - max_level_api + current_level_api
    else:
        return 16 - max_level_api + current_level_api

# Парсинг времени досрочного финиша
def parse_finish_time_msc(ft_str):
    if not ft_str:
        return None
    try:
        year = int(ft_str[0:4])
        month = int(ft_str[4:6])
        day = int(ft_str[6:8])
        hour = int(ft_str[9:11])
        minute = int(ft_str[11:13])
        
        utc_dt = datetime.datetime(year, month, day, hour, minute, tzinfo=datetime.timezone.utc)
        msc_tz = datetime.timezone(datetime.timedelta(hours=3))
        msc_dt = utc_dt.astimezone(msc_tz)
        return msc_dt.strftime('%d.%m.%Y в %H:%M по МСК')
    except:
        return ft_str

# Расчет дней оффлайна на основе lastSeen
def get_days_since_active(last_seen_str):
    if not last_seen_str:
        return 0
    try:
        year = int(last_seen_str[0:4])
        month = int(last_seen_str[4:6])
        day = int(last_seen_str[6:8])
        hour = int(last_seen_str[9:11])
        minute = int(last_seen_str[11:13])
        
        last_seen_dt = datetime.datetime(year, month, day, hour, minute, tzinfo=datetime.timezone.utc)
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        delta = now_utc - last_seen_dt
        return max(0, delta.days)
    except:
        return 0

# Расчет коэффициента щедрости
def calculate_generosity_ratio(row):
    don = row["donations"]
    rec = row["donationsReceived"]
    if rec == 0:
        return 1.0 if don > 0 else 0.0
    return round(don / rec, 2)

# Умный расчет КВ-рейтинга текущей недели
def get_weekly_prod_rating(row, expected):
    fame = row["fame"]
    donations = row["donations"]
    used = row["decksUsed"]
    missed = row["decks_missed"]
    eff = row["efficiency"]
    
    if used == 0:
        score = fame + (donations * 1.5) + (eff * 4)
    else:
        score = fame + (donations * 1.5) - (missed * 150) + (eff * 4)
        
    return max(0.0, score)

# Подключение к базе данных (MongoDB или SQLite)
def get_db_connection():
    if HAS_MONGO and "MONGO_URI" in st.secrets:
        try:
            client = pymongo.MongoClient(st.secrets["MONGO_URI"])
            db = client["clash_royale_analytics"]
            return "mongodb", db
        except:
            pass
    conn = sqlite3.connect("clan_history.db")
    return "sqlite", conn

# Инициализация структуры SQLite
def init_db():
    db_type, db = get_db_connection()
    if db_type == "sqlite":
        cursor = db.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS war_history (
                clan_tag TEXT,
                week_start TEXT,
                player_tag TEXT,
                player_name TEXT,
                role TEXT,
                donations INTEGER,
                fame INTEGER,
                decks_used INTEGER,
                decks_missed INTEGER,
                efficiency REAL,
                PRIMARY KEY (clan_tag, week_start, player_tag)
            )
        """)
        db.commit()
        db.close()

# Экспорт всей базы в CSV
def export_db_to_csv():
    db_type, db = get_db_connection()
    if db_type == "mongodb":
        collection = db["war_history"]
        data = list(collection.find({}, {"_id": 0}))
        df = pd.DataFrame(data)
    else:
        df = pd.read_sql_query("SELECT * FROM war_history", db)
        db.close()
    return df.to_csv(index=False).encode('utf-8-sig')

# Сохранение еженедельной статистики
def save_weekly_stats(clan_tag, week_start, df):
    db_type, db = get_db_connection()
    if db_type == "mongodb":
        collection = db["war_history"]
        for idx, row in df.iterrows():
            doc = {
                "clan_tag": clan_tag,
                "week_start": week_start,
                "player_tag": row["player_tag"],
                "player_name": row["name"],
                "role": row["role"],
                "donations": int(row["donations"]),
                "fame": int(row["fame"]),
                "decks_used": int(row["decksUsed"]),
                "decks_missed": int(row["decks_missed"]),
                "efficiency": float(row["efficiency"])
            }
            collection.update_one(
                {"clan_tag": clan_tag, "week_start": week_start, "player_tag": row["player_tag"]},
                {"$set": doc},
                upsert=True
            )
    else:
        cursor = db.cursor()
        for idx, row in df.iterrows():
            cursor.execute("""
                INSERT OR REPLACE INTO war_history 
                (clan_tag, week_start, player_tag, player_name, role, donations, fame, decks_used, decks_missed, efficiency)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                clan_tag, week_start, row["player_tag"], row["name"], row["role"],
                int(row["donations"]), int(row["fame"]), int(row["decksUsed"]),
                int(row["decks_missed"]), float(row["efficiency"])
            ))
        db.commit()
        db.close()

# Автосохранение в конце КВ
def auto_save_if_needed(clan_tag, week_start, df):
    db_type, db = get_db_connection()
    record_exists = False
    if db_type == "mongodb":
        collection = db["war_history"]
        if collection.find_one({"clan_tag": clan_tag, "week_start": week_start}):
            record_exists = True
    else:
        cursor = db.cursor()
        cursor.execute("SELECT 1 FROM war_history WHERE clan_tag = ? AND week_start = ? LIMIT 1", (clan_tag, week_start))
        if cursor.fetchone():
            record_exists = True
        db.close()
    if not record_exists:
        save_weekly_stats(clan_tag, week_start, df)
        st.toast(f"🔄 Результаты недели ({week_start}) успешно сохранены в историю автоматически!", icon="💾")

# Базовый API-запрос к прокси
def fetch_clash_data(endpoint, token):
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json"
    }
    url = f"https://proxy.royaleapi.dev/v1{endpoint}"
    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            return response.json()
        else:
            st.sidebar.error(f"Ошибка API: {response.status_code}")
            return None
    except Exception as e:
        st.sidebar.error(f"Ошибка соединения: {e}")
        return None

# Кэшируемый запрос деталей клана-оппонента
@st.cache_data(ttl=600)
def fetch_clan_details_cached(clan_tag, token):
    clean_tag = clan_tag.replace("#", "%23")
    return fetch_clash_data(f"/clans/{clean_tag}", token)

# Кэшируемый запрос данных игрока (прокачка)
@st.cache_data(ttl=300)
def fetch_player_data_cached(player_tag, token):
    clean_tag = player_tag.replace("#", "%23")
    return fetch_clash_data(f"/players/{clean_tag}", token)

# Кэшируемый запрос логов боев игрока
@st.cache_data(ttl=300)
def fetch_player_battlelog_cached(player_tag, token):
    clean_tag = player_tag.replace("#", "%23")
    return fetch_clash_data(f"/players/{clean_tag}/battlelog", token)


# =========================================================================
# 3. ИНИЦИАЛИЗАЦИЯ И КАЛЕНДАРНЫЕ РАСЧЕТЫ
# =========================================================================

init_db()

# Расчет дат КВ
now_utc = datetime.datetime.now(datetime.timezone.utc)
weekday = now_utc.weekday()

current_monday = now_utc - datetime.timedelta(days=weekday)
monday_war_start = datetime.datetime(
    current_monday.year, current_monday.month, current_monday.day, 
    9, 30, tzinfo=datetime.timezone.utc
)

if now_utc < monday_war_start:
    war_week_start_utc = monday_war_start - datetime.timedelta(days=7)
else:
    war_week_start_utc = monday_war_start

war_week_end_utc = war_week_start_utc + datetime.timedelta(days=7)

msc_tz = datetime.timezone(datetime.timedelta(hours=3))
start_msc = war_week_start_utc.astimezone(msc_tz)
end_msc = war_week_end_utc.astimezone(msc_tz)

week_interval_str = f"{start_msc.strftime('%d.%m.%Y %H:%M')} — {end_msc.strftime('%d.%m.%Y %H:%M')} по МСК"
st.markdown(f"### **🗓️ Игровая неделя КВ: {week_interval_str}**")

# =========================================================================
# 4. БОКОВАЯ ПАНЕЛЬ НАСТРОЕК (Переопределение глобальных переменных)
# =========================================================================

st.sidebar.header("Настройки подключения")
if "CLASH_API_KEY" in st.secrets:
    API_KEY = st.secrets["CLASH_API_KEY"]
else:
    API_KEY = st.sidebar.text_input("API Токен Supercell", type="password")

CLAN_TAG = st.sidebar.text_input(
    "Тег клана", 
    value="", 
    placeholder="#9PJ82CRC",
    help="Введите тег вашего клана, начиная с символа #"
)

# Отображение статуса базы в сайдбаре
db_status, _ = get_db_connection()
if db_status == "mongodb":
    st.sidebar.success("☁️ Подключено к MongoDB Atlas")
else:
    st.sidebar.info("💾 Хранение: локальная SQLite база")


# --- ОТОБРАЖЕНИЕ АДМИН-ПАНЕЛИ (ДЛЯ ПАТРИКА) ---
st.sidebar.markdown("---")
st.sidebar.subheader("🔒 Панель администратора")
admin_password = st.sidebar.text_input("Пароль доступа", type="password")
is_admin = admin_password == st.secrets.get("ADMIN_PASSWORD", "Patrick123")

if is_admin:
    st.sidebar.success("🔓 Доступ разрешен")
    
    # Бэкап
    try:
        csv_data = export_db_to_csv()
        st.sidebar.download_button(
            label="📥 Скачать бэкап истории (CSV)",
            data=csv_data,
            file_name="clan_war_history_backup.csv",
            mime="text/csv"
        )
    except:
        pass

    # Восстановление из CSV
    uploaded_file = st.sidebar.file_uploader("📤 Восстановить историю из CSV", type="csv")
    if uploaded_file is not None:
        if st.sidebar.button("⚙️ Начать импорт данных"):
            try:
                df_upload = pd.read_csv(uploaded_file, sep=None, engine='python', encoding='utf-8-sig')
                required_cols = ["clan_tag", "week_start", "player_tag", "player_name", "role", "donations", "fame", "decks_used", "decks_missed", "efficiency"]
                
                if not all(col in df_upload.columns for col in ["clan_tag", "week_start", "player_tag"]):
                    st.sidebar.error("Некорректный формат CSV!")
                else:
                    df_upload = df_upload[[col for col in required_cols if col in df_upload.columns]]
                    for col in required_cols:
                        if col not in df_upload.columns:
                            df_upload[col] = 0 if col in ["donations", "fame", "decks_used", "decks_missed"] else (0.0 if col == "efficiency" else "—")
                    
                    db_type, db = get_db_connection()
                    if db_type == "mongodb":
                        collection = db["war_history"]
                        for idx, row in df_upload.iterrows():
                            collection.update_one(
                                {"clan_tag": row["clan_tag"], "week_start": row["week_start"], "player_tag": row["player_tag"]},
                                {"$set": row.to_dict()},
                                upsert=True
                            )
                    else:
                        df_upload.to_sql("war_history", db, if_exists="append", index=False)
                        cursor = db.cursor()
                        cursor.execute("CREATE TABLE IF NOT EXISTS war_history_temp AS SELECT DISTINCT * FROM war_history")
                        cursor.execute("DROP TABLE war_history")
                        cursor.execute("ALTER TABLE war_history_temp RENAME TO war_history")
                        db.commit()
                        db.close()
                    st.sidebar.success("История успешно восстановлена!")
                    st.rerun()
            except Exception as e:
                st.sidebar.error(f"Ошибка импорта: {e}")

    # Сброс данных
    with st.sidebar.expander("🗑️ Сброс базы данных"):
        if st.button("Удалить все данные КВ"):
            st.cache_data.clear()
            db_type, db = get_db_connection()
            if db_type == "mongodb":
                db["war_history"].drop()
            else:
                cursor = db.cursor()
                cursor.execute("DROP TABLE IF EXISTS war_history")
                db.commit()
                db.close()
                init_db()
            st.success("База данных очищена!")
            st.rerun()


# =========================================================================
# 5. ОСНОВНАЯ БИЗНЕС-ЛОГИКА И ПОЛУЧЕНИЕ ДАННЫХ
# =========================================================================

if not API_KEY:
    st.info("🔑 Настройте токен в Secrets или введите на боковой панели.")
elif not CLAN_TAG:
    st.info("🛡️ Пожалуйста, введите тег вашего клана на боковой панели слева.")
else:
    clean_tag = CLAN_TAG.strip().replace("#", "%23")
    
    with st.spinner("Загрузка данных..."):
        clan_data = fetch_clash_data(f"/clans/{clean_tag}", API_KEY.strip())
        war_data = fetch_clash_data(f"/clans/{clean_tag}/currentriverrace", API_KEY.strip())
        
    if clan_data and war_data:
        war_clan = war_data.get("clan", {})
        finish_time_raw = war_clan.get("finishTime")
        finish_time_parsed = parse_finish_time_msc(finish_time_raw)
        
        # КРУПНОЕ ОТОБРАЖЕНИЕ НАЗВАНИЯ КЛАНА НА ГЛАВНОЙ
        clan_name = clan_data.get("name", "Неизвестный Клан")
        st.header(f"🛡️ Клан: {clan_name} ({CLAN_TAG})")
        
        if finish_time_parsed:
            st.info(f"🎉 Речная гонка завершена досрочно: {finish_time_parsed}")
            
        # --- ОБРАБОТКА ДАННЫХ ИГРОКОВ КЛАНА ---
        members = clan_data.get("memberList", [])
        if members:
            # Извлекаем также и 'lastSeen' для Детектора мертвых душ
            df_members = pd.DataFrame(members)[["tag", "name", "role", "donations", "donationsReceived", "trophies", "lastSeen"]]
            df_members = df_members.rename(columns={"lastSeen": "lastSeen_raw", "tag": "player_tag"})
            
            period_index = war_data.get("periodIndex", 0)
            day_of_week = period_index % 7
            if day_of_week >= 3:
                war_day_num = day_of_week - 2
                expected_decks = war_day_num * 4
            else:
                expected_decks = 16
            
            # --- ОБРАБОТКА ДАННЫХ ВОЙНЫ ---
            participants = war_clan.get("participants", [])
            if participants:
                df_war = pd.DataFrame(participants)[["tag", "fame", "decksUsed", "boatAttacks"]]
                df_war = df_war.rename(columns={"tag": "player_tag"})
            else:
                df_war = pd.DataFrame(columns=["player_tag", "fame", "decksUsed", "boatAttacks"])
                
            # Объединение
            df_merged = pd.merge(df_members, df_war, on="player_tag", how="left")
            df_merged["fame"] = df_merged["fame"].fillna(0).astype(int)
            df_merged["decksUsed"] = df_merged["decksUsed"].fillna(0).astype(int)
            df_merged["boatAttacks"] = df_merged["boatAttacks"].fillna(0).astype(int)
            
            # Расчет пропущенных за неделю
            df_merged["decks_missed"] = expected_decks - df_merged["decksUsed"]
            df_merged["decks_missed"] = df_merged["decks_missed"].apply(lambda x: x if x > 0 else 0)
            df_merged["efficiency"] = (df_merged["fame"] / df_merged["decksUsed"]).fillna(0).round(1)
            
            # Детектор оффлайна и Коэффициент щедрости
            df_merged["days_inactive"] = df_merged["lastSeen_raw"].apply(get_days_since_active)
            df_merged["net_donations"] = df_merged["donations"] - df_merged["donationsReceived"]
            df_merged["generosity"] = df_merged.apply(calculate_generosity_ratio, axis=1)
            
            # Расчет КВ-рейтинга
            df_merged["prod_rating"] = df_merged.apply(lambda r: get_weekly_prod_rating(r, expected_decks), axis=1)
            
            # --- ЗАПУСК АВТОМАТИЧЕСКОГО ЛАЗИ-СОХРАНЕНИЯ (По понедельникам после войны) ---
            auto_save_if_needed(CLAN_TAG.strip(), start_msc.strftime('%Y-%m-%d'), df_merged)
            
            # Подгружаем историю из выбранной базы данных
            db_type, db = get_db_connection()
            last_df = pd.DataFrame()
            try:
                if db_type == "mongodb":
                    collection = db["war_history"]
                    pipeline = [{"$match": {"clan_tag": CLAN_TAG.strip()}}, {"$group": {"_id": "$week_start"}}, {"$sort": {"_id": -1}}, {"$limit": 2}]
                    weeks = list(collection.aggregate(pipeline))
                    if len(weeks) >= 1:
                        last_week_date = weeks[0]["_id"]
                        last_data = list(collection.find({"clan_tag": CLAN_TAG.strip(), "week_start": last_week_date}, {"_id": 0}))
                        last_df = pd.DataFrame(last_data)
                else:
                    weeks_df = pd.read_sql_query("SELECT DISTINCT week_start FROM war_history WHERE clan_tag = ? ORDER BY week_start DESC LIMIT 2", db, params=(CLAN_TAG.strip(),))
                    if len(weeks_df) >= 1:
                        last_week_date = weeks_df["week_start"].iloc[0]
                        last_df = pd.read_sql_query("SELECT player_tag, fame, donations, decks_missed, efficiency FROM war_history WHERE clan_tag = ? AND week_start = ?", db, params=(CLAN_TAG.strip(), last_week_date))
                    db.close()
            except:
                pass
                
            # Расчет сдвига рангов, если история есть
            if not last_df.empty:
                df_merged = pd.merge(df_merged, last_df[["player_tag", "fame", "decks_missed"]], on="player_tag", how="left", suffixes=("", "_last"))
                df_merged["fame_last"] = df_merged["fame_last"].fillna(0)
                df_merged["decks_missed_last"] = df_merged["decks_missed_last"].fillna(0)
                
                trend_bonus = (df_merged["decks_missed_last"] == 0).astype(int) * 200
                progress_bonus = (df_merged["fame"] - df_merged["fame_last"]).apply(lambda x: x * 0.5 if x > 0 else 0)
                df_merged["prod_rating"] = df_merged["prod_rating"] + trend_bonus + progress_bonus
                
                last_df["last_prod_rating"] = last_df["fame"] + (last_df["donations"] * 1.5) - (last_df["decks_missed"] * 150) + (last_df["efficiency"] * 4)
                last_df["last_rank"] = last_df["last_prod_rating"].rank(ascending=False, method="min").astype(int)
                df_merged = pd.merge(df_merged, last_df[["player_tag", "last_rank"]], on="player_tag", how="left")
            else:
                df_merged["last_rank"] = None
                
            df_merged["prod_rating"] = df_merged["prod_rating"].clip(lower=0).round(1)
            df_merged["current_rank"] = df_merged["prod_rating"].rank(ascending=False, method="min").astype(int)
            
            # Функция вычисления сдвига позиций
            def get_rank_change(row):
                if "last_rank" not in row or pd.isna(row["last_rank"]):
                    return "Новичок"
                diff = int(row["last_rank"]) - int(row["current_rank"])
                if diff > 0:
                    return f"↑ {diff}"
                elif diff < 0:
                    return f"↓ {abs(diff)}"
                else:
                    return "—"
            
            df_merged["rank_change"] = df_merged.apply(get_rank_change, axis=1)
            
            # Определение значков дисциплины
            def get_status_badge(row):
                if row["decks_missed"] >= 4 or row["days_inactive"] >= 3:
                    return "🧨 "
                elif row["decks_missed"] > 0:
                    return "❗️ "
                return ""
                
            df_merged["name_display"] = df_merged.apply(lambda r: get_status_badge(r) + r["name"], axis=1)
            
            # Считаем количество недель активности в БД для Новичков
            db_type, db = get_db_connection()
            try:
                if db_type == "mongodb":
                    collection = db["war_history"]
                    pipeline = [{"$match": {"clan_tag": CLAN_TAG.strip()}}, {"$group": {"_id": "$player_tag", "weeks_count": {"$addToSet": "$week_start"}}}]
                    weeks_count_raw = list(collection.aggregate(pipeline))
                    weeks_count_df = pd.DataFrame([{"player_tag": x["_id"], "weeks_count": len(x["weeks_count"])} for x in weeks_count_raw])
                else:
                    weeks_count_df = pd.read_sql_query("SELECT player_tag, COUNT(DISTINCT week_start) as weeks_count FROM war_history WHERE clan_tag = ? GROUP BY player_tag", db, params=(CLAN_TAG.strip(),))
                    db.close()
                df_merged = pd.merge(df_merged, weeks_count_df, on="player_tag", how="left")
                df_merged["weeks_count"] = df_merged["weeks_count"].fillna(0).astype(int)
            except:
                df_merged["weeks_count"] = 0
            
            # Перевод ролей
            df_merged["role"] = df_merged["role"].map(ROLE_TRANSLATION).fillna(df_merged["role"])
            
            # --- ВЫВОД ВКЛАДОК ---
            tab_main, tab_dynamics, tab_prediction, tab_scout, tab_deck_ready, tab_help = st.tabs([
                "📋 Текущая статистика клана", 
                "📈 Динамика и Надежность", 
                "🔮 Прогноз и Рекомендации", 
                "🛰️ Военная разведка (Радар)",
                "⚔️ Аудит колод и Боевой лог",
                "📖 О проекте и Справка"
            ])
            
            # ==================== ВКЛАДКА 1: ОСНОВНАЯ СТАТИСТИКА ====================
            with tab_main:
                col1, col2 = st.columns(2)
                col1.metric("Всего игроков в клане", len(df_members), help="Количество участников в клане на текущий момент.")
                col2.metric("Слава текущего состава", int(df_merged["fame"].sum()), help="Точная сумма славы текущего состава.")
                
                st.write("---")
                
                # MVP И ТОП-3
                st.subheader("👑 Зал славы КВ (Итоги недели)")
                df_sorted_prod = df_merged.sort_values(by="prod_rating", ascending=False)
                
                if not df_sorted_prod.empty:
                    mvp_cols = st.columns(3)
                    
                    # 1 Место (MVP)
                    mvp_player = df_sorted_prod.iloc[0]
                    with mvp_cols[0]:
                        st.info(f"🏆 **1 Место (MVP Недели)**\n\n**{mvp_player['name_display']}**\n\nРейтинг: **{mvp_player['prod_rating']}** ({mvp_player['rank_change']})")
                    
                    # 2 Место
                    if len(df_sorted_prod) > 1:
                        top2_player = df_sorted_prod.iloc[1]
                        with mvp_cols[1]:
                            st.success(f"🥈 **2 Место**\n\n**{top2_player['name_display']}**\n\nРейтинг: **{top2_player['prod_rating']}** ({top2_player['rank_change']})")
                    
                    # 3 Место
                    if len(df_sorted_prod) > 2:
                        top3_player = df_sorted_prod.iloc[2]
                        with mvp_cols[2]:
                            st.warning(f"🥉 **3 Место**\n\n**{top3_player['name_display']}**\n\nРейтинг: **{top3_player['prod_rating']}** ({top3_player['rank_change']})")
                
                st.write("---")
                
                # Кнопка ручного сохранения (видна только админу)
                if is_admin:
                    if st.button("💾 Сохранить итоги текущей недели в историю базы данных"):
                        try:
                            save_weekly_stats(CLAN_TAG.strip(), start_msc.strftime('%Y-%m-%d'), df_merged)
                            st.success(f"Данные за игровую неделю успешно зафиксированы в истории!")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Ошибка сохранения: {e}")
                
                # Нарушители и Новички
                st.header("🚨 Дисциплина и Кадры")
                lazy_some = df_merged[(df_merged["decks_missed"] > 0) & (df_merged["decksUsed"] > 0)]
                lazy_week = df_merged[df_merged["decksUsed"] == 0]
                dead_souls = df_merged[df_merged["days_inactive"] >= 3]
                newcomers = df_merged[df_merged["weeks_count"] <= 2]
                
                t1, t2, t3, t4 = st.tabs([
                    "Пропустили атаки КВ (Добить до 24:00 МСК)", 
                    "Вообще не воевали (0 атак)", 
                    "💤 Детектор мертвых душ (оффлайн >= 3д)",
                    "🆕 Новички клана (Неполная статистика <= 2 недель)"
                ])
                with t1:
                    if not lazy_some.empty:
                        st.dataframe(
                            lazy_some[["name_display", "role", "decksUsed", "decks_missed", "fame"]]
                            .rename(columns={"name_display": "Имя", "role": "Роль", "decksUsed": "Сыграно колод", "decks_missed": "Пропущено колод", "fame": "Слава"}),
                            use_container_width=True
                        )
                    else:
                        st.success("Отлично! Все воюющие сыграли все доступные колоды.")
                with t2:
                    if not lazy_week.empty:
                        st.dataframe(
                            lazy_week[["name_display", "role", "donations", "trophies"]]
                            .rename(columns={"name_display": "Имя", "role": "Роль", "donations": "Донаты", "trophies": "Кубки"}),
                            use_container_width=True
                        )
                    else:
                        st.success("Все участники клана воевали на этой неделе.")
                with t3:
                    if not dead_souls.empty:
                        st.write("Список игроков, которые давно не заходили в Clash Royale:")
                        st.dataframe(
                            dead_souls[["name_display", "role", "days_inactive", "donations"]]
                            .rename(columns={"name_display": "Имя", "role": "Роль", "days_inactive": "Дней оффлайна", "donations": "Донаты за неделю"})
                            .sort_values(by="Дней оффлайна", ascending=False),
                            use_container_width=True
                        )
                    else:
                        st.success("Прекрасно! Все участники заходили в игру в течение последних 48 часов.")
                with t4:
                    if not newcomers.empty:
                        st.write("Список участников, по которым накоплено не более 2 недель статистики в базе данных:")
                        st.dataframe(
                            newcomers[["name_display", "role", "weeks_count", "trophies"]]
                            .rename(columns={"name_display": "Имя", "role": "Роль", "weeks_count": "Недель КВ в истории", "trophies": "Кубки"}),
                            use_container_width=True
                        )
                    else:
                        st.success("Все участники клана имеют полную накопленную статистику (3 и более недель).")
                
                st.write("---")
                
                # Полная таблица
                st.header("📋 Полная статистика клана")
                st.info("Указатели статуса: 🧨 — В группе риска. ❗️ — Под повышенным контролем (есть пропуски за текущий день).")
                search_query = st.text_input("Поиск игрока по имени")
                if search_query:
                    df_to_show = df_merged[df_merged["name_display"].str.contains(search_query, case=False)]
                else:
                    df_to_show = df_merged
                    
                sort_by = st.selectbox("Сортировать таблицу по:", ["Рейтинг продуктивности КВ", "Очки славы (Fame)", "Донаты", "Чистый баланс донатов", "Пропущено колод за неделю", "Эффективность атак", "Коэффициент щедрости"])
                sort_mapping = {
                    "Рейтинг продуктивности КВ": ("prod_rating", False),
                    "Очки славы (Fame)": ("fame", False), "Донаты": ("donations", False),
                    "Чистый баланс донатов": ("net_donations", False),
                    "Пропущено колод за неделю": ("decks_missed", False), "Эффективность атак": ("efficiency", False),
                    "Коэффициент щедрости": ("generosity", False)
                }
                col_name, ascending = sort_mapping[sort_by]
                df_to_show = df_to_show.sort_values(by=col_name, ascending=ascending)
                
                df_display = df_to_show.rename(columns={
                    "name_display": "Имя", "role": "Роль", "donations": "Донаты (отдал)", "donationsReceived": "Донаты (получил)",
                    "trophies": "Кубки", "fame": "Очки славы КВ", "decksUsed": "Всего колод сыграно", 
                    "decks_missed": "Пропущено колод за неделю", "efficiency": "Ср. очков за бой", 
                    "days_inactive": "Дней оффлайна", "generosity": "Коэф. щедрости",
                    "net_donations": "Баланс донатов (Отдал-Получил)", "prod_rating": "Рейтинг продуктивности",
                    "rank_change": "Сдвиг ранга"
                })
                st.dataframe(df_display[["Имя", "Роль", "Кубки", "Рейтинг продуктивности", "Сдвиг ранга", "Донаты (отдал)", "Баланс донатов (Отдал-Получил)", "Коэф. щедрости", "Очки славы КВ", "Всего колод сыграно", "Пропущено колод за неделю", "Ср. очков за бой", "Дней оффлайна"]], use_container_width=True)
            
            # ==================== ВКЛАДКА 2: ДИНАМИКА И НАДЕЖНОСТЬ ====================
            with tab_dynamics:
                st.header("📈 Динамика и Индекс надежности участников")
                
                # Выбор игрока из БД
                db_type, db = get_db_connection()
                try:
                    if db_type == "mongodb":
                        collection = db["war_history"]
                        db_players = pd.DataFrame(list(collection.find({"clan_tag": CLAN_TAG.strip()}, {"player_name": 1, "player_tag": 1, "_id": 0})))
                        if not db_players.empty:
                            db_players = db_players.drop_duplicates()
                    else:
                        db_players = pd.read_sql_query("SELECT DISTINCT player_name, player_tag FROM war_history WHERE clan_tag = ?", db, params=(CLAN_TAG.strip(),))
                        db.close()
                except:
                    db_players = pd.DataFrame()
                
                if not db_players.empty:
                    player_options = db_players["player_name"].tolist()
                    selected_player_name = st.selectbox("Выберите игрока для анализа:", player_options)
                    selected_tag = db_players[db_players["player_name"] == selected_player_name]["player_tag"].values[0]
                    
                    db_type, db = get_db_connection()
                    if db_type == "mongodb":
                        collection = db["war_history"]
                        player_history = pd.DataFrame(list(collection.find({"player_tag": selected_tag, "clan_tag": CLAN_TAG.strip()}, {"_id": 0}))).sort_values(by="week_start", ascending=True)
                    else:
                        player_history = pd.read_sql_query("SELECT week_start, fame, donations, decks_used, decks_missed, efficiency FROM war_history WHERE player_tag = ? AND clan_tag = ? ORDER BY week_start ASC", db, params=(selected_tag, CLAN_TAG.strip()))
                        db.close()
                    
                    if not player_history.empty:
                        total_weeks_recorded = len(player_history)
                        weeks_with_perfect_attendance = len(player_history[player_history["decks_missed"] == 0])
                        consistency_score = round((weeks_with_perfect_attendance / total_weeks_recorded) * 100, 1)
                        
                        st.subheader(f"Активность игрока: {selected_player_name}")
                        st.metric(
                            label="Индекс надежности игрока (Consistency Score)", 
                            value=f"{consistency_score}%", 
                            help="Процент игровых недель КВ, которые данный участник отыграл полностью без единого пропуска колод (16/16 атак)."
                        )
                        
                        col_g1, col_g2 = st.columns(2)
                        with col_g1:
                            st.write("**Динамика славы в КВ**")
                            st.line_chart(player_history.set_index("week_start")["fame"])
                        with col_g2:
                            st.write("**Динамика еженедельных донатов**")
                            st.line_chart(player_history.set_index("week_start")["donations"])
                    else:
                        st.warning("Нет истории по выбранному игроку.")
                else:
                    st.info("История пуста. База начнет пополняться по понедельникам.")
                
                st.write("---")
                
                # ИСТОРИЧЕСКАЯ СВОДКА ПО ПРОШЛЫМ НЕДЕЛЯМ
                st.subheader("📅 Историческая сводка клана по неделям")
                db_type, db = get_db_connection()
                try:
                    if db_type == "mongodb":
                        collection = db["war_history"]
                        pipeline = [
                            {"$match": {"clan_tag": CLAN_TAG.strip()}},
                            {"$group": {
                                "_id": "$week_start",
                                "Участников воевало": {"$sum": 1},
                                "Суммарная слава текущих участников": {"$sum": "$fame"},
                                "Суммарные донаты за неделю": {"$sum": "$donations"},
                                "Суммарно пропусков колод": {"$sum": "$decks_missed"}
                            }},
                            {"$sort": {"_id": -1}}
                        ]
                        summary_raw = list(collection.aggregate(pipeline))
                        history_summary = pd.DataFrame(summary_raw).rename(columns={"_id": "Неделя КВ"})
                    else:
                        history_summary = pd.read_sql_query("""
                            SELECT 
                                week_start as "Неделя КВ",
                                COUNT(DISTINCT player_tag) as "Участников воевало",
                                SUM(fame) as "Суммарная слава текущих участников",
                                SUM(donations) as "Суммарные донаты за неделю",
                                SUM(decks_missed) as "Суммарно пропусков колод"
                            FROM war_history 
                            WHERE clan_tag = ?
                            GROUP BY week_start
                            ORDER BY week_start DESC
                        """, db, params=(CLAN_TAG.strip(),))
                        db.close()
                    
                    if not history_summary.empty:
                        st.write("Суммарный отчет о результатах вашего клана за все сохраненные недели КВ:")
                        st.dataframe(history_summary, use_container_width=True)
                    else:
                        st.info("ℹ️ История пуста. Сводные еженедельные отчеты появятся сразу после первого автоматического или ручного сохранения результатов недели.")
                except Exception as e:
                    st.error(f"Не удалось загрузить историческую сводку: {e}")
            
            # ==================== ВКЛАДКА 3: ПРОГНОЗ И РЕКОМЕНДАЦИИ ====================
            with tab_prediction:
                st.header("🔮 Прогнозы и Рекомендации")
                
                db_type, db = get_db_connection()
                try:
                    if db_type == "mongodb":
                        collection = db["war_history"]
                        history_raw = list(collection.find({"clan_tag": CLAN_TAG.strip()}, {"_id": 0}))
                        all_history = pd.DataFrame(history_raw)
                    else:
                        all_history = pd.read_sql_query("SELECT player_tag, player_name, role, fame, donations, decks_missed, efficiency FROM war_history WHERE clan_tag = ?", db, params=(CLAN_TAG.strip(),))
                        db.close()
                except:
                    all_history = pd.DataFrame()
                
                if not all_history.empty:
                    analysis_df = all_history.groupby("player_tag").agg({
                        "player_name": "first",
                        "role": "first",
                        "fame": "mean",
                        "donations": "mean",
                        "decks_missed": "mean",
                        "efficiency": "mean"
                    }).reset_index()
                    
                    # ПРИНУДИТЕЛЬНЫЙ ПЕРЕВОД РОЛЕЙ ИЗ БАЗЫ ДАННЫХ ДЛЯ ПРОГНОЗОВ
                    analysis_df["role"] = analysis_df["role"].map(ROLE_TRANSLATION).fillna(analysis_df["role"])
                    
                    analysis_df["decks_missed"] = analysis_df["decks_missed"].round(0).astype(int)
                    analysis_df["expected_fame_next_week"] = analysis_df["fame"].round(0).astype(int)
                    analysis_df["expected_donations_next_week"] = analysis_df["donations"].round(0).astype(int)
                    analysis_df["efficiency"] = analysis_df["efficiency"].round(1)
                    
                    def generate_cr_recommendation(row):
                        role = str(row["role"]).strip()
                        fame = row["fame"]
                        donations = row["donations"]
                        misses = row["decks_missed"]
                        efficiency = row["efficiency"]
                        
                        is_leader = role in ["Лидер", "leader", "Глава", "Глава клана"]
                        is_coleader = role in ["Соруководитель", "coLeader"]
                        is_elder = role in ["Старейшина", "elder"]
                        is_member = role in ["Участник", "member"]
                        
                        if is_leader:
                            if fame < 1000 or donations < 50 or misses > 2:
                                return "📣 Глава клана (Совет: быть активнее)"
                            return "👑 Глава клана (Стабилен)"
                            
                        if is_coleader:
                            if fame < 1000 or donations < 50 or misses > 2:
                                return "📣 Соруководитель (Совет: быть активнее)"
                            return "⭐ Соруководитель (Стабилен)"
                            
                        if is_elder:
                            if fame < 1000 or donations < 50 or misses >= 4:
                                return "📉 Рекомендация: Понизить до Участника"
                            elif fame >= 2800 and efficiency >= 200 and misses == 0:
                                return "🌟 Рекомендация: Повысить до Соруководителя"
                            return "🛡️ Старейшина (Стабилен)"
                            
                        if is_member:
                            if fame < 1000 and donations < 50 and efficiency < 100:
                                return "❌ Рекомендация: Исключить из клана"
                            elif (1000 <= fame < 1500) or misses > 0:
                                return "👀 На рассмотрении (Повышенный контроль)"
                            elif fame >= 2200 and donations >= 100 and misses == 0:
                                return "🎖️ Рекомендация: Повысить до Старейшины"
                            return "✅ Участник (Стабилен)"
                            
                        return "Стабилен"
                            
                    analysis_df["Автоматическая рекомендация"] = analysis_df.apply(generate_cr_recommendation, axis=1)
                    
                    st.write("Сводная прогностическая модель на основе накопленной истории (Пропуски рассчитываются по количеству **колод**, 1 день пропуска = 4 колоды):")
                    display_pred = analysis_df.rename(columns={
                        "player_name": "Имя", "role": "Роль",
                        "expected_fame_next_week": "Прогноз славы КВ",
                        "expected_donations_next_week": "Прогноз донатов",
                        "decks_missed": "Ср. пропусков колод в неделю",
                        "efficiency": "Ср. эффективность"
                    })
                    st.dataframe(
                        display_pred[["Имя", "Роль", "Прогноз славы КВ", "Прогноз донатов", "Ср. пропусков колод в неделю", "Ср. эффективность", "Автоматическая рекомендация"]]
                        .sort_values(by="Прогноз славы КВ", ascending=False),
                        use_container_width=True
                    )
                else:
                    st.info("Для построения прогнозов требуется накопить историю.")
            
            # ==================== ВКЛАДКА 4: ВОЕННАЯ РАЗВЕДКА (Радар) ====================
            with tab_scout:
                st.header("🛰️ Военная разведка КВ")
                
                clans_in_race = war_data.get("clans", [])
                if clans_in_race:
                    st.subheader("🏁 Радар речной гонки соперников")
                    radar_data = []
                    for c in clans_in_race:
                        finish_time = c.get("finishTime")
                        status_str = "Финишировал" if finish_time else "В гонке"
                        radar_data.append({
                            "Название клана": c.get("name"),
                            "Очки славы": c.get("fame"),
                            "Статус": status_str,
                            "Финиш (МСК)": parse_finish_time_msc(finish_time) if finish_time else "—"
                        })
                    df_radar = pd.DataFrame(radar_data).sort_values(by="Очки славы", ascending=False)
                    st.bar_chart(df_radar.set_index("Название клана")["Очки славы"])
                    
                    st.write("---")
                    
                    # ИСТОРИЧЕСКАЯ ТАБЛИЦА БЕЗ «СРЕДНЕЙ СКОРОСТИ» (Удалена колонка)
                    st.subheader("🕵️ Сводные разведданные по соперникам")
                    
                    with st.spinner("Сбор разведданных с серверов Supercell..."):
                        opponent_reports = []
                        for c in clans_in_race:
                            c_tag = c.get("tag")
                            c_name = c.get("name")
                            c_fame = c.get("fame")
                            
                            details = fetch_clan_details_cached(c_tag, API_KEY.strip())
                            if details:
                                opp_members_count = details.get("members", 0)
                                opp_trophies = details.get("clanScore", 0)
                                opp_donations = details.get("donationsPerWeek", 0)
                            else:
                                opp_members_count = "—"
                                opp_trophies = "—"
                                opp_donations = "—"
                                
                            opponent_reports.append({
                                "Клан": c_name,
                                "Тег": c_tag,
                                "Суммарная слава КВ": c_fame,
                                "Участников": opp_members_count,
                                "Кубки клана (Трофеи)": opp_trophies,
                                "Донаты за неделю": opp_donations
                            })
                            
                        df_scout_opp = pd.DataFrame(opponent_reports).sort_values(by="Суммарная слава КВ", ascending=False)
                        st.dataframe(df_scout_opp, use_container_width=True)
                else:
                    st.warning("Данные по группе речной гонки КВ недоступны.")
                    
            # ==================== ВКЛАДКА 5: АУДИТ КОЛОД И БОЕВОЙ ЛОГ ====================
            with tab_deck_ready:
                st.header("⚔️ Аудит прокачки карт и Анализ личных боев")
                st.write("Выберите любого игрока клана для проведения моментального технического аудита:")
                
                player_names = df_members["name"].tolist()
                target_player_name = st.selectbox("Выберите игрока для аудита прокачки и логов КВ:", player_names)
                
                target_tag = df_members[df_members["name"] == target_player_name]["player_tag"].values[0]
                
                if target_tag:
                    col_p1, col_p2 = st.columns(2)
                    
                    # Аудит прокачки карт
                    with col_p1:
                        st.subheader("🃏 Боеготовность колод к КВ")
                        player_profile = fetch_player_data_cached(target_tag, API_KEY.strip())
                        
                        if player_profile:
                            cards = player_profile.get("cards", [])
                            
                            lvl_13 = sum(1 for c in cards if get_absolute_level(c) == 13)
                            lvl_14 = sum(1 for c in cards if get_absolute_level(c) == 14)
                            lvl_15 = sum(1 for c in cards if get_absolute_level(c) == 15)
                            lvl_16 = sum(1 for c in cards if get_absolute_level(c) == 16)
                            
                            total_cards = len(cards)
                            high_level_cards = lvl_14 + lvl_15 + lvl_16
                            
                            st.write(f"**Всего карт в коллекции:** {total_cards}")
                            st.write(f"🔹 **16 уровень:** {lvl_16} шт.")
                            st.write(f"🔹 **15 уровень:** {lvl_15} шт.")
                            st.write(f"🔹 **14 уровень:** {lvl_14} шт.")
                            st.write(f"🔹 **13 уровень:** {lvl_13} шт.")
                            
                            st.write("---")
                            st.write("**Готовность к боям в Легендарной лиге (Учет 14-16 уровней):**")
                            if high_level_cards >= 32:
                                st.success(f"🔥 Отличная боеготовность! Найдено {high_level_cards}/32 карт 14-16 уровней. Игрок может собрать 4 полноценные сильные колоды.")
                            elif high_level_cards >= 16:
                                st.warning(f"⚠️ Средняя боеготовность. Найдено {high_level_cards}/32 карт 14-16 уровней. Часть колод в КВ будет иметь просадки по уровням.")
                            else:
                                st.error(f"❌ Слабая боеготовность! Найдено всего {high_level_cards}/32 карт 14-16 уровней. В КВ игрок будет испытывать сильные трудности из-за недопрокачки.")
                        else:
                            st.error("Не удалось загрузить детальный профиль игрока.")
                            
                    # Анализ боевого лога
                    with col_p2:
                        st.subheader("🎮 Анализ последних матчей в КВ")
                        battlelog = fetch_player_battlelog_cached(target_tag, API_KEY.strip())
                        
                        if battlelog:
                            war_battles = [b for b in battlelog if b.get("type", "") in ["clanWarWar", "clanWarCollection"]]
                            
                            if war_battles:
                                total_war_games = len(war_battles)
                                wins = 0
                                for b in war_battles:
                                    team = b.get("team", [{}])
                                    opponent = b.get("opponent", [{}])
                                    if team and opponent:
                                        team_crowns = team[0].get("crowns", 0)
                                        opp_crowns = opponent[0].get("crowns", 0)
                                        if team_crowns > opp_crowns:
                                            wins += 1
                                            
                                winrate = round((wins / total_war_games) * 100, 1)
                                
                                st.write(f"**Найдено боев КВ в логе:** {total_war_games} шт.")
                                st.write(f"🏆 **Побед:** {wins}")
                                st.write(f"💀 **Поражений:** {total_war_games - wins}")
                                st.metric("Винрейт игрока в КВ (Win Rate)", f"{winrate}%", help="Процент побед игрока на основе лога его последних матчей в Клановых Войнах.")
                            else:
                                st.info("В логе последних 25 матчей игрока не найдено боев КВ. Вероятно, он давно не воевал.")
                        else:
                            st.error("Не удалось получить боевой лог игрока.")
            
            # ==================== ВКЛАДКА 6: О ПРОЕКТЕ И СПРАВКА ====================
            with tab_help:
                st.header("📖 Документация и Инструкция v1.1")
                
                # Список нововведений версии 1.1 (Changelog)
                with st.expander("🛠️ Список нововведений веб-приложения (Версия 1.1)"):
                    st.write("""
                    **Что нового в версии 1.1 (после первого коммита на GitHub):**
                    
                    *   **☁️ Поддержка MongoDB Atlas:** Внедрена гибридная архитектура БД. При указании `MONGO_URI` в Secrets программа автоматически и навсегда переносит накопление истории в защищенное облако MongoDB.
                    *   **🔐 Панель администратора:** Защищенная паролем панель для управления бэкапами и сбросом базы. Обычные пользователи больше не увидят технические кнопки.
                    *   **🔄 Автоматическое КВ-сохранение (Lazy Auto-save):** Больше не нужно нажимать кнопку вручную в конце недели. Система сама тихо запишет итоги недели в базу при первом заходе любого пользователя на сайт в понедельник-среду.
                    *   **📅 Историческая сводка клана:** Новая секция с детальными суммарными отчетами (какая неделя КВ, сколько людей воевало, общая слава и донаты).
                    *   **👑 Зал славы (MVP и топ-3 игрока недели):** Автоматическая выборка лучших игроков клана на основе нового рейтинга продуктивности.
                    *   **🧮 Алгоритм КВ-рейтинга:** Формула стала умнее. Игроки с нулевой боевой активностью больше не штрафуются досрочно, если КВ только началось (рейтинг рассчитывается честно по донатам).
                    *   **🚨 Дисциплинарные статусы в таблице:** Добавлены автоматические маркеры (🧨 — группа риска, ❗️ — повышенный контроль для тех, кому нужно добить бои до полуночи).
                    *   **🆕 Новички клана:** Порог сокращен до 2 недель активности (вместо 3) для защиты новобранцев.
                    *   **🛠️ Совместимость с Microsoft Excel:** Добавлен экспорт с сигнатурой BOM и адаптивный импорт (pandas сам понимает, если Excel заменил запятые на точки с запятой).
                    *   **🗑️ Опасная зона (Сброс):** Кнопка очистки поврежденной базы и сброса кэша Streamlit в один клик.
                    """)
                
                try:
                    with open("README.md", "r", encoding="utf-8") as f:
                        readme_content = f.read()
                    st.markdown(readme_content)
                except FileNotFoundError:
                    st.warning("Файл README.md не найден в корневом каталоге проекта. Создайте его для корректного отображения справки на сайте.")
        else:
            st.warning("Не удалось получить список участников клана. Возможно, тег введен неверно.")
