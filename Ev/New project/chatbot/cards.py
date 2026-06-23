from __future__ import annotations

from .schemas import InteractionResponse, RunSectionResponse, RunSummaryResponse


def _html_lines(lines: list[str]) -> str:
    return "<br>".join(lines)


def _button_list(options: list[tuple[str, str, dict[str, str] | None]]) -> list[dict]:
    return [
        {
            "textButton": {
                "text": label,
                "onClick": {
                    "action": {
                        "actionMethodName": action_id,
                        **(
                            {
                                "parameters": [
                                    {"key": key, "value": value}
                                    for key, value in parameters.items()
                                ]
                            }
                            if parameters
                            else {}
                        ),
                    }
                },
            }
        }
        for action_id, label, parameters in options
    ]


def _open_link_button(label: str, url: str) -> dict:
    return {
        "textButton": {
            "text": label,
            "onClick": {
                "openLink": {
                    "url": url,
                }
            },
        }
    }


def build_interaction_card(response: InteractionResponse) -> dict:
    return {
        "cardsV2": [
            {
                "cardId": "interaction-card",
                "card": {
                    "header": {
                        "title": response.title,
                        "subtitle": response.message,
                    },
                    "sections": [
                        {
                            "widgets": [
                                {
                                    "buttonList": {
                                        "buttons": _button_list(
                                            [(option.id, option.label, None) for option in response.options]
                                        )
                                    }
                                }
                            ]
                        }
                    ],
                },
            }
        ]
    }


def build_section_card(section: RunSectionResponse) -> dict:
    widgets = []
    for key, value in section.data.items():
        widgets.append(
            {
                "keyValue": {
                    "topLabel": key.replace("_", " ").title(),
                    "content": str(value) if value is not None else "-",
                }
            }
        )

    return {
        "cardsV2": [
            {
                "cardId": f"{section.section}-section-card",
                "card": {
                    "header": {
                        "title": f"{section.pilot.upper()} {section.section.title()}",
                        "subtitle": f"Run {section.run_id} for {section.date}",
                    },
                    "sections": [
                        {
                            "widgets": widgets,
                        }
                    ],
                },
            }
        ]
    }


def build_summary_card(summary: RunSummaryResponse) -> dict:
    return {
        "cardsV2": [
            {
                "cardId": "summary-card",
                "card": {
                    "header": {
                        "title": f"{summary.pilot.upper()} Run Completed",
                        "subtitle": f"{summary.date} | {summary.status}",
                    },
                    "sections": [
                        {
                            "header": "Quick Summary",
                            "widgets": [
                                {
                                    "keyValue": {
                                        "topLabel": "Run ID",
                                        "content": summary.run_id,
                                    }
                                },
                                {
                                    "keyValue": {
                                        "topLabel": "Output Dir",
                                        "content": summary.output_dir or "-",
                                    }
                                },
                                {
                                    "keyValue": {
                                        "topLabel": "Branch",
                                        "content": summary.branch_name or "-",
                                    }
                                },
                                {
                                    "keyValue": {
                                        "topLabel": "PR URL",
                                        "content": summary.pr_url or "-",
                                    }
                                },
                            ],
                        }
                    ],
                },
            }
        ]
    }


def build_message_card(title: str, message: str) -> dict:
    return {
        "cardsV2": [
            {
                "cardId": "message-card",
                "card": {
                    "header": {
                        "title": title,
                    },
                    "sections": [
                        {
                            "widgets": [
                                {
                                    "textParagraph": {
                                        "text": message,
                                    }
                                }
                            ]
                        }
                    ],
                },
            }
        ]
    }


def build_text_card(title: str, lines: list[str], subtitle: str | None = None) -> dict:
    return {
        "cardsV2": [
            {
                "cardId": "text-card",
                "card": {
                    "header": {
                        "title": title,
                        **({"subtitle": subtitle} if subtitle else {}),
                    },
                    "sections": [
                        {
                            "widgets": [
                                {
                                    "textParagraph": {
                                        "text": _html_lines(lines),
                                    }
                                }
                            ]
                        }
                    ],
                },
            }
        ]
    }


def build_numbered_menu_card(title: str, prompt: str, options: list[str]) -> dict:
    lines = [prompt, ""] + [f"{index}. {option}" for index, option in enumerate(options, start=1)]
    return build_text_card(title, lines)


def build_frontend_redirect_card(frontend_url: str) -> dict:
    return {
        "cardsV2": [
            {
                "cardId": "frontend-redirect-card",
                "card": {
                    "header": {
                        "title": "Use The Operations Page",
                        "subtitle": "Google Chat is for quick status only.",
                    },
                    "sections": [
                        {
                            "widgets": [
                                {
                                    "textParagraph": {
                                        "text": (
                                            "For analysis, reruns, special requests, and PR creation, "
                                            "please use the operations page."
                                        ),
                                    }
                                },
                                {
                                    "buttonList": {
                                        "buttons": [
                                            _open_link_button("Open Operations Page", frontend_url),
                                        ]
                                    }
                                },
                            ]
                        }
                    ],
                },
            }
        ]
    }


def build_pilot_choice_card(title: str, message: str, teco_action: str, luma_action: str) -> dict:
    return {
        "cardsV2": [
            {
                "cardId": "pilot-choice-card",
                "card": {
                    "header": {
                        "title": title,
                        "subtitle": message,
                    },
                    "sections": [
                        {
                            "widgets": [
                                {
                                    "buttonList": {
                                        "buttons": _button_list(
                                            [
                                                (teco_action, "Option 1", None),
                                                (luma_action, "Option 2", None),
                                            ]
                                        )
                                    }
                                }
                            ]
                        }
                    ],
                },
            }
        ]
    }


def build_home_chat_card(frontend_url: str) -> dict:
    return {
        "cardsV2": [
            {
                "cardId": "home-chat-card",
                "card": {
                    "header": {
                        "title": "Itron Automation",
                        "subtitle": "Use Google Chat for quick status. Use the frontend for running jobs.",
                    },
                    "sections": [
                        {
                            "widgets": [
                                {
                                    "buttonList": {
                                        "buttons": [
                                            *_button_list([("show_latest_status", "Latest Status", None)]),
                                            _open_link_button("Open Frontend", frontend_url),
                                        ]
                                    }
                                }
                            ]
                        }
                    ],
                },
            }
        ]
    }


def build_run_options_card(summary: RunSummaryResponse) -> dict:
    run_id_params = {"run_id": summary.run_id}
    return {
        "cardsV2": [
            {
                "cardId": "run-options-card",
                "card": {
                    "header": {
                        "title": f"{summary.pilot.upper()} run completed",
                        "subtitle": f"What would you like to see for {summary.date}?",
                    },
                    "sections": [
                        {
                            "widgets": [
                                {
                                    "buttonList": {
                                        "buttons": _button_list(
                                            [
                                                ("show_section_mysql", "MySQL Counts", run_id_params),
                                                ("show_section_redshift", "Redshift Counts", run_id_params),
                                                ("show_section_scripts", "Script Summary", run_id_params),
                                                ("show_section_pr", "PR Details", run_id_params),
                                                ("show_summary", "Show All", run_id_params),
                                            ]
                                        )
                                    }
                                }
                            ]
                        }
                    ],
                },
            }
        ]
    }
