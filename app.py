from datetime import timedelta
from itertools import combinations

import altair as alt
import pandas as pd
import requests
import streamlit as st

st.set_page_config(page_title="Analizator Wyników Lotto", page_icon="🎱", layout="wide")

BASE_URL = "https://developers.lotto.pl/api/open/v1/lotteries/draw-results/"
KONFIGURACJA_GIER = {
    "Lotto": {"kod_api": "Lotto", "max_liczba": 49, "liczba_kul": 6},
    "Eurojackpot": {"kod_api": "EuroJackpot", "max_liczba": 50, "liczba_kul": 5},
    "Mini Lotto": {"kod_api": "MiniLotto", "max_liczba": 42, "liczba_kul": 5},
}
METODA_CZESTOTLIWOSCI = "Analiza częstotliwości (Gorące i Zimne liczby)"
METODA_WSPOLWYSTEPOWANIA = "Analiza współwystępowania (Najczęstsze pary i trójki)"


def zapytaj_api(endpoint, params, klucz):
    response = requests.get(
        BASE_URL + endpoint,
        headers={
            "secret": klucz,
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        },
        params=params,
        timeout=20,
    )
    if response.status_code in (401, 403):
        raise ValueError("Błąd autoryzacji (401/403). Sprawdź klucz API.")
    if response.status_code == 429:
        raise ValueError("Limit zapytań API (429). Odczekaj chwilę i spróbuj ponownie.")
    if response.status_code == 404:
        return []
    if not response.ok:
        raise ValueError(f"Błąd HTTP {response.status_code}: {response.text[:300]}")

    dane = response.json()
    if isinstance(dane, list):
        return dane
    if isinstance(dane, dict) and isinstance(dane.get("items"), list):
        return dane["items"]
    if isinstance(dane, dict) and "results" in dane and "drawDate" in dane:
        return [dane]
    raise ValueError("Nieoczekiwany format odpowiedzi API.")


@st.cache_data(ttl=3600, show_spinner=False)
def pobierz_najnowsze_wyniki(typ_gry, klucz):
    return zapytaj_api("last-results-per-game", {"gameType": typ_gry}, klucz)


@st.cache_data(ttl=3600, show_spinner=False)
def pobierz_wyniki_dnia(typ_gry, dzien, klucz):
    zebrane, widziane = [], set()
    for strona in range(1, 11):
        wyniki = zapytaj_api(
            "by-date-per-game",
            {
                "gameType": typ_gry,
                "drawDate": f"{dzien}T00:00:00",
                "index": strona,
                "size": 50,
                "sort": "drawDate",
                "order": "DESC",
            },
            klucz,
        )
        if not wyniki:
            break
        nowe = 0
        for wynik in wyniki:
            if not isinstance(wynik, dict):
                continue
            identyfikator = (str(wynik.get("drawSystemId")), str(wynik.get("drawDate")))
            if identyfikator not in widziane:
                widziane.add(identyfikator)
                zebrane.append(wynik)
                nowe += 1
        if nowe == 0 or len(wyniki) < 50:
            break
    return zebrane


def przygotuj_tabele(lista_losowan, kod_gry, max_liczba, liczba_kul):
    wiersze = []
    for losowanie in lista_losowan:
        if not isinstance(losowanie, dict):
            continue
        wynik = next(
            (
                w
                for w in losowanie.get("results", [])
                if isinstance(w, dict) and w.get("gameType") == kod_gry
            ),
            None,
        )
        if wynik is None:
            continue

        surowe_liczby = wynik.get("resultsJson", [])
        if not isinstance(surowe_liczby, list):
            continue
        try:
            if any(isinstance(x, bool) or not str(x).strip().isdigit() for x in surowe_liczby):
                continue
            liczby = sorted(int(x) for x in surowe_liczby)
            if (
                len(liczby) != liczba_kul
                or len(set(liczby)) != liczba_kul
                or any(x < 1 or x > max_liczba for x in liczby)
            ):
                continue
            data = pd.to_datetime(losowanie.get("drawDate"), utc=True, errors="coerce")
            if pd.isna(data):
                continue
        except (TypeError, ValueError):
            continue

        numer = losowanie.get("drawSystemId")
        wiersz = {
            "_data": data,
            "Nr losowania": numer,
            "Wylosowane liczby": ", ".join(map(str, liczby)),
            "_liczby_lista": liczby,
            "_id": str(numer) if numer is not None else data.isoformat(),
        }
        if kod_gry == "EuroJackpot":
            euro = wynik.get("specialResults", [])
            wiersz["Liczby dodatkowe (Euro)"] = (
                ", ".join(map(str, euro)) if isinstance(euro, list) else str(euro)
            )
        wiersze.append(wiersz)

    if not wiersze:
        return pd.DataFrame()

    tabela = pd.DataFrame(wiersze).drop_duplicates("_id")
    tabela = tabela.sort_values("_data", ascending=False).reset_index(drop=True)
    tabela.insert(
        0,
        "Data losowania",
        tabela["_data"].dt.tz_convert("Europe/Warsaw").dt.strftime("%Y-%m-%d %H:%M"),
    )
    return tabela


def analiza_czestotliwosci(tabela, max_liczba):
    zliczenia = tabela["_liczby_lista"].explode().dropna().astype(int).value_counts()
    pelne_zliczenia = zliczenia.reindex(range(1, max_liczba + 1), fill_value=0)
    czestosci = pelne_zliczenia.rename_axis("Liczba").reset_index(name="Wystąpienia")
    czestosci["Kula"] = czestosci["Liczba"].map(lambda x: f"{x:02d}")
    czestosci["% losowań"] = (czestosci["Wystąpienia"] / len(tabela) * 100).round(1)

    gorace = czestosci.sort_values(["Wystąpienia", "Liczba"], ascending=[False, True]).head(5)
    zimne = czestosci.sort_values(["Wystąpienia", "Liczba"], ascending=[True, True]).head(5)
    return gorace, zimne, czestosci


def pokaz_czestotliwosci(tabela, max_liczba):
    gorace, zimne, pelne = analiza_czestotliwosci(tabela, max_liczba)
    st.subheader("🔥❄️ Gorące i zimne liczby")
    st.caption(
        "Przy remisie pierwszeństwo ma mniejszy numer. Zimne liczby obejmują również liczby, "
        "które nie padły ani razu w analizowanym okresie."
    )

    lewa, prawa = st.columns(2)
    with lewa:
        st.markdown("### 🔥 TOP 5 najczęstszych")
        st.bar_chart(gorace.set_index("Kula")[["Wystąpienia"]], color="#ff4b4b")
        st.dataframe(
            gorace[["Kula", "Wystąpienia", "% losowań"]],
            hide_index=True,
            use_container_width=True,
        )

    with prawa:
        st.markdown("### ❄️ TOP 5 najrzadszych")
        st.bar_chart(zimne.set_index("Kula")[["Wystąpienia"]], color="#1c83e1")
        st.dataframe(
            zimne[["Kula", "Wystąpienia", "% losowań"]],
            hide_index=True,
            use_container_width=True,
        )

    with st.expander("Pełna częstotliwość wszystkich liczb"):
        st.bar_chart(pelne.set_index("Kula")[["Wystąpienia"]])
        st.dataframe(
            pelne[["Liczba", "Wystąpienia", "% losowań"]],
            hide_index=True,
            use_container_width=True,
        )

    st.caption(
        "Procent oznacza udział losowań zawierających daną liczbę. Analiza opisuje historię "
        "i nie prognozuje kolejnego losowania."
    )


def zlicz_kombinacje(tabela, rozmiar):
    wszystkie = [
        uklad
        for liczby in tabela["_liczby_lista"]
        for uklad in combinations(sorted(set(map(int, liczby))), rozmiar)
    ]
    if not wszystkie:
        return pd.DataFrame(columns=["Układ", "Wystąpienia", "% losowań"])

    czestosci = pd.Series(wszystkie, dtype="object").value_counts()
    rekordy = sorted(czestosci.items(), key=lambda element: (-int(element[1]), element[0]))
    return pd.DataFrame(
        [
            {
                "Układ": " – ".join(f"{x:02d}" for x in uklad),
                "Wystąpienia": int(ile),
                "% losowań": round(100 * int(ile) / len(tabela), 1),
            }
            for uklad, ile in rekordy
        ]
    )


def dane_mapy(tabela, max_liczba, rozmiar, kotwica=None):
    ranking = zlicz_kombinacje(tabela, rozmiar)
    liczniki = {
        tuple(int(x) for x in wiersz["Układ"].split(" – ")): int(wiersz["Wystąpienia"])
        for _, wiersz in ranking.iterrows()
    }
    pula = [n for n in range(1, max_liczba + 1) if n != kotwica]
    rekordy = []
    for a in pula:
        for b in pula:
            if a == b:
                continue
            uklad = tuple(sorted((a, b) if kotwica is None else (a, b, kotwica)))
            ile = liczniki.get(uklad, 0)
            rekordy.append(
                {
                    "A": a,
                    "B": b,
                    "Układ": " – ".join(f"{x:02d}" for x in uklad),
                    "Wystąpienia": ile,
                    "% losowań": round(100 * ile / len(tabela), 1) if len(tabela) else 0.0,
                }
            )
    return pd.DataFrame(rekordy)


def pokaz_mape(dane, tytul):
    maksimum = max(1, int(dane["Wystąpienia"].max()))
    wykres = (
        alt.Chart(dane)
        .mark_rect()
        .encode(
            x=alt.X("A:O", title="Pierwsza liczba", sort="ascending",
                    axis=alt.Axis(labelAngle=0, labelFontSize=9)),
            y=alt.Y("B:O", title="Druga liczba", sort="ascending",
                    axis=alt.Axis(labelFontSize=9)),
            color=alt.Color(
                "Wystąpienia:Q",
                title="Wspólne losowania",
                scale=alt.Scale(scheme="blues", domain=[0, maksimum]),
                legend=alt.Legend(tickMinStep=1),
            ),
            tooltip=[
                alt.Tooltip("Układ:N", title="Liczby"),
                alt.Tooltip("Wystąpienia:Q", title="Wspólne losowania", format="d"),
                alt.Tooltip("% losowań:Q", title="% wszystkich losowań", format=".1f"),
            ],
        )
        .properties(title=tytul, height=650)
    )
    st.altair_chart(wykres, use_container_width=True)
    st.caption(
        "Najedź na komórkę, aby zobaczyć szczegóły. Ciemniejszy kolor oznacza więcej wspólnych "
        "losowań. Mapa jest symetryczna, a przekątna nie jest analizowana."
    )


def pokaz_wspolwystepowanie(tabela, max_liczba):
    st.subheader("🔗 Analiza współwystępowania")
    st.caption(
        "Pary i trójki są zliczane niezależnie od kolejności liczb. Każdy układ może zostać "
        "zaliczony najwyżej raz w jednym losowaniu."
    )
    limit = st.selectbox(
        "Liczba pozycji w rankingu:",
        [5, 10, 20, 50],
        index=1,
        key="wspolwystepowanie_limit",
    )

    zakladka_par, zakladka_trojek = st.tabs(["Najczęstsze pary", "Najczęstsze trójki"])

    with zakladka_par:
        ranking = zlicz_kombinacje(tabela, 2)
        st.markdown(f"### TOP {limit} — pary")
        st.dataframe(ranking.head(limit), hide_index=True, use_container_width=True)
        st.caption("Ranking zawiera wyłącznie pary występujące co najmniej raz.")
        pokaz_mape(dane_mapy(tabela, max_liczba, 2), "Mapa współwystępowania par")
        st.download_button(
            "Eksport pełnego rankingu par do CSV",
            ranking.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"pary_{len(tabela)}_losowan.csv",
            mime="text/csv",
            key="eksport_par",
        )

    with zakladka_trojek:
        ranking = zlicz_kombinacje(tabela, 3)
        st.markdown(f"### TOP {limit} — trójki")
        st.dataframe(ranking.head(limit), hide_index=True, use_container_width=True)
        st.caption("Ranking zawiera wyłącznie trójki występujące co najmniej raz.")
        kotwica = st.selectbox(
            "Trzecia liczba dla mapy trójek:",
            list(range(1, max_liczba + 1)),
            key="wspolwystepowanie_kotwica",
        )
        st.caption(
            f"Komórka A × B pokazuje liczbę losowań zawierających jednocześnie A, B i {kotwica:02d}. "
            "Ranking powyżej obejmuje wszystkie trójki."
        )
        pokaz_mape(
            dane_mapy(tabela, max_liczba, 3, kotwica),
            f"Trójki zawierające liczbę {kotwica:02d}",
        )
        st.download_button(
            "Eksport pełnego rankingu trójek do CSV",
            ranking.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"trojki_{len(tabela)}_losowan.csv",
            mime="text/csv",
            key="eksport_trojek",
        )

    st.info(
        "Współwystępowanie opisuje wyłącznie wyniki historyczne. Nie jest prognozą kolejnego "
        "losowania ani dowodem, że liczby mają tendencję do wspólnego występowania."
    )


METODOLOGIE = {
    METODA_CZESTOTLIWOSCI: pokaz_czestotliwosci,
    METODA_WSPOLWYSTEPOWANIA: pokaz_wspolwystepowanie,
}


def main():
    st.sidebar.header("⚙️ Ustawienia analizy")
    wybrana_etykieta = st.sidebar.selectbox("Wybierz grę liczbową:", list(KONFIGURACJA_GIER))
    konfiguracja = KONFIGURACJA_GIER[wybrana_etykieta]
    zakres_losowan = st.sidebar.radio(
        "Liczba ostatnich losowań:",
        [10, 20, 50],
        horizontal=True,
        help="Ile ostatnich, poprawnych losowań ma zostać przeanalizowanych.",
    )
    metodologia = st.sidebar.selectbox("Wybierz metodologię analizy:", list(METODOLOGIE))
    st.sidebar.info(
        "Dane pozostają w pamięci podręcznej przez 1 godzinę. Pierwsze pobranie historii może "
        "potrwać, ponieważ aplikacja odpytuje API o kolejne daty."
    )
    if st.sidebar.button("🔄 Wymuś odświeżenie z API"):
        pobierz_najnowsze_wyniki.clear()
        pobierz_wyniki_dnia.clear()
        st.rerun()

    try:
        klucz = st.secrets.get("LOTTO_API_KEY")
    except FileNotFoundError:
        klucz = None
    if not klucz:
        st.error('Dodaj do pliku .streamlit/secrets.toml: LOTTO_API_KEY = "twój_klucz_api"')
        st.stop()

    st.title(f"🎱 Analizator: {wybrana_etykieta}")
    postep = st.empty()
    blad_historii = None

    try:
        najnowsze = pobierz_najnowsze_wyniki(konfiguracja["kod_api"], klucz)
        tabela = przygotuj_tabele(
            najnowsze,
            konfiguracja["kod_api"],
            konfiguracja["max_liczba"],
            konfiguracja["liczba_kul"],
        )
        if tabela.empty:
            st.error("Najnowsze wyniki nie zawierają poprawnych liczb dla wybranej gry.")
            with st.expander("🛠️ Podgląd surowych danych (debug)"):
                st.json(najnowsze)
            st.stop()

        surowe_dane = list(najnowsze)
        dzien = tabela["_data"].iloc[0].tz_convert("Europe/Warsaw").date()

        for _ in range(zakres_losowan * 8 + 30):
            if len(tabela) >= zakres_losowan:
                break
            postep.info(
                f"Pobieranie historii: {dzien} | poprawne losowania: {len(tabela)}/{zakres_losowan}"
            )
            try:
                surowe_dane.extend(
                    pobierz_wyniki_dnia(
                        konfiguracja["kod_api"],
                        dzien.isoformat(),
                        klucz,
                    )
                )
            except (requests.RequestException, ValueError) as blad:
                blad_historii = str(blad)
                break
            tabela = przygotuj_tabele(
                surowe_dane,
                konfiguracja["kod_api"],
                konfiguracja["max_liczba"],
                konfiguracja["liczba_kul"],
            )
            dzien -= timedelta(days=1)
    except (requests.RequestException, ValueError) as blad:
        st.error(f"Nie udało się pobrać wyników: {blad}")
        st.stop()
    finally:
        postep.empty()

    if blad_historii:
        st.error(f"Przerwano pobieranie historii: {blad_historii}")

    if len(tabela) < zakres_losowan:
        st.warning(
            f"Wybrano {zakres_losowan} losowań, ale dostępnych jest tylko {len(tabela)} poprawnych, "
            "unikalnych wyników. Analiza zostanie pominięta, aby nie pokazywać niepełnego rankingu."
        )
    else:
        tabela_analizowana = tabela.head(zakres_losowan).copy()
        st.write(f"Analiza obejmuje {len(tabela_analizowana)} ostatnich losowań.")
        st.caption(
            f"Zakres: {tabela_analizowana['Data losowania'].iloc[-1]} — "
            f"{tabela_analizowana['Data losowania'].iloc[0]} (czas polski)."
        )
        if wybrana_etykieta == "Eurojackpot":
            st.info(
                "Analiza dotyczy wyłącznie liczb głównych 1–50. Liczby Euro są pokazywane osobno "
                "w historii i nie trafiają do rankingów."
            )
        METODOLOGIE[metodologia](tabela_analizowana, konfiguracja["max_liczba"])

    st.divider()
    st.subheader(f"📋 Historia losowań ({min(len(tabela), zakres_losowan)})")
    widoczne_kolumny = [kolumna for kolumna in tabela.columns if not kolumna.startswith("_")]
    st.dataframe(
        tabela.head(zakres_losowan)[widoczne_kolumny],
        use_container_width=True,
        hide_index=True,
    )

    with st.expander("🛠️ Podgląd surowych danych (debug)"):
        st.write(f"Pobrano łącznie rekordów: {len(surowe_dane)}")
        st.json(surowe_dane[:3])


if __name__ == "__main__":
    main()