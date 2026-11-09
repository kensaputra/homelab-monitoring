"""Alertmanager -> Telegram bridge, with an optional local-LLM triage note.

Alertmanager POSTs its webhook JSON to /alert. The bridge turns each alert into a
short Telegram message. If OLLAMA_URL is set, it also asks a local model for a
two-line "likely cause / first check" note and appends it, clearly labelled as AI.
The note is advice only: the bridge never runs commands or changes anything.

Standard library only, so the container needs no pip install.

Environment:
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID   where to send (if unset, messages are only logged)
    OLLAMA_URL                             e.g. http://ollama:11434 (optional)
    OLLAMA_MODEL                           default llama3.2:3b
    PORT                                   default 8080
"""
import json
import logging
import os
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

log = logging.getLogger("alert-bridge")

SEVERITY_ICON = {"critical": "🔴", "warning": "🟠", "info": "🔵"}
MAX_BODY = 1_000_000  # bytes; Alertmanager batches are small


def format_alert(alert):
    """Return the human-readable Telegram text for one Alertmanager alert."""
    labels = alert.get("labels", {})
    notes = alert.get("annotations", {})
    resolved = alert.get("status") == "resolved"
    icon = "✅" if resolved else SEVERITY_ICON.get(labels.get("severity", ""), "⚪")
    state = "RESOLVED" if resolved else labels.get("severity", "alert").upper()
    title = notes.get("summary") or labels.get("alertname", "Alert")
    lines = [f"{icon} [{state}] {title}"]
    if notes.get("description") and not resolved:
        lines.append(notes["description"])
    where = labels.get("instance")
    if where:
        lines.append(f"Target: {where}")
    started = (alert.get("startsAt") or "")[:19].replace("T", " ")
    if started and not resolved:
        lines.append(f"Since: {started} UTC")
    return "\n".join(lines)


def build_prompt(alert):
    labels = json.dumps(alert.get("labels", {}), ensure_ascii=False)
    notes = json.dumps(alert.get("annotations", {}), ensure_ascii=False)
    return (
        "You help an on-call IT engineer at a small office. An alert fired.\n"
        f"Labels: {labels}\nAnnotations: {notes}\n"
        "Reply in at most two short lines: 'Likely cause:' and 'First check:'. "
        "Use only what the alert says; if unsure, say so. Do not invent device names or numbers."
    )


def ai_note(alert, url, model, timeout=20):
    """Ask Ollama for a short triage hint. Returns '' on any failure."""
    body = json.dumps({"model": model, "prompt": build_prompt(alert), "stream": False,
                       "options": {"temperature": 0.2, "num_predict": 120}}).encode()
    req = urllib.request.Request(url.rstrip("/") + "/api/generate", data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            text = json.load(resp).get("response", "").strip()
    except Exception as exc:  # the alert must still go out if the model is down
        log.warning("ollama call failed: %s", exc)
        return ""
    return "\n".join(text.splitlines()[:2])[:400]


def send_telegram(text, token, chat_id, timeout=10):
    body = json.dumps({"chat_id": chat_id, "text": text, "disable_web_page_preview": True}).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status == 200


def handle_payload(payload, env=os.environ, send=send_telegram, note=ai_note):
    """Format every alert in an Alertmanager webhook payload and deliver it. Returns the messages."""
    messages = []
    for alert in payload.get("alerts", []):
        text = format_alert(alert)
        if env.get("OLLAMA_URL") and alert.get("status") != "resolved":
            hint = note(alert, env["OLLAMA_URL"], env.get("OLLAMA_MODEL", "llama3.2:3b"))
            if hint:
                text += f"\n\n🤖 AI hint (verify before acting):\n{hint}"
        messages.append(text)
        token, chat = env.get("TELEGRAM_BOT_TOKEN"), env.get("TELEGRAM_CHAT_ID")
        if token and chat:
            try:
                send(text, token, chat)
            except Exception as exc:
                log.error("telegram send failed: %s", exc)
        else:
            log.info("telegram not configured, message:\n%s", text)
    return messages


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/healthz":
            self._reply(200, b"ok")
        else:
            self._reply(404, b"not found")

    def do_POST(self):
        if self.path != "/alert":
            return self._reply(404, b"not found")
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_BODY:
            return self._reply(400, b"bad length")
        try:
            payload = json.loads(self.rfile.read(length))
        except json.JSONDecodeError:
            return self._reply(400, b"bad json")
        handle_payload(payload)
        self._reply(200, b"ok")

    def _reply(self, code, body):
        self.send_response(code)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        log.debug(fmt, *args)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    port = int(os.environ.get("PORT", "8080"))
    log.info("alert bridge listening on :%d", port)
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
