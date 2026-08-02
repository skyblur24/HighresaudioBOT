#!/usr/bin/env python3

# Standard
import os
import re
import sys
import json
import time
import queue
import platform
import threading
import traceback

# Third party
import requests
from tqdm import tqdm
from bs4 import BeautifulSoup, NavigableString, Tag

# ==========================================
# 🔧 RUTA DE DESCARGA (Termux)
# ==========================================
BASE_DOWNLOAD_PATH = "/content/drive/MyDrive/HRA/"
# ==========================================

# ==========================================
# 📤 TELEGRAM
# ==========================================
TG_TOKEN = "8948682374:AAHqnlRcbSnL0pxTLcHOKrINS0NFBSu3kCk"
TG_CHAT_ID = "-1004308273527"

def enviarMensaje(texto):
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    try:
        requests.post(url, data={"chat_id": TG_CHAT_ID, "text": texto}, timeout=30)
    except Exception as e:
        print(f"⚠ Fallo al enviar mensaje: {e}")

def enviarTelegram(ruta_archivo, caption=None):
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendDocument"
    try:
        with open(ruta_archivo, "rb") as f:
            r = requests.post(
                url,
                data={"chat_id": TG_CHAT_ID, "caption": caption or ""},
                files={"document": f},
                timeout=120
            )
        if r.status_code == 200:
            print(f"✅ Enviado a Telegram: {os.path.basename(ruta_archivo)}")
        else:
            print(f"⚠ Error Telegram ({r.status_code}): {r.text}")
    except Exception as e:
        print(f"⚠ Fallo al enviar a Telegram: {e}")
# ==========================================

def getOs():
    return platform.system() == 'Windows'

def osCommands(x):
    if getOs():
        if x == "p":
            os.system('pause >nul')
        elif x == "c":
            os.system('cls')
        elif x == "t":
            os.system('title HRA-DL (Optimized)')
    else:
        if x == "p":
            os.system("read -rsp $''")
        elif x == "c":
            os.system('clear')
        elif x == "t":
            sys.stdout.write("\x1b]2;HRA-DL (Optimized)\x07")

def login(email, pwd):
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:67.0) Gecko/20100101 Firefox/67.0"
    })
    r = session.get(
        f'https://streaming.highresaudio.com:8182/vault3/user/login?password={pwd}&username={email}'
    )
    try:
        data = r.json()
    except Exception:
        data = None

    if r.status_code in (200, 206) and data and data.get("has_subscription"):
        print("Signed in successfully.\n")
        return session, r.text
    else:
        session.close()
        detalle = data if data is not None else r.text[:300]
        raise Exception(f"Login failed (status {r.status_code}): {detalle}")

def fetchAlbumId(session, url):
    soup = BeautifulSoup(session.get(url).text, "html.parser")
    return soup.find(attrs={"data-id": True})['data-id']

def fetchMetadata(session, albumId, userData):
    r = session.get(
        f'https://streaming.highresaudio.com:8182/vault3/vault/album/?album_id={albumId}&userData={userData}'
    )
    if r.status_code != 200:
        raise Exception("Failed to fetch metadata (revisá la URL o la sesión de HighResAudio).")
    return r.json()

def dirSetup(path):
    os.makedirs(path, exist_ok=True)
    return path

def fileSetup(fname):
    if os.path.isfile(fname):
        os.remove(fname)

# ==========================================
# 📄 TRACKLIST GENERATOR
# ==========================================
def save_tracklist(tracks, albumPath):
    tracklist_path = os.path.join(albumPath, "Playlist.txt")
    with open(tracklist_path, "w", encoding="utf-8") as f:
        f.write("Playlist:\n")
        for i, track in enumerate(tracks, start=1):
            number = f"{i:02d}"
            title = track.get("title", "Unknown Title")
            f.write(f"{number} {title}\n")

# ==========================================
# 📝 INFO GENERATOR
# ==========================================
HEADING_TAGS = ('h1', 'h2', 'h3', 'h4', 'h5', 'h6')
BLOCK_TAGS = ('p', 'div', 'li') + HEADING_TAGS

BOLD_MAP = {}
for _i in range(26):
    BOLD_MAP[chr(ord('A') + _i)] = chr(0x1D63C + _i)  # 𝘼-𝙕
    BOLD_MAP[chr(ord('a') + _i)] = chr(0x1D656 + _i)  # 𝙖-𝙯
for _i in range(10):
    BOLD_MAP[chr(ord('0') + _i)] = chr(0x1D7EC + _i)  # 𝟬-𝟵

def toBold(text):
    return "".join(BOLD_MAP.get(ch, ch) for ch in text)

def render_node(el, output):
    if isinstance(el, NavigableString):
        output.append(str(el))
        return
    if not isinstance(el, Tag):
        return
    name = el.name

    if name in ('script', 'style'):
        return

    if name in ('strong', 'b'):
        temp = []
        for child in el.children:
            render_node(child, temp)
        output.append(toBold("".join(temp)))
        return

    if name == 'br':
        output.append("\n")
        return

    if name in HEADING_TAGS:
        temp = []
        for child in el.children:
            render_node(child, temp)
        output.append("\n")
        output.append(toBold("".join(temp)))
        output.append("\n")
        return

    if name in BLOCK_TAGS:
        output.append("\n")
        for child in el.children:
            render_node(child, output)
        output.append("\n")
        return

    for child in el.children:
        render_node(child, output)

def formatText(div, skip_label=None):
    output = []
    for child in div.children:
        render_node(child, output)
    raw = "".join(output)

    # Limpiar espacios dentro de cada línea sin perder los saltos de párrafo
    lines = [re.sub(r'[ \t]+', ' ', line).strip() for line in raw.split("\n")]

    cleaned = []
    prev_blank = True
    for line in lines:
        if line == "":
            if not prev_blank:
                cleaned.append("")
            prev_blank = True
        else:
            cleaned.append(line)
            prev_blank = False

    # Quitar la etiqueta de pestaña duplicada al inicio (p. ej. "Info" suelto antes de "Info for ...")
    if skip_label and cleaned and cleaned[0].strip().lower() == skip_label.lower():
        cleaned = cleaned[1:]
        while cleaned and cleaned[0] == "":
            cleaned = cleaned[1:]

    return "\n".join(cleaned).strip()

def fetchInfo(session, url):
    soup = BeautifulSoup(session.get(url).text, "html.parser")

    info_div = soup.find(id="albumtab-info")
    info_text = formatText(info_div, skip_label="Info") if info_div else "No info found."

    return info_text

def save_info(info_text, albumPath):
    info_path = os.path.join(albumPath, "Info.txt")
    with open(info_path, "w", encoding="utf-8") as f:
        f.write(info_text)

# ==========================================
# 🔥 FETCH TRACK CON ANTI-CONGELAMIENTO
# ==========================================
def fetchTrack(session, albumId, fname, spec, trackNum, trackTitle, trackTotal, url):
    max_retries = 6
    timeout_seconds = 8
    attempt = 0

    while attempt < max_retries:
        try:
            session.headers.update({
                "range": "bytes=0-",
                "referer": f"https://stream-app.highresaudio.com/album/{albumId}"
            })

            print(f"Downloading {trackNum}/{trackTotal}: {trackTitle} - {spec} (Attempt {attempt+1})")

            r = session.get(url, stream=True, timeout=15)
            size = int(r.headers.get('content-length', 0))

            downloaded = 0
            last_progress_time = time.time()

            with open(fname, 'wb') as f:
                with tqdm(total=size, unit='B', unit_scale=True, unit_divisor=1024) as bar:
                    for chunk in r.iter_content(128 * 1024):
                        if chunk:
                            f.write(chunk)
                            chunk_size = len(chunk)
                            downloaded += chunk_size
                            bar.update(chunk_size)
                            last_progress_time = time.time()

                        if time.time() - last_progress_time > timeout_seconds:
                            raise Exception("Download stalled")

            return  # Success

        except Exception:
            attempt += 1
            print(f"\n⚠ Download interrupted. Retrying... ({attempt}/{max_retries})\n")
            time.sleep(2)

    print(f"\n❌ Failed after {max_retries} attempts.\n")

def fetchFile(session, url, dest):
    fileSetup(dest)
    r = session.get(url, stream=True)
    size = int(r.headers.get('content-length', 0))

    with open(dest, 'wb') as f:
        with tqdm(total=size, unit='B', unit_scale=True, unit_divisor=1024) as bar:
            for chunk in r.iter_content(128 * 1024):
                if chunk:
                    f.write(chunk)
                    bar.update(len(chunk))

def sanitizeFname(fname):
    if getOs():
        return re.sub(r'[\\/:*?"><|]', '-', fname)
    else:
        return re.sub('/', '-', fname)

def main(config, url):
    url = url.strip()

    if not re.match(r"https?://(?:www\.)?highresaudio\.com/", url):
        print("Invalid URL.")
        enviarMensaje("❌ URL inválida.")
        return

    session, userData = login(config["email"], config["password"])

    try:
        osCommands('c')

        albumId = fetchAlbumId(session, url)
        metadata = fetchMetadata(session, albumId, userData)

        artist = metadata['data']['results']['artist']
        title = metadata['data']['results']['title']
        tracks = metadata['data']['results']['tracks']

        # ==========================================
        # 🔎 DETECTAR CALIDAD (Sample Rates)
        # ==========================================
        sample_rates = set()
        for t in tracks:
            try:
                rate = float(t['format'])
                sample_rates.add(rate)
            except:
                pass

        sample_rates = sorted(sample_rates)
        formatted_rates = [f"{r:.1f}kHz" if r % 1 == 0 else f"{r}kHz" for r in sample_rates]
        rate_string = " & ".join(formatted_rates)
        quality_tag = f"[HIGHRESAUDIO HRA 24bits/{rate_string}]"

        # ==========================================
        # 📘 DETECTAR BOOKLET
        # ==========================================
        hasBooklet = "booklet" in metadata['data']['results']
        if hasBooklet:
            albumFolder = f"{artist} - {title} {quality_tag} + Digital Booklet"
        else:
            albumFolder = f"{artist} - {title} {quality_tag}"

        print(f"{albumFolder}\n")

        albumPath = dirSetup(
            os.path.join(BASE_DOWNLOAD_PATH, sanitizeFname(albumFolder))
        )

        # Generar Tracklist
        save_tracklist(tracks, albumPath)

        # Generar Info
        info_text = fetchInfo(session, url)
        save_info(info_text, albumPath)

        # ==========================================
        # 🎨 COVER DOWNLOAD
        # ==========================================
        cover_data = metadata['data']['results'].get("cover")
        if cover_data:
            if "master" in cover_data and "file_url" in cover_data["master"]:
                print("Downloading Folder...")
                cover_url = "https://" + cover_data["master"]["file_url"]
                coverPath = os.path.join(albumPath, "Folder.jpg")
                fetchFile(session, cover_url, coverPath)
                enviarTelegram(coverPath, caption="Folder")

        totalTracks = str(len(tracks)).zfill(2)

        for track in tracks:
            trackNum = str(track['trackNumber']).zfill(2)
            trackTitle = sanitizeFname(track['title'])

            tempFile = os.path.join(albumPath, f"{trackNum}.flac")
            finalFile = os.path.join(albumPath, f"{trackNum}. {trackTitle}.flac")

            fileSetup(tempFile)
            fileSetup(finalFile)

            fetchTrack(
                session,
                albumId,
                tempFile,
                f"{track['format']} kHz FLAC",
                trackNum,
                track['title'],
                totalTracks,
                track['url']
            )

            if os.path.exists(tempFile):
                os.rename(tempFile, finalFile)
                enviarTelegram(finalFile, caption=f"{trackTitle}")

        # ==========================================
        # 📘 BOOKLET DOWNLOAD
        # ==========================================
        if hasBooklet:
            print("Downloading Digital Booklet...")
            bookletPath = os.path.join(albumPath, "booklet.pdf")
            fetchFile(
                session,
                f"https://{metadata['data']['results']['booklet']}",
                bookletPath
            )
            enviarTelegram(bookletPath, caption="Digital Booklet")

        print("\nAlbum completed.")
        enviarMensaje(f"✅ Álbum completo: {albumFolder}")
    finally:
        session.close()

# ==========================================
# 📋 COLA DE DESCARGAS
# ==========================================
cola_descargas = queue.Queue()

def trabajador(config):
    while True:
        url = cola_descargas.get()
        try:
            main(config, url)
        except Exception as e:
            traceback.print_exc()
            enviarMensaje(f"❌ Error con {url}: {e}")
        finally:
            cola_descargas.task_done()

# ==========================================
# 👂 LISTENER DE TELEGRAM
# ==========================================
def escucharTelegram(userData):
    print("🤖 Escuchando el grupo de Telegram... (Ctrl+C para salir)")
    offset = None
    esperando_url = False

    while True:
        try:
            params = {"timeout": 30}
            if offset:
                params["offset"] = offset

            r = requests.get(
                f"https://api.telegram.org/bot{TG_TOKEN}/getUpdates",
                params=params, timeout=40
            )
            updates = r.json().get("result", [])

            for update in updates:
                offset = update["update_id"] + 1
                msg = update.get("message")
                if not msg or "text" not in msg:
                    continue

                chat_id = str(msg["chat"]["id"])
                if chat_id != TG_CHAT_ID:
                    continue

                texto = msg["text"].strip()

                if texto == "/download":
                    esperando_url = True
                    enviarMensaje("📥 Enviá la URL del álbum de HIGHRESAUDIO.")

                elif esperando_url and texto.startswith("http"):
                    esperando_url = False
                    cola_descargas.put(texto)
                    pendientes = cola_descargas.qsize()
                    if pendientes > 1:
                        enviarMensaje(f"📋 Agregado a la cola (posición {pendientes}).")
                    else:
                        enviarMensaje(f"⏳ Descargando: {texto}")

        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:
            print(f"⚠ Error en el listener: {e}")
            time.sleep(5)

if __name__ == '__main__':
    osCommands('t')

    with open("config.json") as f:
        config = json.load(f)

    try:
        session_test, _ = login(config["email"], config["password"])
        session_test.close()
    except Exception as e:
        print(f"No se pudo iniciar sesión: {e}")
        sys.exit()

    try:
        threading.Thread(target=trabajador, args=(config,), daemon=True).start()
        escucharTelegram(config)
    except (KeyboardInterrupt, SystemExit):
        sys.exit()
    except:
        traceback.print_exc()
        input("\nAn exception has occurred. Press enter to exit.")
        sys.exit()