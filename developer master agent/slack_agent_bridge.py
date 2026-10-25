"""Slack Socket Mode bridge -> master agent. Replies in-thread with token/cost/time stats."""
from __future__ import annotations

import logging
import os
import re
import uuid

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from config.settings import get_settings  # importing this loads .env
from agents.master_agent import execute_master_agent

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("slack_bridge")

MENTION_RE = re.compile(r"<@[A-Z0-9]+>")
FLOW_RE = re.compile(r"\b(flow|chart|diagram|how it works|kaise kaam)\b", re.IGNORECASE)
SLACK_TEXT_LIMIT = 39_000


def handle_prompt(event: dict, say, client) -> None:
    """Run one Slack message through the agent and update a placeholder reply in-thread."""
    thread_ts = event.get("thread_ts") or event["ts"]
    allowed = get_settings().allowed_slack_users
    if allowed and event.get("user") not in allowed:
        say(text="🚫 You're not authorised to run this agent.", thread_ts=thread_ts)
        return

    prompt = MENTION_RE.sub("", event.get("text", "")).strip()
    if not prompt:
        say(text="👋 Ask me something, e.g. `where is app.py`.", thread_ts=thread_ts)
        return

    log.info("Prompt from %s: %s", event.get("user"), prompt)
    placeholder = say(text=f"⏳ Working on: _{prompt}_", thread_ts=thread_ts)
    try:
        result = execute_master_agent(prompt)
        text = result.to_slack(include_flow=bool(FLOW_RE.search(prompt)))
    except Exception:
        error_id = uuid.uuid4().hex[:8]
        log.exception("Agent failed (error_id=%s)", error_id)
        text = f"❌ Something went wrong (error id `{error_id}`). Check the bot logs."

    if len(text) > SLACK_TEXT_LIMIT:
        text = text[:SLACK_TEXT_LIMIT] + "\n... (truncated)"
    client.chat_update(channel=placeholder["channel"], ts=placeholder["ts"], text=text)


def create_app() -> App:
    app = App(token=os.environ["SLACK_BOT_TOKEN"])

    @app.event("app_mention")
    def on_mention(event, say, client):
        handle_prompt(event, say, client)

    @app.event("message")
    def on_message(event, say, client):
        # Channel messages arrive via app_mention; here we only answer direct messages.
        if event.get("channel_type") == "im" and not event.get("bot_id") and not event.get("subtype"):
            handle_prompt(event, say, client)

    return app


def main() -> None:
    if not os.getenv("SLACK_BOT_TOKEN") or not os.getenv("SLACK_APP_TOKEN"):
        raise SystemExit("SLACK_BOT_TOKEN and SLACK_APP_TOKEN must be set (see .env.example).")
    app = create_app()
    auth = app.client.auth_test()
    log.info("Authenticated as %s", auth["user"])
    SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"]).start()


if __name__ == "__main__":
    main()
