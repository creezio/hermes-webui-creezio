"""Public WhatsApp pairing page + live QR (no CDN cache)."""
from __future__ import annotations

import base64
import json
import secrets
import time
from pathlib import Path
from urllib.parse import urlparse

STATUS_PATH = Path.home() / ".hermes" / "whatsapp-pairing-status.json"
QR_PATH = Path(__file__).resolve().parents[1] / "static" / "whatsapp-pairing-qr.png"
TOKEN_PATH = Path.home() / ".hermes" / "whatsapp-pairing-token.txt"

PAIR_HTML = """<!doctype html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="Cache-Control" content="no-store">
  <title>Hermes — WhatsApp pairing</title>
  <style>
    body { font-family: system-ui, sans-serif; background:#0b0f14; color:#e8eef5; margin:0; padding:24px; text-align:center; }
    .card { max-width:420px; margin:0 auto; background:#121821; border:1px solid #243041; border-radius:16px; padding:24px; }
    img { width:min(320px, 80vw); height:auto; background:#fff; border-radius:12px; padding:12px; }
    .muted { color:#9aa7b8; font-size:14px; line-height:1.5; }
    .status { margin-top:12px; font-size:13px; }
    .ok { color:#22c55e; }
    .err { color:#ef4444; }
    .rev { font-size:11px; color:#6b7c93; margin-top:6px; }
  </style>
</head>
<body>
  <div class="card">
    <h1>WhatsApp pairing</h1>
    <p class="muted">WhatsApp → Paramètres → Appareils connectés → Connecter un appareil. Le QR change toutes les ~20&nbsp;s.</p>
    <img id="qr" alt="QR WhatsApp" src="">
    <div id="status" class="status muted">Chargement…</div>
    <div id="rev" class="rev"></div>
  </div>
  <script>
    const statusEl = document.getElementById('status');
    const revEl = document.getElementById('rev');
    const qrEl = document.getElementById('qr');
    const pairBase = window.location.pathname.replace(/\\/$/, '');
    let lastRevision = null;
    async function refresh() {
      try {
        const res = await fetch(pairBase + '/status?ts=' + Date.now(), { cache: 'no-store' });
        const data = await res.json();
        if (data.qr_data_url) {
          qrEl.src = data.qr_data_url;
          if (data.qr_revision && data.qr_revision !== lastRevision) {
            lastRevision = data.qr_revision;
            revEl.textContent = 'QR #' + data.qr_revision + ' — ' + new Date().toLocaleTimeString();
          }
        }
        if (data.status === 'connected') {
          statusEl.textContent = 'Connecté : ' + (data.account_name || data.account_id || 'OK');
          statusEl.className = 'status ok';
          return;
        }
        if (data.status === 'starting') {
          statusEl.textContent = 'Génération du QR…';
          statusEl.className = 'status muted';
          return;
        }
        if (!data.qr_data_url) {
          statusEl.textContent = data.error || 'En attente du prochain QR…';
          statusEl.className = 'status muted';
          return;
        }
        statusEl.textContent = 'Scannez maintenant — QR live actif';
        statusEl.className = 'status muted';
      } catch (e) {
        statusEl.textContent = 'Impossible de charger le statut';
        statusEl.className = 'status err';
      }
    }
    refresh();
    setInterval(refresh, 2000);
  </script>
</body>
</html>
"""


def ensure_pairing_token() -> str:
    if TOKEN_PATH.exists():
        token = TOKEN_PATH.read_text(encoding="utf-8").strip()
        if token:
            return token
    token = secrets.token_urlsafe(24)
    TOKEN_PATH.write_text(token + "\n", encoding="utf-8")
    TOKEN_PATH.chmod(0o600)
    return token


def _extract_token(path: str) -> str | None:
    parts = [p for p in urlparse(path).path.split("/") if p]
    if len(parts) < 2 or parts[0] != "whatsapp-pair":
        return None
    token = parts[1]
    if token in {"qr", "status"}:
        return None
    return token


def _valid_token(token: str | None) -> bool:
    if not token:
        return False
    expected = TOKEN_PATH.read_text(encoding="utf-8").strip() if TOKEN_PATH.exists() else ""
    return bool(expected) and secrets.compare_digest(token, expected)


def _no_store_headers(handler) -> None:
    handler.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
    handler.send_header("Pragma", "no-cache")
    handler.send_header("CDN-Cache-Control", "no-store")
    handler.send_header("Surrogate-Control", "no-store")


def _load_status_payload() -> dict:
    payload: dict = {"status": "starting", "updated_at": time.time()}
    if STATUS_PATH.exists():
        try:
            payload = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
        except Exception:
            payload = {"status": "error", "error": "invalid status file"}
    if "qr_data_url" not in payload and QR_PATH.exists():
        try:
            encoded = base64.b64encode(QR_PATH.read_bytes()).decode("ascii")
            payload["qr_data_url"] = f"data:image/png;base64,{encoded}"
            payload["qr_revision"] = int(QR_PATH.stat().st_mtime_ns // 1_000_000)
        except Exception:
            pass
    payload["server_time"] = time.time()
    return payload


def handle_whatsapp_pair_route(handler, parsed) -> bool | None:
    token = _extract_token(parsed.path)
    if not _valid_token(token):
        return False

    suffix = urlparse(parsed.path).path.rstrip("/").split("/")[-1]

    if suffix == token:
        if not urlparse(parsed.path).path.endswith("/"):
            location = f"/whatsapp-pair/{token}/"
            handler.send_response(302)
            handler.send_header("Location", location)
            _no_store_headers(handler)
            handler.send_header("Content-Length", "0")
            handler.end_headers()
            return True
        body = PAIR_HTML.encode("utf-8")
        handler.send_response(200)
        handler.send_header("Content-Type", "text/html; charset=utf-8")
        handler.send_header("Content-Length", str(len(body)))
        _no_store_headers(handler)
        handler.end_headers()
        handler.wfile.write(body)
        return True

    if suffix == "status":
        body = json.dumps(_load_status_payload()).encode("utf-8")
        handler.send_response(200)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Content-Length", str(len(body)))
        _no_store_headers(handler)
        handler.end_headers()
        handler.wfile.write(body)
        return True

    if suffix == "qr":
        if not QR_PATH.exists():
            return False
        data = QR_PATH.read_bytes()
        handler.send_response(200)
        handler.send_header("Content-Type", "image/png")
        handler.send_header("Content-Length", str(len(data)))
        _no_store_headers(handler)
        handler.end_headers()
        handler.wfile.write(data)
        return True

    return False
