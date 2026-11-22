"""Число подписчиков канала «Вечірній бульвар» с публичной страницы-приглашения Viber.

Работает без компьютера Ферума: запускается в GitHub Actions раз в час и дописывает
строку «время (Київ),підписники» в stats/pidpysnyky.csv. Страница отдаёт preRenderData
с полем "members" — то же число, что «N подписчиков» в Viber.
"""
import datetime as dt
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

INVITE = "https://invite.viber.com/?g2=AQAIhiP8Zr%2F6j1d%2FLAv2gg6ACy1nbu1I8WAj245XKPP0kCao3Ed1f3nZeeOxDCuu"
CSV = Path(__file__).resolve().parent.parent / "stats" / "pidpysnyky.csv"

req = urllib.request.Request(INVITE, headers={"User-Agent": "Mozilla/5.0"})
html = urllib.parse.unquote(urllib.request.urlopen(req, timeout=30).read().decode("utf-8"))
m = re.search(r'"members":(\d+)', html)
if not m:
    sys.exit("на странице нет members — Viber поменял вёрстку")
chas = dt.datetime.now(ZoneInfo("Europe/Kyiv")).strftime("%Y-%m-%d %H:%M")
if not CSV.exists():
    CSV.parent.mkdir(exist_ok=True)
    CSV.write_text("chas_kyiv,pidpysnyky\n", encoding="utf-8")
with open(CSV, "a", encoding="utf-8") as f:
    f.write(f"{chas},{m.group(1)}\n")
print(chas, m.group(1))
