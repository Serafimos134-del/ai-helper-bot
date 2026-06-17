import os
import subprocess
import hmac
import hashlib
from datetime import datetime
from flask import Flask, request, jsonify

app = Flask(__name__)

# Секрет для проверки подписи GitHub — задаётся в переменной окружения сервиса
SECRET = os.environ['WEBHOOK_SECRET'].encode()
PROJECT_DIR = '/opt/ai-helper-bot'
LOG_FILE = '/var/log/ai-helper-bot-webhook.log'

def log(msg: str) -> None:
    with open(LOG_FILE, 'a') as f:
        f.write(f"{datetime.utcnow().isoformat()} {msg}\n")

def run_cmd(cmd: str) -> None:
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if result.returncode != 0:
        log(f"ERROR: {cmd}\n{result.stderr.strip()}")
    else:
        log(f"OK: {cmd}")

@app.route('/webhook', methods=['POST'])
def webhook():
    signature = request.headers.get('X-Hub-Signature-256')
    if not signature:
        return 'Missing signature', 403

    body = request.get_data()
    mac = hmac.new(SECRET, msg=body, digestmod=hashlib.sha256)
    expected = 'sha256=' + mac.hexdigest()
    if not hmac.compare_digest(signature, expected):
        return 'Invalid signature', 403

    event = request.headers.get('X-GitHub-Event')
    if event != 'push':
        return 'Ignored event', 200

    payload = request.get_json()
    if not payload or payload.get('ref') != 'refs/heads/main':
        return 'Not main branch', 200

    log('Push to main detected, deploying...')

    run_cmd(f'cd {PROJECT_DIR} && git fetch origin main && git reset --hard origin/main')
    run_cmd(f'find {PROJECT_DIR} -type d -name __pycache__ -exec rm -rf {{}} +')
    run_cmd(f'find {PROJECT_DIR} -name "*.pyc" -delete')
    run_cmd('systemctl restart ai-helper-bot')

    log('Deploy completed')
    return 'Deployed', 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=9000)