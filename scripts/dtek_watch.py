"""Сторож графика ДТЕК для черги 2.2 (Авраменка 14 и бул. Вечірній).

Раз в 15 минут (Планировщик Windows) берёт страницу https://www.dtek-dnem.com.ua/ua/shutdowns,
достаёт из неё DisconSchedule.fact (фактический график на сегодня) и сравнивает с тем,
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


def grafik():
    """→ (дата ГГГГ-ММ-ДД, список 24 состояний, время обновления ДТЕК)."""
    req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                               "AppleWebKit/537.36 Chrome/131 Safari/537.36"})
    html = urllib.request.urlopen(req, timeout=40).read().decode("utf-8")
    fact = json_posle(html, "DisconSchedule.fact")
    segodnya = str(fact["today"])
    den = fact["data"][segodnya][CHERGA]
    sost = []
    for h in range(1, 25):
        kod = den[str(h)]
        if kod not in KODY:
            raise ValueError(f"незнакомый код {kod!r} в {h - 1:02d}:00 — смотреть сайт вручную")
        sost.append(KODY[kod])
    data = dt.datetime.fromtimestamp(int(segodnya)).date().isoformat()
    return data, sost, fact.get("update", "")


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
        print(f"черга 2.2, {data}, ДТЕК оновив {onovleno}: {opys(vykl)}", file=sys.stderr)
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
    tekst = (f"⚠️ Графік змінено. ДТЕК оновив графік о {chas}.\n"
             f"💡 Світло сьогодні, {den[0].lower() + den[1:]}, черга 2.2: {opys(vykl)}.\n"
             f"Було: {bulo}.\n\n"
             "Графік може змінитися ще — стежимо й напишемо. Свою чергу перевірте на сайті ДТЕК або в боті ДТЕК у Viber.")
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
