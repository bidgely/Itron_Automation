from datetime import date, timedelta

from pilots.loader import load_pilot_configs


def get_pilot_options() -> dict:
    return load_pilot_configs()["PILOT_NAMES"]


def text_response(text, thread_name=None):
    resp = {"text": text}
    if thread_name:
        resp["thread"] = {"name": thread_name}
    return resp


def main_menu_card(thread_name=None):
    card = {
        "cardsV2": [{
            "cardId": "main_menu",
            "card": {
                "header": {
                    "title": "☀️ Solar Monitor",
                    "subtitle": "What would you like to do?",
                },
                "sections": [{
                    "widgets": [
                        {
                            "buttonList": {
                                "buttons": [
                                    _action_button("📊 Run Solar Analysis", "select_analysis_pilot"),
                                    _action_button("⚡ Run Hourly Check", "select_hourly_pilot"),
                                ]
                            }
                        },
                        {
                            "buttonList": {
                                "buttons": [
                                    _action_button("🔍 View Missing Users", "select_missing_pilot"),
                                    _action_button("📋 Pilot Summary", "show_pilot_summary"),
                                ]
                            }
                        },
                    ]
                }],
            }
        }]
    }
    if thread_name:
        card["thread"] = {"name": thread_name}
    return card


def pilot_select_card(next_action, title="Select Pilot", thread_name=None):
    buttons = [
        _action_button(name, next_action, {"pilot_id": str(pid)})
        for pid, name in get_pilot_options().items()
    ]
    card = {
        "cardsV2": [{
            "cardId": "pilot_select",
            "card": {
                "header": {"title": title},
                "sections": [{
                    "widgets": [
                        {"buttonList": {"buttons": buttons}},
                        _back_button(),
                    ]
                }],
            }
        }]
    }
    if thread_name:
        card["thread"] = {"name": thread_name}
    return card


def date_picker_card(pilot_id, pilot_name, next_action, thread_name=None):
    today = date.today()
    min_date = today - timedelta(days=30)
    card = {
        "cardsV2": [{
            "cardId": "date_picker",
            "card": {
                "header": {
                    "title": f"📅 Select Date",
                    "subtitle": f"Pilot: {pilot_name}  |  Last 30 days only",
                },
                "sections": [{
                    "widgets": [
                        {
                            "dateTimePicker": {
                                "name": "selected_date",
                                "label": f"Date ({min_date} – {today})",
                                "type": "DATE_ONLY",
                            }
                        },
                        {
                            "buttonList": {
                                "buttons": [
                                    {
                                        "text": "Analyze",
                                        "onClick": {
                                            "action": {
                                                "function": next_action,
                                                "parameters": [
                                                    {"key": "pilot_id", "value": str(pilot_id)}
                                                ],
                                            }
                                        },
                                    },
                                    _back_button_inline(),
                                ]
                            }
                        },
                    ]
                }],
            }
        }]
    }
    if thread_name:
        card["thread"] = {"name": thread_name}
    return card


def hour_picker_card(pilot_id, date_str, next_action, thread_name=None):
    hours = [{"text": f"{h:02d}:00 UTC", "value": str(h)} for h in range(24)]
    card = {
        "cardsV2": [{
            "cardId": "hour_picker",
            "card": {
                "header": {"title": f"Select Hour — {date_str}"},
                "sections": [{
                    "widgets": [
                        {
                            "selectionInput": {
                                "name": "selected_hour",
                                "label": "Hour (UTC)",
                                "type": "DROPDOWN",
                                "items": hours,
                            }
                        },
                        {
                            "buttonList": {
                                "buttons": [
                                    {
                                        "text": "Show Missing Users",
                                        "onClick": {
                                            "action": {
                                                "function": next_action,
                                                "parameters": [
                                                    {"key": "pilot_id", "value": str(pilot_id)},
                                                    {"key": "date_str", "value": date_str},
                                                ],
                                            }
                                        },
                                    },
                                    _back_button_inline(),
                                ]
                            }
                        },
                    ]
                }],
            }
        }]
    }
    if thread_name:
        card["thread"] = {"name": thread_name}
    return card


def result_card(pilot_name, date_str, stats, chart_url=None, csv_url=None, thread_name=None):
    total = stats["total_users"]
    present_counts = stats["present_counts"]
    missing_counts = stats["missing_counts"]
    full_hours = stats["full_hours"]

    peak_hour = present_counts.index(max(present_counts))
    peak_pct = round(present_counts[peak_hour] / total * 100, 1) if total else 0
    low_hour = present_counts.index(min(present_counts))
    low_pct = round(present_counts[low_hour] / total * 100, 1) if total else 0

    summary = (
        f"<b>Pilot:</b> {pilot_name}  |  <b>Date:</b> {date_str}\n"
        f"<b>Expected users:</b> {total}\n"
        f"<b>Full-data hours:</b> {len(full_hours)} / 24\n"
        f"<b>Peak present ({peak_hour:02d}:00):</b> {present_counts[peak_hour]} ({peak_pct}%)\n"
        f"<b>Lowest present ({low_hour:02d}:00):</b> {present_counts[low_hour]} ({low_pct}%)"
    )

    widgets = [{"textParagraph": {"text": summary}}]

    if chart_url:
        widgets.append({"image": {"imageUrl": chart_url, "altText": "Hourly Present vs Absent"}})

    buttons = [_action_button("🏠 Main Menu", "show_main_menu")]
    if csv_url:
        buttons.append({
            "text": "⬇️ Download CSV",
            "onClick": {"openLink": {"url": csv_url}},
        })
    if chart_url:
        buttons.append({
            "text": "⬇️ Download Chart",
            "onClick": {"openLink": {"url": chart_url}},
        })
    widgets.append({"buttonList": {"buttons": buttons}})

    card = {
        "cardsV2": [{
            "cardId": "analysis_result",
            "card": {
                "header": {"title": "✅ Analysis Complete"},
                "sections": [{"widgets": widgets}],
            }
        }]
    }
    if thread_name:
        card["thread"] = {"name": thread_name}
    return card


def missing_users_card(pilot_name, date_str, hour, missing_users, csv_url=None, thread_name=None):
    total_missing = len(missing_users)
    preview = missing_users[:20]
    preview_text = "\n".join(f"• {u}" for u in preview)
    if total_missing > 20:
        preview_text += f"\n...and {total_missing - 20} more"

    body = (
        f"<b>Pilot:</b> {pilot_name}  |  <b>Date:</b> {date_str}  |  <b>Hour:</b> {hour:02d}:00 UTC\n"
        f"<b>Missing users:</b> {total_missing}\n\n"
        f"{preview_text if preview_text else 'None — all users present ✅'}"
    )

    buttons = [_action_button("🏠 Main Menu", "show_main_menu")]
    if csv_url:
        buttons.append({
            "text": "⬇️ Download Full List",
            "onClick": {"openLink": {"url": csv_url}},
        })

    card = {
        "cardsV2": [{
            "cardId": "missing_users",
            "card": {
                "header": {"title": f"🔍 Missing Users — Hour {hour:02d}:00"},
                "sections": [{"widgets": [
                    {"textParagraph": {"text": body}},
                    {"buttonList": {"buttons": buttons}},
                ]}],
            }
        }]
    }
    if thread_name:
        card["thread"] = {"name": thread_name}
    return card


def _action_button(label, function, params=None):
    btn = {
        "text": label,
        "onClick": {
            "action": {
                "function": function,
                "parameters": [{"key": k, "value": v} for k, v in (params or {}).items()],
            }
        },
    }
    return btn


def _back_button():
    return {"buttonList": {"buttons": [_back_button_inline()]}}


def _back_button_inline():
    return _action_button("← Back to Menu", "show_main_menu")
