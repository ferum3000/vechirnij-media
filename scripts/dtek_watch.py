"""Сторож графика ДТЕК для черги 2.2 (Авраменка 14 и бул. Вечірній).

С 23.11.2026 график берётся из Telegram-канала Контакт-центру керуючих компаній (@keruiuchi_kompanii, t.me/s/…):
сайт ДТЕК закрыт проверкой на робота, её не обходим. Скрипт берёт последний пост с графиком группы 2.2 на сегодня и сравнивает с тем,
что канал уже сообщил людям (dtek_state.json).

Если график на сегодня изменился и сейчас 07:00–21:59 — рисует карточку «Графік змінено»,
кладёт пост в ochered со status odobreno и сразу запускает post_queue.py.
Публиковать изменения без отдельного «да» разрешил Ферум 22.11.2026.

Запуск:
  python dtek_watch.py              — проверить и при изменении опубликовать
  python dtek_watch.py --zapamyatat — записать текущий график как «уже сообщили» (после утреннего поста)
  python dtek_watch.py --pokazat    — только показать, что сейчас на сайте
Если в dtek_state.json нет записи за сегодня (утренний пост ещё не вышел) — ничего не делает.
"""
import datetime as dt
import json
import subprocess
import sys
import urllib.request
from pathlib import Path

import os
from zoneinfo import ZoneInfo
KYIV = ZoneInfo("Europe/Kyiv")
# Как post_queue.py: в облаке VECHIRNIJ_PAPKA = kanal/ репозитория, шаблон карточки лежит там же.
PAPKA = Path(os.environ.get("VECHIRNIJ_PAPKA") or Path(__file__).resolve().parent).resolve()
SKRIPTY = Path(__file__).resolve().parent
STATE = PAPKA / "dtek_state.json"
LOG = PAPKA / "post.log"
OCHERED = PAPKA / "ochered"
OFORMLENIE = Path(os.environ.get("VECHIRNIJ_OFORMLENIE") or PAPKA.parent / "kampaniya" / "vechirnij" / "oformlenie")
SHABLON = OFORMLENIE / "zmina-grafika.html"
KARTINKY = Path(os.environ.get("VECHIRNIJ_KARTINKY") or OFORMLENIE / "svitlo")
URL = "https://www.dtek-dnem.com.ua/ua/shutdowns"
CHERGA = "GPV2.2"
# коды DisconSchedule.preset.time_type → состояние часа
KODY = {"yes": "on", "no": "off", "first": "half1", "second": "half2"}
CSS = {"on": "", "off": "off", "half1": "half", "half2": "half2"}
DNI = ["понеділок", "вівторок", "середа", "четвер", "п'ятниця", "субота", "неділя"]
MISYATSI = ["січня", "лютого", "березня", "квітня", "травня", "червня", "липня", "серпня",
            "вересня", "жовтня", "листопада", "грудня"]


def log(tekst):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{dt.datetime.now():%Y-%m-%d %H:%M:%S}  ДТЕК: {tekst}\n")


def json_posle(html, metka):
    i = html.index("{", html.index(metka))
    glubina = 0
    for j in range(i, len(html)):
        if html[j] == "{":
            glubina += 1
        elif html[j] == "}":
            glubina -= 1
            if glubina == 0:
                return json.loads(html[i:j + 1])
    raise ValueError(f"не нашёл конец {metka}")


KANAL_TG = "keruiuchi_kompanii"  # Контакт-центр керуючих компаній (Житлосервіс-КР, Сітісервіс-КР, Уют-2011), офіційний
GRUPA = "2.2"


def v_hvylyny(hhmm):
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def razobrat_post(tekst, chas_posta, segodnya):
    """Пост Контакт-центру → список интервалов (минуты) для группы 2.2 на сегодня, или None, если пост не про это.

    Понимает три вида постов:
      «Орієнтовний графік відключень … на 23 листопада  Група 1.1 * 00:00–01:00 … Група 2.2 * … Група 3.2 …»
      «Графік відключення електроенергії на сьогодні ▪️ Група 1.1 — 21:00–00:00; ▪️ Група 2.2 — 21:00–00:00; …»
      «ЗМІНИ В ГРАФІКУ … Черги 1.1, 2.2, 3.2 … — наразі, без відключень»
    Посты о текущем отключении («Відключення … З 07:30 до 11:30 … Черга 2.2 Частково…») — не график дня, пропускаем.
    """
    import re
    t = " ".join(tekst.split())
    nyzh = t.lower()
    if GRUPA not in t or "графік" not in nyzh:
        return None
    pro_segodni = f"{segodnya.day} {MISYATSI[segodnya.month - 1]}" in nyzh or (
        "на сьогодні" in nyzh and chas_posta.date() == segodnya) or (
        "змін" in nyzh and chas_posta.date() == segodnya)
    if not pro_segodni:
        return None
    if "без відключень" in nyzh and re.search(r"черг[аи].{0,80}?2\.2.{0,80}?без відключень", nyzh):
        return []
    m = re.search(r"груп[аи]\s*2\.2(.*?)(?=груп[аи]\s*\d\.\d|інформація може|$)", t, re.I)
    if not m:
        return None
    kuski = re.findall(r"(\d{1,2}:\d{2})\s*[–—-]\s*(\d{1,2}:\d{2})", m.group(1))
    if not kuski:
        return None
    rez = []
    for a, b in kuski:
        a, b = v_hvylyny(a), v_hvylyny(b)
        if b <= a:
            b = 24 * 60  # 21:00–00:00 → до кінця доби
        rez.append((a, b))
    return sorted(rez)


def grafik():
    """→ (дата ГГГГ-ММ-ДД, список 24 состояний, время поста Контакт-центру).

    С 23.11.2026 сайт ДТЕК закрыт проверкой на робота; её не обходим. График берём из официального
    Telegram-канала Контакт-центру керуючих компаній (он публикует график ДТЕК текстом по группам),
    страница t.me/s/keruiuchi_kompanii — обычный запрос, без входа. Берём последний пост с графиком на сегодня."""
    sys.path.insert(0, str(SKRIPTY))
    from tg_chytach import posty
    segodnya = dt.datetime.now(KYIV).date()
    ostanniy = None
    for p in posty(KANAL_TG):
        kuski = razobrat_post(p["tekst"], p["chas"], segodnya)
        if kuski is not None and (ostanniy is None or p["chas"] > ostanniy[0]):
            ostanniy = (p["chas"], kuski, p["url"])
    if ostanniy is None:
        raise LookupError(f"в @{KANAL_TG} нет поста с графиком группы {GRUPA} на {segodnya:%d.%m}")
    chas, kuski, url = ostanniy
    sost = []
    for h in range(24):
        pol = [any(a <= h * 60 + k * 30 and h * 60 + k * 30 + 30 <= b for a, b in kuski) for k in (0, 1)]
        sost.append({(True, True): "off", (True, False): "half1", (False, True): "half2", (False, False): "on"}[tuple(pol)])
    return segodnya.isoformat(), sost, f"{chas:%d.%m.%Y %H:%M}"


def intervaly(sost):
    kuski = []
    for h, s in enumerate(sost):
        if s == "off":
            kuski.append((h * 60, h * 60 + 60))
        elif s == "half1":
            kuski.append((h * 60, h * 60 + 30))
        elif s == "half2":
            kuski.append((h * 60 + 30, h * 60 + 60))
    slity = []
    for a, b in kuski:
        if slity and slity[-1][1] == a:
            slity[-1] = (slity[-1][0], b)
        else:
            slity.append((a, b))
    f = lambda m: f"{m // 60:02d}:{m % 60:02d}"
    return [f"{f(a)}–{f(b)}" for a, b in slity]


def opys(vykl):
    return "без світла " + " і ".join(vykl) if vykl else "відключень за графіком немає"


def kartka(data, sost, vykl, bulo, chas):
    d = dt.date.fromisoformat(data)
    den = f"{DNI[d.weekday()].capitalize()}, {d.day} {MISYATSI[d.month - 1]}"
    status = "Без світла: " + ", ".join(vykl) if vykl else "Відключень за графіком немає"
    hours = "".join(f'<div class="h {CSS[s]}">{h:02d}</div>' for h, s in enumerate(sost))
    html = (SHABLON.read_text(encoding="utf-8").replace("{{CHAS}}", chas).replace("{{DATA}}", den)
            .replace("{{STATUS}}", status).replace("{{HOURS}}", hours)
            .replace("{{BULO}}", ", ".join(bulo) if bulo else "відключень не було"))
    papka = KARTINKY
    papka.mkdir(parents=True, exist_ok=True)
    out = papka / f"zmina-{data}_{dt.datetime.now():%H%M}.png"
    tmp = papka / "_zmina_tmp.html"
    tmp.write_text(html, encoding="utf-8")
    import shutil
    if sys.platform.startswith("linux") and not Path("/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf").exists():
        # облако GitHub: шрифт с эмодзи ставим только когда реально рисуем карточку
        subprocess.run("sudo apt-get update -qq && sudo apt-get install -y -qq fonts-noto-color-emoji fonts-noto-core",
                       shell=True, capture_output=True, timeout=300)
    chrome = next((p for p in [
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
        Path.home() / r"AppData\Local\Google\Chrome\Application\chrome.exe"] if p.exists()), None)         or shutil.which("google-chrome") or shutil.which("chromium")
    subprocess.run([str(chrome), "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-sandbox",
                    f"--user-data-dir={papka.parent / '_chrome_render'}", "--window-size=1080,880",
                    f"--screenshot={out}", tmp.as_uri()], capture_output=True, timeout=90)
    tmp.unlink(missing_ok=True)
    if not out.exists():
        raise RuntimeError("карточка не отрисовалась")
    return out, den


def main():
    if sys.stdout:
        sys.stdout.reconfigure(encoding="utf-8")
    data, sost, onovleno = grafik()
    vykl = intervaly(sost)
    state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}

    if "--klasy" in sys.argv:  # для render-svitlo.py --klasy
        m = {"on": "cell-non-scheduled", "off": "cell-scheduled", "half1": "cell-first-half", "half2": "cell-second-half"}
        print(",".join(m[s] for s in sost))
        print(f"черга 2.2, {data}, пост Контакт-центру {onovleno}: {opys(vykl)}", file=sys.stderr)
        return
    if "--pokazat" in sys.argv:
        print(data, onovleno, opys(vykl), "| уже сообщили:", state)
        return
    if "--zapamyatat" in sys.argv:
        STATE.write_text(json.dumps({"data": data, "vykl": vykl, "onovleno": onovleno},
                                    ensure_ascii=False, indent=1), encoding="utf-8")
        print("запомнил:", data, opys(vykl))
        return

    if state.get("data") != data:
        return  # утренний пост за сегодня ещё не вышел — сообщать изменения не о чем
    if state.get("vykl") == vykl:
        return
    teper = dt.datetime.now()
    if not 7 <= teper.hour < 22:
        log(f"график изменился ({opys(vykl)}), но сейчас ночь — не публикую, проверю утром")
        return

    chas = (onovleno or f"{teper:%H:%M}").split(" ")[-1]
    out, den = kartka(data, sost, vykl, state["vykl"], f"{chas} {dt.date.fromisoformat(data):%d.%m.%Y}")
    bulo = ", ".join(state["vykl"]) if state["vykl"] else "відключень не було"
    tekst = (f"⚠️ Графік змінено. Оновлений графік ДТЕК опублікували о {chas}.\n"
             f"💡 Світло сьогодні, {den[0].lower() + den[1:]}, черга 2.2: {opys(vykl)}.\n"
             f"Було: {bulo}.\n\n"
             "Графік може змінитися ще — стежимо й напишемо. Свою чергу перевірте на сайті ДТЕК або в боті ДТЕК у Viber.\n"
             "Джерело: Контакт-центр керуючих компаній (Telegram).")
    fajl = OCHERED / f"{teper:%Y-%m-%d_%H%M}_zmina-grafika.md"
    fajl.write_text(f"---\nvremya: {teper:%Y-%m-%d %H:%M}\nstatus: odobreno\nkartinka: {out.as_posix()}\n---\n{tekst}\n",
                    encoding="utf-8")
    STATE.write_text(json.dumps({"data": data, "vykl": vykl, "onovleno": onovleno},
                                ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"график изменился: было {bulo}, стало {opys(vykl)} — пост {fajl.name} в очереди")
    if os.environ.get("VECHIRNIJ_BEZ_PUBLIKACII"):
        return  # в облаке post_queue.py запускается следующим шагом
    subprocess.run([sys.executable, str(SKRIPTY / "post_queue.py")], cwd=SKRIPTY, timeout=600)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # сайт лёг, вёрстка поменялась — пишем в лог, ничего не публикуем
        log(f"ОШИБКА: {e}")
        raise
