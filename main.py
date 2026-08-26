# ==========================================
# AI COIN ASSISTANT - V21C ORTAK V4 | 4 MOTOR ORTAK AL KARARI | ERKEN + PARCALI + KAR KORU
# Taban: main_20_coklu_guc_siklastirilmis.py
# Fast Scan V1: 60 sn hızlı ön tarama + 5 dk tam tarama
# AL Relax V1: normal AL için ADX 27 / AI 80
# Final Cleanup / Core Candidate Scanner
# Candidate thresholds synced with latest working Coin Radar
# ==========================================

import os
import time
import json
import requests
import feedparser


BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

CHAT_IDS = [1877715122, 2097448038]

TARAMA_SURESI = 60
TAM_TARAMA_DONGUSU = 5          # 5 x 60 sn = yaklaşık 5 dk
HIZLI_HAREKET_ESIGI = 0.40      # 1 dakikalık fiyat değişimi %0.40+ ise hemen derin analiz
son_fiyatlar = {}
tarama_sayaci = 0

# Early Capture V1: önceki taramadaki hızlanmayı ölçmek için hafıza.
onceki_tarama = {}

# Çoklu Güç Havuzu:
# Güçlenme işareti veren coin 5 dakika boyunca, 1 dk fiyat hareketi %0.40 altında kalsa bile izlenir.
guc_izleme_havuzu = {}
GUC_IZLEME_SURESI = 10 * 60

# Aynı kararın tekrar Telegram gönderimini engeller.
son_ai_kararlar = {}

# V21C - Kısa vade erken yakalama + canlı aday yarışı:
# 1s/3s artık aday bulmanın ana motoru değildir.
# Adaylar 1-3-5-10 dakikalık fiyat/hacim ivmesinden doğar;
# 1s/3s yalnızca trend/risk onayı olarak kullanılır.
canli_aday_havuzu = {}
CANLI_MIN_BEKLEME = 2 * 60
CANLI_MAX_BEKLEME = 10 * 60
CANLI_MIN_GOZLEM = 2
CANLI_MAX_KAZANAN = 2
CANLI_MIN_YARIS_SKORU = 66
BTC_SERT_ZAYIFLIK_ESIGI = -2.0

# ============================================================
# ORTAK V1 - 21C + 22 PARCALI + KAR KORU + 13 ERKENLIK FILTRESI
# Emir vermez; Telegram karar/yonetim mesaji uretir.
# ============================================================
TAKIP_DOSYASI = "v21c_ortak_takip_state.json"
AL_TAKIP = {}

# 22 parcali giris plani
KADEME_1_ORAN = 30
KADEME_2_ORAN = 30
KADEME_3_ORAN = 40
KADEME_2_ESIK = 0.80   # ilk girise gore +%0.8
KADEME_3_ESIK = 1.80   # ilk girise gore +%1.8
KADEME_2_MIN_BEKLE = 60       # ilk AL'dan sonra en az 60 sn
KADEME_3_MIN_BEKLE = 60       # 2. kademeden sonra en az 60 sn
KADEME_ANALIZ_MAX_YAS = 180   # teyit için analiz en fazla 3 dk eski olabilir
KADEME_MAX_TEPE_GERI = -1.00  # sert geri vermede yeni kademe ekleme

# Her coin için son teknik/mikro analiz özeti.
# Pozisyon takibi bir sonraki 60 sn taramada bunu teyit olarak kullanır.
SON_ANALIZ = {}

# 21 kar-koru / 13 AL-SAT
KAR_AL_1_ESIK = 3.0
KAR_AL_1_ORAN = 40
ILK_ZARAR_KES = -1.50
TEPE_GERI_VERME = -1.40
MIN_KAR_KORUMA = 2.50

# 13'ten gelen "gec kalma" fikrini 21C'nin mikro zamanina uyarladik.
# Amaç: coin guclu olsa da hareketin sonundan AL dememek.
MIKRO_GEC_3DK = 4.50
MIKRO_GEC_5DK = 5.50
MIKRO_GEC_10DK = 7.00
TREND_GEC_1S = 5.00
RSI_GEC = 74.0

def _pct(yeni, eski):
    try:
        yeni = float(yeni)
        eski = float(eski)
        if eski == 0:
            return 0.0
        return ((yeni / eski) - 1.0) * 100
    except Exception:
        return 0.0

def _takip_yukle():
    global AL_TAKIP
    try:
        if os.path.exists(TAKIP_DOSYASI):
            with open(TAKIP_DOSYASI, "r", encoding="utf-8") as f:
                veri = json.load(f)
                if isinstance(veri, dict):
                    AL_TAKIP = veri
    except Exception as e:
        print("Takip dosyasi yuklenemedi:", e)

def _takip_kaydet():
    try:
        with open(TAKIP_DOSYASI, "w", encoding="utf-8") as f:
            json.dump(AL_TAKIP, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("Takip dosyasi kaydedilemedi:", e)

def giris_zamanlamasi_ortak(aday):
    teknik = aday.get("teknik") or {}
    mikro = aday.get("mikro") or {}
    d3m = float(mikro.get("d3", 0) or 0)
    d5m = float(mikro.get("d5", 0) or 0)
    d10m = float(mikro.get("d10", 0) or 0)
    d1s = float(aday.get("degisim1", 0) or 0)
    rsi = teknik.get("rsi")
    try:
        rsi = float(rsi) if rsi is not None else None
    except Exception:
        rsi = None

    gec = (
        d3m >= MIKRO_GEC_3DK
        or d5m >= MIKRO_GEC_5DK
        or d10m >= MIKRO_GEC_10DK
        or d1s >= TREND_GEC_1S
        or (rsi is not None and rsi >= RSI_GEC and d5m >= 3.0)
    )
    if gec:
        return "GEC"

    erken = (
        d3m <= 2.8
        and d5m <= 3.8
        and d10m <= 5.0
        and (rsi is None or rsi <= 70)
    )
    return "ERKEN" if erken else "DEVAM"

def ortak_kalite_hesapla(aday):
    teknik = aday.get("teknik") or {}
    mikro = aday.get("mikro") or {}
    ema20 = teknik.get("ema20")
    ema50 = teknik.get("ema50")
    fiyat = float(aday.get("fiyat", 0) or 0)
    rsi = teknik.get("rsi")
    adx = teknik.get("adx")
    macd_hist = teknik.get("macd_hist")

    try:
        rsi = float(rsi) if rsi is not None else None
    except Exception:
        rsi = None
    try:
        adx = float(adx) if adx is not None else None
    except Exception:
        adx = None

    ema_ok = (
        ema20 is not None and ema50 is not None
        and ema20 > ema50 and fiyat > float(ema20)
    )
    macd_ok = macd_hist is not None and macd_hist > 0

    giris = 38.0
    if ema_ok: giris += 14
    if macd_ok: giris += 12
    if rsi is not None and 50 <= rsi <= 68: giris += 12
    elif rsi is not None and 68 < rsi <= 72: giris += 5
    elif rsi is not None and rsi > 74: giris -= 12
    if adx is not None and adx >= 30: giris += 10
    elif adx is not None and adx >= 25: giris += 6
    elif adx is not None and adx < 20: giris -= 10
    if aday.get("basamakli_trend"): giris += 8
    if aday.get("btc_farki_aciliyor"): giris += 5
    if aday.get("lider_gucleniyor"): giris += 5

    hacim_ivme = float(mikro.get("hacim3_ivme", 0) or 0)
    hacim1x = float(mikro.get("hacim1x", 0) or 0)
    d3m = float(mikro.get("d3", 0) or 0)
    d5m = float(mikro.get("d5", 0) or 0)

    if hacim_ivme >= 1.15: giris += 8
    if hacim1x >= 1.20: giris += 5
    if d3m < 0: giris -= 10
    if d5m >= 5.0: giris -= 12

    devam = 35.0
    devam += min(20, max(0, float(aday.get("ai_skoru", 0) or 0) - 70) * 0.5)
    devam += min(15, max(0, float(aday.get("radar_skoru", 0) or 0) - 55) * 0.35)
    if adx is not None and adx >= 30: devam += 10
    if macd_ok: devam += 8
    if aday.get("btcden_guclu"): devam += 6
    if aday.get("lider_gucleniyor"): devam += 5
    if aday.get("momentum_hizlaniyor"): devam += 5
    if aday.get("hacim_hizlaniyor"): devam += 5
    if d3m < -0.5: devam -= 12

    return round(max(0, min(giris, 100)), 1), round(max(0, min(devam, 100)), 1)

def al_takip_baslat(aday):
    symbol = aday.get("symbol")
    fiyat = float(aday.get("fiyat", 0) or 0)
    if not symbol or fiyat <= 0:
        return
    eski = AL_TAKIP.get(symbol)
    if eski and eski.get("durum") == "ACIK":
        return

    AL_TAKIP[symbol] = {
        "durum": "ACIK",
        "giris": fiyat,
        "son_fiyat": fiyat,
        "max_fiyat": fiyat,
        "baslangic": time.time(),
        "kademe2": False,
        "kademe3": False,
        "kademe2_zaman": None,
        "kademe3_zaman": None,
        "kar_al1": False,
        "kalan_oran": 100,
    }
    _takip_kaydet()

def kademe_teyidi(symbol, kademe_no, fiyat, giris, tepe):
    """Yeni kademe için yalnız fiyat değil, son teknik/mikro kaliteyi de kontrol eder."""
    snap = SON_ANALIZ.get(symbol) or {}
    simdi = time.time()

    # Analiz çok eskiyse yeni kademe yok.
    analiz_zaman = float(snap.get("zaman", 0) or 0)
    if analiz_zaman <= 0 or (simdi - analiz_zaman) > KADEME_ANALIZ_MAX_YAS:
        return False, "güncel analiz yok"

    giris_k = float(snap.get("giris_kalitesi", 0) or 0)
    devam_g = float(snap.get("devam_gucu", 0) or 0)
    rsi = float(snap.get("rsi", 999) or 999)
    adx = float(snap.get("adx", 0) or 0)
    macd_ok = bool(snap.get("macd_ok"))
    ema_ok = bool(snap.get("ema_ok"))
    d3 = float(snap.get("d3", 0) or 0)
    d5 = float(snap.get("d5", 0) or 0)
    hacim1x = float(snap.get("hacim1x", 0) or 0)
    hacim_ivme = float(snap.get("hacim_ivme", 0) or 0)

    tepeden = _pct(fiyat, tepe)
    if tepeden <= KADEME_MAX_TEPE_GERI:
        return False, "tepeden sert geri verme"
    if not (ema_ok and macd_ok):
        return False, "EMA/MACD teyidi yok"

    if kademe_no == 2:
        ok = (
            giris_k >= 72
            and devam_g >= 68
            and adx >= 24
            and rsi <= 74
            and d3 >= 0
            and (hacim1x >= 0.70 or hacim_ivme >= 0.90)
        )
        return ok, (
            f"Giriş {giris_k:.0f} | Devam {devam_g:.0f} | "
            f"RSI {rsi:.1f} | ADX {adx:.1f}"
        )

    ok = (
        giris_k >= 75
        and devam_g >= 72
        and adx >= 26
        and rsi <= 72
        and d3 >= 0
        and d5 >= 0
        and (hacim1x >= 0.90 or hacim_ivme >= 1.00)
    )
    return ok, (
        f"Giriş {giris_k:.0f} | Devam {devam_g:.0f} | "
        f"RSI {rsi:.1f} | ADX {adx:.1f}"
    )


def al_takip_guncelle(ticker):
    if not AL_TAKIP:
        return

    fiyatlar = {}
    for coin in ticker:
        try:
            sym = coin.get("pair", "")
            fiyat = float(coin.get("last", 0) or 0)
            if sym and fiyat > 0:
                fiyatlar[sym] = fiyat
        except Exception:
            pass

    degisti = False
    mesajlar = []

    for symbol, p in list(AL_TAKIP.items()):
        if p.get("durum") != "ACIK":
            continue

        fiyat = fiyatlar.get(symbol)
        if not fiyat:
            continue

        giris = float(p.get("giris", fiyat) or fiyat)
        if fiyat > float(p.get("max_fiyat", fiyat) or fiyat):
            p["max_fiyat"] = fiyat
        tepe = float(p.get("max_fiyat", fiyat) or fiyat)
        p["son_fiyat"] = fiyat

        getiri = _pct(fiyat, giris)
        tepeden = _pct(fiyat, tepe)

        # 22 parçalı plan V2:
        # Aynı taramada 2. ve 3. kademe AÇILMAZ.
        # Fiyatın yanında güncel teknik/momentum/hacim teyidi gerekir.
        simdi = time.time()
        baslangic = float(p.get("baslangic", simdi) or simdi)

        if (
            not p.get("kademe2")
            and (simdi - baslangic) >= KADEME_2_MIN_BEKLE
            and KADEME_2_ESIK <= getiri < KAR_AL_1_ESIK
        ):
            k2_ok, k2_neden = kademe_teyidi(symbol, 2, fiyat, giris, tepe)
            if k2_ok:
                p["kademe2"] = True
                p["kademe2_zaman"] = simdi
                mesajlar.append(
                    f"🟢 2. KADEME UYGUN - {symbol}\n"
                    f"Fiyat: {fiyat:.4f} | İlk girişe göre: %{getiri:+.2f}\n"
                    f"Teyit: {k2_neden}\n"
                    f"Plan: +%{KADEME_2_ORAN} kademe; toplam plan %{KADEME_1_ORAN + KADEME_2_ORAN}."
                )
                degisti = True

        # 3. kademe ayrı bir sonraki teyit döngüsünü bekler.
        kademe2_zaman = p.get("kademe2_zaman")
        if (
            p.get("kademe2")
            and not p.get("kademe3")
            and kademe2_zaman
            and (simdi - float(kademe2_zaman)) >= KADEME_3_MIN_BEKLE
            and KADEME_3_ESIK <= getiri < KAR_AL_1_ESIK
        ):
            k3_ok, k3_neden = kademe_teyidi(symbol, 3, fiyat, giris, tepe)
            if k3_ok:
                p["kademe3"] = True
                p["kademe3_zaman"] = simdi
                mesajlar.append(
                    f"🟢 3. KADEME UYGUN - {symbol}\n"
                    f"Fiyat: {fiyat:.4f} | İlk girişe göre: %{getiri:+.2f}\n"
                    f"Teyit: {k3_neden}\n"
                    f"Plan: son +%{KADEME_3_ORAN} kademe; tam plan tamamlandı."
                )
                degisti = True

        # İlk anlamlı kâr: 21/13 kâr koru.
        if not p.get("kar_al1") and getiri >= KAR_AL_1_ESIK:
            p["kar_al1"] = True
            p["kalan_oran"] = 100 - KAR_AL_1_ORAN
            mesajlar.append(
                f"🟠 KÂR KORU - {symbol}\n"
                f"Fiyat: {fiyat:.4f} | Ilk girise gore: %{getiri:+.2f}\n"
                f"Plan: %{KAR_AL_1_ORAN} kâri koru, %{100-KAR_AL_1_ORAN} tasimaya devam et\n"
                f"Tepe: {tepe:.4f}"
            )
            degisti = True

        ilk_bozulma = (not p.get("kar_al1")) and getiri <= ILK_ZARAR_KES
        karli_bozulma = p.get("kar_al1") and (
            getiri >= MIN_KAR_KORUMA and tepeden <= TEPE_GERI_VERME
        )

        if ilk_bozulma or karli_bozulma:
            p["durum"] = "KAPALI"
            p["kapanis"] = fiyat
            p["kapanis_zaman"] = time.time()
            p["sonuc_yuzde"] = round(getiri, 2)

            if p.get("kar_al1"):
                baslik = "🔴 SAT KALAN"
                sebep = "kâr sonrasi tepeden geri verme"
            else:
                baslik = "🔴 AL IPTAL / ÇIK"
                sebep = "ilk giris dogrulanmadi; zarar siniri asildi"

            mesajlar.append(
                f"{baslik} - {symbol}\n"
                f"Fiyat: {fiyat:.4f} | Ilk girise gore: %{getiri:+.2f}\n"
                f"Tepe: {tepe:.4f} | Tepeden: %{tepeden:+.2f}\n"
                f"Sebep: {sebep}"
            )
            degisti = True

    if degisti:
        _takip_kaydet()

    for mesaj in mesajlar:
        print(mesaj)
        telegram_gonder(mesaj)

_takip_yukle()






STABLE_COINLER = [
    "USDT", "USDC", "FDUSD", "TUSD", "DAI", "USDP"
]




RSS_KAYNAKLARI = [
    "https://cointelegraph.com/rss",
    "https://www.coindesk.com/arc/outboundfeeds/rss/?outputType=xml"
]

POZITIF = [
    "listing", "listed", "binance", "coinbase", "partnership",
    "etf", "airdrop", "burn", "launch", "mainnet", "upgrade",
    "integration", "support", "investment", "funding", "approval",
    "adoption", "bullish", "surge", "rally"
]

NEGATIF = [
    "hack", "exploit", "lawsuit", "delist", "sec", "attack",
    "scam", "fraud", "investigation", "outage", "halted",
    "stopped", "shutdown", "pressure", "bearish", "loss",
    "dump", "decline", "crash", "selloff", "down", "weakness"
]


def telegram_gonder(mesaj):
    if not BOT_TOKEN:
        print("BOT_TOKEN bulunamadı. Railway Variables kontrol et.")
        return False

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    en_az_bir_basarili = False

    for chat_id in CHAT_IDS:
        try:
            r = requests.get(
                url,
                params={"chat_id": chat_id, "text": mesaj},
                timeout=10
            )
            try:
                veri = r.json()
            except Exception:
                veri = {}

            if r.ok and veri.get("ok") is True:
                en_az_bir_basarili = True
                print(f"[TELEGRAM OK] {chat_id}")
            else:
                print(f"[TELEGRAM HATA] {chat_id} | HTTP {r.status_code} | {r.text[:300]}")
        except Exception as e:
            print(f"[TELEGRAM HATA] {chat_id} | {e}")

    return en_az_bir_basarili


def veri_getir(symbol, saat=24):
    simdi = int(time.time())
    url = (
        f"https://graph-api.btcturk.com/v1/klines/history?"
        f"symbol={symbol}&resolution=60&from={simdi - (saat * 3600)}&to={simdi}"
    )
    return requests.get(url, timeout=10).json()


def dakika_veri_getir(symbol, dakika=20):
    """1 dakikalık mumlar. Erken yakalama motorunun ana veri kaynağı."""
    simdi = int(time.time())
    url = (
        f"https://graph-api.btcturk.com/v1/klines/history?"
        f"symbol={symbol}&resolution=1&from={simdi - (dakika * 60)}&to={simdi}"
    )
    r = requests.get(url, timeout=8)
    r.raise_for_status()
    return r.json()


def _pct_son(c, n):
    if len(c) <= n or not c[-1-n]:
        return 0.0
    return ((c[-1] - c[-1-n]) / c[-1-n]) * 100


def mikro_ivme_hesapla(symbol):
    """
    1-3-5-10 dk hareket + hacim ivmesi.
    Amaç: coin %10-20 olduktan sonra değil, henüz %0.5-3 civarında güçlenirken görmek.
    Veri alınamazsa eski motor çalışmaya devam eder.
    """
    try:
        d = dakika_veri_getir(symbol, 20)
        o = d.get("o", [])
        h = d.get("h", [])
        l = d.get("l", [])
        c = d.get("c", [])
        v = d.get("v", [])
        if len(c) < 12 or len(v) < 12:
            return None

        d1 = _pct_son(c, 1)
        d3 = _pct_son(c, 3)
        d5 = _pct_son(c, 5)
        d10 = _pct_son(c, 10)

        # Son dakika hacmi önceki 5 dakikanın ortalamasına göre.
        v_prev5 = sum(v[-6:-1]) / 5 if sum(v[-6:-1]) > 0 else 0
        hacim1x = (v[-1] / v_prev5) if v_prev5 > 0 else 0

        # Son 3 dk ortalaması, ondan önceki 3 dk ortalamasına göre hızlanıyor mu?
        son3 = sum(v[-3:]) / 3
        once3 = sum(v[-6:-3]) / 3
        hacim3_ivme = (son3 / once3) if once3 > 0 else 0

        # Mikro basamak: son kısa periyotta dipler ve kapanışlar yukarı taşınıyor mu?
        basamak = False
        if len(c) >= 6:
            son_kapanislar = c[-5:]
            yukselen_kapanis = sum(1 for i in range(1, len(son_kapanislar)) if son_kapanislar[i] >= son_kapanislar[i-1]) >= 3
            if len(l) >= 5:
                son_dipler = l[-5:]
                yukselen_dip = sum(1 for i in range(1, len(son_dipler)) if son_dipler[i] >= son_dipler[i-1]) >= 3
            else:
                yukselen_dip = yukselen_kapanis
            basamak = yukselen_kapanis and yukselen_dip

        # Hız artışı: 1dk temposu 3/5dk ortalama temposundan belirgin yüksek.
        tempo3 = d3 / 3.0
        tempo5 = d5 / 5.0
        fiyat_ivmeleniyor = d1 > 0 and d3 > 0 and (d1 >= tempo3 * 1.15 or tempo3 >= tempo5 * 1.10)
        hacim_ivmeleniyor = hacim1x >= 1.35 or hacim3_ivme >= 1.25

        # Şişme/kovalama filtresi. 10 dk içinde aşırı kaçmış coin yeni AL için uygun değil.
        sisti = d10 >= 6.5 or d5 >= 5.0 or d3 >= 4.0

        mikro_skor = 0
        if d1 >= 0.15: mikro_skor += 10
        if d1 >= 0.30: mikro_skor += 8
        if d3 >= 0.45: mikro_skor += 12
        if d3 >= 0.80: mikro_skor += 8
        if d5 >= 0.70: mikro_skor += 10
        if 0.8 <= d10 <= 5.5: mikro_skor += 8
        if hacim1x >= 1.35: mikro_skor += 12
        if hacim1x >= 1.80: mikro_skor += 8
        if hacim3_ivme >= 1.25: mikro_skor += 10
        if fiyat_ivmeleniyor: mikro_skor += 7
        if hacim_ivmeleniyor: mikro_skor += 7
        if basamak: mikro_skor += 10
        if sisti: mikro_skor -= 30
        mikro_skor = max(0, min(100, mikro_skor))

        return {
            "d1": round(d1, 3), "d3": round(d3, 3), "d5": round(d5, 3), "d10": round(d10, 3),
            "hacim1x": round(hacim1x, 2), "hacim3_ivme": round(hacim3_ivme, 2),
            "fiyat_ivmeleniyor": fiyat_ivmeleniyor, "hacim_ivmeleniyor": hacim_ivmeleniyor,
            "mikro_basamak": basamak, "sisti": sisti, "mikro_skor": mikro_skor
        }
    except Exception as e:
        print(f"[MIKRO VERI] {symbol}: {e}")
        return None



def btc_degisimleri():
    """
    V4.25 BTC Gücü V2 için BTC'nin 1s, 3s ve 24s değişimini hesaplar.
    """
    try:
        d = veri_getir("BTCTRY", 24)
        c = d["c"]

        if len(c) < 24:
            return {"1s": 0, "3s": 0, "24s": 0}

        return {
            "1s": ((c[-1] - c[-2]) / c[-2]) * 100,
            "3s": ((c[-1] - c[-4]) / c[-4]) * 100,
            "24s": ((c[-1] - c[-24]) / c[-24]) * 100
        }
    except Exception:
        return {"1s": 0, "3s": 0, "24s": 0}


def btc_gucu_v2_hesapla(degisim1, degisim3, degisim24, btc_d):
    """
    V4.25 BTC Gücü V2.
    Sadece BTC'den güçlü mü sorusuna bakmaz; 1s, 3s ve 24s farkını 0-10 puana çevirir.
    """
    fark1 = degisim1 - btc_d.get("1s", 0)
    fark3 = degisim3 - btc_d.get("3s", 0)
    fark24 = degisim24 - btc_d.get("24s", 0)

    puan = 0

    if fark1 >= 0.5:
        puan += 2
    elif fark1 >= 0:
        puan += 1

    if fark3 >= 3:
        puan += 4
    elif fark3 >= 1.5:
        puan += 3
    elif fark3 >= 0.5:
        puan += 2

    if fark24 >= 5:
        puan += 4
    elif fark24 >= 3:
        puan += 3
    elif fark24 >= 1:
        puan += 2

    return min(puan, 10), fark1, fark3, fark24


def lider_skoru_hesapla(hacim_kat, degisim1, degisim3, degisim24, btc_fark1, btc_fark3, btc_fark24, zirve_yakin, yeni_zirve):
    """
    V4.25 Lider Skoru.
    Coinin sadece hareket edip etmediğini değil, piyasanın liderlerinden biri olup olmadığını ölçer.
    """
    puan = 0

    if btc_fark24 >= 5:
        puan += 3
    elif btc_fark24 >= 2:
        puan += 2

    if btc_fark3 >= 2:
        puan += 2
    elif btc_fark3 >= 1:
        puan += 1

    if degisim24 >= 6:
        puan += 2
    elif degisim24 >= 3:
        puan += 1

    if hacim_kat >= 10 and degisim1 >= 0 and degisim3 > 0:
        puan += 2
    elif hacim_kat >= 5 and degisim3 > 0:
        puan += 1

    if yeni_zirve:
        puan += 1
    elif zirve_yakin:
        puan += 0.5

    return min(puan, 10)





def guc_skoru_hesapla(
    hacim_kat,
    degisim1,
    degisim3,
    degisim24,
    btc_guc_skoru,
    lider_skoru,
    haber_skoru,
    satis_baskisi,
    btc_fark3=0,
    zirve_yakin=False,
    yeni_zirve=False
):
    """
    Son çalışan Coin Radar eşiklerine uyarlanmış 0-100 aday skoru.
    Momentum daha ağır, yüksek hacim ise momentum/liderlik teyidi olmadan tek başına ödüllendirilmez.
    """
    hacim_puan = min(hacim_kat / 10, 1) * 18
    momentum_puan = min(max(degisim3, 0) / 6, 1) * 34
    btc_puan = (btc_guc_skoru / 10) * 20
    lider_puan = (lider_skoru / 10) * 15
    haber_puan = (min(haber_skoru, 20) / 20) * 10

    toplam = hacim_puan + momentum_puan + btc_puan + lider_puan + haber_puan

    # Son Coin Radar: 3s momentum ana ayırıcı.
    if degisim3 >= 6:
        toplam += 6
    elif degisim3 >= 4:
        toplam += 3
    elif degisim3 >= 2:
        toplam += 1

    # Çok yüksek hacim tek başına güçlü aday sayılmaz.
    if hacim_kat >= 15 and degisim3 >= 6:
        toplam += 2
    elif hacim_kat >= 10 and degisim3 >= 4:
        toplam += 1
    elif hacim_kat >= 10 and degisim3 < 4 and lider_skoru < 7:
        toplam -= 4

    if btc_fark3 >= 4:
        toplam += 2
    elif btc_fark3 >= 2:
        toplam += 1

    if lider_skoru >= 7:
        toplam += 2
    elif lider_skoru >= 5:
        toplam += 1

    if zirve_yakin or yeni_zirve:
        toplam += 1

    if satis_baskisi:
        toplam -= 12

    return round(max(min(toplam, 100), 0), 2)


def stable_coin_mi(symbol):
    coin = symbol.replace("TRY", "")
    return coin in STABLE_COINLER


def haber_puani(symbol):
    coin = symbol.replace("TRY", "").lower()
    puan = 0
    negatif_haber = False

    for kaynak in RSS_KAYNAKLARI:
        try:
            feed = feedparser.parse(kaynak)

            for item in feed.entries[:25]:
                baslik = item.title.lower()

                if coin in baslik:
                    puan += 8

                    for kelime in POZITIF:
                        if kelime in baslik:
                            puan += 5

                    for kelime in NEGATIF:
                        if kelime in baslik:
                            puan -= 15
                            negatif_haber = True
        except:
            pass

    puan = max(min(puan, 20), 0)

    if negatif_haber and puan < 10:
        puan = 0

    return puan



# ==========================================
# H MANTIĞI - TEKNİK ANALİZ KATMANI
# Commit: AI AL V3.2 - Roket RSI ust siniri 75
# Bu katman aday seçimini değiştirmez; Top 10 adayı analiz için zenginleştirir.
# ==========================================

def ema_hesapla(veriler, periyot):
    if len(veriler) < periyot:
        return None
    ema = sum(veriler[:periyot]) / periyot
    k = 2 / (periyot + 1)
    for fiyat in veriler[periyot:]:
        ema = fiyat * k + ema * (1 - k)
    return ema


def ema_serisi(veriler, periyot):
    if len(veriler) < periyot:
        return []
    sonuc = [None] * (periyot - 1)
    ema = sum(veriler[:periyot]) / periyot
    sonuc.append(ema)
    k = 2 / (periyot + 1)
    for fiyat in veriler[periyot:]:
        ema = fiyat * k + ema * (1 - k)
        sonuc.append(ema)
    return sonuc


def rsi_hesapla(kapanislar, periyot=14):
    if len(kapanislar) < periyot + 1:
        return None
    farklar = [kapanislar[i] - kapanislar[i - 1] for i in range(1, len(kapanislar))]
    kazanclar = [max(x, 0) for x in farklar]
    kayiplar = [max(-x, 0) for x in farklar]
    ort_kazanc = sum(kazanclar[:periyot]) / periyot
    ort_kayip = sum(kayiplar[:periyot]) / periyot
    for i in range(periyot, len(farklar)):
        ort_kazanc = ((ort_kazanc * (periyot - 1)) + kazanclar[i]) / periyot
        ort_kayip = ((ort_kayip * (periyot - 1)) + kayiplar[i]) / periyot
    if ort_kayip == 0:
        return 100.0
    rs = ort_kazanc / ort_kayip
    return 100 - (100 / (1 + rs))


def macd_hesapla(kapanislar):
    ema12 = ema_serisi(kapanislar, 12)
    ema26 = ema_serisi(kapanislar, 26)
    if not ema12 or not ema26:
        return None, None, None
    macd_seri = []
    for i in range(len(kapanislar)):
        if i < len(ema12) and i < len(ema26) and ema12[i] is not None and ema26[i] is not None:
            macd_seri.append(ema12[i] - ema26[i])
    if len(macd_seri) < 9:
        return None, None, None
    sinyal = ema_hesapla(macd_seri, 9)
    macd = macd_seri[-1]
    histogram = macd - sinyal if sinyal is not None else None
    return macd, sinyal, histogram


def atr_adx_hesapla(yuksekler, dusukler, kapanislar, periyot=14):
    if len(kapanislar) < (periyot * 2) + 1:
        return None, None
    tr, arti_dm, eksi_dm = [], [], []
    for i in range(1, len(kapanislar)):
        yukari = yuksekler[i] - yuksekler[i - 1]
        asagi = dusukler[i - 1] - dusukler[i]
        arti_dm.append(yukari if yukari > asagi and yukari > 0 else 0)
        eksi_dm.append(asagi if asagi > yukari and asagi > 0 else 0)
        tr.append(max(
            yuksekler[i] - dusukler[i],
            abs(yuksekler[i] - kapanislar[i - 1]),
            abs(dusukler[i] - kapanislar[i - 1])
        ))

    atr = sum(tr[:periyot]) / periyot
    arti_s = sum(arti_dm[:periyot])
    eksi_s = sum(eksi_dm[:periyot])
    dxler = []

    for i in range(periyot, len(tr)):
        atr = ((atr * (periyot - 1)) + tr[i]) / periyot
        arti_s = arti_s - (arti_s / periyot) + arti_dm[i]
        eksi_s = eksi_s - (eksi_s / periyot) + eksi_dm[i]
        arti_di = 100 * (arti_s / (atr * periyot)) if atr else 0
        eksi_di = 100 * (eksi_s / (atr * periyot)) if atr else 0
        toplam = arti_di + eksi_di
        dxler.append(100 * abs(arti_di - eksi_di) / toplam if toplam else 0)

    if len(dxler) < periyot:
        return atr, None
    adx = sum(dxler[:periyot]) / periyot
    for dx in dxler[periyot:]:
        adx = ((adx * (periyot - 1)) + dx) / periyot
    return atr, adx


def teknik_analiz_hesapla(symbol):
    try:
        d = veri_getir(symbol, 120)
        c = d.get("c", [])
        h = d.get("h", [])
        l = d.get("l", [])
        if len(c) < 55 or len(h) != len(c) or len(l) != len(c):
            return None

        ema20 = ema_hesapla(c, 20)
        ema50 = ema_hesapla(c, 50)
        rsi = rsi_hesapla(c, 14)
        macd, macd_sinyal, macd_hist = macd_hesapla(c)
        atr, adx = atr_adx_hesapla(h, l, c, 14)
        fiyat = c[-1]
        atr_yuzde = (atr / fiyat) * 100 if atr is not None and fiyat else None

        return {
            "ema20": round(ema20, 6) if ema20 is not None else None,
            "ema50": round(ema50, 6) if ema50 is not None else None,
            "rsi": round(rsi, 2) if rsi is not None else None,
            "macd": round(macd, 6) if macd is not None else None,
            "macd_sinyal": round(macd_sinyal, 6) if macd_sinyal is not None else None,
            "macd_hist": round(macd_hist, 6) if macd_hist is not None else None,
            "adx": round(adx, 2) if adx is not None else None,
            "atr": round(atr, 6) if atr is not None else None,
            "atr_yuzde": round(atr_yuzde, 2) if atr_yuzde is not None else None
        }
    except Exception as e:
        print(f"Teknik analiz hata ({symbol}):", e)
        return None


# ==========================================
# H MANTIĞI - KARAR MOTORU
# Radar ilk adayları bulur; bu katman teknik yapıyı AL / BEKLE / SAT-PAS kararına çevirir.
# ==========================================

def h_karar_hesapla(aday):
    """
    AI karar motoru V3 - bağımsız AL teyidi.
    Amaç: Coin Radar adayını otomatik onaylamak yerine bağımsız teknik AL teyidi vermek.
    AVNT/ENA gibi zayıf devam teyitlerinde AL'ı zorlaştırır;
    NAP/MIRA gibi güçlü trendleri ve H gibi istisnai Yıldız devamlarını korur.
    """
    teknik = aday.get("teknik")
    if not teknik:
        return {
            "ai_skoru": 0,
            "karar": "🟡 BEKLE",
            "risk": "Bilinmiyor",
            "nedenler": ["Teknik veri yetersiz"]
        }

    ema20 = teknik.get("ema20")
    ema50 = teknik.get("ema50")
    rsi = teknik.get("rsi")
    macd_hist = teknik.get("macd_hist")
    adx = teknik.get("adx")
    atr_yuzde = teknik.get("atr_yuzde")

    fiyat = aday.get("fiyat", 0)
    radar = aday.get("radar_skoru", 0)
    kategori = aday.get("radar_kategori", "")
    lider = aday.get("lider_skoru", 0)
    deg1 = aday.get("degisim1", 0)
    deg3 = aday.get("degisim3", 0)
    deg24 = aday.get("degisim24", 0)

    skor = 20.0
    nedenler = []

    # 1) Radar kalitesi: artık taban skoru şişirmiyor.
    skor += max(0, min((radar - 55) * 0.50, 20))

    # Radar alarm seviyesine küçük kalite bonusu.
    if "Yıldız" in kategori:
        skor += 10
        nedenler.append("Radar Yıldız")
    elif "Elit" in kategori:
        skor += 6
    elif "Trader" in kategori:
        skor += 4
    elif "Roket" in kategori:
        skor += 2

    # 2) EMA: önemli ama tek başına veto değil.
    if ema20 is not None and ema50 is not None:
        if ema20 > ema50:
            skor += 12
            nedenler.append("EMA trendi yukarı")
        else:
            skor -= 8
            nedenler.append("EMA trendi aşağı")

        if fiyat and ema20:
            if fiyat > ema20:
                skor += 4
            else:
                skor -= 5

    # 3) RSI: 50-65 en temiz giriş bölgesi.
    if rsi is not None:
        if 50 <= rsi <= 65:
            skor += 12
            nedenler.append("RSI sağlıklı güçlü bölgede")
        elif 45 <= rsi < 50:
            skor += 5
        elif 65 < rsi <= 72:
            skor += 6
            nedenler.append("RSI güçlü ama ısınıyor")
        elif 72 < rsi <= 78:
            skor += 1
            nedenler.append("RSI yüksek")
        elif 78 < rsi <= 85:
            skor -= 7
            nedenler.append("RSI aşırı alıma yakın")
        elif rsi > 85:
            skor -= 12
            nedenler.append("RSI aşırı alım")
        elif rsi < 40:
            skor -= 10
            nedenler.append("RSI zayıf")

    # 4) MACD: devam teyidi.
    macd_pozitif = macd_hist is not None and macd_hist > 0
    if macd_hist is not None:
        if macd_pozitif:
            skor += 12
            nedenler.append("MACD pozitif")
        else:
            skor -= 14
            nedenler.append("MACD negatif")

    # 5) ADX: AL kararının ana ayırıcılarından biri.
    if adx is not None:
        if adx >= 40:
            skor += 18
            nedenler.append("Trend çok güçlü")
        elif adx >= 30:
            skor += 14
            nedenler.append("Trend çok güçlü")
        elif adx >= 25:
            skor += 9
            nedenler.append("Trend güçlü")
        elif adx >= 20:
            skor += 3
            nedenler.append("Trend orta")
        else:
            skor -= 8
            nedenler.append("Trend gücü düşük")

    # 6) ATR: sağlıklı hareketi ödüllendir, aşırı oynaklığı azalt.
    if atr_yuzde is not None:
        if 1 <= atr_yuzde <= 4.5:
            skor += 5
        elif atr_yuzde > 7:
            skor -= 10
            nedenler.append("Volatilite çok yüksek")
        elif atr_yuzde > 5:
            skor -= 5
            nedenler.append("Volatilite yüksek")

    # 7) Göreceli güç ve liderlik.
    if aday.get("btcden_guclu"):
        skor += 4

    if lider >= 7:
        skor += 5
    elif lider >= 5:
        skor += 2

    # 8) Momentum kalitesi.
    # Çok yükselmiş olmak tek başına kötü değildir; devam gücü varsa H gibi hareketler korunur.
    if 1 <= deg1 <= 4:
        skor += 5
    elif 4 < deg1 <= 8:
        skor += 2
    elif deg1 > 8:
        skor -= 4

    if 3 <= deg3 <= 8:
        skor += 7
    elif 8 < deg3 <= 15:
        skor += 4
    elif deg3 > 15:
        skor += 1

    if deg24 > 30:
        skor -= 5

    # ADX düşükken 100/100 görünmesini engelle.
    if adx is not None:
        if adx < 20:
            skor = min(skor, 74)
        elif adx < 25:
            skor = min(skor, 82)
        elif adx < 30 and "Yıldız" not in kategori:
            skor = min(skor, 90)

    skor = round(max(0, min(skor, 100)), 1)

    # --------------------------------------------------
    # AL KAPISI V3
    # Radar adayı bulur; AI Assistant bağımsız teknik teyit ister.
    # Amaç: Radar'a düşen her coine otomatik AL dememek.
    # --------------------------------------------------
    ema_yukari = (
        ema20 is not None
        and ema50 is not None
        and ema20 > ema50
        and fiyat > ema20
    )

    rsi_temiz = rsi is not None and 48 <= rsi <= 75
    rsi_kabul = rsi is not None and 45 <= rsi <= 75

    # Normal Radar adayında artık daha sıkı teknik teyit:
    # EMA yukarı + sağlıklı RSI + güçlü ADX + pozitif MACD + yüksek AI skoru.
    normal_al = (
        not aday.get("erken_aday", False)
        and ema_yukari
        and rsi_temiz
        and macd_pozitif
        and adx is not None
        and adx >= 27
        and skor >= 80
    )

    # Çok güçlü Elit sinyalde RSI biraz daha geniş olabilir,
    # ama EMA ve trend teyidi yine zorunlu.
    elit_al = (
        "Elit" in kategori
        and radar >= 82
        and ema_yukari
        and rsi_kabul
        and macd_pozitif
        and adx is not None
        and adx >= 28
        and skor >= 85
    )

    # Yıldız istisnası:
    # H örneğinde olduğu gibi çok güçlü devam hareketlerinde EMA aşağı olsa bile
    # Radar + liderlik + ADX + MACD + momentum birlikte güçlü ise AL korunabilir.
    yildiz_istisna = (
        "Yıldız" in kategori
        and radar >= 90
        and lider >= 7
        and aday.get("btcden_guclu")
        and macd_pozitif
        and adx is not None
        and adx >= 28
        and rsi is not None
        and rsi >= 50
        and deg3 >= 8
        and skor >= 85
    )

    # Early Capture ayrı tutulur:
    # erken yakalamanın amacı daha düşük Radar skorunda teknik güçlenmeyi yakalamak.
    # Bu yüzden Radar yüksekliği değil, temiz teknik yapı aranır.
    erken_al = (
        aday.get("erken_aday", False)
        and ema_yukari
        and rsi is not None
        and 48 <= rsi <= 70
        and macd_pozitif
        and adx is not None
        and adx >= 30
        and skor >= 80
    )

    # --------------------------------------------------
    # GEÇ GİRİŞ KONTROLÜ V1
    # Sadece "Çoklu Güç / Güçleniyor" yolundan gelen coinlerde çalışır.
    # RED gibi erken yakalamaları etkilemez.
    #
    # Tek bir göstergeyle AL kesilmez. Aşağıdaki 5 geç-kalma işaretinden
    # en az 3'ü aynı anda varsa giriş artık fazla ilerlemiş kabul edilir:
    #   - RSI >= 70
    #   - 1s hareket >= %3
    #   - 3s hareket >= %4
    #   - fiyat EMA20'den >= 1.5 ATR uzak
    #   - fiyat son zirveye çok yakın
    # --------------------------------------------------
    ema20_uzaklik_yuzde = None
    ema20_atr_uzaklik = None

    if ema20 is not None and ema20 > 0 and fiyat:
        ema20_uzaklik_yuzde = ((fiyat - ema20) / ema20) * 100

        if atr_yuzde is not None and atr_yuzde > 0:
            ema20_atr_uzaklik = ema20_uzaklik_yuzde / atr_yuzde

    gec_giris_isaretleri = 0

    if rsi is not None and rsi >= 70:
        gec_giris_isaretleri += 1

    if deg1 >= 3:
        gec_giris_isaretleri += 1

    if deg3 >= 4:
        gec_giris_isaretleri += 1

    if ema20_atr_uzaklik is not None and ema20_atr_uzaklik >= 1.5:
        gec_giris_isaretleri += 1

    if aday.get("zirve_yakin", False):
        gec_giris_isaretleri += 1

    gec_giris = (
        aday.get("guc_havuzu_adayi", False)
        and not aday.get("erken_aday", False)
        and gec_giris_isaretleri >= 3
    )

    if gec_giris:
        nedenler.append("Giriş geç: hareket fazla ilerlemiş")

    if (normal_al or elit_al or yildiz_istisna or erken_al) and not gec_giris:
        karar = "🟢 AL"
    elif skor >= 55:
        karar = "🟡 BEKLE"
    else:
        karar = "🔴 SAT / PAS"

    # Risk sadece bilgilendirme; Telegram yalnızca AL kararında konuşuyor.
    if atr_yuzde is None:
        risk = "Bilinmiyor"
    elif atr_yuzde <= 3:
        risk = "Düşük"
    elif atr_yuzde <= 5:
        risk = "Orta"
    else:
        risk = "Yüksek"

    if not nedenler:
        nedenler.append("Teknik göstergeler karışık")

    return {
        "ai_skoru": skor,
        "karar": karar,
        "risk": risk,
        "nedenler": nedenler[:4]
    }


def canli_aday_guncelle(aday, simdi=None):
    """Bir adayı 3-10 dakikalık sessiz yarış havuzunda günceller."""
    if simdi is None:
        simdi = time.time()

    symbol = aday.get("symbol")
    if not symbol:
        return

    fiyat = float(aday.get("fiyat", 0) or 0)
    ai = float(aday.get("ai_skoru", 0) or 0)
    radar = float(aday.get("radar_skoru", 0) or 0)
    hacim = float(aday.get("hacim", 0) or 0)
    deg1 = float(aday.get("degisim1", 0) or 0)
    deg3 = float(aday.get("degisim3", 0) or 0)
    mikro = aday.get("mikro") or {}
    mikro_skor = float(mikro.get("mikro_skor", 0) or 0)
    d3k = float(mikro.get("d3", 0) or 0)
    d5k = float(mikro.get("d5", 0) or 0)
    btc_fark3 = float(aday.get("btc_fark3", 0) or 0)
    lider = float(aday.get("lider_skoru", 0) or 0)

    kayit = canli_aday_havuzu.get(symbol)
    if kayit is None:
        kayit = {
            "ilk_zaman": simdi,
            "son_zaman": simdi,
            "gozlem": 0,
            "ilk_fiyat": fiyat,
            "tepe_fiyat": fiyat,
            "ilk_ai": ai,
            "son_ai": ai,
            "ilk_hacim": hacim,
            "son_hacim": hacim,
            "ilk_deg3": deg3,
            "son_deg3": deg3,
            "en_iyi_yaris": 0,
        }
        canli_aday_havuzu[symbol] = kayit

    kayit["son_zaman"] = simdi
    kayit["gozlem"] = int(kayit.get("gozlem", 0)) + 1
    kayit["tepe_fiyat"] = max(float(kayit.get("tepe_fiyat", fiyat) or fiyat), fiyat)

    onceki_ai = float(kayit.get("son_ai", ai) or ai)
    onceki_hacim = float(kayit.get("son_hacim", hacim) or hacim)
    onceki_deg3 = float(kayit.get("son_deg3", deg3) or deg3)

    ai_ivme = ai - onceki_ai
    hacim_ivme = hacim - onceki_hacim
    momentum_ivme = deg3 - onceki_deg3
    tepe = float(kayit.get("tepe_fiyat", fiyat) or fiyat)
    tepe_geri = ((fiyat / tepe) - 1.0) * 100 if tepe > 0 else 0.0

    # 0-100 yarış skoru: final AL skoru değil, adaylar arası canlı göreli güç sıralamasıdır.
    yaris = 0.0
    yaris += min(ai, 100) * 0.38
    yaris += min(radar, 100) * 0.20
    yaris += min(max(lider, 0), 10) * 1.6
    yaris += min(max(btc_fark3, -2), 6) * 2.0
    yaris += min(max(deg3, 0), 8) * 1.5
    yaris += min(max(hacim, 0), 10) * 0.5
    # V21C: canlı yarışta kısa vade ana ağırlık.
    yaris += mikro_skor * 0.22
    yaris += min(max(d3k, 0), 3) * 2.0
    yaris += min(max(d5k, 0), 4) * 1.0
    if mikro.get("fiyat_ivmeleniyor"):
        yaris += 5
    if mikro.get("hacim_ivmeleniyor"):
        yaris += 5
    if mikro.get("mikro_basamak"):
        yaris += 5
    if mikro.get("sisti"):
        yaris -= 22

    if ai_ivme >= 2:
        yaris += 5
    elif ai_ivme < -3:
        yaris -= 7

    if hacim_ivme >= 0.5:
        yaris += 4
    elif hacim_ivme < -1.0:
        yaris -= 4

    if momentum_ivme >= 0.25:
        yaris += 4
    elif momentum_ivme < -0.35:
        yaris -= 5

    if aday.get("basamakli_trend"):
        yaris += 5
    if aday.get("btcden_guclu"):
        yaris += 4
    if aday.get("dinamik_teyit_sayisi", 0) >= 3:
        yaris += 4

    # Tepe sonrası sert geri verme, mesaj anında yakalanan 'pump sonu' riskini düşürür.
    if tepe_geri <= -2.0:
        yaris -= 12
    elif tepe_geri <= -1.0:
        yaris -= 6

    # Son saat mumu negatife dönmüşse canlı liderlik puanı kırılır.
    if deg1 < 0:
        yaris -= 6

    yaris = round(max(0, min(yaris, 100)), 1)

    kayit["son_ai"] = ai
    kayit["son_hacim"] = hacim
    kayit["son_deg3"] = deg3
    kayit["son_yaris"] = yaris
    kayit["en_iyi_yaris"] = max(float(kayit.get("en_iyi_yaris", 0) or 0), yaris)
    kayit["tepe_geri"] = round(tepe_geri, 2)
    kayit["son_aday"] = aday


def canli_havuzu_temizle(simdi=None):
    if simdi is None:
        simdi = time.time()
    for symbol, kayit in list(canli_aday_havuzu.items()):
        ilk = float(kayit.get("ilk_zaman", simdi) or simdi)
        son = float(kayit.get("son_zaman", simdi) or simdi)
        if simdi - ilk > CANLI_MAX_BEKLEME or simdi - son > 3 * 60:
            canli_aday_havuzu.pop(symbol, None)


def canli_kazananlari_bul(btc_3s, simdi=None):
    """Sadece olgunlaşmış, hâlâ AL olan ve yarışta üstte kalan en fazla 2 coini döndürür."""
    if simdi is None:
        simdi = time.time()

    if btc_3s <= BTC_SERT_ZAYIFLIK_ESIGI:
        print(f"[CANLI YARIŞ] BTC 3s %{btc_3s:.2f}: sert zayıflık, yeni AL kapalı.")
        return []

    uygun = []
    for symbol, kayit in canli_aday_havuzu.items():
        aday = kayit.get("son_aday") or {}
        yas = simdi - float(kayit.get("ilk_zaman", simdi) or simdi)
        gozlem = int(kayit.get("gozlem", 0) or 0)
        yaris = float(kayit.get("son_yaris", 0) or 0)
        tepe_geri = float(kayit.get("tepe_geri", 0) or 0)

        if yas < CANLI_MIN_BEKLEME:
            continue
        if gozlem < CANLI_MIN_GOZLEM:
            continue
        # V21B: canlı yarış artık gerçekten final karar katmanı.
        # Eski AL kapısını geçen coin doğrudan yarışabilir.
        # Eski motor BEKLE dese bile teknik yapı temiz + yarış çok güçlüyse
        # final AL üretilebilir. Böylece iki sıkı kapının üst üste binmesi önlenir.
        eski_al = "🟢 AL" in str(aday.get("karar", ""))
        teknik = aday.get("teknik") or {}
        ema20 = teknik.get("ema20")
        ema50 = teknik.get("ema50")
        rsi = teknik.get("rsi")
        macd_hist = teknik.get("macd_hist")
        adx = teknik.get("adx")
        fiyat = float(aday.get("fiyat", 0) or 0)
        ai = float(aday.get("ai_skoru", 0) or 0)
        radar = float(aday.get("radar_skoru", 0) or 0)
        deg3 = float(aday.get("degisim3", 0) or 0)
        btc_fark3 = float(aday.get("btc_fark3", 0) or 0)
        nedenler = aday.get("nedenler") or []
        mikro = aday.get("mikro") or {}
        mikro_skor = float(mikro.get("mikro_skor", 0) or 0)

        teknik_temiz = (
            ema20 is not None and ema50 is not None
            and fiyat > 0 and ema20 > ema50 and fiyat > ema20
            and rsi is not None and 47 <= rsi <= 74
            and macd_hist is not None and macd_hist > 0
            and adx is not None and adx >= 24
        )
        yaris_al = (
            not eski_al
            and teknik_temiz
            and ai >= 72
            and radar >= 48
            and yaris >= 70
            and btc_fark3 >= -0.2
            and not any("Giriş geç" in str(x) for x in nedenler)
        )

        # V21C kısa-vade AL yolu: eski 1s/3s hareketin büyümesini beklemez.
        # Coin henüz şişmeden 1-3-5-10dk ivmesi + hacim + mikro basamak ile öne çıkabilir.
        mikro_al = (
            not eski_al
            and mikro_skor >= 68
            and not mikro.get("sisti", False)
            and float(mikro.get("d3", 0) or 0) >= 0.35
            and float(mikro.get("d5", 0) or 0) >= 0.55
            and (mikro.get("hacim_ivmeleniyor") or float(mikro.get("hacim1x", 0) or 0) >= 1.5)
            and (mikro.get("fiyat_ivmeleniyor") or mikro.get("mikro_basamak"))
            and ai >= 66
            and radar >= 40
            and yaris >= 68
            and btc_fark3 >= -0.5
            and not any("Giriş geç" in str(x) for x in nedenler)
        )

        if mikro.get("sisti", False):
            continue
        if not (eski_al or yaris_al or mikro_al):
            continue
        if yaris < CANLI_MIN_YARIS_SKORU:
            continue
        if tepe_geri <= -2.0:
            continue
        if float(aday.get("degisim1", 0) or 0) < 0:
            continue

        aday["canli_final_tipi"] = "ESKI_AL" if eski_al else ("MIKRO_AL" if mikro_al else "YARIS_AL")
        uygun.append((yaris, ai, symbol, aday, kayit))

    uygun.sort(reverse=True, key=lambda x: (x[0], x[1]))
    return uygun[:CANLI_MAX_KAZANAN]


while True:
    try:
        print()
        print("AI COIN ASSISTANT - V21C ORTAK V4 | 4 MOTOR ORTAK AL KARARI | ERKEN + PARCALI + KAR KORU")
        print("--------------------------------")

        btc_d = btc_degisimleri()
        btc = btc_d.get("3s", 0)

        tarama_sayaci += 1
        tam_tarama = (tarama_sayaci == 1 or tarama_sayaci % TAM_TARAMA_DONGUSU == 0)

        if tam_tarama:
            print("Tarama modu: TAM PIYASA TARAMASI")
        else:
            print("Tarama modu: HIZLI HAREKET TARAMASI")

        ticker_response = requests.get(
            "https://api.btcturk.com/api/v2/ticker",
            timeout=10
        )
        ticker_response.raise_for_status()
        ticker = ticker_response.json().get("data", [])

        # Acik AL sinyallerini her 60 sn yonet.
        al_takip_guncelle(ticker)

        adaylar = []

        for coin in ticker:
            try:
                symbol = coin.get("pair", "")

                if not symbol.endswith("TRY"):
                    continue
                if symbol == "BTCTRY":
                    continue
                if stable_coin_mi(symbol):
                    continue
                if len(symbol) > 15:
                    continue

                # 1 dakikalık hızlı ön tarama:
                # Ticker fiyatını önceki dakikayla karşılaştır.
                try:
                    ticker_fiyat = float(coin.get("last", 0) or 0)
                except (TypeError, ValueError):
                    ticker_fiyat = 0

                onceki_fiyat = son_fiyatlar.get(symbol)
                hizli_degisim = 0.0

                if ticker_fiyat > 0 and onceki_fiyat and onceki_fiyat > 0:
                    hizli_degisim = ((ticker_fiyat - onceki_fiyat) / onceki_fiyat) * 100

                if ticker_fiyat > 0:
                    son_fiyatlar[symbol] = ticker_fiyat

                # 5 dakikalık tam taramalar arasında:
                # - %0.40+ hızlı hareket eden coinler,
                # - veya Çoklu Güç Havuzu'nda bulunan coinler
                # derin analiz edilir.
                simdi = time.time()
                izleme_bitis = guc_izleme_havuzu.get(symbol, 0)
                havuzda = izleme_bitis > simdi

                if izleme_bitis and not havuzda:
                    guc_izleme_havuzu.pop(symbol, None)

                if not tam_tarama and abs(hizli_degisim) < HIZLI_HAREKET_ESIGI and not havuzda:
                    continue

                if not tam_tarama:
                    kaynak = "HAVUZ" if havuzda and abs(hizli_degisim) < HIZLI_HAREKET_ESIGI else "HIZLI"
                    print(f"[{kaynak}] {symbol} | 1dk: %{hizli_degisim:.2f}")

                d = veri_getir(symbol, 24)
                o = d.get("o", [])
                h = d.get("h", [])
                c = d.get("c", [])
                v = d.get("v", [])

                if min(len(o), len(h), len(c), len(v)) < 24:
                    continue

                fiyat = c[-1]
                if not fiyat or not c[-2] or not c[-4] or not c[-24]:
                    continue

                degisim1 = ((c[-1] - c[-2]) / c[-2]) * 100
                degisim3 = ((c[-1] - c[-4]) / c[-4]) * 100
                degisim24 = ((c[-1] - c[-24]) / c[-24]) * 100

                son_hacim = v[-1]
                ort_hacim = sum(v[-6:-1]) / 5
                if ort_hacim <= 0:
                    continue

                hacim_kat = son_hacim / ort_hacim

                # V21C: 1-3-5-10 dakikalık erken hareket motoru.
                mikro = mikro_ivme_hesapla(symbol) or {}
                mikro_skor = float(mikro.get("mikro_skor", 0) or 0)

                btc_guc_skoru, btc_fark1, btc_fark3, btc_fark24 = btc_gucu_v2_hesapla(
                    degisim1, degisim3, degisim24, btc_d
                )

                btcden_guclu = btc_guc_skoru >= 4 and btc_fark3 >= 0.5
                son_mum_yesil = c[-1] > o[-1]
                zirve_yakin = fiyat > max(h[-12:-1]) * 0.995
                yeni_zirve = fiyat >= max(h[-24:-1])
                satis_baskisi = son_hacim > ort_hacim * 5 and degisim1 < 0
                haber_skoru = haber_puani(symbol)

                hacim_skoru = min(hacim_kat * 2, 10)
                momentum_skoru = max(0, degisim3 * 2)
                mum_skoru = 1 if son_mum_yesil else 0
                zirve_skoru = 1 if zirve_yakin else 0

                genel_skor = (
                    hacim_skoru * 0.50
                    + momentum_skoru * 0.20
                    + btc_guc_skoru * 0.15
                    + haber_skoru * 0.20
                    + mum_skoru
                    + zirve_skoru
                )

                kalite_skoru = (
                    hacim_skoru * 0.55
                    + momentum_skoru * 0.30
                    + btc_guc_skoru * 0.15
                    + mum_skoru
                    + zirve_skoru
                )

                if hacim_kat >= 5:
                    genel_skor += 4
                if hacim_kat >= 8:
                    genel_skor += 6

                if haber_skoru >= 15:
                    genel_skor += 4
                if haber_skoru > 0 and hacim_kat > 3:
                    genel_skor += 5

                if degisim24 > 10:
                    genel_skor -= 4
                if degisim3 > 7:
                    genel_skor -= 4
                if degisim1 > 4:
                    genel_skor -= 4
                if degisim24 > 0 and degisim3 > degisim24 * 0.85:
                    genel_skor -= 2
                if degisim3 > 0 and degisim1 > degisim3 * 0.65:
                    genel_skor -= 2
                if hacim_kat > 7 and degisim3 > 6:
                    genel_skor -= 3
                if satis_baskisi:
                    genel_skor -= 5

                if btc_fark3 >= 4:
                    genel_skor += 2
                elif btc_fark3 >= 2:
                    genel_skor += 1

                lider_skoru = lider_skoru_hesapla(
                    hacim_kat, degisim1, degisim3, degisim24,
                    btc_fark1, btc_fark3, btc_fark24,
                    zirve_yakin, yeni_zirve
                )

                if lider_skoru >= 7:
                    genel_skor += 2
                elif lider_skoru >= 5:
                    genel_skor += 1

                if zirve_yakin or yeni_zirve:
                    genel_skor += 1

                radar_skoru = guc_skoru_hesapla(
                    hacim_kat, degisim1, degisim3, degisim24,
                    btc_guc_skoru, lider_skoru, haber_skoru,
                    satis_baskisi, btc_fark3, zirve_yakin, yeni_zirve
                )

                # --------------------------------------------------
                # Early Capture V1 + gerçek Coin Radar alarm kapıları
                # --------------------------------------------------
                onceki = onceki_tarama.get(symbol)

                hacim_hizlaniyor = False
                momentum_hizlaniyor = False
                btc_farki_aciliyor = False
                lider_gucleniyor = False

                if onceki:
                    eski_hacim = onceki.get("hacim", hacim_kat)
                    eski_degisim3 = onceki.get("degisim3", degisim3)
                    eski_btc_fark3 = onceki.get("btc_fark3", btc_fark3)
                    eski_lider = onceki.get("lider_skoru", lider_skoru)

                    hacim_hizlaniyor = (
                        eski_hacim > 0
                        and hacim_kat >= eski_hacim * 1.25
                        and hacim_kat - eski_hacim >= 0.8
                    )
                    momentum_hizlaniyor = degisim3 - eski_degisim3 >= 0.45
                    btc_farki_aciliyor = btc_fark3 - eski_btc_fark3 >= 0.35
                    lider_gucleniyor = lider_skoru - eski_lider >= 1

                onceki_tarama[symbol] = {
                    "hacim": hacim_kat,
                    "degisim3": degisim3,
                    "btc_fark3": btc_fark3,
                    "lider_skoru": lider_skoru,
                    "zaman": time.time()
                }

                # Dinamik hareket teyitleri:
                # Bunlar RED/ATM tipi "nedenleri dolu" sinyallerin hareket tarafını oluşturur.
                dinamik_teyit_sayisi = sum([
                    bool(hacim_hizlaniyor),
                    bool(momentum_hizlaniyor),
                    bool(btc_farki_aciliyor),
                    bool(lider_gucleniyor),
                ])

                # Mevcut Early yolu korunuyor; sadece 3s üst sınırı 3'ten 5'e açıldı.
                # Böylece güçlenmeye devam eden coin Early ile Roket arasında boşluğa düşmez.
                erken_aday = (
                    2.5 <= hacim_kat < 8
                    and 0.5 <= degisim3 < 5
                    and degisim1 > 0
                    and btc_guc_skoru >= 3
                    and btc_fark3 >= 0
                    and radar_skoru >= 45
                    and kalite_skoru >= 6
                    and not satis_baskisi
                    and (
                        (hacim_hizlaniyor and momentum_hizlaniyor)
                        or (momentum_hizlaniyor and btc_farki_aciliyor)
                        or (hacim_hizlaniyor and lider_gucleniyor)
                    )
                )

                # ENA tipi basamaklı güçlenme:
                # Bir anda %0.40 sıçramasa bile 3s momentumunu koruyan,
                # hacmi canlı, BTC'ye göre zayıflamayan ve liderliği oluşan coinleri izler.
                basamakli_trend = False
                if onceki:
                    eski_degisim3 = onceki.get("degisim3", degisim3)
                    eski_hacim = onceki.get("hacim", hacim_kat)
                    basamakli_trend = (
                        1.0 <= degisim3 <= 10
                        and degisim1 > 0
                        and hacim_kat >= 1.8
                        and hacim_kat >= eski_hacim * 0.90
                        and degisim3 >= eski_degisim3 - 0.15
                        and btc_fark3 >= 0
                        and lider_skoru >= 4
                        and not satis_baskisi
                    )

                # Çoklu Güç Havuzu adayı:
                # Radar kategorisine girmese bile en az 2 dinamik teyidi olan
                # veya basamaklı trendi koruyan coin teknik motora alınır.
                guc_havuzu_adayi = (
                    not satis_baskisi
                    and radar_skoru >= 40
                    and kalite_skoru >= 5
                    and 0.5 <= degisim3 <= 10
                    and degisim1 > -0.5
                    and hacim_kat >= 1.8
                    and btc_fark3 >= -0.5
                    and (
                        (
                            dinamik_teyit_sayisi >= 3
                            and (hacim_hizlaniyor or momentum_hizlaniyor)
                        )
                        or (
                            basamakli_trend
                            and dinamik_teyit_sayisi >= 1
                        )
                    )
                )

                # V21C: Ana erken aday yolu. 1 saatlik hareketin büyümesini beklemez.
                mikro_aday = (
                    bool(mikro)
                    and not mikro.get("sisti", False)
                    and mikro_skor >= 48
                    and float(mikro.get("d3", 0) or 0) >= 0.20
                    and float(mikro.get("d5", 0) or 0) >= 0.30
                    and (mikro.get("fiyat_ivmeleniyor") or mikro.get("mikro_basamak"))
                    and (mikro.get("hacim_ivmeleniyor") or float(mikro.get("hacim1x", 0) or 0) >= 1.30)
                    and btc_fark3 >= -1.0
                    and not satis_baskisi
                )

                if erken_aday or guc_havuzu_adayi or mikro_aday:
                    guc_izleme_havuzu[symbol] = time.time() + GUC_IZLEME_SURESI

                yildiz_adayi = (
                    radar_skoru >= 88
                    and lider_skoru >= 7
                    and btc_guc_skoru >= 7
                    and kalite_skoru >= 14
                    and hacim_kat >= 5
                    and degisim1 > 1
                    and degisim3 >= 4
                    and zirve_yakin
                )

                elit_adayi = (
                    radar_skoru >= 74
                    and lider_skoru >= 5
                    and btc_guc_skoru >= 5
                    and kalite_skoru >= 10
                    and hacim_kat >= 8
                    and degisim1 > 0
                    and degisim3 >= 3
                    and btcden_guclu
                )

                trader_adayi = (
                    radar_skoru >= 55
                    and hacim_kat >= 15
                    and btcden_guclu
                    and btc_guc_skoru >= 4
                    and degisim3 >= 6
                )

                roket_adayi = (
                    radar_skoru >= 62
                    and kalite_skoru >= 8
                    and hacim_kat >= 5
                    and degisim1 > 0
                    and degisim3 >= 1.5
                    and not (hacim_kat >= 10 and degisim3 < 4 and lider_skoru < 7)
                    and btcden_guclu
                    and btc_guc_skoru >= 4
                    and (haber_skoru > 0 or lider_skoru >= 5)
                )

                if not (mikro_aday or erken_aday or guc_havuzu_adayi or yildiz_adayi or elit_adayi or trader_adayi or roket_adayi):
                    continue

                if yildiz_adayi:
                    radar_kategori = "⭐ Yıldız"
                elif elit_adayi:
                    radar_kategori = "🔥 Elit Roket"
                elif trader_adayi:
                    radar_kategori = "📊 Trader Hacim"
                elif roket_adayi:
                    radar_kategori = "🚀 Roket Adayı"
                elif mikro_aday:
                    radar_kategori = "🌱 Mikro Güçleniyor"
                elif erken_aday:
                    radar_kategori = "🌱 Erken Aday"
                else:
                    radar_kategori = "⚡ Güçleniyor"

                adaylar.append({
                    "symbol": symbol,
                    "fiyat": fiyat,
                    "radar_skoru": radar_skoru,
                    "radar_kategori": radar_kategori,
                    "erken_aday": erken_aday,
                    "mikro_aday": mikro_aday,
                    "mikro": mikro,
                    "guc_havuzu_adayi": guc_havuzu_adayi,
                    "basamakli_trend": basamakli_trend,
                    "dinamik_teyit_sayisi": dinamik_teyit_sayisi,
                    "hacim_hizlaniyor": hacim_hizlaniyor,
                    "momentum_hizlaniyor": momentum_hizlaniyor,
                    "btc_farki_aciliyor": btc_farki_aciliyor,
                    "lider_gucleniyor": lider_gucleniyor,
                    "genel_skor": round(genel_skor, 2),
                    "kalite_skoru": round(kalite_skoru, 2),
                    "hacim": round(hacim_kat, 2),
                    "degisim1": round(degisim1, 2),
                    "degisim3": round(degisim3, 2),
                    "degisim24": round(degisim24, 2),
                    "btcden_guclu": btcden_guclu,
                    "btc_fark3": round(btc_fark3, 2),
                    "btc_guc_skoru": btc_guc_skoru,
                    "lider_skoru": round(lider_skoru, 2),
                    "haber_skoru": haber_skoru,
                    "zirve_yakin": zirve_yakin,
                    "yeni_zirve": yeni_zirve
                })

            except Exception as e:
                print(f"Coin hata ({coin.get('pair', '?')}):", e)

        adaylar.sort(
            key=lambda x: (x["radar_skoru"], x["genel_skor"]),
            reverse=True
        )

        radar_top10 = adaylar[:10]

        # Radar Top10 dışında, hareket teyidi yüksek coinleri de teknik motora sok.
        guc_top10 = sorted(
            [a for a in adaylar if a.get("guc_havuzu_adayi")],
            key=lambda x: (
                x.get("dinamik_teyit_sayisi", 0),
                1 if x.get("basamakli_trend") else 0,
                x.get("genel_skor", 0),
                x.get("radar_skoru", 0),
            ),
            reverse=True
        )[:6]

        # Aynı coin iki listede varsa tek kez analiz edilir.
        top10 = []
        gorulenler = set()
        for aday in radar_top10 + guc_top10:
            symbol = aday.get("symbol")
            if symbol in gorulenler:
                continue
            gorulenler.add(symbol)
            top10.append(aday)

        print(
            f"Teknik havuz: RadarTop10={len(radar_top10)} | "
            f"ÇokluGüç={len(guc_top10)} | Benzersiz={len(top10)}"
        )

        # H mantığı: Radar Top10 + Çoklu Güç Havuzu üzerinde teknik analiz + karar motoru.
        for a in top10:
            teknik = teknik_analiz_hesapla(a["symbol"])
            a["teknik"] = teknik
            karar = h_karar_hesapla(a)
            a.update(karar)

            # ORTAK V1: 21C erkenlik + 13 gec filtre + kalite/devam puani.
            a["giris_turu_ortak"] = giris_zamanlamasi_ortak(a)
            giris_k, devam_g = ortak_kalite_hesapla(a)
            a["giris_kalitesi_ortak"] = giris_k
            a["devam_gucu_ortak"] = devam_g

            # Açık pozisyon kademelerinde kullanılmak üzere güncel analiz özeti.
            _t = a.get("teknik") or {}
            _m = a.get("mikro") or {}
            _ema20 = _t.get("ema20")
            _ema50 = _t.get("ema50")
            _fiyat = float(a.get("fiyat", 0) or 0)
            SON_ANALIZ[a["symbol"]] = {
                "zaman": time.time(),
                "giris_kalitesi": giris_k,
                "devam_gucu": devam_g,
                "rsi": _t.get("rsi"),
                "adx": _t.get("adx"),
                "macd_ok": (_t.get("macd_hist") is not None and _t.get("macd_hist") > 0),
                "ema_ok": (
                    _ema20 is not None and _ema50 is not None
                    and _ema20 > _ema50 and _fiyat > float(_ema20)
                ),
                "d3": _m.get("d3", 0),
                "d5": _m.get("d5", 0),
                "hacim1x": _m.get("hacim1x", 0),
                "hacim_ivme": _m.get("hacim3_ivme", 0),
            }

            # --------------------------------------------------
            # AL DEBUG LOG
            # Telegram'a hiçbir şey göndermez.
            # Railway logunda coin neden AL / BEKLE olduğunu gösterir.
            # --------------------------------------------------
            if teknik:
                ema20 = teknik.get("ema20")
                ema50 = teknik.get("ema50")
                rsi = teknik.get("rsi")
                macd_hist = teknik.get("macd_hist")
                adx = teknik.get("adx")
                fiyat = a.get("fiyat", 0)
                kategori = a.get("radar_kategori", "")
                ai_skor = a.get("ai_skoru", 0)

                ema_ok = (
                    ema20 is not None
                    and ema50 is not None
                    and fiyat
                    and ema20 > ema50
                    and fiyat > ema20
                )
                macd_ok = macd_hist is not None and macd_hist > 0

                if a.get("erken_aday"):
                    rsi_ok = rsi is not None and 48 <= rsi <= 70
                    adx_ok = adx is not None and adx >= 30
                    skor_ok = ai_skor >= 80
                elif "Elit" in kategori:
                    rsi_ok = rsi is not None and 45 <= rsi <= 75
                    adx_ok = adx is not None and adx >= 28
                    skor_ok = ai_skor >= 85
                elif "Yıldız" in kategori:
                    # Yıldızlarda normal teknik kapıyı göster.
                    # H tipi istisnai devam varsa karar motoru ayrıca AL verebilir.
                    rsi_ok = rsi is not None and 48 <= rsi <= 70
                    adx_ok = adx is not None and adx >= 30
                    skor_ok = ai_skor >= 85
                else:
                    rsi_ok = rsi is not None and 48 <= rsi <= 75
                    adx_ok = adx is not None and adx >= 27
                    skor_ok = ai_skor >= 80

                def durum(ok):
                    return "✅" if ok else "❌"

                rsi_txt = "NA" if rsi is None else f"{rsi:.1f}"
                adx_txt = "NA" if adx is None else f"{adx:.1f}"
                macd_txt = "NA" if macd_hist is None else f"{macd_hist:.5f}"

                print(
                    f"[AL DEBUG] {a['symbol']} | {a.get('karar', '🟡 BEKLE')} | "
                    f"{kategori} | "
                    f"EMA {durum(ema_ok)} | "
                    f"RSI {rsi_txt} {durum(rsi_ok)} | "
                    f"MACD {macd_txt} {durum(macd_ok)} | "
                    f"ADX {adx_txt} {durum(adx_ok)} | "
                    f"AI {ai_skor}/100 {durum(skor_ok)} | "
                    f"Radar {a.get('radar_skoru', 0)}"
                )
            else:
                print(
                    f"[AL DEBUG] {a['symbol']} | 🟡 BEKLE | "
                    f"Teknik veri alınamadı"
                )

        # V21: teknik analizden geçen bütün adaylar sessiz canlı yarış havuzuna yazılır.
        simdi_yaris = time.time()
        for a in top10:
            symbol = a.get("symbol")
            # V21B: tekrar mesaj kilidini eski BEKLE/AL kararına göre açma.
            # Canlı yarış artık final karar katmanı olduğu için aynı coin gereksiz tekrar etmez.
            canli_aday_guncelle(a, simdi_yaris)
        canli_havuzu_temizle(simdi_yaris)

        if canli_aday_havuzu:
            sirali_havuz = sorted(
                canli_aday_havuzu.items(),
                key=lambda kv: float(kv[1].get("son_yaris", 0) or 0),
                reverse=True
            )
            ozet = ", ".join(
                f"{sym}:{kayit.get('son_yaris', 0)}"
                for sym, kayit in sirali_havuz[:6]
            )
            print(f"[CANLI YARIŞ] Havuz: {ozet}")

        # İlk aday sıralamasını Radar yapar; H motorundan sonra en güçlü teknik fırsat üste çıkar.
        top10.sort(
            key=lambda x: (x.get("ai_skoru", 0), x.get("radar_skoru", 0)),
            reverse=True
        )

        if not top10:
            print("Şu an uygun aday yok.")
        else:
            # V21: AL kararı tek başına mesaj değildir. Önce canlı yarışta 3-10 dk olgunlaşır.
            kazananlar = canli_kazananlari_bul(btc, time.time())
            gonderilecekler = []

            for sira, (yaris_skoru, _ai, symbol, a, kayit) in enumerate(kazananlar, start=1):
                onceki_karar = son_ai_kararlar.get(symbol)

                # Aynı gerçek gönderimi yeniden yollama.
                if onceki_karar == "GONDERILDI":
                    continue

                a["yaris_skoru"] = yaris_skoru
                a["yaris_sirasi"] = sira
                a["yaris_suresi_dk"] = round((time.time() - kayit.get("ilk_zaman", time.time())) / 60, 1)
                a["yaris_gozlem"] = kayit.get("gozlem", 0)

                # FINAL ORTAK KAPI:
                # 21C yarışı kazanmış + geç kalmamış + teknik/kalite/devam yeterli.
                # ==========================================================
                # V4 - DÖRT MOTOR ORTAK AL KARARI
                # Tek bir motorun AL demesi yetmez. En az 3/4 onay gerekir.
                # ==========================================================
                gec = a.get("giris_turu_ortak") == "GEC"
                teknik = a.get("teknik") or {}
                mikro = a.get("mikro") or {}

                try:
                    adx = float(teknik.get("adx", 0) or 0)
                except Exception:
                    adx = 0.0
                try:
                    rsi = float(teknik.get("rsi", 999) or 999)
                except Exception:
                    rsi = 999.0
                try:
                    hacim = float(a.get("hacim", 0) or 0)
                except Exception:
                    hacim = 0.0
                try:
                    ai_skoru = float(a.get("ai_skoru", 0) or 0)
                except Exception:
                    ai_skoru = 0.0
                try:
                    radar_skoru = float(a.get("radar_skoru", 0) or 0)
                except Exception:
                    radar_skoru = 0.0
                try:
                    giris_k = float(a.get("giris_kalitesi_ortak", 0) or 0)
                except Exception:
                    giris_k = 0.0
                try:
                    devam_g = float(a.get("devam_gucu_ortak", 0) or 0)
                except Exception:
                    devam_g = 0.0

                ema20 = teknik.get("ema20")
                ema50 = teknik.get("ema50")
                fiyat = float(a.get("fiyat", 0) or 0)
                ema_ok = (
                    ema20 is not None and ema50 is not None
                    and float(ema20) > float(ema50)
                    and fiyat > float(ema20)
                )
                macd_ok = teknik.get("macd_hist") is not None and teknik.get("macd_hist") > 0

                d3m = float(mikro.get("d3", 0) or 0)
                d5m = float(mikro.get("d5", 0) or 0)
                hacim1x = float(mikro.get("hacim1x", 0) or 0)
                hacim_ivme = float(mikro.get("hacim3_ivme", 0) or 0)

                # Sert güvenlik kapısı: 4/4 onay olsa bile bunlar bozuksa AL yok.
                hard_block = (
                    gec
                    or rsi > 74
                    or adx < 24
                    or hacim < 0.50
                    or not ema_ok
                    or not macd_ok
                )

                # 1) 21C kısa-vade + canlı yarış
                oy_21c = (
                    yaris_skoru >= 72
                    and d3m >= 0
                    and d5m >= 0
                    and (hacim1x >= 0.80 or hacim_ivme >= 1.00)
                    and not gec
                )

                # 2) 13 AL erken/teknik motoru
                oy_13 = (
                    ai_skoru >= 84
                    and ema_ok
                    and macd_ok
                    and adx >= 27
                    and 50 <= rsi <= 72
                    and float(a.get("degisim1", 0) or 0) < 5.0
                    and float(a.get("degisim3", 0) or 0) < 8.0
                )

                # 3) Coin Radar giriş kalitesi + devam gücü
                oy_radar = (
                    giris_k >= 80
                    and devam_g >= 70
                    and radar_skoru >= 55
                    and hacim >= 0.50
                )

                # 4) Radar + AL teknik ortak teyidi
                teknik_puan = sum([
                    1 if ema_ok else 0,
                    1 if (50 <= rsi <= 72) else 0,
                    1 if adx >= 24 else 0,
                    1 if macd_ok else 0,
                ])
                oy_radar_al = (
                    teknik_puan == 4
                    and ai_skoru >= 82
                    and radar_skoru >= 50
                    and giris_k >= 75
                )

                oylar = {
                    "21C": oy_21c,
                    "13": oy_13,
                    "RADAR": oy_radar,
                    "RADAR+AL": oy_radar_al,
                }
                onay_sayisi = sum(1 for v in oylar.values() if v)
                a["dortlu_oylar"] = oylar
                a["dortlu_onay"] = onay_sayisi
                a["teknik_puan"] = teknik_puan

                # Yalnız 21C gördü diye artık AL mesajı çıkmaz.
                if hard_block or onay_sayisi < 3:
                    print(
                        f"[4 MOTOR] {symbol} sessiz izleme | "
                        f"Onay={onay_sayisi}/4 | Oylar={oylar} | "
                        f"AI={ai_skoru:.1f} Radar={radar_skoru:.1f} "
                        f"Giris={giris_k:.1f} Devam={devam_g:.1f} "
                        f"RSI={rsi:.1f} ADX={adx:.1f} Hacim={hacim:.2f}"
                    )
                    continue

                if (
                    onay_sayisi == 4
                    and giris_k >= 90
                    and devam_g >= 78
                    and ai_skoru >= 88
                ):
                    a["final_etiket"] = "🟢 ÇOK GÜÇLÜ AL"
                elif onay_sayisi == 4:
                    a["final_etiket"] = "🟢 GÜÇLÜ AL"
                elif a.get("giris_turu_ortak") == "ERKEN":
                    a["final_etiket"] = "🌱 ERKEN AL"
                else:
                    a["final_etiket"] = "🟢 AL"

                gonderilecekler.append(a)

            if not gonderilecekler:
                print("Canlı yarışta olgunlaşmış yeni AL kazananı yok. Telegram sessiz.")
            else:
                mesaj = (
                    "🟢 AL - CANLI YARIŞ KAZANANI\n"
                    f"BTC 3s: %{round(btc, 2)}\n\n"
                )

                for a in gonderilecekler:
                    teknik = a.get("teknik")
                    if not teknik:
                        continue

                    ema_yon = "Yukarı" if teknik["ema20"] > teknik["ema50"] else "Aşağı"
                    macd_yon = "Pozitif" if teknik["macd_hist"] is not None and teknik["macd_hist"] > 0 else "Negatif"
                    nedenler = list(a.get("nedenler", []))
                    hizlar = []

                    if a.get("hacim_hizlaniyor"):
                        hizlar.append("hacim hızlanıyor")
                    if a.get("momentum_hizlaniyor"):
                        hizlar.append("momentum hızlanıyor")
                    if a.get("btc_farki_aciliyor"):
                        hizlar.append("BTC farkı açılıyor")
                    if a.get("lider_gucleniyor"):
                        hizlar.append("lider güçleniyor")
                    if a.get("basamakli_trend"):
                        hizlar.append("basamaklı trend korunuyor")

                    if hizlar:
                        baslik = "Erken yakalama" if a.get("erken_aday") else "Hareket teyidi"
                        nedenler.insert(0, baslik + ": " + ", ".join(hizlar))

                    # 7+ gerçek olumlu neden varsa yalnızca Neden başına alarm koy.
                    # AL kararı veya filtrelerde hiçbir etkisi yok.
                    toplam_neden_sayisi = len(a.get("nedenler", [])) + len(hizlar)
                    neden_alarm = "🚨 🚨 " if toplam_neden_sayisi >= 7 else ""
                    neden = " • ".join(nedenler[:5])

                    mesaj += (
                        f"🟢 {a['symbol']} | {a.get('final_etiket', 'AL')}\n"
                        f"Kategori: {a.get('radar_kategori', '')} | Risk: {a.get('risk', 'Bilinmiyor')}\n\n"

                        f"📊 Skorlar\n"
                        f"AI: {a.get('ai_skoru', 0)}/100 | Radar: {a.get('radar_skoru', 0)}/100\n"
                        f"🎯 Giriş: {a.get('giris_kalitesi_ortak', 0)}/100 | 🚀 Devam: {a.get('devam_gucu_ortak', 0)}/100\n"
                        f"🏁 Yarış: #{a.get('yaris_sirasi', '?')} | Güç: {a.get('yaris_skoru', 0)}/100\n"
                        f"🤝 Ortak Onay: {a.get('dortlu_onay', 0)}/4 | Teknik: {a.get('teknik_puan', 0)}/4\n\n"

                        f"📈 Hareket\n"
                        f"1dk: %{(a.get('mikro') or {}).get('d1', 0)} | 3dk: %{(a.get('mikro') or {}).get('d3', 0)} | "
                        f"5dk: %{(a.get('mikro') or {}).get('d5', 0)} | 10dk: %{(a.get('mikro') or {}).get('d10', 0)}\n"
                        f"Hacim: {a.get('hacim', 0)}x | 1dk hacim: {(a.get('mikro') or {}).get('hacim1x', 0)}x | "
                        f"İvme: {(a.get('mikro') or {}).get('hacim3_ivme', 0)}x\n\n"

                        f"🧠 Teknik\n"
                        f"EMA: {ema_yon} | RSI: {teknik['rsi']} | ADX: {teknik['adx']}\n"
                        f"MACD: {macd_yon} | ATR: %{teknik['atr_yuzde']}\n\n"

                        f"💰 Plan\n"
                        f"%{KADEME_1_ORAN} ilk giriş | +%{KADEME_2_ORAN} teyit | +%{KADEME_3_ORAN} güçlü devam\n"
                        f"Kâr koru: +%{KAR_AL_1_ESIK:.1f} | Zarar sınırı: %{ILK_ZARAR_KES:.1f}\n\n"

                        f"💵 Fiyat: {round(a['fiyat'], 4)}\n"
                        f"✅ Onaylayanlar: {', '.join([k for k, v in a.get('dortlu_oylar', {}).items() if v])}\n"
                        f"{neden_alarm}📝 Neden: {neden}\n\n"
                    )

                print(mesaj)
                telegram_basarili = telegram_gonder(mesaj)

                # Mesaj gerçekten Telegram'a ulaştıysa pozisyonu açılmış say.
                # Token/chat sorunu varsa bir sonraki taramada yeniden denensin.
                if telegram_basarili:
                    for _aday in gonderilecekler:
                        al_takip_baslat(_aday)
                        son_ai_kararlar[_aday["symbol"]] = "GONDERILDI"
                    print("Ortak AL mesajı Telegram'a başarıyla gönderildi.")
                else:
                    print("Ortak AL bulundu fakat Telegram gönderilemedi; sonraki taramada tekrar denenecek.")

        print("60 sn bekleniyor...")
        time.sleep(TARAMA_SURESI)

    except Exception as e:
        print("Bot genel hata:", e)
        time.sleep(30)
