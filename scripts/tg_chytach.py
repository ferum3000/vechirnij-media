"""Читатель публичных Telegram-каналов Кривого Рога без Telegram и без API: страница t.me/s/<канал>.

Работает с любого компьютера и из облака GitHub (обычный HTTP-запрос). Берёт последние ~20 постов канала,
оставляет посты за последние N часов, пишет текст, время (Київ), ссылку на пост и есть ли фото/видео.

    python tg_chytach.py                 — все каналы из KANALY за 24 часа → печать
    python tg_chytach.py --godyn 12      — за 12 часов
    python tg_chytach.py --out fajl.md   — сохранить сводку в файл (для збирача)
    python tg_chytach.py keruiuchi_kompanii typical_kr   — только эти каналы

Это СЫРЬЁ для збирача, а не готовые новости: каждую новость перед постом сверяем
(особенно Труха и «Типовий» — агрегаторы, часто без первоисточника). Чужие фото и видео не перезаливаем.
"""
import datetime as dt
import html
import re
import sys
import urllib.request
from zoneinfo import ZoneInfo

KYIV = ZoneInfo("Europe/Kyiv")
# адрес → (название, тип источника). Тип: офіційне — первоисточник; агрегатор — только наводка, искать первоисточник.
KANALY = {
    "keruiuchi_kompanii": ("Контакт-центр керуючих компаній (Житлосервіс-КР, Сітісервіс-КР, Уют-2011)", "офіційне"),
    "vilkul": ("Олександр Вілкул", "офіційне"),
    "atktcinfo": ("Криворізька тепло­централь. Оперативна інформація", "офіційне"),
    "typical_kr": ("ТИПОВИЙ КРИВИЙ РІГ", "агрегатор"),
    "truexakrivojrog": ("Труха⚡Кривий Ріг", "агрегатор"),
    "svoiKR": ("СВОЇ | Кривий Ріг", "агрегатор"),
    "onekrua1": ("Перший Міський. Кривий Ріг", "ЗМІ"),
}


def posty(kanal):
    req = urllib.request.Request(f"https://t.me/s/{kanal}", headers={"User-Agent": "Mozilla/5.0"})
    stranica = urllib.request.urlopen(req, timeout=30).read().decode("utf-8")
    for blok in stranica.split('<div class="tgme_widget_message_wrap')[1:]:
        nomer = re.search(r'data-post="([^"]+)"', blok)
        chas = re.search(r'<time datetime="([^"]+)"', blok)
        tekst = re.search(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', blok, re.S)
        if not (nomer and chas):
            continue
        t = tekst.group(1) if tekst else ""
        t = re.sub(r"<br\s*/?>", "\n", t)
        t = html.unescape(re.sub(r"<[^>]+>", "", t)).strip()
        media = "фото" if "tgme_widget_message_photo" in blok else ("відео" if "tgme_widget_message_video" in blok else "")
        yield {
            "chas": dt.datetime.fromisoformat(chas.group(1)).astimezone(KYIV),
            "url": f"https://t.me/{nomer.group(1)}",
            "tekst": t,
            "media": media,
        }


def main():
    sys.stdout.reconfigure(encoding="utf-8") if sys.stdout else None
    args = sys.argv[1:]
    godyn = 24
    out = None
    if "--godyn" in args:
        godyn = int(args[args.index("--godyn") + 1])
        del args[args.index("--godyn"):args.index("--godyn") + 2]
    if "--out" in args:
        out = args[args.index("--out") + 1]
        del args[args.index("--out"):args.index("--out") + 2]
    vybir = args or list(KANALY)
    vid = dt.datetime.now(KYIV) - dt.timedelta(hours=godyn)
    ryadky = [f"# Telegram Кривого Рогу за {godyn} год (до {dt.datetime.now(KYIV):%d.%m %H:%M})\n"]
    for k in vybir:
        nazva, typ = KANALY.get(k, (k, "?"))
        try:
            svizhi = [p for p in posty(k) if p["chas"] >= vid]
        except Exception as e:
            ryadky.append(f"\n## {nazva} (@{k}) — ПОМИЛКА: {e}\n")
            continue
        ryadky.append(f"\n## {nazva} (@{k}, {typ}) — {len(svizhi)} пост.\n")
        for p in svizhi:
            t = p["tekst"].replace("\n", " ")
            ryadky.append(f"- {p['chas']:%d.%m %H:%M} {('[' + p['media'] + '] ') if p['media'] else ''}{t[:400]}{'…' if len(t) > 400 else ''} ({p['url']})")
    rez = "\n".join(ryadky)
    if out:
        open(out, "w", encoding="utf-8").write(rez + "\n")
        print(out)
    else:
        print(rez)


if __name__ == "__main__":
    main()
