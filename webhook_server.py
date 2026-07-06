import os
import time
import ipaddress
import subprocess
import hmac
import hashlib
from datetime import datetime
from collections import deque
from flask import Flask, request, jsonify

app = Flask(__name__)

# Секрет для проверки подписи GitHub — задаётся в переменной окружения сервиса
SECRET = os.environ['WEBHOOK_SECRET'].encode()
PROJECT_DIR = '/opt/ai-helper-bot'
LOG_FILE = '/var/log/ai-helper-bot-webhook.log'

# Ожидаемый репозиторий — доп. проверка, что запрос про наш проект,
# а не просто про какой-то репозиторий с тем же секретом.
EXPECTED_REPO = os.environ.get('WEBHOOK_EXPECTED_REPO', '')  # напр. "Serafimos134-del/ai-helper-bot"

# Ограничение размера входящего тела запроса (защита от DoS большими payload).
app.config['MAX_CONTENT_LENGTH'] = 1 * 1024 * 1024  # 1 MB

# GitHub Webhook IP-диапазоны (см. https://api.github.com/meta -> "hooks").
# Если сервис стоит за прокси и IP-проверка ломает деплой — можно отключить
# через WEBHOOK_SKIP_IP_CHECK=1, но это снижает защиту.
GITHUB_HOOK_RANGES = [
    ipaddress.ip_network('192.30.252.0/22'),
    ipaddress.ip_network('185.199.108.0/22'),
    ipaddress.ip_network('140.82.112.0/20'),
    ipaddress.ip_network('143.55.64.0/20'),
]
SKIP_IP_CHECK = os.environ.get('WEBHOOK_SKIP_IP_CHECK') == '1'

# Простая защита от перебора: не более 10 неудачных попыток подписи в минуту.
_failed_attempts = deque()
MAX_FAILED_PER_MINUTE = 10


def log(msg: str) -> None:
    with open(LOG_FILE, 'a') as f:
        f.write(f"{datetime.utcnow().isoformat()} {msg}\n")


def run_cmd(cmd: str) -> None:
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if result.returncode != 0:
        log(f"ERROR: {cmd}\n{result.stderr.strip()}")
    else:
        log(f"OK: {cmd}")


def _client_ip() -> str:
    # Если сервис за доверенным реверс-прокси, X-Forwarded-For настраивается там же.
    forwarded = request.headers.get('X-Forwarded-For')
    return forwarded.split(',')[0].strip() if forwarded else request.remote_addr


def _ip_allowed(ip: str) -> bool:
    if SKIP_IP_CHECK:
        return True
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(addr in net for net in GITHUB_HOOK_RANGES)


def _register_failed_attempt() -> bool:
    """Возвращает True, если лимит неудачных попыток превышен."""
    now = time.monotonic()
    _failed_attempts.append(now)
    while _failed_attempts and now - _failed_attempts[0] > 60:
        _failed_attempts.popleft()
    return len(_failed_attempts) > MAX_FAILED_PER_MINUTE


@app.route('/webhook', methods=['POST'])
def webhook():
    ip = _client_ip()

    if not _ip_allowed(ip):
        log(f"BLOCKED: request from disallowed IP {ip}")
        return 'Forbidden', 403

    signature = request.headers.get('X-Hub-Signature-256')
    if not signature:
        log(f"REJECTED: missing signature from {ip}")
        return 'Missing signature', 403

    body = request.get_data()
    mac = hmac.new(SECRET, msg=body, digestmod=hashlib.sha256)
    expected = 'sha256=' + mac.hexdigest()
    if not hmac.compare_digest(signature, expected):
        if _register_failed_attempt():
            log(f"BLOCKED: too many failed signature attempts from {ip}")
            return 'Too many attempts', 429
        log(f"REJECTED: invalid signature from {ip}")
        return 'Invalid signature', 403

    event = request.headers.get('X-GitHub-Event')
    if event != 'push':
        return 'Ignored event', 200

    payload = request.get_json()
    if not payload or payload.get('ref') != 'refs/heads/main':
        return 'Not main branch', 200

    if EXPECTED_REPO and payload.get('repository', {}).get('full_name') != EXPECTED_REPO:
        log(f"REJECTED: unexpected repository in payload from {ip}")
        return 'Unexpected repository', 403

    log(f'Push to main detected from {ip}, deploying...')

    run_cmd(f'cd {PROJECT_DIR} && git fetch origin main && git reset --hard origin/main')
    run_cmd(f'find {PROJECT_DIR} -type d -name __pycache__ -exec rm -rf {{}} +')
    run_cmd(f'find {PROJECT_DIR} -name "*.pyc" -delete')
    run_cmd('systemctl restart ai-helper-bot')

    log('Deploy completed')
    return 'Deployed', 200


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=9000)