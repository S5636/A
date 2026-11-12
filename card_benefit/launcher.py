# -*- coding: utf-8 -*-
"""START.bat이 호출하는 실행 스크립트.

같은 와이파이에 있는 PC와 폰이 같이 접속할 수 있도록 0.0.0.0으로 서버를 열고,
폰에서 접속할 때 쓸 이 PC의 사설 IP 주소를 화면에 안내한다.
"""
import hashlib
import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_SETUP_MARKER = os.path.join(BASE_DIR, ".setup_done")
PORT = 5050

# 깃허브 저장소에서 최신 코드를 직접 받아온다(매번 update.py를 따로
# 받아서 실행하지 않아도, 껐다 켜기만 하면 자동으로 최신 버전이 되게).
# 이 저장소/브랜치에 새 코드를 올릴 때마다 여기서 그대로 받아가므로,
# 브랜치 이름이 바뀌면(예: 나중에 main으로 합쳐지면) 이 값도 맞춰 바꿔야 함.
_UPDATE_OWNER = "S5636"
_UPDATE_REPO = "A"
_UPDATE_BRANCH = "claude/card-benefit-check-program-xpvbd6"
_UPDATE_SUBDIR = "card_benefit"
_UPDATE_FILES = [
    "app.py",
    "models.py",
    "launcher.py",
    "static/css/style.css",
    "static/js/app.js",
    "static/img/logo.png",
    "static/img/wordmark.png",
    "static/img/icon-192.png",
    "static/img/icon-512.png",
    "static/img/apple-touch-icon.png",
    "static/manifest.json",
    "templates/index.html",
    "requirements.txt",
]


def local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def tailscale_ip():
    """Tailscale을 쓰면 이 PC에 100.64.0.0/10 대역 IP가 하나 더 생기는데,
    그 주소는 밖에서(같은 와이파이가 아니어도) 접속할 때 써야 한다.
    ipconfig 결과에서 그 대역 IPv4만 찾아서 돌려준다(없으면 None)."""
    try:
        result = subprocess.run(["ipconfig"], capture_output=True, text=True, timeout=5)
        for line in result.stdout.splitlines():
            line = line.strip()
            if line.lower().startswith("ipv4"):
                ip = line.split(":")[-1].strip()
                parts = ip.split(".")
                if len(parts) == 4 and parts[0] == "100" and parts[1].isdigit() and 64 <= int(parts[1]) <= 127:
                    return ip
    except Exception:
        pass
    return None


def open_browser_later():
    time.sleep(2)
    try:
        webbrowser.open(f"http://127.0.0.1:{PORT}/")
    except Exception:
        pass


def self_update():
    """매번 update.py를 받아서 직접 실행하는 대신, 꺼졌다 켜지기만 하면
    깃허브에 올라온 최신 코드를 자동으로 받아오게 한다(옆방 마진 대시보드
    처럼). card_benefit.db(실제 데이터)는 절대 건드리지 않고, 코드
    파일만 내용이 다를 때만 덮어쓴다. 인터넷이 안 되거나 깃허브 접속이
    막힌 PC에서도 여기서 막히면 안 되므로(그러면 프로그램 자체가 안
    켜짐), 실패하면 그냥 지금 있는 파일 그대로 넘어간다 - 조용히."""
    import urllib.request

    updated = []
    for rel in _UPDATE_FILES:
        url = (
            f"https://raw.githubusercontent.com/{_UPDATE_OWNER}/{_UPDATE_REPO}/"
            f"{_UPDATE_BRANCH}/{_UPDATE_SUBDIR}/{rel}"
        )
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                remote_data = resp.read()
        except Exception:
            continue  # 이 파일 하나 실패해도 나머지는 계속 시도
        local_path = os.path.join(BASE_DIR, rel)
        try:
            with open(local_path, "rb") as f:
                local_data = f.read()
        except Exception:
            local_data = None
        if remote_data != local_data:
            try:
                os.makedirs(os.path.dirname(local_path), exist_ok=True)
                with open(local_path, "wb") as f:
                    f.write(remote_data)
                updated.append(rel)
            except Exception:
                pass
    if updated:
        print(f"최신 버전으로 자동 업데이트됨: {', '.join(updated)}")
        print()


def _requirements_hash():
    try:
        with open(os.path.join(BASE_DIR, "requirements.txt"), "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except Exception:
        return ""


def main():
    print("============================================")
    print("  카드 남은혜택 체크")
    print("============================================")
    print()

    print("최신 버전 확인 중...")
    self_update()

    current_hash = _requirements_hash()
    already_done = False
    try:
        with open(_SETUP_MARKER, "r", encoding="utf-8") as f:
            already_done = f.read().strip() == current_hash and bool(current_hash)
    except Exception:
        already_done = False

    if already_done:
        print("필요한 프로그램이 이미 설치되어 있어서 설치 단계를 건너뜁니다.")
    else:
        print("필요한 프로그램을 확인/설치하는 중입니다...")
        result = subprocess.run([sys.executable, "-m", "pip", "install",
                                  "--disable-pip-version-check", "-r", "requirements.txt"])
        if result.returncode != 0:
            print()
            print("[오류] 설치 중 문제가 발생했습니다. 위 내용을 캡처해서 보내주세요.")
            return False
        try:
            with open(_SETUP_MARKER, "w", encoding="utf-8") as f:
                f.write(current_hash)
        except Exception:
            pass

    ip = local_ip()
    ts_ip = tailscale_ip()
    print()
    print("설치 완료. 서버를 시작합니다.")
    print(f"이 PC에서 볼 때:   http://127.0.0.1:{PORT}")
    print(f"폰에서 볼 때(같은 와이파이):   http://{ip}:{PORT}")
    if ts_ip:
        print(f"폰에서 볼 때(밖에서, Tailscale):   http://{ts_ip}:{PORT}")
    print()
    print("이 창을 닫으면 프로그램이 종료됩니다. 계속 이 창을 켜두세요.")
    print("============================================")
    print()

    threading.Thread(target=open_browser_later, daemon=True).start()

    import app  # requirements 설치가 끝난 뒤에 import
    app.start_stock_monitor()
    app.start_subscription_logo_fetch()
    app.app.run(host="0.0.0.0", port=PORT, debug=False)
    return True


if __name__ == "__main__":
    ok = True
    try:
        ok = main()
    except Exception:
        ok = False
        print()
        print("============================================")
        print("[오류] 실행 중 문제가 발생했습니다:")
        print("============================================")
        import traceback
        traceback.print_exc()

    print()
    print("============================================")
    if ok:
        print("서버가 종료되었습니다.")
    else:
        print("서버 실행에 실패했습니다. 위 내용을 캡처해서 보내주세요.")
    print("============================================")
    input("아무 키나 누르면 창이 닫힙니다...")
