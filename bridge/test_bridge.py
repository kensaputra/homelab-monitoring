import unittest

from bridge import build_prompt, format_alert, handle_payload

FIRING = {
    "status": "firing",
    "labels": {"alertname": "DeviceDown", "severity": "critical", "instance": "192.168.1.21",
               "device": "ap-floor-1"},
    "annotations": {"summary": "ap-floor-1 (192.168.1.21) is not answering ping",
                    "description": "No ICMP reply for 2 minutes."},
    "startsAt": "2026-11-09T08:15:00.000Z",
}
RESOLVED = {**FIRING, "status": "resolved"}


class FormatTests(unittest.TestCase):
    def test_firing_message(self):
        text = format_alert(FIRING)
        self.assertTrue(text.startswith("🔴 [CRITICAL] ap-floor-1"))
        self.assertIn("No ICMP reply", text)
        self.assertIn("Since: 2026-11-09 08:15:00 UTC", text)

    def test_resolved_message_is_short(self):
        text = format_alert(RESOLVED)
        self.assertTrue(text.startswith("✅ [RESOLVED]"))
        self.assertNotIn("No ICMP reply", text)

    def test_prompt_forbids_inventing(self):
        self.assertIn("Do not invent", build_prompt(FIRING))


class DeliveryTests(unittest.TestCase):
    def test_sends_with_ai_hint_when_configured(self):
        sent = []
        env = {"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_CHAT_ID": "1", "OLLAMA_URL": "http://x"}
        msgs = handle_payload({"alerts": [FIRING]}, env=env,
                              send=lambda text, *_: sent.append(text),
                              note=lambda *_: "Likely cause: AP lost power\nFirst check: PoE port")
        self.assertEqual(len(sent), 1)
        self.assertIn("AI hint (verify before acting)", msgs[0])

    def test_no_ai_call_for_resolved(self):
        called = []
        handle_payload({"alerts": [RESOLVED]}, env={"OLLAMA_URL": "http://x"},
                       note=lambda *a: called.append(a) or "x")
        self.assertEqual(called, [])

    def test_model_failure_still_sends_alert(self):
        sent = []
        env = {"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_CHAT_ID": "1", "OLLAMA_URL": "http://x"}
        msgs = handle_payload({"alerts": [FIRING]}, env=env,
                              send=lambda text, *_: sent.append(text), note=lambda *_: "")
        self.assertEqual(len(sent), 1)
        self.assertNotIn("AI hint", msgs[0])


if __name__ == "__main__":
    unittest.main()
