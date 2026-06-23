import json
from urllib import request
from urllib.error import HTTPError, URLError

from config import GCHAT_ENABLED, GCHAT_TIMEOUT_SECONDS, GCHAT_WEBHOOK_URL
from utils.logger import get_logger

logger = get_logger("GoogleChatSender")


class GoogleChatSender:
    def __init__(self):
        self.enabled = GCHAT_ENABLED
        self.webhook_url = GCHAT_WEBHOOK_URL
        self.timeout_seconds = GCHAT_TIMEOUT_SECONDS

    def send_text(self, text):
        if not self.enabled:
            logger.info("Google Chat disabled (GCHAT_ENABLED=false). Skipping send.")
            return False

        if not self.webhook_url:
            logger.warning("Google Chat enabled but GCHAT_WEBHOOK_URL is empty. Skipping send.")
            return False

        payload = {"text": text}
        return self._send_payload(payload)

    def send_report(self, text, image_url=None, csv_url=None, chart_url=None):
        """Send a formatted report to Google Chat using cards.

        Places the main report text inside a textParagraph widget (supports simple
        Markdown-like formatting) and includes an image and CSV download button
        when provided.
        """
        # Base card with header and the textual content in a textParagraph widget.
        sections = [
            {
                "widgets": [
                    {"textParagraph": {"text": text}}
                ]
            }
        ]

        # If an image was provided, add a separate section that shows the image
        # and optionally buttons to download the chart/CSV.
        if image_url:
            image_widgets = [
                {"image": {"imageUrl": image_url, "altText": "Hourly Present vs Absent Chart"}},
            ]
            button_list = []
            if chart_url:
                button_list.append(
                    {
                        "textButton": {
                            "text": "Download Chart",
                            "onClick": {"openLink": {"url": chart_url}},
                        }
                    }
                )
            if csv_url:
                button_list.append(
                    {
                        "textButton": {
                            "text": "Download CSV",
                            "onClick": {"openLink": {"url": csv_url}},
                        }
                    }
                )
            if button_list:
                image_widgets.append({"buttons": button_list})
            sections.append({"widgets": image_widgets})

        payload = {
            "cards": [
                {
                    "header": {
                        "title": "Solar Audit Daily Report",
                        "subtitle": "Hourly Present vs Absent"
                    },
                    "sections": sections,
                }
            ],
        }

        ok = self._send_payload(payload)
        if ok:
            return True

        logger.warning("Falling back to text-only Google Chat message.")
        return self.send_text(text)

    def _send_payload(self, payload_obj):
        if not self.enabled:
            logger.info("Google Chat disabled (GCHAT_ENABLED=false). Skipping send.")
            return False

        if not self.webhook_url:
            logger.warning("Google Chat enabled but GCHAT_WEBHOOK_URL is empty. Skipping send.")
            return False

        payload = json.dumps(payload_obj).encode("utf-8")
        req = request.Request(
            self.webhook_url,
            data=payload,
            headers={"Content-Type": "application/json; charset=UTF-8"},
            method="POST",
        )

        try:
            with request.urlopen(req, timeout=self.timeout_seconds) as resp:
                status = getattr(resp, "status", None)
                if status and 200 <= status < 300:
                    logger.info("Google Chat message sent successfully.")
                    return True
                logger.warning(f"Google Chat returned unexpected status: {status}")
                return False
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="ignore")
            logger.error(f"Google Chat HTTPError {exc.code}: {body}")
            return False
        except URLError as exc:
            logger.error(f"Google Chat URLError: {exc}")
            return False
        except Exception as exc:
            logger.error(f"Google Chat send failed: {exc}")
            return False
