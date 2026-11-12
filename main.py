"""
BTCTurk Hareket Döngüsü Motoru

1) ⚡ SAAT İÇİ DÖNÜŞ: 15-60 dakikalık sert çekilme sonrası dönüş.
2) 🔄 SAATLERCE DÜŞÜP DÖNEN: birkaç saatlik düşüş sonrası dönüş.
3) 🚀↩️ GÜÇLÜ YÜKSELİŞ GERİ ÇEKİLMESİ: güçlü hamle sonrası sağlıklı
   geri çekilmenin yeniden yukarı dönmesi.
4) 🌱⚡ YENİ HIZLANMA: henüz şişmeden fiyat ve hacim ivmesinin başlaması.

Erken aday ve normal teyit yalnız gölgede ölçülür. Telegram'a sadece
her yolun kendi yapısına göre seçilen "EN GÜÇLÜ" dönüş sınıfı gönderilir:
- Saat içi: erken tepkinin kaliteli ve kovalanmamış hali.
- Saatlerce düşen: normal teyit almış daha güvenli dönüş.
Özel API, bakiye, gerçek AL/SAT veya otomatik emir içermez.
"""

import json
import os
import statistics
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests


BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
_CHAT_ENV = os.getenv("CHAT_IDS", "").strip()
CHAT_IDS = (
    [int(x.strip()) for x in _CHAT_ENV.split(",") if x.strip()]
    if _CHAT_ENV else [2097448038]
)

TARAMA_SURESI = 60
HIZLI_TAKIP_SURESI = 15
TAKIP_EDILECEK_LIKIT_COIN = 25
MIN_TAHMINI_CIRO_TRY = 1_000_000
MAX_SPREAD = 0.60
MAX_HIZLI_ORDERBOOK = 12

# Tek coin yaşam döngüsü ve ardışık teyit.
# Aday -> Geri çekiliyor -> Tutunuyor -> Dönüş teyitli -> Mesajlandı/Bozuldu
ARDISIK_TARAMA_ADEDI = 3
ARDISIK_TARAMA_GECIS = 2
MIN_TEYIT_PUANI = 4
MIN_RISK_GETIRI = 0.90
TAHMINI_TOPLAM_MALIYET = float(os.getenv("TAHMINI_TOPLAM_MALIYET", "0.40"))

# Ön havuz ve gerçek zamanlı dönüş aşamaları. İlk tarama yalnız adayı bulur;
# Telegram kararı 15 saniyelik takipte verilir.
ADAY_HAVUZ_SURESI = 2 * 60 * 60
YENI_DIP_BEKLEME = 45
ERKEN_DONUS_MIN = 0.40
ERKEN_DONUS_MAX_SAATLIK = 1.20
ERKEN_DONUS_MAX_DERIN = 1.50
TEYIT_DONUS_MIN = 0.80
TEYIT_DONUS_MAX = 2.50
ERKEN_HIZ_MIN = 0.12
ERKEN_D3_MIN = -0.50
ERKEN_D5_MIN = -0.80
ERKEN_HACIM_MIN = 0.60
ERKEN_ORDERBOOK_MIN = 0.95
TEYIT_ORDERBOOK_MIN = 1.10
TEYIT_HACIM_MIN = 1.30

# Telegram'a çıkacak tek sınıf: EN GÜÇLÜ DÖNÜŞ.
# Üç günlük sonuç hareket yollarının aynı kapıyla seçilmemesi gerektiğini gösterdi.
# Saat içi yol erken tepkiyi, derin yol ise normal teyidi esas alır.
SAATLIK_GUCLU_DONUS_MIN = 0.45
SAATLIK_GUCLU_DONUS_MAX = 1.35
SAATLIK_GUCLU_D3_MIN = -0.30
SAATLIK_GUCLU_D5_MIN = -0.60
SAATLIK_GUCLU_HACIM_MIN = 0.80
SAATLIK_GUCLU_ORDERBOOK_MIN = 1.10
SAATLIK_GUCLU_HIZ_MIN = 0.12
SAATLIK_GUCLU_SPREAD_MAX = 0.22

DERIN_GUCLU_DONUS_MIN = 0.90
DERIN_GUCLU_DONUS_MAX = 2.20
DERIN_GUCLU_D3_MIN = 0.05
DERIN_GUCLU_D5_MIN = 0.05
DERIN_GUCLU_HACIM_MIN = 1.30
DERIN_GUCLU_ORDERBOOK_MIN = 1.15
DERIN_GUCLU_HIZ_MIN = 0.00
DERIN_GUCLU_SPREAD_MAX = 0.20

# Güçlü yükseliş sonrası geri çekilme:
# Önce belirgin bir yükseliş ve hacim hamlesi görülür. Zirveden çekilme
# yaşandıktan sonra yalnız canlı fiyat yeniden yukarı dönerse mesaj üretilir.
TREND_YUKSELIS_MIN = 4.00
TREND_CEKILIS_MIN = 1.80
TREND_CEKILIS_MAX = 8.00
TREND_TEPE_YAS_MAX = 30
TREND_HAMLE_HACIM_MIN = 1.50
TREND_KORUMA_MIN = 1.00
TREND_GUCLU_DONUS_MIN = 0.45
TREND_GUCLU_DONUS_MAX = 1.60
TREND_GUCLU_D3_MIN = -3.00
TREND_GUCLU_D5_MIN = -5.00
TREND_GUCLU_HACIM_MIN = 0.50
TREND_GUCLU_ORDERBOOK_MIN = 1.10
TREND_GUCLU_HIZ_MIN = 0.12
TREND_GUCLU_SPREAD_MAX = 0.22
TREND_GERI_VERME_ORAN_MIN = 18.0
TREND_GERI_VERME_ORAN_MAX = 72.0

# Yeni hızlanma: erken ivme var, fakat hareket henüz 10 dakikada şişmemiş.
HIZLANMA_D1_MIN = 0.18
HIZLANMA_D3_MIN = 0.35
HIZLANMA_D3_MAX = 2.50
HIZLANMA_D5_MIN = 0.35
HIZLANMA_D10_MAX = 3.50
HIZLANMA_HACIM_MIN = 1.20
HIZLANMA_DONUS_MIN = 0.35
HIZLANMA_DONUS_MAX = 1.50
HIZLANMA_ORDERBOOK_MIN = 1.10
HIZLANMA_HIZ_MIN = 0.12
HIZLANMA_SPREAD_MAX = 0.22

EN_GUCLU_BTC_5DK_MIN = -0.75
EN_GUCLU_PIYASA_5DK_MIN = -0.60

# Saat içi dönüş: yakın tepeden çekilme ve dipten toparlanma.
SAATLIK_CEKILIS_MIN = 1.50
SAATLIK_CEKILIS_MAX = 5.00
SAATLIK_DONUS_MIN = 0.50
SAATLIK_MUM_GOVDE_MIN = 0.08
SAATLIK_HACIM_MIN = 1.25

# Derin dönüş: 2-12 saatlik zayıflama ve daha güçlü toparlanma.
DERIN_CEKILIS_MIN = 4.00
DERIN_CEKILIS_MAX = 15.00
DERIN_DONUS_MIN = 1.00
DERIN_HACIM_MIN = 1.50
DERIN_KIRMIZI_PAY_MIN = 0.55

# Genel piyasa güvenlik kapısı. Kötü piyasa tamamen yasak değildir; fakat
# BTC/piyasa yeni bir sert aşağı dalga içindeyken dönüş mesajı verilmez.
BTC_5DK_ALT_SINIR = -1.50
PIYASA_5DK_ALT_SINIR = -1.10

AYNI_TIP_SOGUMA = 3 * 60 * 60
DERIN_SOGUMA = 6 * 60 * 60
SONUC_SURESI = 3 * 60 * 60
YANLIS_ESIK = -1.00
HEDEFLER = (1.0, 1.5, 2.0, 3.0)

_RAILWAY_VOLUME = os.getenv("RAILWAY_VOLUME_MOUNT_PATH", "").strip()
_DATA_DIR = _RAILWAY_VOLUME if _RAILWAY_VOLUME else ("/data" if os.path.isdir("/data") else ".")
KAYIT_DOSYA = os.getenv(
    "DONUS_GOLGE_DOSYA", os.path.join(_DATA_DIR, "btcturk_iki_donus_golge.json")
)
META_DOSYA = os.getenv(
    "DONUS_GOLGE_META_DOSYA", os.path.join(_DATA_DIR, "btcturk_iki_donus_meta.json")
)
HAREKET_DURUM_DOSYA = os.getenv(
    "HAREKET_DURUM_DOSYA", os.path.join(_DATA_DIR, "btcturk_hareket_dongusu.json")
)

SESSION = requests.Session()


def pct(yeni, eski):
    try:
        return ((float(yeni) / float(eski)) - 1.0) * 100.0 if float(eski) else 0.0
    except Exception:
        return 0.0


def atomik_json_yaz(dosya, veri):
    klasor = os.path.dirname(os.path.abspath(dosya))
    if klasor:
        os.makedirs(klasor, exist_ok=True)
    gecici = dosya + ".tmp"
    with open(gecici, "w", encoding="utf-8") as f:
        json.dump(veri, f, ensure_ascii=False)
    os.replace(gecici, dosya)


def json_oku(dosya, varsayilan):
    try:
        if os.path.exists(dosya):
            with open(dosya, "r", encoding="utf-8") as f:
                veri = json.load(f)
            return veri
    except Exception as e:
        print("Kayıt okunamadı:", dosya, e)
    return varsayilan


KAYITLAR = json_oku(KAYIT_DOSYA, [])
META = json_oku(META_DOSYA, {"son_rapor": time.time()})
_DURUM = json_oku(HAREKET_DURUM_DOSYA, {})
ADAY_HAVUZU = _DURUM.get("aday_havuzu", {}) if isinstance(_DURUM, dict) else {}
TICK_GECMISI = _DURUM.get("tick_gecmisi", {}) if isinstance(_DURUM, dict) else {}
PIYASA_DURUM = (
    _DURUM.get("piyasa_durum", {"btc5": 0.0, "piyasa5": 0.0, "zaman": 0.0})
    if isinstance(_DURUM, dict) else {"btc5": 0.0, "piyasa5": 0.0, "zaman": 0.0}
)


def hareket_durumu_kaydet():
    """Railway yeniden başlasa da aday evresi ve kısa teyit geçmişi korunur."""
    simdi = time.time()
    temiz_tick = {}
    for symbol, gecmis in TICK_GECMISI.items():
        yeni = [[float(z), float(f)] for z, f in gecmis if float(z) >= simdi - 120]
        if yeni:
            temiz_tick[symbol] = yeni
    atomik_json_yaz(
        HAREKET_DURUM_DOSYA,
        {
            "aday_havuzu": ADAY_HAVUZU,
            "tick_gecmisi": temiz_tick,
            "piyasa_durum": PIYASA_DURUM,
            "kayit_zamani": simdi,
        },
    )


def telegram_gonder(mesaj):
    if not BOT_TOKEN:
        print(mesaj)
        print("BOT_TOKEN yok; mesaj yalnız loga yazıldı.")
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    for chat_id in CHAT_IDS:
        try:
            r = SESSION.post(
                url, json={"chat_id": chat_id, "text": mesaj}, timeout=12
            )
            r.raise_for_status()
        except Exception as e:
            print("Telegram hatası:", chat_id, e)


def ticker_getir():
    r = SESSION.get("https://api.btcturk.com/api/v2/ticker", timeout=12)
    r.raise_for_status()
    return r.json().get("data", [])


def kline_getir(symbol, resolution, dakika):
    simdi = int(time.time())
    url = "https://graph-api.btcturk.com/v1/klines/history"
    r = SESSION.get(
        url,
        params={
            "symbol": symbol,
            "resolution": resolution,
            "from": simdi - dakika * 60,
            "to": simdi,
        },
        timeout=12,
    )
    r.raise_for_status()
    d = r.json()
    return d if isinstance(d, dict) else {}


def orderbook_ozeti(symbol):
    try:
        r = SESSION.get(
            "https://api.btcturk.com/api/v2/orderbook",
            params={"pairSymbol": symbol, "limit": 10},
            timeout=8,
        )
        r.raise_for_status()
        d = r.json().get("data", {})
        bids = d.get("bids", [])
        asks = d.get("asks", [])
        if not bids or not asks:
            return {}
        bid = float(bids[0][0])
        ask = float(asks[0][0])
        orta = (bid + ask) / 2.0
        spread = ((ask - bid) / orta) * 100.0 if orta else 99.0
        bid_tutar = sum(float(x[0]) * float(x[1]) for x in bids[:10])
        ask_tutar = sum(float(x[0]) * float(x[1]) for x in asks[:10])
        dengesizlik = bid_tutar / ask_tutar if ask_tutar > 0 else 0.0
        return {"bid": bid, "ask": ask, "spread": spread, "dengesizlik": dengesizlik}
    except Exception:
        return {}


def stable_coin(symbol):
    return symbol in {"USDTTRY", "USDCTRY", "DAITRY"}


def likit_coinleri_sec(ticker):
    secilen = []
    for x in ticker:
        try:
            symbol = str(x.get("pair", ""))
            if not symbol.endswith("TRY") or symbol == "BTCTRY" or stable_coin(symbol):
                continue
            fiyat = float(x.get("last", 0) or 0)
            hacim = float(x.get("volume", 0) or 0)
            ortalama = float(x.get("average", 0) or fiyat)
            ciro = hacim * ortalama
            bid = float(x.get("bid", 0) or 0)
            ask = float(x.get("ask", 0) or 0)
            orta = (bid + ask) / 2 if bid > 0 and ask > 0 else 0
            spread = ((ask - bid) / orta) * 100 if orta else 99
            if fiyat <= 0 or ciro < MIN_TAHMINI_CIRO_TRY or spread > MAX_SPREAD:
                continue
            secilen.append((ciro, symbol, fiyat, spread))
        except Exception:
            continue
    secilen.sort(reverse=True)
    return secilen[:TAKIP_EDILECEK_LIKIT_COIN]


def mumlari_duzelt(d):
    """Son kapanmış 1dk mumunu esas alan ortak görünüm."""
    try:
        o = [float(x) for x in d.get("o", [])]
        h = [float(x) for x in d.get("h", [])]
        l = [float(x) for x in d.get("l", [])]
        c = [float(x) for x in d.get("c", [])]
        v = [float(x) for x in d.get("v", [])]
        t = d.get("t", list(range(len(c))))
        n = min(len(o), len(h), len(l), len(c), len(v), len(t))
        if n < 15:
            return {}
        # Son eleman oluşmakta olan mum olabilir; sinyalde yalnız kapanmış -2 kullanılır.
        return {"o": o[:n], "h": h[:n], "l": l[:n], "c": c[:n], "v": v[:n], "t": t[:n]}
    except Exception:
        return {}


def coin_verisini_getir(symbol):
    try:
        bir = mumlari_duzelt(kline_getir(symbol, 1, 100))
        onbes = mumlari_duzelt(kline_getir(symbol, 15, 12 * 60))
        return symbol, bir, onbes
    except Exception as e:
        print(symbol, "kline hatası:", e)
        return symbol, {}, {}


def temel_1dk_ozet(d):
    if not d or len(d["c"]) < 65:
        return {}
    o, h, l, c, v, t = d["o"], d["h"], d["l"], d["c"], d["v"], d["t"]
    i = -2
    onceki_hacimler = v[-22:-2]
    med_hacim = statistics.median(onceki_hacimler) if onceki_hacimler else 0
    hacim_x = v[i] / med_hacim if med_hacim > 0 else 0
    govde = pct(c[i], o[i])
    d1 = pct(c[i], c[-3])
    d3 = pct(c[i], c[-5])
    d5 = pct(c[i], c[-7])
    d10 = pct(c[i], c[-12])
    tepe60 = max(h[-62:-2])
    dip15 = min(l[-17:-2])
    dip5 = min(l[-7:-2])
    cekilis = pct(dip15, tepe60)
    donus = pct(c[i], dip15)
    yesil = c[i] > o[i] and c[i] > c[i - 1]
    onceki_teyit = c[i] >= h[i - 1] or (govde >= 0.18 and d3 > 0)

    # Güçlü yükseliş -> zirveden geri çekilme -> yeniden dönüş profili.
    # Yalnız son 30 dakikada oluşan tepe kullanılır; eski yükseliş kovalanmaz.
    i_abs = len(c) - 2
    bas = max(0, i_abs - 45)
    tepe_aralik = h[bas:i_abs + 1]
    tepe_idx = bas + max(range(len(tepe_aralik)), key=lambda j: tepe_aralik[j])
    tepe = float(h[tepe_idx])
    tepe_yasi = i_abs - tepe_idx
    baz_son = max(bas + 1, tepe_idx - 4)
    baz_aralik = l[bas:baz_son]
    trend_baz = min(baz_aralik) if baz_aralik else float(l[bas])
    geri_aralik = l[tepe_idx + 1:i_abs + 1]
    trend_geri_dip = min(geri_aralik) if geri_aralik else float(l[i_abs])
    trend_yukselis = pct(tepe, trend_baz)
    trend_cekilis = pct(trend_geri_dip, tepe)
    trend_donus = pct(c[i_abs], trend_geri_dip)
    trend_koruma = pct(c[i_abs], trend_baz)

    baz_hacimler = v[max(0, tepe_idx - 35):max(1, tepe_idx - 5)]
    baz_hacim = statistics.median(baz_hacimler) if baz_hacimler else 0
    hamle_hacimler = v[max(0, tepe_idx - 5):tepe_idx + 1]
    hamle_hacim_x = max(hamle_hacimler) / baz_hacim if baz_hacim > 0 and hamle_hacimler else 0
    geri_hacimler = v[tepe_idx + 1:i_abs + 1]
    geri_hacim_ort = statistics.mean(geri_hacimler) if geri_hacimler else 0.0
    hamle_hacim_tepe = max(hamle_hacimler) if hamle_hacimler else 0.0
    geri_hacim_orani = geri_hacim_ort / hamle_hacim_tepe if hamle_hacim_tepe > 0 else 0.0
    geri_verme_orani = (
        abs(trend_cekilis) / trend_yukselis * 100.0 if trend_yukselis > 0 else 0.0
    )

    return {
        "kapanis": c[i], "mum_zamani": t[i], "hacim_x": hacim_x,
        "govde": govde, "d1": d1, "d3": d3, "d5": d5, "d10": d10,
        "tepe60": tepe60, "dip15": dip15, "dip5": dip5,
        "cekilis": cekilis, "donus": donus,
        "yesil": yesil, "teyit": onceki_teyit,
        "trend_yukselis": trend_yukselis,
        "trend_cekilis": trend_cekilis,
        "trend_donus": trend_donus,
        "trend_koruma": trend_koruma,
        "trend_geri_dip": trend_geri_dip,
        "trend_tepe_yasi": tepe_yasi,
        "trend_hamle_hacim_x": hamle_hacim_x,
        "trend_geri_hacim_orani": geri_hacim_orani,
        "trend_geri_verme_orani": geri_verme_orani,
    }


def strateji_profili(tip):
    """Her hareket türünün hedef ve bozulma tamponu."""
    return {
        "saatlik": {"ilk": 1.0, "guclu": 2.0, "tampon": 0.35},
        "derin": {"ilk": 1.5, "guclu": 3.0, "tampon": 0.50},
        "trend_geri": {"ilk": 1.5, "guclu": 3.0, "tampon": 0.35},
        "hizlanma": {"ilk": 1.5, "guclu": 3.0, "tampon": 0.35},
    }.get(tip, {"ilk": 1.0, "guclu": 2.0, "tampon": 0.40})


def giris_plani_hesapla(tip, fiyat, dip, spread):
    profil = strateji_profili(tip)
    pay = max(0.10, min(0.25, float(spread or 0) * 1.50))
    bozulma = float(dip) * (1.0 - profil["tampon"] / 100.0)
    risk = max(0.10, -pct(bozulma, fiyat))
    return {
        "giris_alt": fiyat * (1.0 - pay / 100.0),
        "giris_ust": fiyat * (1.0 + pay / 100.0),
        "bozulma": bozulma,
        "risk_yuzde": risk,
        "ilk_hedef": profil["ilk"],
        "guclu_hedef": profil["guclu"],
        "risk_getiri": profil["ilk"] / risk if risk > 0 else 0.0,
    }


def derin_ozet(d15, bir_ozet):
    if not d15 or len(d15["c"]) < 34 or not bir_ozet:
        return {}
    o, h, l, c = d15["o"], d15["h"], d15["l"], d15["c"]
    # Son 8 saatin tepesi ile son iki saatin dibi arasındaki ana düşüş.
    tepe8s = max(h[-34:-2])
    dip2s = min(l[-10:-1])
    cekilis = pct(dip2s, tepe8s)
    donus = pct(bir_ozet["kapanis"], dip2s)
    son8 = list(zip(o[-10:-2], c[-10:-2]))
    kirmizi_pay = sum(1 for ac, kap in son8 if kap < ac) / len(son8) if son8 else 0
    saat3 = pct(c[-2], c[-14]) if len(c) >= 14 else 0.0
    return {
        "tepe8s": tepe8s, "dip2s": dip2s, "cekilis": cekilis,
        "donus": donus, "kirmizi_pay": kirmizi_pay, "saat3": saat3,
    }


def son_mesaj_zamani(symbol, tip, asama=None):
    for k in reversed(KAYITLAR):
        kayit_asama = k.get("asama", "teyitli")
        if (
            k.get("symbol") == symbol
            and k.get("tip") == tip
            and (asama is None or kayit_asama == asama)
        ):
            return float(k.get("zaman", 0) or 0)
    return 0.0


def kayit_ac(
    symbol, tip, fiyat, oz, derin, ob, btc5, piyasa5,
    asama="teyitli", donus_override=None, cekilis_override=None,
    telegram_gonderildi=True, hiz=None, plan=None, teyit_puani=0,
    teyit_nedenleri=None,
):
    if tip == "derin":
        cekilis, donus = derin.get("cekilis"), derin.get("donus")
    elif tip == "trend_geri":
        cekilis, donus = oz.get("trend_cekilis"), oz.get("trend_donus")
    elif tip == "hizlanma":
        cekilis, donus = oz.get("d5"), oz.get("donus")
    else:
        cekilis, donus = oz.get("cekilis"), oz.get("donus")
    if cekilis_override is not None:
        cekilis = cekilis_override
    if donus_override is not None:
        donus = donus_override
    event = {
        "symbol": symbol, "tip": tip, "asama": asama,
        "telegram_gonderildi": telegram_gonderildi,
        "zaman": time.time(), "giris": fiyat,
        "mum_zamani": oz.get("mum_zamani"), "max_getiri": 0.0,
        "min_getiri": 0.0, "son_getiri": 0.0, "tamamlandi": False,
        "cekilis": cekilis, "donus": donus,
        "hacim_x": oz.get("hacim_x"), "d3": oz.get("d3"), "d5": oz.get("d5"),
        "spread": ob.get("spread"), "orderbook": ob.get("dengesizlik"),
        "btc5": btc5, "piyasa5": piyasa5, "canli_hiz": hiz,
        "durum": "MESAJLANDI" if telegram_gonderildi else asama.upper(),
        "teyit_puani": teyit_puani,
        "teyit_nedenleri": teyit_nedenleri or [],
        "onceki_yukselis": oz.get("trend_yukselis") if tip == "trend_geri" else None,
        "hamle_hacim_x": oz.get("trend_hamle_hacim_x") if tip == "trend_geri" else None,
    }
    if plan:
        event.update(plan)
    KAYITLAR.append(event)
    del KAYITLAR[:-4000]
    atomik_json_yaz(KAYIT_DOSYA, KAYITLAR)
    return event


def mesaj_uret(event):
    coin = str(event["symbol"]).replace("TRY", "")
    asama = event.get("asama", "teyitli")
    if event["tip"] == "derin":
        baslik = "🏆🔄 SAATLERCE DÜŞÜP DÖNEN — TEYİTLİ EN GÜÇLÜ"
        sure = "birkaç saatlik düşüş"
        durum = "✅ Birkaç saatlik düşüş sonrası 3/5dk momentum ve dönüş teyidi alındı."
    elif event["tip"] == "trend_geri":
        baslik = "🏆🚀↩️ GÜÇLÜ YÜKSELİŞ GERİ ÇEKİLMESİ — DÖNÜŞ TEYİTLİ"
        sure = "zirveden geri çekilme"
        durum = "✅ Güçlü yükseliş sonrası geri çekilme bitti; canlı dönüş ve alış derinliği teyit edildi."
    elif event["tip"] == "hizlanma":
        baslik = "🏆🌱⚡ YENİ HIZLANMA — TEYİTLİ EN GÜÇLÜ"
        sure = "son 5dk hareket"
        durum = "✅ Yeni ivme; üç canlı gözlemin en az ikisinde fiyat, hız ve derinlik korundu."
    else:
        baslik = "🏆⚡ SAAT İÇİ ERKEN DÖNÜŞ — EN GÜÇLÜ"
        sure = "15–60dk çekiliş"
        durum = "✅ Erken tepki; canlı hız + derinlik + spread kalitesiyle seçildi."
    onceki_yukselis_satiri = (
        f"🚀 Önceki yükseliş: %{float(event.get('onceki_yukselis', 0) or 0):+.2f}\n"
        if event.get("tip") == "trend_geri" else ""
    )
    return (
        f"{baslik}\n"
        f"{coin}\n"
        f"💰 Fiyat: {event['giris']:.8g}\n"
        f"🎯 Giriş bölgesi: {float(event.get('giris_alt', event['giris'])):.8g} – "
        f"{float(event.get('giris_ust', event['giris'])):.8g}\n"
        f"🛑 Yapı bozulma seviyesi: {float(event.get('bozulma', 0)):.8g} "
        f"(yaklaşık risk %{float(event.get('risk_yuzde', 0)):.2f})\n"
        f"🎯 İlk hedef: +%{float(event.get('ilk_hedef', 0)):.1f} | "
        f"Güçlü devam: +%{float(event.get('guclu_hedef', 0)):.1f} | "
        f"Risk/Getiri: {float(event.get('risk_getiri', 0)):.2f}x\n"
        f"🧠 Canlı teyit: {int(event.get('teyit_puani', 0) or 0)}/6\n"
        f"{onceki_yukselis_satiri}"
        f"📉 {sure}: %{float(event.get('cekilis', 0)):.2f}\n"
        f"📈 Dipten dönüş: %{float(event.get('donus', 0)):+.2f}\n"
        f"🟢 3dk: %{float(event.get('d3', 0)):+.2f} | 5dk: %{float(event.get('d5', 0)):+.2f}\n"
        f"🔥 Hacim: {float(event.get('hacim_x', 0)):.2f}x | "
        f"Spread: %{float(event.get('spread', 0)):.2f}\n"
        f"📚 Alış/Satış derinliği: {float(event.get('orderbook', 0)):.2f}x\n"
        f"⚡ Canlı dönüş hızı: %{float(event.get('canli_hiz', 0) or 0):+.2f}\n"
        f"🌍 BTC 5dk: %{float(event.get('btc5', 0)):+.2f} | "
        f"Piyasa 5dk: %{float(event.get('piyasa5', 0)):+.2f}\n"
        f"{durum}\n"
        "🧪 Sonuçlar strateji hedefine göre izlenir — gerçek emir yok"
    )


def teyit_gozlemi_ekle(aday, ob, hiz, fiyat, donus):
    gecmis = aday.setdefault("teyit_gecmisi", [])
    gecmis.append({
        "zaman": time.time(), "fiyat": float(fiyat), "donus": float(donus),
        "hiz": float(hiz), "spread": float(ob.get("spread", 99) or 99),
        "derinlik": float(ob.get("dengesizlik", 0) or 0),
    })
    del gecmis[:-ARDISIK_TARAMA_ADEDI]


def ardisik_teyit_hesapla(aday, tip, oz, btc5, piyasa5):
    """Tek anlık sıçrama yerine son üç canlı gözlemin en az ikisini ister."""
    gecmis = aday.get("teyit_gecmisi", [])
    if len(gecmis) < ARDISIK_TARAMA_ADEDI:
        return False, 0, [f"gözlem {len(gecmis)}/{ARDISIK_TARAMA_ADEDI}"]
    gecmis = gecmis[-ARDISIK_TARAMA_ADEDI:]
    if tip == "derin":
        derinlik_min, hiz_min, spread_max = DERIN_GUCLU_ORDERBOOK_MIN, DERIN_GUCLU_HIZ_MIN, DERIN_GUCLU_SPREAD_MAX
    elif tip == "trend_geri":
        derinlik_min, hiz_min, spread_max = TREND_GUCLU_ORDERBOOK_MIN, TREND_GUCLU_HIZ_MIN, TREND_GUCLU_SPREAD_MAX
    elif tip == "hizlanma":
        derinlik_min, hiz_min, spread_max = HIZLANMA_ORDERBOOK_MIN, HIZLANMA_HIZ_MIN, HIZLANMA_SPREAD_MAX
    else:
        derinlik_min, hiz_min, spread_max = SAATLIK_GUCLU_ORDERBOOK_MIN, SAATLIK_GUCLU_HIZ_MIN, SAATLIK_GUCLU_SPREAD_MAX

    derinlik_ok = sum(g["derinlik"] >= derinlik_min for g in gecmis) >= ARDISIK_TARAMA_GECIS
    hiz_ok = sum(g["hiz"] >= hiz_min for g in gecmis) >= ARDISIK_TARAMA_GECIS
    spread_ok = sum(g["spread"] <= spread_max for g in gecmis) >= ARDISIK_TARAMA_GECIS
    fiyat_ok = gecmis[-1]["fiyat"] >= min(g["fiyat"] for g in gecmis)
    piyasa_ok = btc5 >= EN_GUCLU_BTC_5DK_MIN and piyasa5 >= EN_GUCLU_PIYASA_5DK_MIN
    if tip == "trend_geri":
        hacim_ok = float(oz.get("trend_geri_hacim_orani", 99) or 99) <= 0.90
    elif tip == "hizlanma":
        hacim_ok = float(oz.get("hacim_x", 0) or 0) >= HIZLANMA_HACIM_MIN
    else:
        hacim_ok = float(oz.get("hacim_x", 0) or 0) >= 0.80
    kosullar = {
        "derinlik": derinlik_ok, "hız": hiz_ok, "spread": spread_ok,
        "fiyat tutunması": fiyat_ok, "hacim yapısı": hacim_ok, "piyasa": piyasa_ok,
    }
    puan = sum(bool(v) for v in kosullar.values())
    nedenler = [ad for ad, uygun in kosullar.items() if uygun]
    return puan >= MIN_TEYIT_PUANI and derinlik_ok and spread_ok, puan, nedenler


def normal_teyit_mi(donus, oz, ob, hiz):
    """Öğrenme grubu; Telegram üretmez."""
    return bool(
        TEYIT_DONUS_MIN <= donus <= TEYIT_DONUS_MAX
        and oz.get("yesil") and oz.get("teyit")
        and float(oz.get("d3", 0) or 0) > 0
        and float(oz.get("d5", 0) or 0) > 0
        and float(oz.get("hacim_x", 0) or 0) >= TEYIT_HACIM_MIN
        and float(ob.get("dengesizlik", 0) or 0) >= TEYIT_ORDERBOOK_MIN
        and hiz >= 0
    )


def en_guclu_teyit_mi(tip, donus, oz, ob, hiz, btc5, piyasa5):
    """Her dönüş yolunu kendi başarılı yapısına göre seçer."""
    piyasa_ok = bool(
        btc5 >= EN_GUCLU_BTC_5DK_MIN
        and piyasa5 >= EN_GUCLU_PIYASA_5DK_MIN
    )
    if not piyasa_ok:
        return False

    if tip == "saatlik":
        # Rapor: erken grup +%1/+%1.5 hedeflerinde açık ara daha başarılı.
        # Pozitif 3/5dk zorunluluğu koyulmaz; bu, erken avantajı yok eder.
        return bool(
            SAATLIK_GUCLU_DONUS_MIN <= donus <= SAATLIK_GUCLU_DONUS_MAX
            and float(oz.get("d3", 0) or 0) >= SAATLIK_GUCLU_D3_MIN
            and float(oz.get("d5", 0) or 0) >= SAATLIK_GUCLU_D5_MIN
            and float(oz.get("hacim_x", 0) or 0) >= SAATLIK_GUCLU_HACIM_MIN
            and float(ob.get("dengesizlik", 0) or 0) >= SAATLIK_GUCLU_ORDERBOOK_MIN
            and float(ob.get("spread", 99) or 99) <= SAATLIK_GUCLU_SPREAD_MAX
            and hiz >= SAATLIK_GUCLU_HIZ_MIN
        )

    if tip == "trend_geri":
        # Kırmızı mumun kendisi sinyal değildir. Önceki yükseliş/hacim yapısı
        # korunmalı; dipten canlı fiyat dönüşü ve alış derinliği birlikte gelmeli.
        return bool(
            TREND_GUCLU_DONUS_MIN <= donus <= TREND_GUCLU_DONUS_MAX
            and float(oz.get("trend_yukselis", 0) or 0) >= TREND_YUKSELIS_MIN
            and -TREND_CEKILIS_MAX <= float(oz.get("trend_cekilis", 0) or 0) <= -TREND_CEKILIS_MIN
            and int(oz.get("trend_tepe_yasi", 999) or 999) <= TREND_TEPE_YAS_MAX
            and float(oz.get("trend_koruma", 0) or 0) >= TREND_KORUMA_MIN
            and float(oz.get("trend_hamle_hacim_x", 0) or 0) >= TREND_HAMLE_HACIM_MIN
            and TREND_GERI_VERME_ORAN_MIN
            <= float(oz.get("trend_geri_verme_orani", 0) or 0)
            <= TREND_GERI_VERME_ORAN_MAX
            and float(oz.get("trend_geri_hacim_orani", 99) or 99) <= 0.90
            and float(oz.get("d3", 0) or 0) >= TREND_GUCLU_D3_MIN
            and float(oz.get("d5", 0) or 0) >= TREND_GUCLU_D5_MIN
            and float(oz.get("hacim_x", 0) or 0) >= TREND_GUCLU_HACIM_MIN
            and float(ob.get("dengesizlik", 0) or 0) >= TREND_GUCLU_ORDERBOOK_MIN
            and float(ob.get("spread", 99) or 99) <= TREND_GUCLU_SPREAD_MAX
            and hiz >= TREND_GUCLU_HIZ_MIN
            and (oz.get("yesil") or hiz >= TREND_GUCLU_HIZ_MIN + 0.08)
        )

    if tip == "hizlanma":
        return bool(
            HIZLANMA_DONUS_MIN <= donus <= HIZLANMA_DONUS_MAX
            and float(oz.get("d1", 0) or 0) >= HIZLANMA_D1_MIN
            and HIZLANMA_D3_MIN <= float(oz.get("d3", 0) or 0) <= HIZLANMA_D3_MAX
            and float(oz.get("d5", 0) or 0) >= HIZLANMA_D5_MIN
            and float(oz.get("d10", 0) or 0) <= HIZLANMA_D10_MAX
            and float(oz.get("hacim_x", 0) or 0) >= HIZLANMA_HACIM_MIN
            and float(ob.get("dengesizlik", 0) or 0) >= HIZLANMA_ORDERBOOK_MIN
            and float(ob.get("spread", 99) or 99) <= HIZLANMA_SPREAD_MAX
            and hiz >= HIZLANMA_HIZ_MIN
        )

    # Rapor: saatlerce düşenlerde normal teyit, erken girişten daha güvenli.
    # Bu yol yeşil/teyitli mum ve pozitif 3/5dk olmadan Telegram'a çıkmaz.
    return bool(
        DERIN_GUCLU_DONUS_MIN <= donus <= DERIN_GUCLU_DONUS_MAX
        and oz.get("yesil") and oz.get("teyit")
        and float(oz.get("d3", 0) or 0) >= DERIN_GUCLU_D3_MIN
        and float(oz.get("d5", 0) or 0) >= DERIN_GUCLU_D5_MIN
        and float(oz.get("hacim_x", 0) or 0) >= DERIN_GUCLU_HACIM_MIN
        and float(ob.get("dengesizlik", 0) or 0) >= DERIN_GUCLU_ORDERBOOK_MIN
        and float(ob.get("spread", 99) or 99) <= DERIN_GUCLU_SPREAD_MAX
        and hiz >= DERIN_GUCLU_HIZ_MIN
    )


def takip_guncelle(ticker):
    fiyatlar = {}
    for x in ticker:
        try:
            fiyatlar[str(x.get("pair", ""))] = float(x.get("last", 0) or 0)
        except Exception:
            pass
    simdi = time.time()
    degisti = False
    hedef_bildirimleri = []
    for k in KAYITLAR:
        if k.get("tamamlandi"):
            continue
        fiyat = fiyatlar.get(k.get("symbol"))
        giris = float(k.get("giris", 0) or 0)
        if not fiyat or giris <= 0:
            continue
        getiri = pct(fiyat, giris)
        k["max_getiri"] = round(max(float(k.get("max_getiri", 0) or 0), getiri), 3)
        k["min_getiri"] = round(min(float(k.get("min_getiri", 0) or 0), getiri), 3)
        k["son_getiri"] = round(getiri, 3)
        for hedef in HEDEFLER:
            anahtar = "hedef_" + str(hedef).replace(".", "_") + "_zamani"
            if getiri >= hedef and not k.get(anahtar):
                k[anahtar] = simdi
        bozulma = float(k.get("bozulma", 0) or 0)
        if bozulma > 0 and fiyat <= bozulma and not k.get("bozuldu_zamani"):
            k["bozuldu_zamani"] = simdi
        if (
            k.get("asama") == "en_guclu"
            and k.get("telegram_gonderildi")
            and float(k.get("max_getiri", 0) or 0) >= 3.0
            and not k.get("hedef3_bildirildi")
        ):
            k["hedef3_bildirildi"] = True
            k["hedef3_zamani"] = simdi
            coin = str(k.get("symbol", "")).replace("TRY", "")
            kaynak = (
                "🏆🔄 Saatlerce Düşüp Dönen — En Güçlü"
                if k.get("tip") == "derin"
                else (
                    "🏆🚀↩️ Güçlü Yükseliş Geri Çekilmesi — Dönüş Teyitli"
                    if k.get("tip") == "trend_geri"
                    else (
                        "🏆🌱⚡ Yeni Hızlanma — En Güçlü"
                        if k.get("tip") == "hizlanma"
                        else "🏆⚡ Saat İçi Dönüş — En Güçlü"
                    )
                )
            )
            hedef_bildirimleri.append(
                "🎯 +%3 HEDEF GÖRÜLDÜ — " + coin + "\n"
                + kaynak + "\n"
                + f"Başlangıç: {giris:.8g} | Güncel: {fiyat:.8g}\n"
                + f"Getiri: %{getiri:+.2f}"
            )
        if simdi - float(k.get("zaman", simdi) or simdi) >= SONUC_SURESI:
            k["tamamlandi"] = True
            k["tamamlanma_zamani"] = simdi
        degisti = True
    if degisti:
        atomik_json_yaz(KAYIT_DOSYA, KAYITLAR)
    for mesaj in hedef_bildirimleri:
        telegram_gonder(mesaj)


def oran(grup, hedef):
    return (
        sum(1 for k in grup if float(k.get("max_getiri", 0) or 0) >= hedef)
        / len(grup) * 100 if grup else 0.0
    )


def grup_ozeti(grup):
    if not grup:
        return 0.0, 0.0, 0.0, None
    tepe = statistics.mean(float(k.get("max_getiri", 0) or 0) for k in grup)
    dip = statistics.mean(float(k.get("min_getiri", 0) or 0) for k in grup)
    evler, sureler = [], []
    for k in grup:
        hedef = float(k.get("ilk_hedef", strateji_profili(k.get("tip"))["ilk"]) or 0)
        risk = float(k.get("risk_yuzde", 1.0) or 1.0)
        if float(k.get("max_getiri", 0) or 0) >= hedef:
            sonuc = hedef
            anahtar = "hedef_" + str(hedef).replace(".", "_") + "_zamani"
            if k.get(anahtar):
                sureler.append((float(k[anahtar]) - float(k.get("zaman", 0))) / 60.0)
        elif float(k.get("min_getiri", 0) or 0) <= -risk:
            sonuc = -risk
        else:
            sonuc = max(-risk, min(hedef, float(k.get("son_getiri", 0) or 0)))
        evler.append(sonuc - TAHMINI_TOPLAM_MALIYET)
    return tepe, dip, statistics.mean(evler), statistics.median(sureler) if sureler else None


def gunluk_rapor_gerekirse():
    simdi = time.time()
    son = float(META.get("son_rapor", simdi) or simdi)
    if simdi - son < 24 * 60 * 60:
        return
    pencere = [
        k for k in KAYITLAR
        if k.get("tamamlandi") and simdi - float(k.get("zaman", 0) or 0) <= 3 * 24 * 60 * 60
    ]
    satirlar = ["📊 3 GÜNLÜK HAREKET DÖNGÜSÜ RAPORU"]
    siniflar = (
        ("saatlik", "erken", "⚡ Saat İçi — Erken/Gölge"),
        ("saatlik", "teyitli", "⚡ Saat İçi — Normal Teyit/Gölge"),
        ("saatlik", "en_guclu", "🏆⚡ Saat İçi — Erken Güçlü/Gönderilen"),
        ("derin", "erken", "🔄 Saatlerce Düşen — Erken/Gölge"),
        ("derin", "teyitli", "🔄 Saatlerce Düşen — Normal Teyit/Gölge"),
        ("derin", "en_guclu", "🏆🔄 Saatlerce Düşen — Teyitli Güçlü/Gönderilen"),
        ("trend_geri", "erken", "🚀↩️ Güçlü Yükseliş Geri Çekilmesi — Erken/Gölge"),
        ("trend_geri", "teyitli", "🚀↩️ Güçlü Yükseliş Geri Çekilmesi — Normal Teyit/Gölge"),
        ("trend_geri", "en_guclu", "🏆🚀↩️ Güçlü Yükseliş Geri Çekilmesi — Gönderilen"),
        ("hizlanma", "erken", "🌱⚡ Yeni Hızlanma — Erken/Gölge"),
        ("hizlanma", "teyitli", "🌱⚡ Yeni Hızlanma — Normal Teyit/Gölge"),
        ("hizlanma", "en_guclu", "🏆🌱⚡ Yeni Hızlanma — Gönderilen"),
    )
    for tip, asama, ad in siniflar:
        g = [
            k for k in pencere
            if k.get("tip") == tip and k.get("asama", "teyitli") == asama
        ]
        yanlis = sum(
            1 for k in g
            if float(k.get("max_getiri", 0) or 0) < 1.5
            and float(k.get("min_getiri", 0) or 0) <= YANLIS_ESIK
        ) / len(g) * 100 if g else 0.0
        tepe, dip, ev, sure = grup_ozeti(g)
        sure_metin = f"{sure:.0f}dk" if sure is not None else "—"
        satirlar.append(
            f"• {ad}: n={len(g)} | +%1 %{oran(g,1):.1f} | +%1.5 %{oran(g,1.5):.1f} | "
            f"+%2 %{oran(g,2):.1f} | +%3 %{oran(g,3):.1f} | yanlış %{yanlis:.1f} | "
            f"tepe %{tepe:+.2f} | dip %{dip:+.2f} | maliyetli EV %{ev:+.2f} | ilk hedef {sure_metin}"
        )
    gec = [k for k in pencere if k.get("asama") == "gec"]
    eski_sessiz = [k for k in pencere if k.get("asama") == "teyitli_sessiz"]
    if eski_sessiz:
        satirlar.append(
            f"• 🔕 Önceki sürüm sessiz teyit: n={len(eski_sessiz)} | "
            f"+%1 %{oran(eski_sessiz,1):.1f} | +%2 %{oran(eski_sessiz,2):.1f}"
        )
    satirlar.append(
        f"• ⏱️ Geç kalan (>+%{TEYIT_DONUS_MAX:.1f}) ve gönderilmeyen: n={len(gec)} | "
        f"sonradan +%1 %{oran(gec,1):.1f} | +%2 %{oran(gec,2):.1f}"
    )
    satirlar.append(
        "• Dört yol ayrı ölçülür; EV varsayımı toplam %"
        f"{TAHMINI_TOPLAM_MALIYET:.2f} maliyet içerir. Gerçek emir yok."
    )
    durum_sayilari = {}
    for aday in ADAY_HAVUZU.values():
        durum = aday.get("durum", "ADAY")
        durum_sayilari[durum] = durum_sayilari.get(durum, 0) + 1
    if durum_sayilari:
        satirlar.append(
            "• Aktif yaşam döngüsü: "
            + " | ".join(f"{ad}={n}" for ad, n in sorted(durum_sayilari.items()))
        )
    telegram_gonder("\n".join(satirlar))
    META["son_rapor"] = simdi
    atomik_json_yaz(META_DOSYA, META)


def _ticker_haritasi(ticker):
    sonuc = {}
    for x in ticker:
        try:
            symbol = str(x.get("pair", ""))
            fiyat = float(x.get("last", 0) or 0)
            bid = float(x.get("bid", 0) or 0)
            ask = float(x.get("ask", 0) or 0)
            orta = (bid + ask) / 2.0 if bid > 0 and ask > 0 else 0.0
            spread = ((ask - bid) / orta) * 100.0 if orta else 99.0
            if symbol and fiyat > 0:
                sonuc[symbol] = {
                    "fiyat": fiyat, "bid": bid, "ask": ask, "spread": spread,
                }
        except Exception:
            continue
    return sonuc


def _hizli_momentum(symbol, simdi):
    gecmis = TICK_GECMISI.get(symbol, [])
    referanslar = [(z, f) for z, f in gecmis if z <= simdi - YENI_DIP_BEKLEME]
    if not referanslar:
        return None
    return pct(gecmis[-1][1], referanslar[-1][1])


def hizli_adaylari_takip(ticker):
    """Adayları 15 saniyede bir tek yaşam döngüsü içinde ilerletir."""
    simdi = time.time()
    fiyatlar = _ticker_haritasi(ticker)

    # 45-90 saniyelik gerçek zamanlı yönü ölçmek için kısa fiyat belleği.
    for symbol, aday in list(ADAY_HAVUZU.items()):
        bilgi = fiyatlar.get(symbol)
        if not bilgi or simdi > float(aday.get("sona_erme", 0) or 0):
            if simdi > float(aday.get("sona_erme", 0) or 0):
                aday["durum"] = "BOZULDU"
                ADAY_HAVUZU.pop(symbol, None)
                TICK_GECMISI.pop(symbol, None)
            continue
        gecmis = TICK_GECMISI.setdefault(symbol, [])
        gecmis.append((simdi, bilgi["fiyat"]))
        gecmis[:] = [(z, f) for z, f in gecmis if z >= simdi - 100]

        dip = float(aday.get("dip", bilgi["fiyat"]) or bilgi["fiyat"])
        if bilgi["fiyat"] < dip:
            aday["dip"] = bilgi["fiyat"]
            aday["son_dip_zamani"] = simdi
            aday["durum"] = "GERI_CEKILIYOR"
            aday["teyit_gecmisi"] = []
            # Yeni dip yeni bir dönüş denemesidir; fakat soğuma yine korunur.
            aday["gec_kayit"] = False

    btc5 = float(PIYASA_DURUM.get("btc5", 0) or 0)
    piyasa5 = float(PIYASA_DURUM.get("piyasa5", 0) or 0)
    piyasa_uygun = btc5 >= BTC_5DK_ALT_SINIR and piyasa5 >= PIYASA_5DK_ALT_SINIR

    degerlendirmeler = []
    for symbol, aday in ADAY_HAVUZU.items():
        bilgi = fiyatlar.get(symbol)
        if not bilgi or aday.get("guclu_gonderildi") or aday.get("gec_kayit"):
            continue
        dip = float(aday.get("dip", 0) or 0)
        if dip <= 0:
            continue
        donus = pct(bilgi["fiyat"], dip)
        hiz = _hizli_momentum(symbol, simdi)

        profil = strateji_profili(aday.get("tip"))
        erken_bozulma = dip * (1.0 - profil["tampon"] / 100.0)
        if bilgi["fiyat"] <= erken_bozulma:
            aday["durum"] = "BOZULDU"
            aday["teyit_gecmisi"] = []
            continue

        # +%2.5 üzerindeki ilk giriş artık kovalanmaz; yalnız gölgede kaydedilir.
        if donus > TEYIT_DONUS_MAX:
            if not aday.get("gec_kayit"):
                oz = aday.get("oz") or {}
                derin = aday.get("derin") or {}
                ob = {"spread": bilgi.get("spread", 99), "dengesizlik": 0.0}
                kayit_ac(
                    symbol, aday["tip"], bilgi["fiyat"], oz, derin, ob,
                    btc5, piyasa5, asama="gec", donus_override=donus,
                    cekilis_override=aday.get("cekilis"), telegram_gonderildi=False,
                    hiz=hiz,
                )
            aday["gec_kayit"] = True
            continue

        dipten_sure = simdi - float(aday.get("son_dip_zamani", simdi) or simdi)
        if (
            dipten_sure < YENI_DIP_BEKLEME
            or hiz is None
            or donus < ERKEN_DONUS_MIN - 0.10
            or bilgi.get("spread", 99) > MAX_SPREAD
        ):
            continue
        aday["durum"] = "TUTUNUYOR"
        degerlendirmeler.append((donus, symbol, aday, bilgi, hiz))

    # API yükünü sınırlamak için yalnız dönüş bandına en yakın adaylarda derinlik bakılır.
    degerlendirmeler.sort(key=lambda x: x[0], reverse=True)
    degerlendirmeler = degerlendirmeler[:MAX_HIZLI_ORDERBOOK]
    orderbookler = {}
    if degerlendirmeler:
        with ThreadPoolExecutor(max_workers=6) as havuz:
            gelecekler = {
                havuz.submit(orderbook_ozeti, symbol): symbol
                for _, symbol, _, _, _ in degerlendirmeler
            }
            for gelecek in as_completed(gelecekler):
                orderbookler[gelecekler[gelecek]] = gelecek.result()

    for donus, symbol, aday, bilgi, hiz in degerlendirmeler:
        ob = orderbookler.get(symbol) or {}
        if not ob or ob.get("spread", 99) > MAX_SPREAD or not piyasa_uygun:
            continue
        tip = aday["tip"]
        soguma = DERIN_SOGUMA if tip == "derin" else AYNI_TIP_SOGUMA
        oz = aday.get("oz") or {}
        derin = aday.get("derin") or {}
        fiyat = float(ob.get("ask", 0) or bilgi["fiyat"])

        teyit_gozlemi_ekle(aday, ob, hiz, fiyat, donus)
        ardisik_ok, teyit_puani, teyit_nedenleri = ardisik_teyit_hesapla(
            aday, tip, oz, btc5, piyasa5
        )
        aday["teyit_puani"] = teyit_puani
        aday["teyit_nedenleri"] = teyit_nedenleri

        en_guclu = en_guclu_teyit_mi(tip, donus, oz, ob, hiz, btc5, piyasa5)
        son_guclu = son_mesaj_zamani(symbol, tip, "en_guclu")
        plan = giris_plani_hesapla(tip, fiyat, float(aday.get("dip", fiyat)), ob.get("spread", 0))
        if (
            en_guclu and ardisik_ok
            and plan["risk_getiri"] >= MIN_RISK_GETIRI
            and not aday.get("guclu_gonderildi")
            and simdi - son_guclu >= soguma
        ):
            aday["durum"] = "DONUS_TEYITLI"
            event = kayit_ac(
                symbol, tip, fiyat, oz, derin, ob, btc5, piyasa5,
                asama="en_guclu", donus_override=donus,
                cekilis_override=aday.get("cekilis"),
                telegram_gonderildi=True, hiz=hiz, plan=plan,
                teyit_puani=teyit_puani, teyit_nedenleri=teyit_nedenleri,
            )
            aday["guclu_gonderildi"] = True
            aday["durum"] = "MESAJLANDI"
            telegram_gonder(mesaj_uret(event))
            continue

        # Normal teyit yalnız öğrenme kaydıdır. Güçlenme ihtimali için aday
        # havuzda tutulur ve sonraki hızlı taramalarda yeniden değerlendirilir.
        teyitli = normal_teyit_mi(donus, oz, ob, hiz)
        if teyitli:
            if not aday.get("teyit_kayitli"):
                kayit_ac(
                    symbol, tip, fiyat, oz, derin, ob, btc5, piyasa5,
                    asama="teyitli", donus_override=donus,
                    cekilis_override=aday.get("cekilis"),
                    telegram_gonderildi=False, hiz=hiz,
                )
                aday["teyit_kayitli"] = True
            continue

        erken_ust = (
            ERKEN_DONUS_MAX_DERIN
            if tip == "derin"
            else (TREND_GUCLU_DONUS_MAX if tip == "trend_geri" else ERKEN_DONUS_MAX_SAATLIK)
        )
        erken = bool(
            ERKEN_DONUS_MIN <= donus <= erken_ust
            and hiz >= ERKEN_HIZ_MIN
            and float(oz.get("d3", 0) or 0) >= ERKEN_D3_MIN
            and float(oz.get("d5", 0) or 0) >= ERKEN_D5_MIN
            and float(oz.get("hacim_x", 0) or 0) >= ERKEN_HACIM_MIN
            and float(ob.get("dengesizlik", 0) or 0) >= ERKEN_ORDERBOOK_MIN
        )
        if (
            erken
            and not aday.get("erken_kayitli")
            and simdi - son_mesaj_zamani(symbol, tip, "erken") >= soguma
        ):
            kayit_ac(
                symbol, tip, fiyat, oz, derin, ob, btc5, piyasa5,
                asama="erken", donus_override=donus,
                cekilis_override=aday.get("cekilis"),
                telegram_gonderildi=False, hiz=hiz,
            )
            aday["erken_kayitli"] = True

    hareket_durumu_kaydet()


def tara():
    ticker = ticker_getir()
    takip_guncelle(ticker)
    secilen = likit_coinleri_sec(ticker)
    symbol_list = [x[1] for x in secilen]
    if "BTCTRY" not in symbol_list:
        symbol_list.append("BTCTRY")

    veriler = {}
    with ThreadPoolExecutor(max_workers=8) as havuz:
        gelecekler = {havuz.submit(coin_verisini_getir, s): s for s in symbol_list}
        for gelecek in as_completed(gelecekler):
            symbol, bir, onbes = gelecek.result()
            veriler[symbol] = (bir, onbes)

    ozetler = {s: temel_1dk_ozet(veriler.get(s, ({}, {}))[0]) for s in symbol_list}
    btc5 = float((ozetler.get("BTCTRY") or {}).get("d5", 0) or 0)
    piyasa5_list = [
        float((ozetler.get(s) or {}).get("d5", 0) or 0)
        for s in symbol_list if s != "BTCTRY" and ozetler.get(s)
    ]
    piyasa5 = statistics.median(piyasa5_list) if piyasa5_list else 0.0
    PIYASA_DURUM.update({"btc5": btc5, "piyasa5": piyasa5, "zaman": time.time()})
    if btc5 < BTC_5DK_ALT_SINIR or piyasa5 < PIYASA_5DK_ALT_SINIR:
        print(f"Yeni düşüş dalgası: BTC5={btc5:+.2f}, piyasa5={piyasa5:+.2f}; gönderim bekler.")

    simdi = time.time()
    for _, symbol, ticker_fiyat, ticker_spread in secilen:
        oz = ozetler.get(symbol) or {}
        if not oz:
            continue
        d15 = veriler.get(symbol, ({}, {}))[1]
        derin = derin_ozet(d15, oz)
        mevcut = ADAY_HAVUZU.get(symbol)
        if mevcut:
            mevcut["oz"] = oz
            mevcut["derin"] = derin

        # Ön havuz için yeşil mum/hacim teyidi beklenmez; yalnız düşüş profili aranır.
        derin_aday = bool(
            derin
            and -DERIN_CEKILIS_MAX <= derin.get("cekilis", 0) <= -DERIN_CEKILIS_MIN
            and derin.get("kirmizi_pay", 0) >= DERIN_KIRMIZI_PAY_MIN
        )
        saatlik_aday = bool(
            -SAATLIK_CEKILIS_MAX <= oz.get("cekilis", 0) <= -SAATLIK_CEKILIS_MIN
        )
        trend_geri_aday = bool(
            float(oz.get("trend_yukselis", 0) or 0) >= TREND_YUKSELIS_MIN
            and -TREND_CEKILIS_MAX
            <= float(oz.get("trend_cekilis", 0) or 0)
            <= -TREND_CEKILIS_MIN
            and int(oz.get("trend_tepe_yasi", 999) or 999) <= TREND_TEPE_YAS_MAX
            and float(oz.get("trend_koruma", 0) or 0) >= TREND_KORUMA_MIN
            and float(oz.get("trend_hamle_hacim_x", 0) or 0) >= TREND_HAMLE_HACIM_MIN
            and TREND_GERI_VERME_ORAN_MIN
            <= float(oz.get("trend_geri_verme_orani", 0) or 0)
            <= TREND_GERI_VERME_ORAN_MAX
            and float(oz.get("trend_geri_hacim_orani", 99) or 99) <= 0.90
        )
        hizlanma_aday = bool(
            float(oz.get("d1", 0) or 0) >= HIZLANMA_D1_MIN
            and HIZLANMA_D3_MIN <= float(oz.get("d3", 0) or 0) <= HIZLANMA_D3_MAX
            and float(oz.get("d5", 0) or 0) >= HIZLANMA_D5_MIN
            and float(oz.get("d10", 0) or 0) <= HIZLANMA_D10_MAX
            and float(oz.get("hacim_x", 0) or 0) >= HIZLANMA_HACIM_MIN
        )
        yeni_tip = (
            "trend_geri" if trend_geri_aday
            else (
                "derin" if derin_aday
                else ("saatlik" if saatlik_aday else ("hizlanma" if hizlanma_aday else None))
            )
        )
        tip = mevcut.get("tip") if mevcut else yeni_tip
        if not tip:
            continue

        if mevcut and not yeni_tip:
            # Profil bandından çıksa da önceden yakalanan aday süre sonuna kadar izlenir.
            continue
        if tip == "derin":
            dip = float(derin.get("dip2s", 0) or 0)
            cekilis = float(derin.get("cekilis", 0) or 0)
        elif tip == "trend_geri":
            dip = float(oz.get("trend_geri_dip", 0) or 0)
            cekilis = float(oz.get("trend_cekilis", 0) or 0)
        elif tip == "hizlanma":
            dip = float(oz.get("dip5", 0) or 0)
            cekilis = float(oz.get("d5", 0) or 0)
        else:
            dip = float(oz.get("dip15", 0) or 0)
            cekilis = float(oz.get("cekilis", 0) or 0)
        if dip <= 0:
            continue
        if mevcut:
            if dip < float(mevcut.get("dip", dip) or dip):
                mevcut["dip"] = dip
                mevcut["son_dip_zamani"] = simdi
                mevcut["gec_kayit"] = False
            mevcut.update({
                "oz": oz, "derin": derin, "cekilis": cekilis,
                "sona_erme": simdi + ADAY_HAVUZ_SURESI,
            })
            mevcut.setdefault("durum", "ADAY")
            mevcut.setdefault("teyit_gecmisi", [])
        else:
            ADAY_HAVUZU[symbol] = {
                "tip": tip, "dip": dip, "cekilis": cekilis,
                "oz": oz, "derin": derin, "eklenme": simdi,
                "sona_erme": simdi + ADAY_HAVUZ_SURESI,
                "son_dip_zamani": simdi, "erken_kayitli": False,
                "teyit_kayitli": False, "guclu_gonderildi": False,
                "gec_kayit": False, "durum": "ADAY", "teyit_gecmisi": [],
            }

    hizli_adaylari_takip(ticker)

    gunluk_rapor_gerekirse()
    hareket_durumu_kaydet()


def main():
    telegram_gonder(
        "✅ Hareket Döngüsü Motoru başladı.\n"
        "⚡ Saat İçi + 🔄 Saatlerce Düşen + 🚀↩️ Trend Geri Çekilmesi + 🌱⚡ Yeni Hızlanma izleniyor.\n"
        "🏆 Saat içi: erken kalitesi güçlü olan gönderilir.\n"
        "🏆 Saatlerce düşen: yalnız teyitli güçlü olan gönderilir.\n"
        "🏆 Güçlü yükseliş geri çekilmesi: dipten dönüş teyit edilince gönderilir.\n"
        "🏆 Yeni hızlanma: üç canlı gözlemin en az ikisinde yapı korunursa gönderilir.\n"
        "🎯 Gönderilen sinyal +%3 olursa tek hedef mesajı gelir.\n"
        "Erken aday ve normal teyit gölgede ölçülür.\n"
        "Gerçek emir yok."
    )
    son_tam_tarama = 0.0
    while True:
        bas = time.time()
        try:
            if bas - son_tam_tarama >= TARAMA_SURESI:
                tara()
                son_tam_tarama = time.time()
            else:
                ticker = ticker_getir()
                takip_guncelle(ticker)
                hizli_adaylari_takip(ticker)
                gunluk_rapor_gerekirse()
        except Exception as e:
            print("Genel tarama hatası:", e)
        bekle = max(2, HIZLI_TAKIP_SURESI - (time.time() - bas))
        time.sleep(bekle)


if __name__ == "__main__":
    main()
