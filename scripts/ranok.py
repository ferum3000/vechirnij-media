"""Утренняя карточка «Доброго ранку!» в облаке — когда компьютер Ферума выключен (24.11.2026).

Смена kanal.yml вызывает скрипт каждые 5 минут. Он что-то делает только с 07:10 до 07:29 и только если
на сегодня в очереди и в опубликованном ещё нет `<дата>_0730_svitlo.md` (то есть утренняя задача Claude
на компьютере пост не приготовила). Тогда:
  1. график черги 2.2 — тот же разбор поста Контакт-центру, что у сторожа (dtek_watch.grafik);
  2. погода и световой день — open-meteo.com;
  3. сюрприз дня — только из kanal/ranok/surprizy.md (заранее проверенные строки «ГГГГ-ММ-ДД | Заголовок | текст»);
  4. блока «Що за ніч» нет: ночные новости в облаке никто не проверял, писать «важливого не сталося» нельзя;
  5. рисует карточку и кладёт пост на 07:30. Статус берётся из kanal/ranok/nastroyky.json:
     "rezhym": "avto" → odobreno (выходит сам, сторож запоминает график), иначе chernovyk (ничего не выходит).

Если Claude на компьютере потом поставит свой пост с тем же именем — он заменит облачный.
Ошибка (нет поста Контакт-центру, open-meteo лёг) → строка в post.log, поста нет. График не выдумываем.
"""
import datetime as dt
import json
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dtek_watch as dw  # noqa: E402

PAPKA = dw.PAPKA
OCHERED = PAPKA / "ochered"
GOTOVO = PAPKA / "opublikovano"
RANOK = PAPKA / "ranok"
OFORMLENIE = dw.OFORMLENIE
SHABLON = OFORMLENIE / "svitlo-sogodni.html"

WMO = {0: "ясно", 1: "переважно ясно", 2: "мінлива хмарність", 3: "хмарно", 45: "туман", 48: "туман, ожеледь",
       51: "мряка", 53: "мряка", 55: "сильна мряка", 56: "крижана мряка", 57: "крижана мряка",
       61: "невеликий дощ", 63: "дощ", 65: "сильний дощ", 66: "крижаний дощ", 67: "крижаний дощ",
       71: "невеликий сніг", 73: "сніг", 75: "сильний сніг", 77: "снігова крупа",
       80: "короткочасний дощ", 81: "злива", 82: "сильна злива", 85: "снігопад", 86: "сильний снігопад",
       95: "гроза", 96: "гроза з градом", 99: "гроза з градом"}
FRAZY = {"turka": "Кава з турки — і день піде!", "kakao": "Какао, теплі шкарпетки — і вперед!",
         "syrnyky": "Сирники, кава — і гарного дня!", "chai": "Чай з лимоном — і вперед!"}


def log(tekst):
    dw.log("РАНОК: " + tekst)


def pogoda():
    url = ("https://api.open-meteo.com/v1/forecast?latitude=47.935&longitude=33.426"
           "&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code,"
           "wind_speed_10m_max,sunrise,sunset&timezone=Europe%2FKyiv&forecast_days=1")
    d = json.loads(urllib.request.urlopen(url, timeout=20).read())["daily"]
    tmax, tmin = round(d["temperature_2m_max"][0]), round(d["temperature_2m_min"][0])
    znak = lambda t: f"+{t}" if t > 0 else str(t)
    veter = round(d["wind_speed_10m_max"][0] / 3.6)
    opad = d["precipitation_probability_max"][0]
    tekst = f"{WMO.get(d['weather_code'][0], '')}, вітер до {veter} м/с" + (f", опади {opad}%" if opad and opad >= 30 else "")
    return f"{znak(tmin)}…{znak(tmax)}°", tekst, d["sunrise"][0][-5:], d["sunset"][0][-5:]


def surpriz(data):
    f = RANOK / "surprizy.md"
    if not f.exists():
        return None
    for s in f.read_text(encoding="utf-8").splitlines():
        chasti = [c.strip() for c in s.split("|")]
        if len(chasti) == 3 and chasti[0] == data and chasti[1] and chasti[2]:
            return chasti[1], chasti[2]
    return None


def porada(sost):
    """Порада дня из самого графика (без внешних фактов): самое длинное окно со светом между 07:00 и 22:00."""
    if all(x == "on" for x in sost):
        return "Порада дня", "Відключень за графіком немає — гарний день, щоб зарядити павербанк і ліхтарик про запас."
    pivgodyny = []  # 30-минутные куски с 07:00 до 22:00: True — свет есть
    for h in range(7, 22):
        x = sost[h]
        pivgodyny += [x in ("on", "half2"), x in ("on", "half1")]
    najkrashche, start = (0, 0), None
    for i, svitlo in enumerate(pivgodyny + [False]):
        if svitlo and start is None:
            start = i
        elif not svitlo and start is not None:
            if i - start > najkrashche[1] - najkrashche[0]:
                najkrashche = (start, i)
            start = None
    a, b = najkrashche
    if b - a < 2:
        return None
    f = lambda k: f"{7 + k // 2:02d}:{'30' if k % 2 else '00'}"
    return "Порада дня", f"Найдовше світло за графіком — з {f(a)} до {f(b)}. Встигніть зарядити павербанк і телефони."


def narysovat(data, sost, vykl, onovleno, out):
    d = dt.date.fromisoformat(data)
    den = f"{dw.DNI[d.weekday()]}, {d.day} {dw.MISYATSI[d.month - 1]}"
    status = "Без світла: " + ", ".join(vykl) if vykl else "Відключень за графіком не заплановано"
    hours = "".join(f'<div class="h {dw.CSS[s]}">{h:02d}</div>' for h, s in enumerate(sost))
    temp, pog, sunrise, sunset = pogoda()
    s = surpriz(data) or porada(sost)
    surp = f'<div class="surp"><h2>{s[0]}</h2><div class="txt">{s[1]}</div></div>' if s else ""
    chas = onovleno.split(" ")[-1] + " " + onovleno.split(" ")[0]
    html = (SHABLON.read_text(encoding="utf-8")
            .replace("{{DATA}}", den[0].upper() + den[1:]).replace("{{CHAS}}", chas)
            .replace("{{STATUS}}", status).replace("{{HOURS}}", hours)
            .replace("{{TEMP}}", temp).replace("{{POGODA}}", pog)
            .replace("{{SUNRISE}}", sunrise).replace("{{SUNSET}}", sunset)
            .replace("{{SURPRIZ}}", surp))
    # «Що за ніч» убираем целиком: в облаке ночные новости не проверены.
    html = re.sub(r'\s*<div class="surp"[^>]*><h2[^>]*>Що за ніч</h2><div class="txt">\{\{NOVYNY\}\}</div></div>', "", html)
    shapky = sorted((OFORMLENIE / "ranok" / "shapky").glob("*.jpg"))
    if shapky:
        imya = shapky[d.toordinal() % len(shapky)].name
        html = html.replace("ranok/shapka.jpg", f"ranok/shapky/{imya}")
        for k, fraza in FRAZY.items():
            if k in imya:
                html = html.replace("Кава, тепла батарея — і вперед!", fraza)
    tmp = OFORMLENIE / "_ranok_tmp.html"  # рядом с шаблоном, чтобы картинки шапки нашлись
    tmp.write_text(html, encoding="utf-8")
    if sys.platform.startswith("linux") and not Path("/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf").exists():
        subprocess.run("sudo apt-get update -qq && sudo apt-get install -y -qq fonts-noto-color-emoji fonts-noto-core",
                       shell=True, capture_output=True, timeout=300)
    chrome = next((p for p in [
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
        Path.home() / r"AppData\Local\Google\Chrome\Application\chrome.exe"] if p.exists()), None) \
        or shutil.which("google-chrome") or shutil.which("chromium")
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([str(chrome), "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-sandbox",
                    f"--user-data-dir={PAPKA / '_chrome_render'}", "--window-size=1080,1350",
                    f"--screenshot={out}", tmp.as_uri()], capture_output=True, timeout=90)
    tmp.unlink(missing_ok=True)
    if not out.exists():
        raise RuntimeError("карточка не отрисовалась")
    return den, s


def main():
    if sys.stdout:
        sys.stdout.reconfigure(encoding="utf-8")
    teper = dt.datetime.now(dw.KYIV)
    data = teper.date().isoformat()
    imya = f"{data}_0730_svitlo.md"
    proba = "--proba" in sys.argv  # нарисовать сейчас, в очередь не класть
    if not proba:
        if not (teper.hour == 7 and 10 <= teper.minute < 30):
            return
        if (OCHERED / imya).exists() or (GOTOVO / imya).exists():
            return  # пост на сегодня уже есть (сделал Claude на компьютере или прошлый круг)
    nastroyky = json.loads((RANOK / "nastroyky.json").read_text(encoding="utf-8")) if (RANOK / "nastroyky.json").exists() else {}
    avto = nastroyky.get("rezhym") == "avto"

    d_grafika, sost, onovleno = dw.grafik()
    vykl = dw.intervaly(sost)
    out = (PAPKA / "kartinky" / f"svitlo-{data}{'-proba' if proba else ''}.png")
    den, s = narysovat(d_grafika, sost, vykl, onovleno, out)
    tekst = (f"💡 Світло сьогодні, {den}: {dw.opys(vykl)}.\n"
             "Черга 2.2 — Авраменка 14 і перевірені будинки бул. Вечірній. Свою чергу — на сайті ДТЕК або в боті ДТЕК у Viber.\n\n"
             "Перешліть у чат свого будинку 🙌")
    if proba:
        print(out)
        print(tekst)
        return
    status = "odobreno" if avto else "chernovyk"
    (OCHERED / imya).write_text(f"---\nvremya: {data} 07:30\nstatus: {status}\nkartinka: kartinky/{out.name}\n---\n{tekst}\n",
                                encoding="utf-8")
    if avto:
        dw.STATE.write_text(json.dumps({"data": d_grafika, "vykl": vykl, "onovleno": onovleno},
                                       ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"облачная карточка {imya}: {dw.opys(vykl)} (Контакт-центр {onovleno}), статус {status}"
        + (", сюрприз: " + s[0] if s else ", без сюрприза"))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"ОШИБКА, утреннего поста из облака нет: {e}")
        raise
