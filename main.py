# ==========================================
# BIRLESIK 4 BOT - V1 | V13 AL/SAT TABANI
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


# Humanity Railway projesiyle aynı Telegram değişkenlerini kullanır.
# Railway > Variables tarafında mevcut değerler otomatik okunur;
# token/chat ID GitHub koduna yazılmaz.
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
CHAT_IDS = [TELEGRAM_CHAT_ID] if TELEGRAM_CHAT_ID else []

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
GUC_IZLEME_SURESI = 5 * 60

# Aynı kararın tekrar Telegram gönderimini engeller.
son_ai_kararlar = {}

# ==========================================
# V13 AL/SAT - AL SONRASI POZİSYON TAKİBİ
# 13'ün erken/gevşek aday seçimi korunur.
# AL verilen coin her 60 sn takip edilir; emir göndermez, yalnızca mesaj üretir.
# ==========================================
TAKIP_DOSYASI = "v13_al_sat_takip_state.json"
AL_TAKIP = {}
KAR_AL_1_ESIK = 3.0          # İlk AL fiyatına göre +%3
KAR_AL_1_ORAN = 40           # İlk kârda pozisyonun %40'ını koru
ILK_ZARAR_KES = -1.50        # İlk AL doğrulanmazsa yaklaşık -%1.5
TEPE_GERI_VERME = -1.40      # Kâr alındıktan sonra tepeden %1.4 geri verme
MIN_KAR_KORUMA = 2.50        # Trailing korumanın aktif sayıldığı minimum kâr

# ==========================================
# V13 ERKENLİK FİLTRESİ
# Amaç: güçlü ama şişmiş coini AL diye kovalamamak.
# 13'ün aday bulma yeteneği korunur; sadece giriş türü yeniden sınıflanır.
# ==========================================
ERKEN_1S_MAX = 3.00
ERKEN_RSI_MAX = 70.0
GEC_1S_ESIK = 3.50
GEC_RSI_ESIK = 74.0
GEC_1S_MUTLAK = 5.00
GEC_3S_MUTLAK = 8.00

def giris_zamanlamasi(aday):
    teknik = aday.get("teknik") or {}
    d1 = float(aday.get("degisim1", 0) or 0)
    d3 = float(aday.get("degisim3", 0) or 0)
    rsi = teknik.get("rsi")
    try:
        rsi = float(rsi) if rsi is not None else None
    except Exception:
        rsi = None

    # Tek başına güçlü görünmek yeterli değil. Çoktan kaçmışsa yeni giriş yok.
    gec = (
        d1 >= GEC_1S_MUTLAK
        or d3 >= GEC_3S_MUTLAK
        or (d1 >= GEC_1S_ESIK and rsi is not None and rsi >= GEC_RSI_ESIK)
    )
    if gec:
        return "GEC", "⛔ GEÇ / ALMA"

    # Henüz şişmemiş, kısa güçlenme işaretleri olan sinyali öne çıkar.
    erken = (
        d1 <= ERKEN_1S_MAX
        and (rsi is None or rsi <= ERKEN_RSI_MAX)
        and (
            aday.get("erken_aday")
            or aday.get("hacim_hizlaniyor")
            or aday.get("momentum_hizlaniyor")
            or aday.get("basamakli_trend")
        )
    )
    if erken:
        return "ERKEN", "🌱 ERKEN AL"

    return "DEVAM", "🟢 DEVAM AL"


# ==========================================
# 4 BOT BIRLESIK KARAR KATMANI V1
# 13 erken yakalama + AI teknik kalite + Radar V5.5.1 giris/devam mantigi
# + 21B canli yaris fikrini tek puanda birlestirir.
# Amaç daha fazla mesaj değil; aynı fırsat için tek ve daha kaliteli karar.
# ==========================================
BIRLESIK_MIN_GIRIS = 68
BIRLESIK_MIN_DEVAM = 52
BIRLESIK_MIN_SKOR = 70
BIRLESIK_MAX_MESAJ = 2


def _clamp100(x):
    try:
        return round(max(0.0, min(100.0, float(x))), 1)
    except Exception:
        return 0.0


def birlesik_giris_kalitesi(aday):
    """Radar'ın giriş kalitesi fikrini mevcut 13 verileriyle hesaplar.
    Şişmiş hareketi özellikle cezalandırır; erken ama temiz yapıyı ödüllendirir.
    """
    teknik = aday.get("teknik") or {}
    ai = float(aday.get("ai_skoru", 0) or 0)
    radar = float(aday.get("radar_skoru", 0) or 0)
    d1 = float(aday.get("degisim1", 0) or 0)
    d3 = float(aday.get("degisim3", 0) or 0)
    hacim = float(aday.get("hacim", 0) or 0)
    btc_fark = float(aday.get("btc_fark3", 0) or 0)
    lider = float(aday.get("lider_skoru", 0) or 0)
    rsi = teknik.get("rsi")
    adx = teknik.get("adx")
    macd = teknik.get("macd_hist")
    ema20 = teknik.get("ema20")
    ema50 = teknik.get("ema50")
    fiyat = float(aday.get("fiyat", 0) or 0)

    skor = ai * 0.30 + radar * 0.20
    if ema20 is not None and ema50 is not None and fiyat > 0 and ema20 > ema50 and fiyat > ema20:
        skor += 12
    if macd is not None and macd > 0:
        skor += 8
    if rsi is not None:
        if 50 <= rsi <= 68:
            skor += 10
        elif 45 <= rsi <= 72:
            skor += 5
        elif rsi >= 75:
            skor -= 12
    if adx is not None:
        if adx >= 30:
            skor += 8
        elif adx >= 24:
            skor += 4
    if 1.4 <= hacim <= 7:
        skor += min(10, hacim * 1.5)
    elif hacim > 10 and d3 > 5:
        skor -= 8
    if btc_fark >= 1:
        skor += 5
    elif btc_fark < -0.5:
        skor -= 8
    if lider >= 5:
        skor += 5
    if aday.get("erken_aday"):
        skor += 6
    if aday.get("hacim_hizlaniyor"):
        skor += 4
    if aday.get("momentum_hizlaniyor"):
        skor += 4
    if aday.get("basamakli_trend"):
        skor += 5

    # Geç giriş cezası: güçlü görünse bile kovalamayalım.
    if d1 >= 4:
        skor -= 18
    elif d1 >= 3:
        skor -= 8
    if d3 >= 7:
        skor -= 15
    elif d3 >= 5:
        skor -= 7
    return _clamp100(skor)


def birlesik_devam_gucu(aday):
    """Radar V5.5.1'in devam gücü yaklaşımını kısa ve canlı verilerle özetler."""
    teknik = aday.get("teknik") or {}
    d1 = float(aday.get("degisim1", 0) or 0)
    d3 = float(aday.get("degisim3", 0) or 0)
    hacim = float(aday.get("hacim", 0) or 0)
    btc_fark = float(aday.get("btc_fark3", 0) or 0)
    lider = float(aday.get("lider_skoru", 0) or 0)
    adx = teknik.get("adx")
    macd = teknik.get("macd_hist")

    skor = 25
    if 0.2 <= d1 <= 2.8:
        skor += 12
    elif d1 < -0.2:
        skor -= 18
    if 0.5 <= d3 <= 5:
        skor += 12
    elif d3 > 7:
        skor -= 8
    if hacim >= 1.5:
        skor += min(14, hacim * 2)
    if aday.get("hacim_hizlaniyor"):
        skor += 12
    if aday.get("momentum_hizlaniyor"):
        skor += 12
    if aday.get("btc_farki_aciliyor"):
        skor += 7
    if aday.get("lider_gucleniyor") or lider >= 5:
        skor += 7
    if aday.get("basamakli_trend"):
        skor += 10
    if btc_fark >= 0.5:
        skor += 5
    if macd is not None and macd > 0:
        skor += 5
    if adx is not None and adx >= 25:
        skor += 5
    return _clamp100(skor)


def birlesik_yaris_skoru(aday):
    """21B'deki canlı yarış fikri: aynı taramadaki adayları tek puanla sıralar."""
    giris = birlesik_giris_kalitesi(aday)
    devam = birlesik_devam_gucu(aday)
    ai = float(aday.get("ai_skoru", 0) or 0)
    radar = float(aday.get("radar_skoru", 0) or 0)
    skor = giris * 0.35 + devam * 0.30 + ai * 0.20 + radar * 0.15
    if aday.get("erken_aday"):
        skor += 4
    return _clamp100(skor)


def birlesik_metrikleri_ekle(aday):
    aday["giris_kalitesi_birlesik"] = birlesik_giris_kalitesi(aday)
    aday["devam_gucu_birlesik"] = birlesik_devam_gucu(aday)
    aday["yaris_skoru_birlesik"] = birlesik_yaris_skoru(aday)
    return aday

def _pct(simdi, baz):
    return ((simdi / baz) - 1.0) * 100.0 if baz else 0.0

def _takip_yukle():
    global AL_TAKIP
    try:
        if os.path.exists(TAKIP_DOSYASI):
            with open(TAKIP_DOSYASI, "r", encoding="utf-8") as f:
                veri = json.load(f)
                if isinstance(veri, dict):
                    AL_TAKIP = veri
    except Exception as e:
        print("[TAKIP] state okunamadı:", e)
        AL_TAKIP = {}

def _takip_kaydet():
    try:
        gecici = TAKIP_DOSYASI + ".tmp"
        with open(gecici, "w", encoding="utf-8") as f:
            json.dump(AL_TAKIP, f, ensure_ascii=False, indent=2)
        os.replace(gecici, TAKIP_DOSYASI)
    except Exception as e:
        print("[TAKIP] state yazılamadı:", e)

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
        "max_fiyat": fiyat,
        "ilk_zaman": time.time(),
        "kar_al1": False,
        "kalan_oran": 100,
        "son_fiyat": fiyat,
    }
    _takip_kaydet()

def al_takip_guncelle(ticker):
    """13'ün verdiği AL sinyallerini fiyat bazlı takip eder.
    Çok mesaj üretmez: yalnızca KÂR AL 1 veya SAT/İPTAL olduğunda Telegram'a yazar.
    """
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
        eski_max = float(p.get("max_fiyat", fiyat) or fiyat)
        if fiyat > eski_max:
            p["max_fiyat"] = fiyat
        tepe = float(p.get("max_fiyat", fiyat) or fiyat)
        p["son_fiyat"] = fiyat

        getiri = _pct(fiyat, giris)
        tepeden = _pct(fiyat, tepe)

        # 1) İlk anlamlı kâr: tamamını satma; bir kısmını koru.
        if not p.get("kar_al1") and getiri >= KAR_AL_1_ESIK:
            p["kar_al1"] = True
            p["kalan_oran"] = 100 - KAR_AL_1_ORAN
            mesajlar.append(
                f"🟠 KÂR AL 1 - {symbol}\n"
                f"Fiyat: {fiyat:.4f} | İlk AL'a göre: %{getiri:+.2f}\n"
                f"Plan: %{KAR_AL_1_ORAN} kârı koru, %{100-KAR_AL_1_ORAN} taşımaya devam et\n"
                f"Tepe: {tepe:.4f}\n"
                f"Kalan bölüm trend bozulana kadar takipte."
            )
            degisti = True
            continue

        # 2) İlk kâr gelmeden yanlış sinyali büyütme.
        ilk_bozulma = (not p.get("kar_al1")) and getiri <= ILK_ZARAR_KES

        # 3) Kâr alındıktan sonra tepeden geri verme ile kalan kısmı koru.
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
                sebep = "kâr sonrası tepeden geri verme"
            else:
                baslik = "🔴 AL İPTAL / ÇIK"
                sebep = "ilk AL doğrulanmadı; zarar sınırı aşıldı"

            mesajlar.append(
                f"{baslik} - {symbol}\n"
                f"Fiyat: {fiyat:.4f} | İlk AL'a göre: %{getiri:+.2f}\n"
                f"Tepe: {tepe:.4f} | Tepeden: %{tepeden:+.2f}\n"
                f"Sebep: {sebep}\n"
                f"Yeniden güçlenirse yeni AL olarak tekrar değerlendirilebilir."
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
        print("TELEGRAM_BOT_TOKEN bulunamadı. Humanity Railway Variables kontrol et.")
        return
    if not CHAT_IDS:
        print("TELEGRAM_CHAT_ID bulunamadı. Humanity Railway Variables kontrol et.")
        return

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    for chat_id in CHAT_IDS:
        try:
            r = requests.get(
                url,
                params={"chat_id": chat_id, "text": mesaj},
                timeout=10
            )
            print(chat_id, r.text)
        except Exception as e:
            print(chat_id, e)


def veri_getir(symbol, saat=24):
    simdi = int(time.time())
    url = (
        f"https://graph-api.btcturk.com/v1/klines/history?"
        f"symbol={symbol}&resolution=60&from={simdi - (saat * 3600)}&to={simdi}"
    )
    return requests.get(url, timeout=10).json()



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

    if normal_al or elit_al or yildiz_istisna or erken_al:
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


while True:
    try:
        print()
        print("BIRLESIK 4 BOT - V1 | ERKEN + KALITE + YARIS + AL/SAT")
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

        # Daha önce AL verilmiş coinleri her 60 sn bağımsız takip et.
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
                            dinamik_teyit_sayisi >= 2
                            and (hacim_hizlaniyor or momentum_hizlaniyor)
                        )
                        or basamakli_trend
                    )
                )

                if erken_aday or guc_havuzu_adayi:
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

                if not (erken_aday or guc_havuzu_adayi or yildiz_adayi or elit_adayi or trader_adayi or roket_adayi):
                    continue

                if yildiz_adayi:
                    radar_kategori = "⭐ Yıldız"
                elif elit_adayi:
                    radar_kategori = "🔥 Elit Roket"
                elif trader_adayi:
                    radar_kategori = "📊 Trader Hacim"
                elif roket_adayi:
                    radar_kategori = "🚀 Roket Adayı"
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
        )[:10]

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
            birlesik_metrikleri_ekle(a)

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

        # İlk aday sıralamasını Radar yapar; H motorundan sonra en güçlü teknik fırsat üste çıkar.
        # 4 bot tek yarış: önce birleşik skor, sonra giriş kalitesi ve AI.
        top10.sort(
            key=lambda x: (
                x.get("yaris_skoru_birlesik", 0),
                x.get("giris_kalitesi_birlesik", 0),
                x.get("ai_skoru", 0),
            ),
            reverse=True
        )

        if not top10:
            print("Şu an uygun aday yok.")
        else:
            gonderilecekler = []

            for a in top10:
                symbol = a["symbol"]
                karar = a.get("karar", "🟡 BEKLE")
                onceki_karar = son_ai_kararlar.get(symbol)
                son_ai_kararlar[symbol] = karar

                # Telegram yalnızca gerçek AL kararlarında konuşur.
                # BEKLE ve SAT/PAS arka planda/loglarda izlenmeye devam eder.
                if "🟢 AL" not in karar:
                    continue

                # Aynı AL kararını tekrar gönderme.
                if onceki_karar == karar:
                    continue

                giris_turu, giris_etiketi = giris_zamanlamasi(a)

                # Geç / şişmiş sinyaller Telegram'a hiç gönderilmez.
                # Bunlar yeni pozisyon olarak da AL/SAT takibine alınmaz.
                if giris_turu == "GEC":
                    print(f"[AL DEBUG] {symbol} | geç giriş filtresi -> Telegram sessiz")
                    continue

                a["giris_turu"] = giris_turu
                a["giris_etiketi"] = giris_etiketi

                # Dört botun ortak final kapısı. Erken sinyale biraz esneklik,
                # devam sinyaline daha yüksek giriş kalitesi ister.
                gk = float(a.get("giris_kalitesi_birlesik", 0) or 0)
                dg = float(a.get("devam_gucu_birlesik", 0) or 0)
                ys = float(a.get("yaris_skoru_birlesik", 0) or 0)
                erken_esnek = giris_turu == "ERKEN" and gk >= 64 and dg >= 50 and ys >= 67
                normal_onay = gk >= BIRLESIK_MIN_GIRIS and dg >= BIRLESIK_MIN_DEVAM and ys >= BIRLESIK_MIN_SKOR
                if not (erken_esnek or normal_onay):
                    print(f"[BIRLESIK] {symbol} elendi | Giriş={gk} Devam={dg} Yarış={ys}")
                    continue

                gonderilecekler.append(a)

            # Aynı anda mesaj yağmurunu önle: yalnızca yarışın en iyi adayları.
            gonderilecekler.sort(
                key=lambda x: (x.get("yaris_skoru_birlesik", 0), x.get("giris_kalitesi_birlesik", 0)),
                reverse=True
            )
            gonderilecekler = gonderilecekler[:BIRLESIK_MAX_MESAJ]

            if not gonderilecekler:
                print("Yeni birleşik AL kararı yok. Telegram sessiz.")
            else:
                mesaj = (
                    "🧠 BİRLEŞİK COIN ASİSTANI - TEK KARAR\n"
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

                    # 6+ gerçek olumlu neden varsa yalnızca Neden başına alarm koy.
                    # AL kararı veya filtrelerde hiçbir etkisi yok.
                    toplam_neden_sayisi = len(a.get("nedenler", [])) + len(hizlar)
                    neden_alarm = "🚨 🚨 " if toplam_neden_sayisi >= 6 else ""
                    neden = " • ".join(nedenler[:5])

                    giris_etiketi = a.get("giris_etiketi", "🟢 DEVAM AL")
                    gec_mi = a.get("giris_turu") == "GEC"
                    plan_satiri = (
                        "📌 Plan: YENİ GİRİŞ YOK — hareket fazla ilerlemiş\n"
                        if gec_mi
                        else f"📌 Plan: ilk giriş max %33 | Kâr Al 1: +%{KAR_AL_1_ESIK:.1f} | Zarar sınırı: %{ILK_ZARAR_KES:.1f}\n"
                    )

                    mesaj += (
                        f"{a['symbol']} | {a.get('radar_kategori', '')}\n"
                        f"{giris_etiketi} | AI Skoru: {a.get('ai_skoru', 0)}/100 | Risk: {a.get('risk', 'Bilinmiyor')}\n"
                        f"{plan_satiri}"
                        f"🎯 Giriş Kalitesi: {a.get('giris_kalitesi_birlesik', 0)}/100 | "
                        f"🚀 Devam Gücü: {a.get('devam_gucu_birlesik', 0)}/100 | "
                        f"🏁 Yarış: {a.get('yaris_skoru_birlesik', 0)}/100\n"
                        f"Radar: {a['radar_skoru']}/100 | Fiyat: {round(a['fiyat'], 4)} | Hacim: {a['hacim']}x\n"
                        f"1s: %{a['degisim1']} | 3s: %{a['degisim3']} | 24s: %{a['degisim24']}\n"
                        f"EMA: {ema_yon} | RSI: {teknik['rsi']} | ADX: {teknik['adx']}\n"
                        f"MACD: {macd_yon} | ATR: %{teknik['atr_yuzde']}\n"
                        f"{neden_alarm}Neden: {neden}\n\n"
                    )

                # Yalnızca gerçekten yeni giriş yapılabilir sinyalleri AL/SAT takibine al.
                # GEÇ / ALMA mesajı bilgi amaçlıdır; pozisyon açılmış sayılmaz.
                for _aday in gonderilecekler:
                    if _aday.get("giris_turu") != "GEC":
                        al_takip_baslat(_aday)

                print(mesaj)
                telegram_gonder(mesaj)

        print("60 sn bekleniyor...")
        time.sleep(TARAMA_SURESI)

    except Exception as e:
        print("Bot genel hata:", e)
        time.sleep(30)
