import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from chatbot.cards import (
    get_pilot_options,
    text_response,
    main_menu_card,
    pilot_select_card,
    date_picker_card,
    hour_picker_card,
    result_card,
    missing_users_card,
)
from chatbot.service import (
    run_analysis_background,
    get_hourly_check_text,
    get_pilot_summary_text,
    get_missing_users,
    is_within_30_days,
    parse_date_from_form,
    parse_hour_from_form,
)
from utils.logger import get_logger

logger = get_logger("ChatbotMain")

app = FastAPI(title="Solar Monitor Chatbot")

GCHAT_WEBHOOK_URL = os.getenv("GCHAT_WEBHOOK_URL", "")


# ─── Health ───────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok"}


# ─── Main Google Chat webhook ─────────────────────────────────────────────────

@app.post("/gchat/events")
async def handle_event(request: Request):
    try:
        event = await request.json()
    except Exception:
        return JSONResponse({"text": "Invalid request."}, status_code=400)

    event_type = event.get("type", "")
    space_name = (
        event.get("space", {}).get("name")
        or event.get("message", {}).get("space", {}).get("name", "")
    )
    user_name = (
        event.get("user", {}).get("name")
        or event.get("message", {}).get("sender", {}).get("name", "")
    )
    thread_name = (
        event.get("message", {}).get("thread", {}).get("name")
        or event.get("thread", {}).get("name", "")
    )

    logger.info("Event: type=%s space=%s user=%s", event_type, space_name, user_name)

    if event_type == "ADDED_TO_SPACE":
        return _json(text_response(
            "👋 Hi! I'm the *Solar Monitor* bot.\n"
            "Type *hi* or *menu* to get started.",
            thread_name,
        ))

    if event_type == "MESSAGE":
        text = event.get("message", {}).get("text", "").strip().lower()
        if any(kw in text for kw in ("hi", "hello", "help", "menu", "start")):
            return _json(main_menu_card(thread_name))
        return _json(text_response("Type *menu* to see available options.", thread_name))

    if event_type == "CARD_CLICKED":
        action_method = event.get("action", {}).get("actionMethodName", "")
        params = {
            p["key"]: p["value"]
            for p in event.get("action", {}).get("parameters", [])
        }
        form_inputs = event.get("common", {}).get("formInputs", {})
        response = _route(action_method, params, form_inputs, thread_name)
        response["actionResponse"] = {"type": "NEW_MESSAGE"}
        return _json(response)

    return _json({})


# ─── Action router ────────────────────────────────────────────────────────────

def _route(action, params, form_inputs, thread_name):
    if action == "show_main_menu":
        return main_menu_card(thread_name)

    # ── Solar Analysis ────────────────────────────────────────────────────────
    if action == "select_analysis_pilot":
        return pilot_select_card("show_analysis_date_picker", "📊 Select Pilot", thread_name)

    if action == "show_analysis_date_picker":
        pilot_id = int(params["pilot_id"])
        return date_picker_card(pilot_id, get_pilot_options()[pilot_id], "submit_analysis", thread_name)

    if action == "submit_analysis":
        pilot_id = int(params["pilot_id"])
        date_str = parse_date_from_form(form_inputs)
        if not date_str:
            return text_response("⚠️ Please select a date.", thread_name)
        if not is_within_30_days(date_str):
            return text_response(
                "⚠️ Please select a date within the last 30 days.", thread_name
            )
        pilot_name = get_pilot_options()[pilot_id]
        threading.Thread(
            target=run_analysis_background,
            args=(pilot_id, date_str, GCHAT_WEBHOOK_URL, thread_name),
            daemon=True,
        ).start()
        return text_response(
            f"⏳ Analyzing *{pilot_name}* for *{date_str}*...\n"
            "I'll post the results here shortly.",
            thread_name,
        )

    # ── Hourly Check ──────────────────────────────────────────────────────────
    if action == "select_hourly_pilot":
        return pilot_select_card("run_hourly_check", "⚡ Select Pilot for Hourly Check", thread_name)

    if action == "run_hourly_check":
        pilot_id_str = params.get("pilot_id")
        if pilot_id_str:
            pilots = [int(pilot_id_str)]
        else:
            pilots = list(get_pilot_options().keys())
        result_text = get_hourly_check_text(pilots)
        return text_response(result_text, thread_name)

    # ── Missing Users ─────────────────────────────────────────────────────────
    if action == "select_missing_pilot":
        return pilot_select_card("show_missing_date_picker", "🔍 Select Pilot", thread_name)

    if action == "show_missing_date_picker":
        pilot_id = int(params["pilot_id"])
        return date_picker_card(
            pilot_id, get_pilot_options()[pilot_id], "show_missing_hour_picker", thread_name
        )

    if action == "show_missing_hour_picker":
        pilot_id = int(params["pilot_id"])
        date_str = parse_date_from_form(form_inputs)
        if not date_str:
            return text_response("⚠️ Please select a date.", thread_name)
        if not is_within_30_days(date_str):
            return text_response(
                "⚠️ Please select a date within the last 30 days.", thread_name
            )
        return hour_picker_card(pilot_id, date_str, "show_missing_users", thread_name)

    if action == "show_missing_users":
        pilot_id = int(params["pilot_id"])
        date_str = params.get("date_str", "")
        hour = parse_hour_from_form(form_inputs)
        if hour is None:
            return text_response("⚠️ Please select an hour.", thread_name)
        pilot_name = get_pilot_options()[pilot_id]
        expected, missing = get_missing_users(pilot_id, date_str, hour)
        if expected is None:
            return text_response(
                f"⚠️ No DB snapshot found for *{pilot_name}* on *{date_str}*.", thread_name
            )
        if missing is None:
            return text_response(
                f"❌ Failed to read S3 data for *{pilot_name}* on *{date_str}* hour {hour:02d}.",
                thread_name,
            )
        return missing_users_card(pilot_name, date_str, hour, missing, thread_name=thread_name)

    # ── Pilot Summary ─────────────────────────────────────────────────────────
    if action == "show_pilot_summary":
        summary = get_pilot_summary_text(list(get_pilot_options().keys()))
        return text_response(summary, thread_name)

    return text_response("❓ Unknown action. Type *menu* to start over.", thread_name)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _json(body):
    return JSONResponse(content=body)
