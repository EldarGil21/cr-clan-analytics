import streamlit as st
import requests
import pandas as pd
import datetime
import sqlite3

# Настройки страницы Streamlit
st.set_page_config(page_title="Clash Royale Clan Analytics", layout="wide")
st.title("Clash Royale Clan Analytics 📊")

# --- ИНИЦИАЛИЗАЦИЯ ЛОКАЛЬНОЙ БАЗЫ ДАННЫХ (SQLite) ---
def init_db():
    conn = sqlite3.connect("clan_history.db")
    cursor = conn.cursor()
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
    conn.commit()
    conn.close()

init_db()

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---

# Конвертация относительных уровней API в абсолютные (игровые) уровни 1-16
def get_absolute_level(card):
    max_level_api = card.get("maxLevel", 15)
    current_level_api = card.get("level", 15)
    
    # Список максимальных значений в API для старой шкалы (где макс. уровень в игре был 15)
    old_system_max_levels = [15, 13, 10, 7, 5]
    
    if max_level_api in old_system_max_levels:
        # Шкала с максимальным уровнем 15
        return 15 - max_level_api + current_level_api
    else:
        # Новая шкала с максимальным уровнем 16
        return 16 - max_level_api + current_level_api

# Парсинг времени досрочного финиша (перевод из UTC в МСК)
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

# Расчет коэффициента щедрости без аномальных перекосов
def calculate_generosity_ratio(row):
    don = row["donations"]
    rec = row["donationsReceived"]
    if rec == 0:
        return 1.0 if don > 0 else 0.0
    return round(don / rec, 2)

# Боковая панель
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

ROLE_TRANSLATION = {
    "leader": "Лидер",
    "coLeader": "Соруководитель",
    "elder": "Старейшина",
    "member": "Участник"
}

# --- КОРРЕКТНЫЙ РАСЧЕТ ИГРОВОЙ НЕДЕЛИ КВ ПО МОСКОВСКОМУ ВРЕМЕНИ ---
now_utc = datetime.datetime.now(datetime.timezone.utc)
weekday = now_utc.weekday()  # 0: Пн, ..., 6: Вс

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

# Функция запроса данных
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

# Кэшируемые запросы
@st.cache_data(ttl=600)
def fetch_clan_details_cached(clan_tag, token):
    clean_tag = clan_tag.replace("#", "%23")
    return fetch_clash_data(f"/clans/{clean_tag}", token)

@st.cache_data(ttl=300)
def fetch_player_data_cached(player_tag, token):
    clean_tag = player_tag.replace("#", "%23")
    return fetch_clash_data(f"/players/{clean_tag}", token)

@st.cache_data(ttl=300)
def fetch_player_battlelog_cached(player_tag, token):
    clean_tag = player_tag.replace("#", "%23")
    return fetch_clash_data(f"/players/{clean_tag}/battlelog", token)


# --- ОСНОВНАЯ ЛОГИКА ---
if not API_KEY:
    st.info("🔑 Пожалуйста, настройте API-токен Supercell на боковой панели или в файле secrets.toml.")
elif not CLAN_TAG:
    st.info("🛡️ Пожалуйста, введите тег вашего клана на боковой панели слева (например, #9PJ82CRC), чтобы начать анализ.")
else:
    clean_tag = CLAN_TAG.strip().replace("#", "%23")
    
    with st.spinner("Загрузка данных из Clash Royale API..."):
        clan_data = fetch_clash_data(f"/clans/{clean_tag}", API_KEY.strip())
        war_data = fetch_clash_data(f"/clans/{clean_tag}/currentriverrace", API_KEY.strip())
        
    if clan_data and war_data:
        war_clan = war_data.get("clan", {})
        finish_time_raw = war_clan.get("finishTime")
        finish_time_parsed = parse_finish_time_msc(finish_time_raw)
        
        if finish_time_parsed:
            st.info(f"🎉 Речная гонка завершена вашим кланом досрочно: {finish_time_parsed}")
            
        # --- ОБРАБОТКА ДАННЫХ ИГРОКОВ КЛАНА ---
        members = clan_data.get("memberList", [])
        if members:
            df_members = pd.DataFrame(members)[["tag", "name", "role", "donations", "donationsReceived", "trophies", "lastSeen"]]
            df_members = df_members.rename(columns={"lastSeen": "lastSeen_raw"})
            
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
            else:
                df_war = pd.DataFrame(columns=["tag", "fame", "decksUsed", "boatAttacks"])
                
            # Объединение
            df_merged = pd.merge(df_members, df_war, on="tag", how="left")
            df_merged["fame"] = df_merged["fame"].fillna(0).astype(int)
            df_merged["decksUsed"] = df_merged["decksUsed"].fillna(0).astype(int)
            df_merged["boatAttacks"] = df_merged["boatAttacks"].fillna(0).astype(int)
            
            # Расчет пропущенных за неделю
            df_merged["decks_missed"] = expected_decks - df_merged["decksUsed"]
            df_merged["decks_missed"] = df_merged["decks_missed"].apply(lambda x: x if x > 0 else 0)
            df_merged["efficiency"] = (df_merged["fame"] / df_merged["decksUsed"]).fillna(0).round(1)
            
            # Детектор оффлайна
            df_merged["days_inactive"] = df_merged["lastSeen_raw"].apply(get_days_since_active)
            
            # Логика донатов
            df_merged["net_donations"] = df_merged["donations"] - df_merged["donationsReceived"]
            df_merged["generosity"] = df_merged.apply(calculate_generosity_ratio, axis=1)
            
            # Перевод ролей
            df_merged["role"] = df_merged["role"].map(ROLE_TRANSLATION).fillna(df_merged["role"])
            
            # --- ВКЛАДКИ ---
            tab_main, tab_dynamics, tab_prediction, tab_scout, tab_deck_ready = st.tabs([
                "📋 Текущая статистика клана", 
                "📈 Динамика и Надежность", 
                "🔮 Прогноз и Рекомендации", 
                "🛰️ Военная разведка (Радар)",
                "⚔️ Аудит колод и Боевой лог"
            ])
            
            # ==================== ВКЛАДКА 1: ОСНОВНАЯ СТАТИСТИКА ====================
            with tab_main:
                col1, col2 = st.columns(2)
                col1.metric("Всего игроков в клане", len(df_members), help="Количество участников в клане на текущий момент.")
                col2.metric("Слава текущего состава", int(df_merged["fame"].sum()), help="Точная сумма славы текущего состава.")
                
                # Сохранение в БД
                st.write("")
                if st.button("💾 Сохранить итоги текущей недели в историю базы данных"):
                    try:
                        conn = sqlite3.connect("clan_history.db")
                        cursor = conn.cursor()
                        for idx, row in df_merged.iterrows():
                            cursor.execute("""
                                INSERT OR REPLACE INTO war_history 
                                (clan_tag, week_start, player_tag, player_name, role, donations, fame, decks_used, decks_missed, efficiency)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """, (
                                CLAN_TAG.strip(),
                                thursday.strftime('%Y-%m-%d'),
                                row["tag"],
                                row["name"],
                                row["role"],
                                int(row["donations"]),
                                int(row["fame"]),
                                int(row["decksUsed"]),
                                int(row["decks_missed"]),
                                float(row["efficiency"])
                            ))
                        conn.commit()
                        conn.close()
                        st.success(f"Данные за игровую неделю успешно зафиксированы в истории!")
                    except Exception as e:
                        st.error(f"Ошибка сохранения: {e}")
                
                st.write("---")
                
                # Нарушители
                st.header("🚨 Нарушители дисциплины")
                lazy_some = df_merged[(df_merged["decks_missed"] > 0) & (df_merged["decksUsed"] > 0)]
                lazy_week = df_merged[df_merged["decksUsed"] == 0]
                dead_souls = df_merged[df_merged["days_inactive"] >= 3]
                
                t1, t2, t3 = st.tabs(["Пропустили атаки за неделю", "Вообще не воевали (0 атак)", "💤 Детектор мертвых душ (оффлайн >= 3д)"])
                with t1:
                    if not lazy_some.empty:
                        st.dataframe(
                            lazy_some[["name", "role", "decksUsed", "decks_missed", "fame"]]
                            .rename(columns={"name": "Имя", "role": "Роль", "decksUsed": "Сыграно колод", "decks_missed": "Пропущено колод", "fame": "Слава"}),
                            use_container_width=True
                        )
                    else:
                        st.success("Отлично! Все воюющие сыграли все доступные колоды.")
                with t2:
                    if not lazy_week.empty:
                        st.dataframe(
                            lazy_week[["name", "role", "donations", "trophies"]]
                            .rename(columns={"name": "Имя", "role": "Роль", "donations": "Донаты", "trophies": "Кубки"}),
                            use_container_width=True
                        )
                    else:
                        st.success("Все участники клана воевали на этой неделе.")
                with t3:
                    if not dead_souls.empty:
                        st.write("Список игроков, которые давно не заходили в Clash Royale:")
                        st.dataframe(
                            dead_souls[["name", "role", "days_inactive", "donations"]]
                            .rename(columns={"name": "Имя", "role": "Роль", "days_inactive": "Дней оффлайна", "donations": "Донаты за неделю"})
                            .sort_values(by="Дней оффлайна", ascending=False),
                            use_container_width=True
                        )
                    else:
                        st.success("Прекрасно! Все участники заходили в игру в течение последних 48 часов.")
                
                st.write("---")
                
                # Полная таблица
                st.header("📋 Полная статистика клана")
                search_query = st.text_input("Поиск игрока по имени")
                if search_query:
                    df_to_show = df_merged[df_merged["name"].str.contains(search_query, case=False)]
                else:
                    df_to_show = df_merged
                    
                sort_by = st.selectbox("Сортировать таблицу по:", ["Очки славы (Fame)", "Донаты", "Чистый баланс донатов", "Пропущено колод за неделю", "Эффективность атак", "Коэффициент щедрости"])
                sort_mapping = {
                    "Очки славы (Fame)": ("fame", False), "Донаты": ("donations", False),
                    "Чистый баланс донатов": ("net_donations", False),
                    "Пропущено колод за неделю": ("decks_missed", False), "Эффективность атак": ("efficiency", False),
                    "Коэффициент щедрости": ("generosity", False)
                }
                col_name, ascending = sort_mapping[sort_by]
                df_to_show = df_to_show.sort_values(by=col_name, ascending=ascending)
                
                df_display = df_to_show.rename(columns={
                    "name": "Имя", "role": "Роль", "donations": "Донаты (отдал)", "donationsReceived": "Донаты (получил)",
                    "trophies": "Кубки", "fame": "Очки славы КВ", "decksUsed": "Всего колод сыграно", 
                    "decks_missed": "Пропущено колод за неделю", "efficiency": "Ср. очков за бой", 
                    "days_inactive": "Дней оффлайна", "generosity": "Коэф. щедрости",
                    "net_donations": "Баланс донатов (Отдал-Получил)"
                })
                st.dataframe(df_display[["Имя", "Роль", "Кубки", "Донаты (отдал)", "Баланс донатов (Отдал-Получил)", "Коэф. щедрости", "Очки славы КВ", "Всего колод сыграно", "Пропущено колод за неделю", "Ср. очков за бой", "Дней оффлайна"]], use_container_width=True)
            
            # ==================== ВКЛАДКА 2: ДИНАМИКА И НАДЕЖНОСТЬ ====================
            with tab_dynamics:
                st.header("📈 Динамика и Индекс надежности участников")
                
                # Генератор демо-данных
                if st.button("🧬 Сгенерировать демо-историю (для тестирования графиков)"):
                    try:
                        conn = sqlite3.connect("clan_history.db")
                        cursor = conn.cursor()
                        test_weeks = ["2026-05-04", "2026-05-11", "2026-05-18"]
                        for week in test_weeks:
                            for idx, player in df_members.head(5).iterrows():
                                import random
                                random.seed(idx + hash(week))
                                fake_fame = random.randint(1200, 3200)
                                fake_donations = random.randint(50, 400)
                                fake_missed = random.choice([0, 0, 0, 4]) 
                                fake_decks = 16 - fake_missed
                                cursor.execute("""
                                    INSERT OR REPLACE INTO war_history 
                                    (clan_tag, week_start, player_tag, player_name, role, donations, fame, decks_used, decks_missed, efficiency)
                                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                                """, (
                                    CLAN_TAG.strip(),
                                    week,
                                    player["tag"],
                                    player["name"],
                                    ROLE_TRANSLATION.get(player["role"], player["role"]),
                                    fake_donations,
                                    fake_fame,
                                    fake_decks,
                                    fake_missed,
                                    round(fake_fame / fake_decks, 1) if fake_decks > 0 else 0
                                ))
                        conn.commit()
                        conn.close()
                        st.success("Демо-данные добавлены! Выберите одного из первых 5 игроков ниже.")
                    except Exception as e:
                        st.error(f"Ошибка при создании демо-данных: {e}")
                
                # Выбор игрока из БД
                conn = sqlite3.connect("clan_history.db")
                db_players = pd.read_sql_query(
                    "SELECT DISTINCT player_name, player_tag FROM war_history WHERE clan_tag = ?", 
                    conn, params=(CLAN_TAG.strip(),)
                )
                conn.close()
                
                if not db_players.empty:
                    player_options = db_players["player_name"].tolist()
                    selected_player_name = st.selectbox("Выберите игрока для анализа:", player_options)
                    selected_tag = db_players[db_players["player_name"] == selected_player_name]["player_tag"].values[0]
                    
                    conn = sqlite3.connect("clan_history.db")
                    player_history = pd.read_sql_query(
                        "SELECT week_start, fame, donations, decks_used, decks_missed, efficiency FROM war_history WHERE player_tag = ? AND clan_tag = ? ORDER BY week_start ASC",
                        conn, params=(selected_tag, CLAN_TAG.strip())
                    )
                    conn.close()
                    
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
                    st.info("История пуста. База начнет пополняться по кнопке сохранения результатов.")
            
            # ==================== ВКЛАДКА 3: ПРОГНОЗ И РЕКОМЕНДАЦИИ ====================
            with tab_prediction:
                st.header("🔮 Прогнозы и Рекомендации")
                
                conn = sqlite3.connect("clan_history.db")
                all_history = pd.read_sql_query(
                    "SELECT player_tag, player_name, role, fame, donations, decks_missed FROM war_history WHERE clan_tag = ?",
                    conn, params=(CLAN_TAG.strip(),)
                )
                conn.close()
                
                if not all_history.empty:
                    analysis_df = all_history.groupby("player_tag").agg({
                        "player_name": "first",
                        "role": "first",
                        "fame": "mean",
                        "donations": "mean",
                        "decks_missed": "mean"
                    }).reset_index()
                    
                    analysis_df["expected_fame_next_week"] = analysis_df["fame"].round(0).astype(int)
                    analysis_df["expected_donations_next_week"] = analysis_df["donations"].round(0).astype(int)
                    
                    def generate_recommendation(row):
                        fame = row["fame"]
                        misses = row["decks_missed"]
                        role = row["role"]
                        
                        if misses >= 4.0:
                            return "❌ Кандидат на исключение (высокий уровень пропусков КВ)"
                        elif fame >= 2400 and role == "Участник":
                            return "🎖️ Рекомендация: Повысить до Старейшины"
                        elif fame >= 2800 and role == "Старейшина":
                            return "🌟 Рекомендация: Повысить до Соруководителя"
                        elif fame < 800 or misses > 1.5:
                            return "⚠️ Предупредить (низкая эффективность / пропуски атак)"
                        else:
                            return "✅ Стабильный боец (соответствует критериям)"
                            
                    analysis_df["Автоматическая рекомендация"] = analysis_df.apply(generate_recommendation, axis=1)
                    
                    st.write("Сводная прогностическая модель на основе накопленной истории:")
                    display_pred = analysis_df.rename(columns={
                        "player_name": "Имя", "role": "Роль",
                        "expected_fame_next_week": "Прогноз славы",
                        "expected_donations_next_week": "Прогноз донатов",
                        "decks_missed": "Ср. пропусков в неделю"
                    })
                    st.dataframe(
                        display_pred[["Имя", "Роль", "Прогноз славы", "Прогноз донатов", "Ср. пропусков в неделю", "Автоматическая рекомендация"]]
                        .sort_values(by="Прогноз славы", ascending=False),
                        use_container_width=True
                    )
                else:
                    st.info("Для построения точных прогнозов требуется накопить историю (сохранить результаты КВ хотя бы за 2 недели).")
            
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
                                "Очки КВ (Слава)": c_fame,
                                "Участников": opp_members_count,
                                "Кубки клана (Трофеи)": opp_trophies,
                                "Донаты за неделю": opp_donations
                            })
                            
                        df_scout_opp = pd.DataFrame(opponent_reports).sort_values(by="Очки КВ (Слава)", ascending=False)
                        st.dataframe(df_scout_opp, use_container_width=True)
                else:
                    st.warning("Данные по группе речной гонки КВ недоступны.")
                    
            # ==================== ВКЛАДКА 5: АУДИТ КОЛОД И БОЕВОЙ ЛОГ ====================
            with tab_deck_ready:
                st.header("⚔️ Аудит прокачки карт и Анализ личных боев")
                st.write("Выберите любого игрока клана для проведения моментального технического аудита:")
                
                player_names = df_members["name"].tolist()
                target_player_name = st.selectbox("Выберите игрока для аудита прокачки и логов КВ:", player_names)
                
                target_tag = df_members[df_members["name"] == target_player_name]["tag"].values[0]
                
                if target_tag:
                    col_p1, col_p2 = st.columns(2)
                    
                    # 1. АУДИТ ПРОКАЧКИ КАРТ (С адаптивным расчетом уровней)
                    with col_p1:
                        st.subheader("🃏 Боеготовность колод к КВ")
                        player_profile = fetch_player_data_cached(target_tag, API_KEY.strip())
                        
                        if player_profile:
                            cards = player_profile.get("cards", [])
                            
                            # Подсчет абсолютных игровых уровней по новой формуле
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
                            
                    # 2. АНАЛИЗ БОЕВОГО ЛОГА
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
        else:
            st.warning("Не удалось получить список участников клана. Возможно, тег введен неверно.")
