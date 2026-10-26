from datetime import timedelta
import pandas as pd
import requests
import streamlit as st

st.set_page_config(page_title="Analizator Wyników Lotto", page_icon="🎱", layout="wide")
BASE_URL = "https://developers.lotto.pl/api/open/v1/lotteries/draw-results/"
GRY = {
    "Lotto": {"kod": "Lotto", "max": 49, "kul": 6},
    "Eurojackpot": {"kod": "EuroJackpot", "max": 50, "kul": 5},
    "Mini Lotto": {"kod": "MiniLotto", "max": 42, "kul": 5},
}
METODA = "Analiza częstotliwości (Gorące i Zimne liczby)"


def zapytaj_api(endpoint, params, klucz):
    response = requests.get(
        BASE_URL + endpoint,
        headers={"secret": klucz, "Accept": "application/json",
                 "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
        params=params, timeout=20,
    )
    if response.status_code in (401, 403):
        raise ValueError("Błąd autoryzacji (401/403). Sprawdź klucz API.")
    if response.status_code == 429:
        raise ValueError("Limit zapytań API (429). Odczekaj i spróbuj ponownie. Pobrane dni pozostają w cache.")
    if response.status_code == 404:
        return []
    if not response.ok:
        raise ValueError(f"Błąd HTTP {response.status_code}: {response.text[:400]}")
    dane = response.json()
    if isinstance(dane, list):
        return dane
    if isinstance(dane, dict) and isinstance(dane.get("items"), list):
        return dane["items"]
    if isinstance(dane, dict) and "results" in dane and "drawDate" in dane:
        return [dane]
    raise ValueError("Nieoczekiwany format odpowiedzi API: brak listy wyników lub pola items.")


@st.cache_data(ttl=3600, show_spinner=False)
def pobierz_wyniki_lotto(typ_gry, klucz):
    # Działający endpoint z pliku bazowego.
    return zapytaj_api("last-results-per-game", {"gameType": typ_gry}, klucz)


@st.cache_data(ttl=3600, show_spinner=False)
def pobierz_dzien(typ_gry, dzien, klucz):
    zebrane, widziane = [], set()
    for index in range(1, 11):
        wyniki = zapytaj_api("by-date-per-game", {
            "gameType": typ_gry, "drawDate": f"{dzien}T00:00:00",
            "index": index, "size": 50, "sort": "drawDate", "order": "DESC",
        }, klucz)
        if not wyniki:
            break
        nowe = 0
        for wynik in wyniki:
            if not isinstance(wynik, dict):
                continue
            klucz_rekordu = (str(wynik.get("drawSystemId")), str(wynik.get("drawDate")))
            if klucz_rekordu not in widziane:
                widziane.add(klucz_rekordu)
                zebrane.append(wynik)
                nowe += 1
        if not nowe or len(wyniki) < 50:
            break
    return zebrane


def przygotuj_tabele(lista_losowan, wybrana_gra, max_liczba, liczba_kul):
    wiersze = []
    for losowanie in lista_losowan:
        if not isinstance(losowanie, dict):
            continue
        wyniki = losowanie.get("results") or []
        wynik = next((w for w in wyniki if isinstance(w, dict)
                      and w.get("gameType") == wybrana_gra), None)
        if wynik is None:
            continue
        surowe = wynik.get("resultsJson", [])
        if not isinstance(surowe, list):
            continue
        try:
            if any(isinstance(x, bool) or not str(x).strip().isdigit() for x in surowe):
                continue
            liczby = sorted(int(x) for x in surowe)
            if (len(liczby) != liczba_kul or len(set(liczby)) != liczba_kul
                    or any(x < 1 or x > max_liczba for x in liczby)):
                continue
            data = pd.to_datetime(losowanie.get("drawDate"), utc=True, errors="coerce")
            if pd.isna(data):
                continue
        except (TypeError, ValueError):
            continue
        numer = losowanie.get("drawSystemId")
        wiersz = {
            "_data": data, "Nr losowania": numer,
            "Wylosowane liczby": ", ".join(map(str, liczby)),
            "_liczby_lista": liczby,
            "_id": str(numer) if numer is not None else data.isoformat(),
        }
        if wybrana_gra == "EuroJackpot":
            euro = wynik.get("specialResults") or []
            wiersz["Liczby dodatkowe (Euro)"] = ", ".join(map(str, euro)) if isinstance(euro, list) else str(euro)
        wiersze.append(wiersz)
    if not wiersze:
        return pd.DataFrame()
    df = pd.DataFrame(wiersze).drop_duplicates("_id").sort_values("_data", ascending=False).reset_index(drop=True)
    df.insert(0, "Data losowania", df["_data"].dt.tz_convert("Europe/Warsaw").dt.strftime("%Y-%m-%d %H:%M"))
    return df


def analiza_czestotliwosci(df, max_liczba):
    zliczenia = df["_liczby_lista"].explode().dropna().astype(int).value_counts()
    pelne = zliczenia.reindex(range(1, max_liczba + 1), fill_value=0)
    tabela = pelne.rename_axis("Liczba").reset_index(name="Wystąpienia")
    tabela["Kula"] = tabela["Liczba"].map(lambda x: f"{x:02d}")
    tabela["% losowań"] = (tabela["Wystąpienia"] / len(df) * 100).round(1) if len(df) else 0.0
    gorace = tabela.sort_values(["Wystąpienia", "Liczba"], ascending=[False, True]).head(5)
    zimne = tabela.sort_values(["Wystąpienia", "Liczba"], ascending=[True, True]).head(5)
    return gorace, zimne, tabela


def pokaz_analize(df, max_liczba):
    gorace, zimne, pelna = analiza_czestotliwosci(df, max_liczba)
    st.subheader("🔥❄️ Gorące i zimne liczby")
    st.caption("Przy remisie pierwszeństwo ma mniejszy numer. Zimne liczby obejmują również liczby niewylosowane ani razu.")
    for kolumna, tytul, dane, kolor in zip(
        st.columns(2), ["🔥 TOP 5 najczęstszych", "❄️ TOP 5 najrzadszych"],
        [gorace, zimne], ["#ff4b4b", "#1c83e1"],
    ):
        with kolumna:
            st.markdown("### " + tytul)
            st.bar_chart(dane.set_index("Kula")[["Wystąpienia"]], color=kolor)
            st.dataframe(dane[["Kula", "Wystąpienia", "% losowań"]], hide_index=True, use_container_width=True)
    with st.expander("Pełna częstotliwość wszystkich liczb"):
        st.bar_chart(pelna.set_index("Kula")[["Wystąpienia"]])
        st.dataframe(pelna[["Liczba", "Wystąpienia", "% losowań"]], hide_index=True, use_container_width=True)
    st.caption("Procent oznacza udział losowań zawierających daną liczbę. Analiza opisuje historię, a nie przewiduje kolejnego wyniku.")


METODOLOGIE = {METODA: pokaz_analize}


def main():
    st.sidebar.header("⚙️ Ustawienia analizy")
    gra = st.sidebar.selectbox("Wybierz grę liczbową:", list(GRY))
    konfiguracja = GRY[gra]
    zakres = st.sidebar.radio("Liczba ostatnich losowań:", [20, 50], horizontal=True)
    metoda = st.sidebar.selectbox("Metodologia:", list(METODOLOGIE))
    st.sidebar.info("Cache: 1 godzina. Pierwsze pobranie historii może potrwać — API jest odpytywane o kolejne daty, także dni bez losowania.")
    if st.sidebar.button("🔄 Wymuś odświeżenie z API"):
        pobierz_wyniki_lotto.clear()
        pobierz_dzien.clear()
        st.rerun()
    try:
        klucz = st.secrets.get("LOTTO_API_KEY")
    except FileNotFoundError:
        klucz = None
    if not klucz:
        st.error('Dodaj do .streamlit/secrets.toml: LOTTO_API_KEY = "twój_klucz_api"')
        st.stop()
    st.title(f"🎱 Analizator wyników: {gra}")
    postep = st.empty()
    try:
        najnowsze = pobierz_wyniki_lotto(konfiguracja["kod"], klucz)
        tabela = przygotuj_tabele(najnowsze, konfiguracja["kod"], konfiguracja["max"], konfiguracja["kul"])
        if tabela.empty:
            st.error("Najnowsze wyniki nie zawierają poprawnych liczb dla wybranej gry. Sprawdź podgląd API.")
            st.json(najnowsze)
            st.stop()
        surowe = list(najnowsze)
        # Najnowszy wynik wyznacza początek historii; nie zakładamy kalendarza losowań.
        dzien = tabela["_data"].iloc[0].tz_convert("Europe/Warsaw").date()
        blad_historii = None
        for _ in range(zakres * 8 + 30):
            if len(tabela) >= zakres:
                break
            postep.info(f"Pobieranie historii: {dzien} | poprawne losowania: {len(tabela)}/{zakres}")
            try:
                surowe.extend(pobierz_dzien(konfiguracja["kod"], dzien.isoformat(), klucz))
            except (requests.RequestException, ValueError) as exc:
                blad_historii = str(exc)
                break
            tabela = przygotuj_tabele(surowe, konfiguracja["kod"], konfiguracja["max"], konfiguracja["kul"])
            dzien -= timedelta(days=1)
    except (requests.RequestException, ValueError) as exc:
        st.error(f"Nie udało się pobrać wyników: {exc}")
        st.stop()
    finally:
        postep.empty()
    if blad_historii:
        st.error(f"Przerwano pobieranie historii: {blad_historii}")
    if len(tabela) < zakres:
        st.warning(f"Wybrano {zakres} losowań, ale pobrano tylko {len(tabela)} poprawnych, unikalnych wyników. Pełny ranking nie zostanie wyświetlony.")
    else:
        tabela = tabela.head(zakres).copy()
        st.write(f"Analiza ostatnich {len(tabela)} losowań.")
        st.caption(f"Zakres: {tabela['Data losowania'].iloc[-1]} — {tabela['Data losowania'].iloc[0]} (czas polski).")
        if gra == "Eurojackpot":
            st.info("Ranking dotyczy tylko liczb głównych 1–50. Liczby Euro są pokazane osobno w historii.")
        METODOLOGIE[metoda](tabela, konfiguracja["max"])
    st.subheader(f"📋 Historia pobranych losowań ({min(len(tabela), zakres)})")
    st.dataframe(tabela.head(zakres)[[c for c in tabela.columns if not c.startswith("_")]], hide_index=True, use_container_width=True)
    with st.expander("🛠️ Podgląd surowych danych (debug)"):
        st.json(surowe[:3])


if __name__ == "__main__":
    main()