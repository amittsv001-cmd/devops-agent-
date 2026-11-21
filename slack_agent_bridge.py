import os
import re
import logging
from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from agents.master_agent import execute_master_agent

load_dotenv()

logging.basicConfig(level=logging.INFO)

SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
SLACK_APP_TOKEN = os.getenv("SLACK_APP_TOKEN")

app = App(token=SLACK_BOT_TOKEN)

# Main App Mention Event Handler
@app.event("app_mention")
def handle_mention(body, say):
    print("\n🔔 [APP MENTION DETECTED]:", body.get("event", {}).get("text", ""))
    process_message(body, say)

# Fallback Message Event Handler
@app.event("message")
def handle_message_events(body, logger):
    event = body.get("event", {})
    if "bot_id" not in event:
        print("\n💬 [MESSAGE EVENT RECEIVED]:", event.get("text", ""))

def process_message(body, say):
    try:
        event = body["event"]
        raw_text = event["text"]
        
        # Slack user mention (<@U123456>) ko clean query mein convert karein
        clean_prompt = re.sub(r"<@.*?>", "", raw_text).strip()
        
        print(f"💬 Clean Prompt: \"{clean_prompt}\"")
        
        # Direct channel main feed par message bhejna (bina thread_ts ke)
        say(f"⏳ *Master Agent triggered for:* \"{clean_prompt}\"")
        
        # LangGraph Master Agent Trigger
        result = execute_master_agent(clean_prompt)
        
        # Direct channel main feed par final response bhejna
        say(result)
        print("✅ Response sent successfully to main channel chat!")
        
    except Exception as e:
        print(f"❌ Execution Error: {e}")
        say(f"❌ *Error executing task:* `{str(e)}`")

if __name__ == "__main__":
    if not SLACK_BOT_TOKEN or not SLACK_APP_TOKEN:
        print("❌ Tokens missing in .env! Please check SLACK_BOT_TOKEN and SLACK_APP_TOKEN.")
    else:
        try:
            print("🔄 Validating Slack credentials...")
            auth_info = app.client.auth_test()
            print(f"✅ Bot Token Authenticated! Bot Name: {auth_info['user']}")
            
            print("⚡ Starting Socket Mode Handler...")
            handler = SocketModeHandler(app, SLACK_APP_TOKEN)
            handler.start()
        except Exception as e:
            print(f"❌ Connection Error: {e}")