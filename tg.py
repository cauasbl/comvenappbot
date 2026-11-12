"""Cliente minimo da Bot API do Telegram (so urllib, sem dependencias).

O token nunca aparece em mensagens de erro.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request


class TelegramError(RuntimeError):
    pass


class Telegram:
    def __init__(self, token: str):
        self._url = f"https://api.telegram.org/bot{token}/"

    def call(self, method: str, _http_timeout: int = 35, **params):
        # "timeout" e parametro da API (getUpdates), por isso o timeout HTTP tem outro nome
        req = urllib.request.Request(self._url + method, data=json.dumps(params).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=_http_timeout) as r:
                body = json.load(r)
        except urllib.error.HTTPError as e:
            try:
                body = json.loads(e.read().decode())
            except ValueError:
                body = {"ok": False, "description": f"HTTP {e.code}"}
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise TelegramError(f"{method}: falha de rede ({type(e).__name__})") from None
        if not body.get("ok"):
            raise TelegramError(f"{method}: {body.get('description', 'erro')}")
        return body["result"]

    def get_updates(self, offset: int | None, timeout: int = 25) -> list[dict]:
        params = {"timeout": timeout, "allowed_updates": ["message", "callback_query"]}
        if offset is not None:
            params["offset"] = offset
        return self.call("getUpdates", _http_timeout=timeout + 10, **params)

    @staticmethod
    def _markup(buttons):
        return {"inline_keyboard": buttons} if buttons else {"inline_keyboard": []}

    def send(self, chat_id, text: str, buttons=None) -> int:
        params = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
        if buttons:
            params["reply_markup"] = self._markup(buttons)
        return self.call("sendMessage", **params)["message_id"]

    def answer(self, callback_id: str, text: str = "") -> None:
        self.call("answerCallbackQuery", callback_query_id=callback_id, text=text)

    def edit_text(self, chat_id, message_id: int, text: str, buttons=None) -> None:
        self.call("editMessageText", chat_id=chat_id, message_id=message_id, text=text, parse_mode="HTML",
                  disable_web_page_preview=True, reply_markup=self._markup(buttons))

    def edit_markup(self, chat_id, message_id: int, buttons) -> None:
        self.call("editMessageReplyMarkup", chat_id=chat_id, message_id=message_id,
                  reply_markup=self._markup(buttons))
