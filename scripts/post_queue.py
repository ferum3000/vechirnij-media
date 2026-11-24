"""Публикация постов из очереди в Viber-канал «Вечірній бульвар | Новини району».

Очередь: папка ochered/, один пост = один файл .md:

    ---
    vremya: 2026-11-22 09:00
    status: odobreno
    ---
    Текст поста (до 7000 знаков).

Публикуются только файлы со status: odobreno, у которых время уже наступило.
После публикации файл переезжает в opublikovano/, при ошибке остаётся на месте, ошибка пишется в post.log.

Токен берётся из переменной Windows VIBER_CHANNEL_TOKEN (в файлах его нет).

Запуск:
    python post_queue.py            # опубликовать всё, что пора
    python post_queue.py --dry-run  # только показать, что ушло бы, ничего не отправлять
"""
import json
import os
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

# Папки берутся из переменных окружения (в облаке GitHub Actions: kanal/ репозитория vechirnij-media),
# по умолчанию — рядом со скриптом, как на компьютере Ферума.
PAPKA = Path(os.environ.get("VECHIRNIJ_PAPKA") or Path(__file__).resolve().parent).resolve()
OCHERED = PAPKA / "ochered"
GOTOVO = PAPKA / "opublikovano"
LOG = PAPKA / "post.log"
API = "https://chatapi.viber.com/pa/"
MAX_DLINA = 7000
MAX_DLINA_S_KARTINKOY = 768
MEDIA = Path(os.environ.get("VECHIRNIJ_MEDIA") or PAPKA / "media").resolve()  # клон github.com/ferum3000/vechirnij-media (публичный, только картинки и видео канала)
MEDIA_URL = "https://raw.githubusercontent.com/ferum3000/vechirnij-media/main/"
VIDEO_EXT = {".mp4", ".mov", ".m4v"}
MAX_VIDEO_BAYT = 26 * 1024 * 1024  # лимит Viber для type: video
MAX_VIDEO_SEK = 180


def tokon():
    t = os.environ.get("VIBER_CHANNEL_TOKEN", "").strip()
    if not t and os.name == "nt":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
                t = str(winreg.QueryValueEx(k, "VIBER_CHANNEL_TOKEN")[0]).strip()
        except OSError:
            pass
    if not t:
        sys.exit("Нет токена: переменная VIBER_CHANNEL_TOKEN пустая.")
    return t


def zapros(metod, dannye):
    req = urllib.request.Request(
        API + metod,
        data=json.dumps(dannye, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def zapis_v_log(stroka):
    # В облаке очередь проверяется каждые 5 минут: одинаковую строку за сегодня второй раз не пишем.
    segodnya = f"{datetime.now():%Y-%m-%d}"
    if LOG.exists() and any(s.startswith(segodnya) and s.rstrip().endswith(stroka)
                            for s in LOG.read_text(encoding="utf-8").splitlines()[-300:]):
        return
    with LOG.open("a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S}  {stroka}\n")
    print(stroka)


def razobrat(fail):
    tekst = fail.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    if not tekst.startswith("---\n"):
        raise ValueError("нет шапки --- в начале файла")
    shapka, _, telo = tekst[4:].partition("\n---\n")
    polya = {}
    for stroka in shapka.splitlines():
        if ":" in stroka:
            k, v = stroka.split(":", 1)
            polya[k.strip()] = v.strip()
    vremya = datetime.strptime(polya.get("vremya", ""), "%Y-%m-%d %H:%M")
    telo = telo.strip()
    if not telo:
        raise ValueError("пустой текст")
    kartinka = polya.get("kartinka", "").strip()
    video = polya.get("video", "").strip()
    if kartinka and video:
        raise ValueError("в шапке и kartinka, и video — нужно что-то одно")
    if video:
        # Видео: Viber не даёт подпись к type: video, поэтому текст уходит отдельным постом сразу перед видео.
        put = Path(video)
        if not put.is_absolute():
            put = (PAPKA / put).resolve()
        if not put.exists():
            raise ValueError(f"нет файла видео {put}")
        if put.suffix.lower() not in VIDEO_EXT:
            raise ValueError(f"видео должно быть {', '.join(sorted(VIDEO_EXT))}: {put.name}")
        if put.stat().st_size > MAX_VIDEO_BAYT:
            raise ValueError(f"видео больше 26 МБ ({put.stat().st_size // 1024 // 1024} МБ)")
        if len(telo) > MAX_DLINA:
            raise ValueError(f"текст длиннее {MAX_DLINA} знаков ({len(telo)})")
        return vremya, polya.get("status", ""), telo, put
    if kartinka:
        put = Path(kartinka)
        if not put.is_absolute():
            put = (PAPKA / put).resolve()
        if not put.exists():
            raise ValueError(f"нет файла картинки {put}")
        if len(telo) > MAX_DLINA_S_KARTINKOY:
            raise ValueError(f"текст поста с картинкой длиннее {MAX_DLINA_S_KARTINKOY} знаков ({len(telo)})")
        kartinka = put
    elif len(telo) > MAX_DLINA:
        raise ValueError(f"текст длиннее {MAX_DLINA} знаков ({len(telo)})")
    return vremya, polya.get("status", ""), telo, kartinka or None


def podgotovit_kartinku(put):
    """PNG/JPG → JPEG ≤1 МБ + миниатюра ≤100 КБ, в репозиторий vechirnij-media, push, ждём публичную ссылку."""
    from PIL import Image
    papka = MEDIA / datetime.now().strftime("%Y-%m")
    papka.mkdir(parents=True, exist_ok=True)
    imya = put.stem
    big, small = papka / f"{imya}.jpeg", papka / f"{imya}_thumb.jpeg"
    img = Image.open(put).convert("RGB")
    for q in (90, 82, 74, 66, 58):
        img.save(big, "JPEG", quality=q, optimize=True)
        if big.stat().st_size <= 1_000_000:
            break
    else:
        raise ValueError("картинка больше 1 МБ даже после сжатия")
    th = img.copy()
    th.thumbnail((400, 400))
    for q in (80, 65, 50):
        th.save(small, "JPEG", quality=q, optimize=True)
        if small.stat().st_size <= 100_000:
            break
    return vylozhyty([big, small], f"kartinka {imya}")


# Без окна консоли: скрипт идёт через pythonw, и без этого флага на каждый git/ffmpeg Windows открывает чёрное окно
# (22.11.2026 окно закрыли посреди commit, пост с картинкой не вышел).
BEZ_OKNA = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def vylozhyty(faily, soobshchenie):
    """Файлы из MEDIA → commit, push, ждём, пока первый откроется по публичной ссылке. Возвращает ссылки."""
    rel = lambda p: p.relative_to(MEDIA).as_posix()
    git = lambda *a: subprocess.run(["git", "-C", str(MEDIA), *a], check=True, capture_output=True, text=True, creationflags=BEZ_OKNA)
    git("pull", "--ff-only")
    git("add", *[rel(f) for f in faily])
    if subprocess.run(["git", "-C", str(MEDIA), "diff", "--cached", "--quiet"], creationflags=BEZ_OKNA).returncode != 0:
        git("commit", "-m", soobshchenie)
    git("push")
    urls = [MEDIA_URL + rel(f) for f in faily]
    for _ in range(45):
        try:
            with urllib.request.urlopen(urllib.request.Request(urls[0], method="HEAD"), timeout=10) as r:
                if r.status == 200:
                    return urls
        except Exception:
            pass
        time.sleep(4)
    raise ValueError(f"файл не открылся по ссылке {urls[0]}")


def podgotovit_video(put):
    """MP4 → копия в vechirnij-media + миниатюра JPEG ≤100 КБ из кадра на 1-й секунде; длительность через ffprobe."""
    import shutil
    from PIL import Image
    run = lambda *a: subprocess.run(list(a), check=True, capture_output=True, text=True, creationflags=BEZ_OKNA)
    dlit = float(run("ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(put)).stdout.strip())
    if dlit > MAX_VIDEO_SEK:
        raise ValueError(f"видео длиннее {MAX_VIDEO_SEK} с ({dlit:.0f} с)")
    papka = MEDIA / datetime.now().strftime("%Y-%m")
    papka.mkdir(parents=True, exist_ok=True)
    imya = put.stem
    kopiya, small, kadr = papka / f"{imya}{put.suffix.lower()}", papka / f"{imya}_thumb.jpeg", papka / f"{imya}_kadr.png"
    shutil.copyfile(put, kopiya)
    run("ffmpeg", "-y", "-v", "error", "-ss", "1", "-i", str(put), "-frames:v", "1", str(kadr))
    th = Image.open(kadr).convert("RGB")
    kadr.unlink()
    th.thumbnail((400, 400))
    for q in (80, 65, 50, 40):
        th.save(small, "JPEG", quality=q, optimize=True)
        if small.stat().st_size <= 100_000:
            break
    url_video, url_small = vylozhyty([kopiya, small], f"video {imya}")
    return url_video, url_small, kopiya.stat().st_size, int(round(dlit))


def main():
    dry = "--dry-run" in sys.argv
    OCHERED.mkdir(exist_ok=True)
    GOTOVO.mkdir(exist_ok=True)
    seychas = datetime.now()

    gotovye = []
    for fail in sorted(OCHERED.glob("*.md")):
        try:
            vremya, status, telo, kartinka = razobrat(fail)
        except Exception as e:
            zapis_v_log(f"ОШИБКА В ФАЙЛЕ {fail.name}: {e}")
            continue
        if status != "odobreno":
            continue
        if vremya > seychas:
            continue
        # Защита (после 22.11.2026: пост на 19:00 ушёл в 04:42, когда компьютер проснулся).
        # Ночью (22:00–07:00) не публикуем ничего; опоздание больше 2 часов — пост не выпускаем, ждём решения Ферума.
        if seychas.hour >= 22 or seychas.hour < 7:
            continue
        if (seychas - vremya).total_seconds() > 2 * 3600:
            if "--force" not in sys.argv:
                zapis_v_log(f"ПРОСРОЧЕН, НЕ ОПУБЛИКОВАН {fail.name}: время {vremya:%d.%m %H:%M}, опоздание больше 2 часов. Поменять vremya или запустить с --force.")
                continue
        gotovye.append((vremya, fail, telo, kartinka))

    if not gotovye:
        print("Публиковать нечего.")
        return

    t = tokon()
    if dry:
        for vremya, fail, telo, kartinka in sorted(gotovye, key=lambda x: x[0]):
            vid = kartinka and kartinka.suffix.lower() in VIDEO_EXT
            print(f"[dry-run] ушло бы: {fail.name} ({vremya:%d.%m %H:%M}), {len(telo)} знаков" + (f", {'видео' if vid else 'картинка'} {kartinka.name}" if kartinka else ""))
        return

    info = zapros("get_account_info", {"auth_token": t})
    if info.get("status") != 0:
        zapis_v_log(f"ОШИБКА доступа к каналу: {info.get('status_message')}")
        sys.exit(1)
    superadmin = next((m["id"] for m in info.get("members", []) if m.get("role") == "superadmin"), None)
    if not superadmin:
        zapis_v_log("ОШИБКА: в канале не найден суперадмин")
        sys.exit(1)

    for vremya, fail, telo, kartinka in sorted(gotovye, key=lambda x: x[0]):
        if kartinka and kartinka.suffix.lower() in VIDEO_EXT:
            # Сначала готовим видео (если упадёт — ничего не публикуем), потом текст, потом само видео.
            try:
                url_video, url_small, razmer, sek = podgotovit_video(kartinka)
            except Exception as e:
                zapis_v_log(f"ОШИБКА ВИДЕО {fail.name}: {e}")
                continue
            otvet = zapros("post", {"auth_token": t, "from": superadmin, "type": "text", "text": telo})
            if otvet.get("status") != 0:
                zapis_v_log(f"ОШИБКА {fail.name} (текст перед видео): {otvet.get('status')} {otvet.get('status_message')}")
                continue
            otvet = zapros("post", {"auth_token": t, "from": superadmin, "type": "video", "media": url_video,
                                    "thumbnail": url_small, "size": razmer, "duration": sek})
            if otvet.get("status") != 0:
                # Текст уже ушёл: файл убираем из очереди, чтобы текст не продублировался, видео выложить вручную.
                fail.replace(GOTOVO / fail.name)
                zapis_v_log(f"ОШИБКА ВИДЕО {fail.name}: текст опубликован, видео НЕТ ({otvet.get('status')} {otvet.get('status_message')}). Выложить видео вручную.")
                continue
            fail.replace(GOTOVO / fail.name)
            zapis_v_log(f"ОПУБЛИКОВАНО {fail.name} (текст + видео)")
            continue
        if kartinka:
            try:
                url_big, url_small = podgotovit_kartinku(kartinka)
            except Exception as e:
                zapis_v_log(f"ОШИБКА КАРТИНКИ {fail.name}: {e}")
                continue
            otvet = zapros("post", {"auth_token": t, "from": superadmin, "type": "picture", "text": telo, "media": url_big, "thumbnail": url_small})
        else:
            otvet = zapros("post", {"auth_token": t, "from": superadmin, "type": "text", "text": telo})
        if otvet.get("status") == 0:
            fail.replace(GOTOVO / fail.name)
            zapis_v_log(f"ОПУБЛИКОВАНО {fail.name}")
        else:
            zapis_v_log(f"ОШИБКА {fail.name}: {otvet.get('status')} {otvet.get('status_message')}")


if __name__ == "__main__":
    main()
