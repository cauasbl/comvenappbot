"""Bot do Telegram: varre escanteios, avisa +EV e registra apostei/ignorei.

Uso (PowerShell, dentro de ev_bot):
    $env:TELEGRAM_BOT_TOKEN = "..."; $env:ODDSPAPI_KEY = "..."
    python bot.py
Depois mande /start para o bot no Telegram (o primeiro chat a mandar vira o dono).
"""
from __future__ import annotations

import html
import json
import logging
import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from store import Store
from tg import Telegram, TelegramError

log = logging.getLogger("bot")

HELP = ("<b>Comandos</b>\n/scan - varrer agora\n/status - resumo e filtros\n/ligas - marcar/desmarcar ligas\n"
        "/banca 1000 - definir banca\n/auto 120 - varrer sozinho a cada N min (0 desliga)")

ON, OFF = "✅", "⬜"


def fmt_time(s: str | None) -> str:
    """Horario no fuso do computador, com o UTC entre parenteses: '12/11 16:45 (2026-11-12 19:45 UTC)'."""
    if not s:
        return "?"
    utc = s.replace("T", " ")[:16] + " UTC"
    try:
        t = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return utc
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return f"{t.astimezone().strftime('%d/%m %H:%M')} ({utc})"


def format_alert(a: dict, footer: str = "") -> str:
    pin = json.loads(a["pin_odds"])
    text = (
        f"⚽ <b>{html.escape(a['fixture'])}</b>\n"
        f"{html.escape(a['market'])} — <b>{html.escape(a['outcome'])}</b>\n\n"
        f"Betano <b>{a['betano_odd']:.2f}</b> · odd justa {1 / a['fair_prob']:.2f}\n"
        f"Pinnacle {' / '.join(f'{x:.3f}' for x in pin)}\n"
        f"EV <b>{a['ev']:+.1%}</b> · ¼ Kelly {a['kelly_pct']:.2%} · stake <b>{a['stake']:.2f}</b>\n"
        f"Início: {fmt_time(a['start_time'])}"
    )
    return text + (f"\n\n{footer}" if footer else "")


def decision_buttons(aid: int) -> list:
    return [[{"text": "✅ Apostei", "callback_data": f"bet:{aid}"},
             {"text": "❌ Ignorei", "callback_data": f"ign:{aid}"}]]


def current_stake(a: dict, cfg: dict) -> float:
    """Stake com a banca ATUAL. O valor gravado no alerta usa a banca da epoca em que ele foi criado."""
    return round(min(a["kelly_pct"], cfg.get("max_stake_pct", 0.02)) * cfg["bankroll"], 2)


def league_enabled(v) -> bool:
    return v.get("enabled", True) if isinstance(v, dict) else True


class Bot:
    def __init__(self, tg, store: Store, cfg: dict, scanner, cfg_path: str = "config.json"):
        self.tg, self.store, self.cfg, self.scanner, self.cfg_path = tg, store, cfg, scanner, cfg_path
        self.lock = threading.Lock()  # uma varredura por vez (Telegram, app e agendador compartilham)
        self.stop = threading.Event()
        self.next_scan_at: float | None = None
        self.last_result: dict | None = None

    # ---- config ----
    def save_cfg(self) -> None:
        tmp = Path(self.cfg_path + ".tmp")
        tmp.write_text(json.dumps(self.cfg, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.cfg_path)

    @property
    def chat(self) -> str | None:
        return self.store.get("chat_id")

    # ---- varredura ----
    def _say(self, chat, text: str) -> None:
        if self.tg and chat:
            self.tg.send(chat, text)

    def scan(self) -> dict:
        """Varre, grava os alertas novos e (se houver Telegram ligado) envia. 'sent' = alertas novos criados."""
        if not self.lock.acquire(blocking=False):
            raise RuntimeError("ja existe uma varredura em andamento")
        try:
            edges = self.scanner(self.cfg)
            sent = dup = sus = 0
            ids: list[int] = []  # so os novos
            all_ids: list[int] = []  # todos os sinais desta varredura (novos + ja enviados), na ordem de EV
            for e in edges:
                if e.suspicious:
                    sus += 1
                    continue
                aid = self.store.add_alert(e)
                if aid is None:
                    dup += 1
                    old = self.store.id_for(e)
                    if old is not None:
                        all_ids.append(old)
                    continue
                all_ids.append(aid)
                if self.tg and self.chat:
                    mid = self.tg.send(self.chat, format_alert(self.store.alert(aid)), decision_buttons(aid))
                    self.store.set_message_id(aid, mid)
                ids.append(aid)
                sent += 1
            self.last_result = {"sent": sent, "dup": dup, "suspicious": sus, "ids": ids, "all_ids": all_ids,
                                "at": time.time()}
            return self.last_result
        finally:
            self.lock.release()

    def run_scan(self, reply_to=None) -> dict | None:
        self._say(reply_to, "\U0001f50e Varrendo...")
        try:
            r = self.scan()
        except TelegramError:
            raise
        except Exception as e:  # erro de API/orcamento nao pode derrubar o bot
            log.exception("scan falhou")
            self.last_result = {"error": str(e), "at": time.time()}
            self._say(reply_to, f"⚠️ Varredura falhou: {html.escape(str(e))}")
            return None
        self._say(reply_to, f"Fim: {r['sent']} novo(s) alerta(s), {r['dup']} repetido(s), "
                            f"{r['suspicious']} suspeito(s) ignorado(s).")
        return r

    def decide_alert(self, aid: int, status: str) -> dict | None:
        """Registra apostei ('bet') / ignorei ('ignored') e atualiza a mensagem no Telegram, se existir."""
        cur = self.store.alert(aid)
        stake = current_stake(cur, self.cfg) if cur and status == "bet" else None
        row = self.store.decide(aid, status, stake)
        if row and self.tg and self.chat and row.get("message_id"):
            footer = (f"✅ <b>APOSTEI</b> — odd {row['bet_odd']:.2f}, stake {row['bet_stake']:.2f}"
                      if status == "bet" else "❌ <b>IGNORADO</b>")
            try:
                self.tg.edit_text(self.chat, row["message_id"], format_alert(row, footer))
            except TelegramError:
                log.warning("nao consegui atualizar a mensagem do alerta %s", aid)
        return row

    # ---- comandos ----
    def status_text(self) -> str:
        from oddspapi import budget_used
        c = self.store.counts()
        on = [n for n, v in self.cfg["leagues"].items() if league_enabled(v)]
        auto = self.cfg.get("scan_interval_min", 0)
        auto_txt = f"a cada {auto} min" if auto else "desligada"
        return (f"<b>Status</b>\nBanca: {self.cfg['bankroll']:.2f}\n"
                f"Filtros: EV ≥ {self.cfg['min_ev']:.1%}, ¼ Kelly ≥ {self.cfg['min_kelly_pct']:.2%}, "
                f"EV máx {self.cfg['max_ev']:.0%}\n"
                f"Ligas ativas: {html.escape(', '.join(on) or 'nenhuma')}\n"
                f"Varredura automática: {auto_txt}\n"
                f"Consultas OddsPapi no mês: {budget_used()}/{self.cfg['monthly_request_limit']}\n"
                f"Alertas: {c.get('bet', 0)} apostei · {c.get('ignored', 0)} ignorei · {c.get('pending', 0)} pendentes")

    def league_buttons(self) -> list:
        rows = []
        for i, (name, v) in enumerate(self.cfg["leagues"].items()):
            mark = ON if league_enabled(v) else OFF
            rows.append([{"text": f"{mark} {name}", "callback_data": f"lg:{i}"}])
        return rows

    def handle(self, update: dict) -> None:
        if "callback_query" in update:
            return self._callback(update["callback_query"])
        msg = update.get("message")
        if not msg or "text" not in msg:
            return
        chat = str(msg["chat"]["id"])
        cmd, _, arg = msg["text"].strip().partition(" ")
        cmd, arg = cmd.split("@")[0].lower(), arg.strip()
        if cmd == "/start":
            if self.chat is None:
                self.store.set("chat_id", chat)
                self.tg.send(chat, "✅ Chat registrado. Você receberá os alertas aqui.\n\n" + HELP)
            elif self.chat == chat:
                self.tg.send(chat, HELP)
            return
        if chat != self.chat:  # ignora qualquer outro chat
            return
        if cmd == "/scan":
            self.run_scan(chat)
        elif cmd == "/status":
            self.tg.send(chat, self.status_text())
        elif cmd == "/ligas":
            self.tg.send(chat, "Toque para ativar/desativar as ligas da varredura:", self.league_buttons())
        elif cmd == "/banca":
            try:
                v = float(arg.replace(",", "."))
                if v <= 0:
                    raise ValueError
            except ValueError:
                self.tg.send(chat, "Use: /banca 1000")
                return
            self.cfg["bankroll"] = v
            self.save_cfg()
            self.tg.send(chat, f"Banca definida: {v:.2f}")
        elif cmd == "/auto":
            if not arg.isdigit():
                self.tg.send(chat, "Use: /auto 120 (minutos; 0 desliga). Atenção: cada varredura gasta "
                                   "consultas do plano.")
                return
            self.cfg["scan_interval_min"] = int(arg)
            self.save_cfg()
            txt = f"a cada {arg} min" if int(arg) else "desligada"
            self.tg.send(chat, f"Varredura automática: {txt}")
        else:
            self.tg.send(chat, HELP)

    def _callback(self, cb: dict) -> None:
        msg = cb.get("message") or {}
        chat = str(msg.get("chat", {}).get("id", ""))
        if chat != self.chat:
            return
        kind, _, val = cb.get("data", "").partition(":")
        mid = msg.get("message_id")
        if kind == "lg" and val.isdigit():
            names = list(self.cfg["leagues"])
            if int(val) < len(names):
                name = names[int(val)]
                v = self.cfg["leagues"][name]
                if not isinstance(v, dict):
                    v = self.cfg["leagues"][name] = {"id": v, "enabled": True}
                v["enabled"] = not v.get("enabled", True)
                self.save_cfg()
                self.tg.edit_markup(chat, mid, self.league_buttons())
            self.tg.answer(cb["id"])
        elif kind in ("bet", "ign") and val.isdigit():
            cur = self.store.alert(int(val))
            stake = current_stake(cur, self.cfg) if cur and kind == "bet" else None
            row = self.store.decide(int(val), "bet" if kind == "bet" else "ignored", stake)
            if row is None:
                self.tg.answer(cb["id"], "Já registrado")
                return
            footer = (f"✅ <b>APOSTEI</b> — odd {row['bet_odd']:.2f}, stake {row['bet_stake']:.2f}"
                      if kind == "bet" else "❌ <b>IGNORADO</b>")
            self.tg.edit_text(chat, mid, format_alert(row, footer))
            self.tg.answer(cb["id"], "Registrado")
        else:
            self.tg.answer(cb["id"])


def run(bot: Bot, poll: int = 25) -> None:
    """Laco do Telegram (long polling). A varredura automatica fica em scheduler()."""
    offset = None
    while not bot.stop.is_set():
        if bot.tg is None:  # token removido na tela: espera sem erro
            bot.stop.wait(2)
            continue
        try:
            for u in bot.tg.get_updates(offset, poll):
                offset = u["update_id"] + 1
                try:
                    bot.handle(u)
                except Exception:
                    log.exception("falha ao tratar update")
        except TelegramError as e:
            log.warning("Telegram: %s", e)
            bot.stop.wait(5)
        except Exception:
            log.exception("erro inesperado no laco do Telegram")
            bot.stop.wait(5)


def scheduler(bot: Bot, tick: float = 5.0) -> None:
    """Varredura automatica: a cada scan_interval_min minutos (0 = desligada). Le a config a cada tick,
    entao mudar o intervalo (no app ou com /auto) vale na hora e reinicia a contagem."""
    last, last_iv = time.time(), None
    while not bot.stop.wait(tick):
        iv = int(bot.cfg.get("scan_interval_min", 0) or 0)
        if iv != last_iv:
            last_iv, last = iv, time.time()
        bot.next_scan_at = last + iv * 60 if iv else None
        if iv and time.time() - last >= iv * 60:
            last = time.time()
            try:
                bot.run_scan(bot.chat)
            except Exception:
                log.exception("varredura automatica falhou")


def setup_logging() -> None:
    handlers = [logging.FileHandler("bot.log", encoding="utf-8")]
    if sys.stderr is not None:  # app empacotado sem console nao tem stderr
        handlers.insert(0, logging.StreamHandler())
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=handlers)


def main() -> int:
    setup_logging()
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        sys.exit("Defina TELEGRAM_BOT_TOKEN")
    cfg_path = "config.json"
    cfg = json.loads(Path(cfg_path).read_text(encoding="utf-8-sig"))  # aceita arquivo salvo com BOM
    import corners
    bot = Bot(Telegram(token), Store("bot.db"), cfg,
              scanner=lambda c: corners.run_live(c, progress=log.info), cfg_path=cfg_path)
    threading.Thread(target=scheduler, args=(bot,), daemon=True).start()
    log.info("Bot iniciado. Mande /start para ele no Telegram.")
    try:
        run(bot)
    except KeyboardInterrupt:
        bot.stop.set()
        log.info("Encerrado.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
