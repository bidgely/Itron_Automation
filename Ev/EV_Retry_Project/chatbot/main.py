from __future__ import annotations

import contextlib
import os
import socket
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

import secrets

from fastapi import FastAPI, HTTPException, Request, UploadFile, File
from fastapi.responses import HTMLResponse

from .cards import (
    build_numbered_menu_card,
    build_frontend_redirect_card,
    build_home_chat_card,
    build_message_card,
    build_pilot_choice_card,
    build_run_options_card,
    build_section_card,
    build_summary_card,
    build_text_card,
)
from .schemas import (
    InteractionOption,
    InteractionResponse,
    NormalRunRequest,
    RunSectionResponse,
    RunSummaryResponse,
    SpecialRunRequest,
)
from app.config import get_utils_repo_path as _get_utils_repo_path
from app.pilots import get_all_pilot_definitions
from .service import run_normal_flow, run_special_flow
from .storage import (
    build_run_section,
    clear_chat_session,
    load_chat_session,
    load_latest_run_summary,
    load_run_summary,
    load_run_summary_by_date,
    save_chat_session,
)


app = FastAPI(title="Itron Chatbot Backend", version="0.1.0")


def _session_id_from_event(event: dict) -> str:
    space_name = event.get("space", {}).get("name", "space-unknown").replace("/", "_")
    sender_name = event.get("message", {}).get("sender", {}).get("name", "sender-unknown").replace("/", "_")
    return f"{space_name}__{sender_name}"


def _today_str() -> str:
    return datetime.now().strftime("%Y%m%d")


def _main_menu_card() -> dict:
    return build_numbered_menu_card(
        "Itron Automation",
        "What would you like to do? Reply with the option number.",
        [
            "Run normal flow",
            "Run special request",
            "Show latest counts",
            "Show latest PR details",
            "View run by date",
        ],
    )


def _main_menu_text() -> dict:
    return {
        "text": "\n".join(
            [
                "Itron Automation",
                "",
                "What would you like to do? Reply with the option number.",
                "",
                "1. Run normal flow",
                "2. Run special request",
                "3. Show latest counts",
                "4. Show latest PR details",
                "5. View run by date",
            ]
        )
    }


def _choose_pilot_card(title: str) -> dict:
    return build_numbered_menu_card(
        title,
        "Choose a pilot. Reply with the option number.",
        [pilot.display_name for pilot in get_all_pilot_definitions().values()],
    )


def _choose_pilot_text(title: str) -> dict:
    pilot_lines = [
        f"{index}. {pilot.display_name}"
        for index, pilot in enumerate(get_all_pilot_definitions().values(), start=1)
    ]
    return {
        "text": "\n".join(
            [
                title,
                "",
                "Choose a pilot. Reply with the option number.",
                "",
                *pilot_lines,
            ]
        )
    }


def _pilot_key_for_choice(choice: int) -> str | None:
    pilots = list(get_all_pilot_definitions().values())
    if choice < 1 or choice > len(pilots):
        return None
    return pilots[choice - 1].key


def _choose_pr_card(title: str) -> dict:
    return build_numbered_menu_card(
        title,
        "Create PR also? Reply with the option number.",
        ["Yes", "No"],
    )


def _choose_pr_text(title: str) -> dict:
    return {
        "text": "\n".join(
            [
                title,
                "",
                "Create PR also? Reply with the option number.",
                "",
                "1. Yes",
                "2. No",
            ]
        )
    }


def _build_date_picker_card(pilot: str) -> dict:
    return {
        "cardsV2": [{
            "cardId": "date_picker_card",
            "card": {
                "header": {"title": f"{pilot.upper()} — View Run by Date"},
                "sections": [{
                    "widgets": [
                        {
                            "dateTimePicker": {
                                "label": "Select date (last 30 days only)",
                                "name": "run_date",
                                "type": "DATE_ONLY",
                            }
                        },
                        {
                            "buttonList": {
                                "buttons": [{
                                    "text": "Get Results",
                                    "onClick": {
                                        "action": {
                                            "function": "view_run_by_date",
                                            "parameters": [
                                                {"key": "pilot", "value": pilot}
                                            ],
                                        }
                                    },
                                }]
                            }
                        },
                    ]
                }]
            }
        }]
    }


def _format_latest_counts(summary: RunSummaryResponse) -> dict:
    mysql_counts = summary.mysql_counts
    redshift_counts = summary.redshift_counts
    script_counts = summary.script_counts
    effective = mysql_counts.get("effective", mysql_counts.get("full_list", 0) - mysql_counts.get("checkforev_0", 0))
    lines = [
        f"{summary.pilot.upper()} run completed for {summary.date}",
        "",
        "MySQL Counts",
        f"Full EV list -> {mysql_counts.get('full_list', 0)}",
        f"CheckForEV=0 -> {mysql_counts.get('checkforev_0', 0)}",
        f"Null configs -> {mysql_counts.get('null_config', 0)}",
        f"Effective -> {effective}",
        "",
        "Redshift Counts",
        f"HSM+HAS completed -> {redshift_counts.get('hsm_has_completed', 0)}",
        f"HSM completed -> {redshift_counts.get('hsm_completed', 0)}",
        f"HAS completed -> {redshift_counts.get('has_completed', 0)}",
        f"HAS retry -> {redshift_counts.get('has_retry', 0)}",
        f"HSM retry -> {redshift_counts.get('hsm_retry', 0)}",
        "",
        "Scripts",
        f"HSM+HAS complete files -> {script_counts.get('mark_completed_hsm_has_files', 0)}",
        f"HSM only completed files -> {script_counts.get('mark_completed_hsm_only_files', 0)}",
        f"Mark failed files -> {script_counts.get('mark_failed_files', 0)}",
        f"HAS retry files -> {script_counts.get('retry_has_files', 0)}",
        f"HSM retry files -> {script_counts.get('retry_hsm_files', 0)}",
        "",
        "Segregated Meters Extracted Folder",
        f"{summary.meter_files_folder_s3_uri or '-'}",
        "",
        f"PR -> {summary.pr_url or '-'}",
    ]
    return build_text_card(f"{summary.pilot.upper()} Counts", lines)


def _format_latest_counts_text(summary: RunSummaryResponse) -> dict:
    mysql_counts = summary.mysql_counts
    redshift_counts = summary.redshift_counts
    script_counts = summary.script_counts
    effective = mysql_counts.get("effective", mysql_counts.get("full_list", 0) - mysql_counts.get("checkforev_0", 0))
    return {
        "text": "\n".join(
            [
                f"{summary.pilot.upper()} run completed for {summary.date}",
                "",
                "MySQL Counts",
                f"Full EV list -> {mysql_counts.get('full_list', 0)}",
                f"CheckForEV=0 -> {mysql_counts.get('checkforev_0', 0)}",
                f"Effective -> {effective}",
                "",
                "Redshift Counts",
                f"HSM+HAS completed -> {redshift_counts.get('hsm_has_completed', 0)}",
                f"HSM completed -> {redshift_counts.get('hsm_completed', 0)}",
                f"HAS retry -> {redshift_counts.get('has_retry', 0)}",
                f"HSM retry -> {redshift_counts.get('hsm_retry', 0)}",
                f"Leftovers -> {redshift_counts.get('leftovers', 0)}",
                "",
                "Scripts",
                f"HSM+HAS complete files -> {script_counts.get('mark_completed_hsm_has_files', 0)}",
                f"HSM only completed files -> {script_counts.get('mark_completed_hsm_only_files', 0)}",
                f"Mark failed files -> {script_counts.get('mark_failed_files', 0)}",
                f"HAS retry files -> {script_counts.get('retry_has_files', 0)}",
                f"HSM retry files -> {script_counts.get('retry_hsm_files', 0)}",
                "",
                "Segregated Meters Extracted Folder",
                f"{summary.meter_files_folder_s3_uri or '-'}",
                "",
                f"PR -> {summary.pr_url or '-'}",
            ]
        )
    }


def _format_latest_pr(summary: RunSummaryResponse) -> dict:
    lines = [
        f"Latest PR details for {summary.pilot.upper()}",
        "",
        f"Run Date -> {summary.date}",
        f"Branch -> {summary.branch_name or '-'}",
        f"PR -> {summary.pr_url or '-'}",
    ]
    return build_text_card(f"{summary.pilot.upper()} PR Details", lines)


def _format_latest_pr_text(summary: RunSummaryResponse) -> dict:
    return {
        "text": "\n".join(
            [
                f"Latest PR details for {summary.pilot.upper()}",
                "",
                f"Run Date -> {summary.date}",
                f"Branch -> {summary.branch_name or '-'}",
                f"PR -> {summary.pr_url or '-'}",
            ]
        )
    }


def _extract_attachment(message: dict) -> dict | None:
    attachments = message.get("attachment") or message.get("attachments") or []
    if not attachments:
        return None
    attachment = attachments[0]
    attachment_data_ref = attachment.get("attachmentDataRef") or {}
    # attachment.name = "spaces/{space}/messages/{msg}/attachments/{att}"
    attachment_full_name = attachment.get("name") or ""
    parts = attachment_full_name.split("/")
    space_name = "/".join(parts[:2]) if len(parts) >= 2 and parts[0] == "spaces" else None
    return {
        "name": attachment.get("contentName") or attachment_full_name or "uploaded-file",
        "download_uri": attachment.get("downloadUri") or attachment.get("downloadUriLink"),
        "resource_name": attachment_data_ref.get("resourceName") or attachment_full_name or None,
        "space_name": space_name,  # e.g. "spaces/sCvMRyAAAE"
        "raw": attachment,
    }


def _is_html_content(data: bytes) -> bool:
    """Return True if the bytes look like an HTML page (Google login redirect)."""
    snippet = data[:300].lower()
    return snippet.startswith(b"<!doctype") or b"<html" in snippet or b"google accounts" in snippet


def _download_via_service_account(
    resource_name: str | None,
    download_uri: str | None = None,
    space_name: str | None = None,
) -> bytes | None:
    """Download a Google Chat attachment using the service account credentials.

    Tries:
    1. Authenticated Bearer-token GET on the Chat API media URL built from resource_name.
    2. Chat Media API (google-api-python-client) — if 403 "not a member", attempt to
       add the bot to the space first then retry once.
    """
    try:
        key_path = os.environ.get("GOOGLE_SERVICE_ACCOUNT_KEY", "")
        if not key_path or not os.path.exists(key_path):
            print("Service account key not found, skipping authenticated download.", flush=True)
            return None

        from google.oauth2 import service_account
        from google.auth.transport.requests import Request as GoogleAuthRequest
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaIoBaseDownload
        import requests as _req
        import io

        creds = service_account.Credentials.from_service_account_file(
            key_path,
            scopes=["https://www.googleapis.com/auth/chat.bot"],
        )
        print(f"[SA download] service account: {creds.service_account_email}", flush=True)

        # Refresh to obtain a Bearer token
        auth_req = GoogleAuthRequest()
        creds.refresh(auth_req)
        auth_headers = {"Authorization": f"Bearer {creds.token}"}

        # ── Helper: build & call the Chat Media API ─────────────────────────
        def _media_download() -> bytes:
            service = build("chat", "v1", credentials=creds)
            request = service.media().download_media(resourceName=resource_name)
            fh = io.BytesIO()
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
            return fh.getvalue()

        # ── Helper: attempt to add the bot itself as a space member ─────────
        # CreateMembership requires the chat.memberships.app scope (not chat.bot).
        def _try_join_space() -> bool:
            if not space_name:
                return False
            try:
                print(f"[SA] Attempting to join space {space_name} (chat.memberships.app scope)...", flush=True)
                membership_creds = service_account.Credentials.from_service_account_file(
                    key_path,
                    scopes=["https://www.googleapis.com/auth/chat.memberships.app"],
                )
                membership_creds.refresh(auth_req)
                membership_service = build("chat", "v1", credentials=membership_creds)
                membership_service.spaces().members().create(
                    parent=space_name,
                    body={"member": {"name": "users/app", "type": "BOT"}},
                ).execute()
                print(f"[SA] Successfully joined space {space_name}", flush=True)
                return True
            except Exception as join_exc:
                join_msg = str(join_exc).lower()
                if "already" in join_msg or "409" in join_msg:
                    print(f"[SA] Bot already a member of {space_name}", flush=True)
                    return True
                print(f"[SA] Could not join space {space_name}: {join_exc}", flush=True)
                return False

        # ── Method 1: authenticated GET using the Chat API media endpoint ────
        # (downloadUri from Google Chat is a browser-only URL; build the real one instead)
        if resource_name:
            api_url = f"https://chat.googleapis.com/v1/media/{resource_name}?alt=media"
            try:
                print(f"[SA method 1] authenticated GET on media URL", flush=True)
                resp = _req.get(api_url, headers=auth_headers, allow_redirects=True, timeout=120)
                print(f"[SA method 1] status={resp.status_code} size={len(resp.content)}", flush=True)
                if resp.status_code == 200 and not _is_html_content(resp.content):
                    print(f"[SA method 1] SUCCESS {len(resp.content)} bytes", flush=True)
                    return resp.content
                elif resp.status_code in (403, 401):
                    print(f"[SA method 1] auth/membership error: {resp.text[:200]}", flush=True)
                else:
                    print(f"[SA method 1] unexpected: {resp.status_code} {resp.text[:200]}", flush=True)
            except Exception as exc1:
                print(f"[SA method 1] error: {exc1}", flush=True)

        # ── Diagnostic: list spaces the bot is actually a member of ──────────
        try:
            spaces_resp = build("chat", "v1", credentials=creds).spaces().list().execute()
            bot_spaces = [s.get("name") for s in spaces_resp.get("spaces", [])]
            print(f"[SA diagnostic] bot is member of {len(bot_spaces)} spaces: {bot_spaces}", flush=True)
            print(f"[SA diagnostic] current space {space_name!r} in list: {space_name in bot_spaces}", flush=True)
        except Exception as diag_exc:
            print(f"[SA diagnostic] could not list spaces: {diag_exc}", flush=True)

        # ── Method 2: Chat Media API via google-api-python-client ────────────
        if resource_name:
            try:
                print(f"[SA method 2] media API resource: {resource_name}", flush=True)
                content = _media_download()
                print(f"[SA method 2] SUCCESS {len(content)} bytes", flush=True)
                return content
            except Exception as exc2:
                exc2_str = str(exc2)
                print(f"[SA method 2] error: {exc2_str}", flush=True)
                if "not a member" in exc2_str.lower():
                    # ── Auto-join and retry ─────────────────────────────────
                    if _try_join_space():
                        try:
                            print("[SA method 2 retry] retrying after joining space...", flush=True)
                            time.sleep(1)
                            content = _media_download()
                            print(f"[SA method 2 retry] SUCCESS {len(content)} bytes", flush=True)
                            return content
                        except Exception as exc_retry:
                            print(f"[SA method 2 retry] still failed: {exc_retry}", flush=True)

        return None
    except Exception as exc:
        print(f"[SA download] fatal error: {exc}", flush=True)
        return None


def _download_chat_attachment(
    download_uri: str | None,
    resource_name: str | None = None,
    space_name: str | None = None,
) -> bytes | None:
    """Download a Google Chat attachment.

    Priority order:
    1. Authenticated service account download (method 1 & 2 inside helper,
       with auto-join attempt on 403 "not a member").
    2. Unauthenticated GET on downloadUri — rejected if response is HTML
       (Google login redirect served when no auth is provided).
    """
    # Attempt authenticated download
    content = _download_via_service_account(resource_name, download_uri=download_uri, space_name=space_name)
    if content:
        return content

    # Unauthenticated fallback — only useful for non-Google-protected URLs
    if not download_uri:
        return None
    try:
        import requests as _req
        print("[download] Method 3: unauthenticated GET on downloadUri", flush=True)
        resp = _req.get(download_uri, allow_redirects=True, timeout=120, stream=True)
        print(f"[download] status={resp.status_code} size={resp.headers.get('content-length', '?')}", flush=True)
        resp.raise_for_status()
        content = resp.content
        if _is_html_content(content):
            print("[download] Method 3 returned HTML (Google login page) — ignoring", flush=True)
            return None
        print(f"[download] Method 3 SUCCESS {len(content)} bytes", flush=True)
        return content
    except Exception as exc:
        print(f"[download] Method 3 error: {exc}", flush=True)
        return None


def _register_membership_via_rest(space_name: str) -> None:
    """Send a REST API message to the space so Google's REST layer registers
    this service account as the bot member of the space.

    Without this call, media.download() (and spaces.list()) return
    "not a member" even after the bot is added via Apps & Integrations,
    because the HTTP endpoint event delivery and the REST API membership
    are tracked separately by Google Chat.
    """
    if not space_name:
        return
    try:
        key_path = os.environ.get("GOOGLE_SERVICE_ACCOUNT_KEY", "")
        if not key_path or not os.path.exists(key_path):
            return
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
        creds = service_account.Credentials.from_service_account_file(
            key_path,
            scopes=["https://www.googleapis.com/auth/chat.bot"],
        )
        service = build("chat", "v1", credentials=creds)
        result = service.spaces().messages().create(
            parent=space_name,
            body={
                "text": (
                    "Itron Automation bot is ready!\n"
                    "Type *menu* to see available options."
                )
            },
        ).execute()
        print(f"[register] REST API message sent to {space_name}: {result.get('name')}", flush=True)
    except Exception as exc:
        print(f"[register] REST API message failed for {space_name}: {exc}", flush=True)


def _save_attachment_to_s3(content: bytes, filename: str) -> str | None:
    """Save attachment bytes to S3 and return the S3 URI."""
    try:
        import boto3
        from datetime import datetime
        bucket = os.environ.get("ITRON_S3_BUCKET", "bidgely-artifacts2")
        region = os.environ.get("AWS_REGION", "us-west-2")
        date_str = datetime.now().strftime("%Y%m%d")
        prefix = os.environ.get("ITRON_CHAT_UPLOAD_S3_PREFIX", "Murali_Users/special/chat_uploads").strip("/")
        key = f"{prefix}/{date_str}/{filename}"
        client = boto3.client("s3", region_name=region)
        client.put_object(Bucket=bucket, Key=key, Body=content)
        return f"s3://{bucket}/{key}"
    except Exception as exc:
        print(f"S3 upload of attachment failed: {exc}", flush=True)
        return None


def _prepare_utils_repo(repo_path: str) -> None:
    subprocess.run(["git", "-C", repo_path, "reset", "--hard", "origin/master"], check=True, capture_output=True)
    subprocess.run(["git", "-C", repo_path, "clean", "-fd"], check=True, capture_output=True)


def _tunnel_port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def _wait_for_tunnel(port: int, timeout: int = 30) -> bool:
    """Wait until the tunnel port accepts connections, up to timeout seconds."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _tunnel_port_in_use(port):
            return True
        time.sleep(1)
    return False


@contextlib.contextmanager
def _ensure_mysql_tunnel():
    tunnel_port = int(os.environ.get("ITRON_TUNNEL_PORT", "3308"))
    if _tunnel_port_in_use(tunnel_port):
        yield
        return

    tunnel_key = os.environ.get("ITRON_TUNNEL_KEY", "")
    tunnel_host = os.environ.get("ITRON_TUNNEL_HOST", "")
    tunnel_target = os.environ.get("ITRON_TUNNEL_TARGET", "")

    if not all([tunnel_key, tunnel_host, tunnel_target]):
        yield
        return

    proc = subprocess.Popen([
        "ssh", "-i", tunnel_key,
        "-o", "ExitOnForwardFailure=yes",
        "-o", "ServerAliveInterval=60",
        "-o", "ServerAliveCountMax=3",
        "-L", f"{tunnel_port}:{tunnel_target}",
        tunnel_host, "-N",
    ])
    if not _wait_for_tunnel(tunnel_port, timeout=30):
        proc.terminate()
        raise RuntimeError(f"SSH tunnel on port {tunnel_port} did not become ready in 30 seconds")
    try:
        yield
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


@contextlib.contextmanager
def _ensure_uat_mysql_tunnel():
    tunnel_port = int(os.environ.get("ITRON_UAT_TUNNEL_PORT", "3311"))
    if _tunnel_port_in_use(tunnel_port):
        yield
        return

    tunnel_key = os.environ.get("ITRON_UAT_TUNNEL_KEY", "")
    tunnel_host = os.environ.get("ITRON_UAT_TUNNEL_HOST", "")
    tunnel_target = os.environ.get("ITRON_UAT_TUNNEL_TARGET", "")

    if not all([tunnel_key, tunnel_host, tunnel_target]):
        yield
        return

    proc = subprocess.Popen([
        "ssh", "-i", tunnel_key,
        "-o", "ExitOnForwardFailure=yes",
        "-o", "StrictHostKeyChecking=no",
        "-o", "ServerAliveInterval=60",
        "-o", "ServerAliveCountMax=3",
        "-L", f"{tunnel_port}:{tunnel_target}",
        tunnel_host, "-N",
    ])
    if not _wait_for_tunnel(tunnel_port, timeout=30):
        proc.terminate()
        raise RuntimeError(f"SSH UAT tunnel on port {tunnel_port} did not become ready in 30 seconds")
    try:
        yield
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def _post_to_webhook(text: str, thread_name: str | None = None) -> None:
    webhook_url = os.environ.get("GCHAT_WEBHOOK_URL")
    if not webhook_url:
        return
    try:
        import requests as _req
        payload: dict = {"text": text}
        if thread_name:
            payload["thread"] = {"name": thread_name}
        _req.post(webhook_url, json=payload, timeout=30)
    except Exception:
        pass


def _log_error(msg: str) -> None:
    try:
        import traceback
        log_path = Path("logs/chatbot_errors.log")
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a") as f:
            f.write(f"\n[{datetime.now().isoformat()}] {msg}\n")
            f.write(traceback.format_exc())
    except Exception:
        pass


def _run_normal_flow_background(pilot: str, create_pr: bool, repo_path_str: str | None, thread_name: str | None = None) -> None:
    try:
        from app.pilots import get_pilot_definition as _get_pilot_def
        is_uat = _get_pilot_def(pilot).uat
        print(f"[{pilot.upper()}] Background flow started. create_pr={create_pr} uat={is_uat}", flush=True)
        if create_pr and repo_path_str:
            _prepare_utils_repo(repo_path_str)
        request = NormalRunRequest(
            pilot=pilot,
            date=_today_str(),
            create_pr=create_pr,
            repo_path=repo_path_str,
            gchat_webhook_url=None,
            frontend_url=None,
            output_dir="output",
            checkforev_zero_min_id=995,
        )
        tunnel_ctx = _ensure_uat_mysql_tunnel() if is_uat else _ensure_mysql_tunnel()
        with tunnel_ctx:
            summary = run_normal_flow(request)
        print(f"[{pilot.upper()}] Background flow complete.", flush=True)
        from .gchat_webhook import post_run_summary_to_google_chat
        webhook_url = os.environ.get("GCHAT_WEBHOOK_URL")
        if webhook_url:
            post_run_summary_to_google_chat(summary, webhook_url, thread_name=thread_name)
    except Exception as exc:
        print(f"[{pilot.upper()}] Background flow FAILED: {exc}", flush=True)
        _log_error(f"{pilot.upper()} normal flow failed: {exc}")
        _post_to_webhook(f"{pilot.upper()} normal flow failed:\n{exc}", thread_name=thread_name)


def _start_normal_flow(session_id: str, pilot: str, create_pr: bool, thread_name: str | None = None) -> dict:
    repo_path = _get_utils_repo_path() if create_pr else None
    if create_pr and repo_path is None:
        clear_chat_session(session_id)
        return {
            "text": "\n".join([
                "Cannot Create PR",
                "",
                "ITRON_UTILS_REPO_PATH is not configured on the server.",
                "Set the env var or choose No for PR and create it manually.",
            ])
        }
    clear_chat_session(session_id)
    threading.Thread(
        target=_run_normal_flow_background,
        args=(pilot, create_pr, str(repo_path) if repo_path else None, thread_name),
        daemon=True,
    ).start()
    pr_note = " A PR will be created when done." if create_pr else ""
    return {
        "text": f"{pilot.upper()} normal flow started!{pr_note}\nResults will be posted here in a few minutes."
    }


def _run_special_flow_background(pilot: str, create_pr: bool, repo_path_str: str | None, meter_list_s3: str, request_name: str, thread_name: str | None = None) -> None:
    try:
        from app.pilots import get_pilot_definition as _get_pilot_def
        is_uat = _get_pilot_def(pilot).uat
        print(f"[{pilot.upper()}] Background special flow started. create_pr={create_pr} uat={is_uat}", flush=True)
        if create_pr and repo_path_str:
            _prepare_utils_repo(repo_path_str)
        request = SpecialRunRequest(
            pilot=pilot,
            date=_today_str(),
            meter_list_s3=meter_list_s3,
            request_name=request_name,
            create_pr=create_pr,
            repo_path=repo_path_str,
            gchat_webhook_url=None,
            frontend_url=None,
            output_dir="output/special",
            checkforev_zero_min_id=995,
        )
        tunnel_ctx = _ensure_uat_mysql_tunnel() if is_uat else _ensure_mysql_tunnel()
        with tunnel_ctx:
            summary = run_special_flow(request)
        print(f"[{pilot.upper()}] Background special flow complete.", flush=True)
        from .gchat_webhook import post_run_summary_to_google_chat
        webhook_url = os.environ.get("GCHAT_WEBHOOK_URL")
        if webhook_url:
            post_run_summary_to_google_chat(summary, webhook_url, thread_name=thread_name)
    except Exception as exc:
        print(f"[{pilot.upper()}] Background special flow FAILED: {exc}", flush=True)
        _log_error(f"{pilot.upper()} special flow failed: {exc}")
        _post_to_webhook(f"{pilot.upper()} special flow failed:\n{exc}", thread_name=thread_name)


def _start_special_flow(session_id: str, pilot: str, create_pr: bool, attachment: dict, thread_name: str | None = None) -> dict:
    repo_path = _get_utils_repo_path() if create_pr else None
    if create_pr and repo_path is None:
        clear_chat_session(session_id)
        return {
            "text": "\n".join([
                "Cannot Create PR",
                "",
                "ITRON_UTILS_REPO_PATH is not configured on the server.",
                "Set the env var or choose No for PR and create it manually.",
            ])
        }
    attachment_name = attachment.get("name", "uploaded-file")
    download_uri = attachment.get("download_uri")
    request_name = Path(attachment_name).stem.replace(" ", "_").lower() or f"{pilot}_special_request"
    if not download_uri or not str(download_uri).startswith("s3://"):
        return {
            "text": "\n".join(
                [
                    "Special Request",
                    "",
                    f"No S3 path found for: {attachment_name}",
                    "",
                    "File upload may have failed. Please try again or send the S3 URI directly.",
                ]
            )
        }
    clear_chat_session(session_id)
    threading.Thread(
        target=_run_special_flow_background,
        args=(pilot, create_pr, str(repo_path) if repo_path else None, download_uri, request_name, thread_name),
        daemon=True,
    ).start()
    pr_note = " A PR will be created when done." if create_pr else ""
    return {
        "text": f"{pilot.upper()} special flow started!{pr_note}\nResults will be posted here in a few minutes."
    }


def _extract_action_parameters(event: dict) -> dict[str, str]:
    parameters = (
        event.get("common", {})
        .get("parameters", [])
    )
    # Direct Chat API sends parameters as a dict {"key": "value"}
    # Workspace add-on sends parameters as a list [{"key": ..., "value": ...}]
    if isinstance(parameters, dict):
        return {k: str(v) for k, v in parameters.items()}
    return {
        parameter.get("key"): parameter.get("value", "")
        for parameter in parameters
        if isinstance(parameter, dict) and parameter.get("key")
    }


def _route_message_to_card(text: str, frontend_url: str) -> dict:
    normalized = text.lower().strip()
    if "status" in normalized or "latest" in normalized:
        return _choose_pilot_text("Show Latest Counts")
    if normalized in {"hi", "hello", "help", "start"}:
        return build_home_chat_card(frontend_url)
    return build_frontend_redirect_card(frontend_url)


def _handle_numbered_menu_response(event: dict, frontend_url: str) -> dict:
    session_id = _session_id_from_event(event)
    message = event.get("message", {})
    text = (message.get("argumentText") or message.get("text") or "").strip()
    attachment = _extract_attachment(message)
    thread_name = message.get("thread", {}).get("name")
    session = load_chat_session(session_id) or {"state": "main_menu"}
    state = session.get("state", "main_menu")

    if state == "awaiting_special_file":
        if text.startswith("s3://"):
            session["attachment"] = {"name": text.split("/")[-1], "download_uri": text}
            session["state"] = "awaiting_special_pilot"
            save_chat_session(session_id, session)
            return _choose_pilot_text("Special Request")

        # Generate a one-time web-upload link
        upload_token = _create_upload_token(session_id)
        upload_url = f"https://tacky-truce-cohesive.ngrok-free.dev/upload/{upload_token}"
        return {
            "text": "\n".join([
                "Special Request — Upload Meter List",
                "",
                "Click the link below to upload the CSV file:",
                upload_url,
                "",
                "Or paste an S3 URI directly:",
                "  s3://bucket/path/to/meter_list.csv",
                "",
                "Format: one meterid per row, header: meterid",
                "After uploading, send any message here to continue.",
            ])
        }

    # ── Post-web-upload: any message triggers the pilot choice ──────────────
    # The browser upload sets state=awaiting_special_pilot + web_upload_row_count.
    # The next Chat message (anything the user sends) should show confirmation.
    if state == "awaiting_special_pilot" and "web_upload_row_count" in session:
        row_count = session.pop("web_upload_row_count")
        filename = session.get("attachment", {}).get("name", "file")
        save_chat_session(session_id, session)
        return {
            "text": "\n".join([
                f"File received: {filename}",
                f"Rows: {row_count:,}",
                "",
                "Choose a pilot. Reply with the option number.",
                "",
                *[
                    f"{index}. {pilot.display_name}"
                    for index, pilot in enumerate(get_all_pilot_definitions().values(), start=1)
                ],
            ])
        }

    if text.lower() in {"menu", "start", "help", "home"}:
        clear_chat_session(session_id)
        return _main_menu_text()

    if not text.isdigit():
        return {
            "text": "\n".join(
                [
                    "Itron Automation",
                    "",
                    "Please reply with an option number like 1, 2, or 3.",
                    "",
                    "Reply with 'menu' any time to see the main options again.",
                ]
            )
        }

    choice = int(text)

    if state == "main_menu":
        if choice == 1:
            save_chat_session(session_id, {"state": "awaiting_normal_pilot"})
            return _choose_pilot_text("Run Normal Flow")
        if choice == 2:
            session_id_local = _session_id_from_event(event)
            save_chat_session(session_id_local, {"state": "awaiting_special_file"})
            upload_token = _create_upload_token(session_id_local)
            upload_url = f"https://tacky-truce-cohesive.ngrok-free.dev/upload/{upload_token}"
            return {
                "text": "\n".join([
                    "Special Request — Upload Meter List",
                    "",
                    "Click the link below to upload the CSV file:",
                    upload_url,
                    "",
                    "Or paste an S3 URI directly:",
                    "  s3://bucket/path/to/meter_list.csv",
                    "",
                    "Format: one meterid per row, header: meterid",
                    "After uploading, send any message here to continue.",
                ])
            }
        if choice == 3:
            save_chat_session(session_id, {"state": "awaiting_counts_pilot"})
            return _choose_pilot_text("Show Latest Counts")
        if choice == 4:
            save_chat_session(session_id, {"state": "awaiting_pr_pilot"})
            return _choose_pilot_text("Latest PR Details")
        if choice == 5:
            save_chat_session(session_id, {"state": "awaiting_date_pilot"})
            return _choose_pilot_text("View Run by Date")
        return _main_menu_text()

    if state == "awaiting_normal_pilot":
        pilot = _pilot_key_for_choice(choice)
        if not pilot:
            return _choose_pilot_text("Run Normal Flow")
        return _start_normal_flow(session_id, pilot, create_pr=True, thread_name=thread_name)

    if state == "awaiting_special_pilot":
        pilot = _pilot_key_for_choice(choice)
        if not pilot:
            return _choose_pilot_text("Special Request")
        attachment = session.get("attachment")
        if not attachment:
            return _choose_pilot_text("Special Request")
        return _start_special_flow(session_id, pilot, create_pr=True, attachment=attachment, thread_name=thread_name)

    if state == "awaiting_counts_pilot":
        pilot = _pilot_key_for_choice(choice)
        if not pilot:
            return _choose_pilot_text("Show Latest Counts")
        clear_chat_session(session_id)
        return _format_latest_counts_text(load_latest_run_summary(pilot))

    if state == "awaiting_pr_pilot":
        pilot = _pilot_key_for_choice(choice)
        if not pilot:
            return _choose_pilot_text("Latest PR Details")
        clear_chat_session(session_id)
        return _format_latest_pr_text(load_latest_run_summary(pilot))

    if state == "awaiting_date_pilot":
        pilot = _pilot_key_for_choice(choice)
        if not pilot:
            return _choose_pilot_text("View Run by Date")
        clear_chat_session(session_id)
        return _build_date_picker_card(pilot)

    clear_chat_session(session_id)
    return _main_menu_text()


def _route_action_to_card(action_name: str, parameters: dict[str, str], frontend_url: str, form_inputs: dict | None = None) -> dict:
    if action_name in {"normal_run", "special_request"}:
        return build_frontend_redirect_card(frontend_url)
    if action_name == "show_latest_status":
        return _choose_pilot_text("Show Latest Counts")
    if action_name == "show_summary":
        return build_summary_card(load_run_summary(parameters["run_id"]))
    if action_name.startswith("show_section_"):
        section = action_name.removeprefix("show_section_")
        return build_section_card(build_run_section(parameters["run_id"], section))
    if action_name == "view_run_by_date":
        from datetime import datetime, timedelta
        pilot = parameters.get("pilot", "")
        inputs = form_inputs or {}
        date_input = inputs.get("run_date", {}).get("dateInput", {})
        ms = date_input.get("msSinceEpoch")
        if not ms:
            return {"text": "Please select a date and press Get Results."}
        selected_date = datetime.utcfromtimestamp(int(ms) / 1000)
        cutoff = datetime.now() - timedelta(days=30)
        if selected_date < cutoff:
            return {"text": "Date must be within the last 30 days."}
        date_str = selected_date.strftime("%Y%m%d")
        summary = load_run_summary_by_date(pilot, date_str)
        if not summary:
            return {"text": f"No data found for {pilot.upper()} on {date_str}."}
        return _format_latest_counts_text(summary)

    return build_message_card("Unknown Action", f"Action not handled: {action_name}")


# ── Web-upload flow ────────────────────────────────────────────────────────
# Tokens map: token → {"session_id": ..., "expires": unix_ts}
_upload_tokens: dict[str, dict] = {}
_UPLOAD_TOKEN_TTL = 30 * 60  # 30 minutes


def _create_upload_token(session_id: str) -> str:
    token = secrets.token_urlsafe(24)
    _upload_tokens[token] = {
        "session_id": session_id,
        "expires": time.time() + _UPLOAD_TOKEN_TTL,
    }
    return token


@app.get("/upload/{token}", response_class=HTMLResponse)
def upload_form(token: str) -> HTMLResponse:
    """Show a simple HTML upload form for the special-request CSV."""
    entry = _upload_tokens.get(token)
    if not entry or time.time() > entry["expires"]:
        return HTMLResponse("<h2>Link expired or invalid. Please restart the special request in Chat.</h2>", status_code=410)
    return HTMLResponse(f"""
<!doctype html><html><head>
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Itron — Upload Meter List</title>
<style>
  body{{font-family:sans-serif;max-width:480px;margin:60px auto;padding:0 20px}}
  h2{{color:#1a73e8}}
  input[type=file]{{display:block;margin:16px 0}}
  button{{background:#1a73e8;color:#fff;border:none;padding:10px 24px;border-radius:4px;font-size:16px;cursor:pointer}}
  button:disabled{{background:#aaa}}
  #msg{{margin-top:16px;font-weight:bold}}
</style></head><body>
<h2>Upload Meter List CSV</h2>
<p>Select the CSV file (one <code>meterid</code> per row, header: <code>meterid</code>).</p>
<form id="f" enctype="multipart/form-data">
  <input type="file" id="file" accept=".csv,.txt" required>
  <button type="submit" id="btn">Upload</button>
</form>
<div id="msg"></div>
<script>
document.getElementById('f').addEventListener('submit', async e => {{
  e.preventDefault();
  const btn = document.getElementById('btn');
  const msg = document.getElementById('msg');
  btn.disabled = true;
  btn.textContent = 'Uploading…';
  const fd = new FormData();
  fd.append('file', document.getElementById('file').files[0]);
  try {{
    const r = await fetch('/upload/{token}/submit', {{method:'POST', body:fd}});
    const d = await r.json();
    if (d.ok) {{
      msg.style.color='green';
      msg.textContent = '✓ ' + d.message + ' — you can close this tab and return to Google Chat.';
    }} else {{
      msg.style.color='red';
      msg.textContent = '✗ ' + d.error;
      btn.disabled = false;
      btn.textContent = 'Upload';
    }}
  }} catch(err) {{
    msg.style.color='red';
    msg.textContent = 'Network error: ' + err;
    btn.disabled = false;
    btn.textContent = 'Upload';
  }}
}});
</script></body></html>
""")


@app.post("/upload/{token}/submit")
async def upload_submit(token: str, file: UploadFile = File(...)) -> dict:
    """Receive the uploaded CSV, save to S3, and update the chat session."""
    entry = _upload_tokens.get(token)
    if not entry or time.time() > entry["expires"]:
        raise HTTPException(status_code=410, detail="Link expired")

    session_id = entry["session_id"]
    session = load_chat_session(session_id) or {}

    content = await file.read()
    if not content:
        return {"ok": False, "error": "Empty file."}
    if _is_html_content(content):
        return {"ok": False, "error": "Received HTML, not a CSV. Please upload a plain CSV file."}

    filename = file.filename or "meter_list.csv"
    lines = content.splitlines()
    row_count = max(0, len(lines) - 1)

    s3_uri = _save_attachment_to_s3(content, filename)
    if not s3_uri:
        return {"ok": False, "error": "S3 upload failed. Check server logs."}

    # Advance the session so the next Chat message goes straight to pilot choice
    session["attachment"] = {"name": filename, "download_uri": s3_uri}
    session["state"] = "awaiting_special_pilot"
    session["web_upload_row_count"] = row_count
    save_chat_session(session_id, session)

    # Clean up the token (one-time use)
    _upload_tokens.pop(token, None)

    print(f"[web-upload] {filename} → {s3_uri} ({row_count} rows) for session {session_id}", flush=True)
    return {
        "ok": True,
        "message": f"File received: {filename} ({row_count:,} rows). Return to Google Chat to continue.",
    }


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/pilots")
def list_pilots() -> dict:
    return {
        "pilots": [
            {
                "key": pilot.key,
                "display_name": pilot.display_name,
                "pilot_id": pilot.pilot_id,
                "environment": "uat" if pilot.uat else "prod",
                "checkforev_zero_min_id": pilot.checkforev_zero_min_id,
                "special_request_s3_prefix": pilot.special_request_s3_prefix,
            }
            for pilot in get_all_pilot_definitions().values()
        ]
    }


def _normalize_event(event: dict) -> dict:
    """Convert Workspace add-on CommonEventObject format to standard Chat API format."""
    if "commonEventObject" not in event:
        return event
    chat = event.get("chat", {})
    if "messagePayload" in chat:
        payload = chat["messagePayload"]
        config_uri = payload.get("configCompleteRedirectUri")
        if config_uri:
            try:
                import requests as _req
                _req.get(config_uri, timeout=3)
            except Exception:
                pass
        return {
            "type": "MESSAGE",
            "message": payload.get("message", {}),
            "space": payload.get("space", {}),
            "user": chat.get("user", {}),
        }
    if "addedToSpacePayload" in chat:
        payload = chat["addedToSpacePayload"]
        return {
            "type": "ADDED_TO_SPACE",
            "space": payload.get("space", {}),
            "user": chat.get("user", {}),
        }
    if "buttonClickedPayload" in chat:
        action = chat["buttonClickedPayload"].get("action", {})
        common_event = event.get("commonEventObject", {})
        return {
            "type": "CARD_CLICKED",
            "common": {
                "invokedFunction": action.get("function", ""),
                "parameters": action.get("parameters", []),
                "formInputs": common_event.get("formInputs", {}),
            },
            "user": chat.get("user", {}),
        }
    return {"type": "UNKNOWN"}


@app.post("/gchat/events")
def handle_google_chat_event(event: dict, request: Request) -> dict:
    frontend_url = str(request.url_for("ui_home"))
    event = _normalize_event(event)
    event_type = event.get("type")
    thread_name = event.get("message", {}).get("thread", {}).get("name")

    if event_type == "ADDED_TO_SPACE":
        space_name = event.get("space", {}).get("name", "")
        # Fire a REST API message in the background so Google's REST layer
        # registers this service account as the space's bot member.
        # Without this, media.download() always returns "not a member".
        threading.Thread(
            target=_register_membership_via_rest,
            args=(space_name,),
            daemon=True,
        ).start()
        response = _main_menu_text()

    elif event_type == "APP_COMMAND":
        response = _main_menu_text()

    elif event_type == "MESSAGE":
        message = event.get("message", {})
        text = (message.get("argumentText") or message.get("text") or "").strip()
        normalized = text.lower().strip()
        if normalized in {"hi", "hello", "help", "start", "menu"}:
            clear_chat_session(_session_id_from_event(event))
            response = _main_menu_text()
        elif "latest" in normalized or "status" in normalized:
            save_chat_session(_session_id_from_event(event), {"state": "awaiting_counts_pilot"})
            response = _choose_pilot_text("Show Latest Counts")
        elif normalized == "i want analysis":
            response = _route_message_to_card(text, frontend_url)
        else:
            response = _handle_numbered_menu_response(event, frontend_url)

    elif event_type == "CARD_CLICKED":
        action_name = event.get("common", {}).get("invokedFunction", "")
        parameters = _extract_action_parameters(event)
        form_inputs = event.get("common", {}).get("formInputs", {})
        response = _route_action_to_card(action_name, parameters, frontend_url, form_inputs=form_inputs)

    else:
        response = build_message_card("Unsupported Event", f"Event type not handled: {event_type}")

    if thread_name:
        response = {**response, "thread": {"name": thread_name}}
    return response


@app.get("/chat/home", response_model=InteractionResponse)
def chat_home() -> InteractionResponse:
    return InteractionResponse(
        title="Itron Automation",
        message="What do you want to do?",
        options=[
            InteractionOption(id="normal_run", label="Normal Run"),
            InteractionOption(id="special_request", label="Special Request"),
            InteractionOption(id="show_latest_status", label="Show Latest Status"),
        ],
    )


@app.get("/chat/home/card")
def chat_home_card() -> dict:
    return build_home_chat_card("/ui")


@app.post("/run/normal", response_model=RunSummaryResponse)
def run_normal(request: NormalRunRequest) -> RunSummaryResponse:
    try:
        return run_normal_flow(request)
    except Exception as exc:  # pragma: no cover - surfaced to API caller
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/run/special", response_model=RunSummaryResponse)
def run_special(request: SpecialRunRequest) -> RunSummaryResponse:
    try:
        return run_special_flow(request)
    except Exception as exc:  # pragma: no cover - surfaced to API caller
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/runs/{run_id}/summary", response_model=RunSummaryResponse)
def get_run_summary(run_id: str) -> RunSummaryResponse:
    try:
        return load_run_summary(run_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/runs/{run_id}/summary/card")
def get_run_summary_card(run_id: str) -> dict:
    return build_summary_card(get_run_summary(run_id))


@app.get("/runs/latest/{pilot}", response_model=RunSummaryResponse)
def get_latest_run_summary(pilot: str) -> RunSummaryResponse:
    try:
        return load_latest_run_summary(pilot.lower())
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/runs/{run_id}/section/{section}", response_model=RunSectionResponse)
def get_run_section(run_id: str, section: str) -> RunSectionResponse:
    try:
        return build_run_section(run_id, section)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/runs/{run_id}/section/{section}/card")
def get_run_section_card(run_id: str, section: str) -> dict:
    return build_section_card(get_run_section(run_id, section))


@app.get("/runs/{run_id}/options", response_model=InteractionResponse)
def get_run_options(run_id: str) -> InteractionResponse:
    try:
        summary = load_run_summary(run_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return InteractionResponse(
        title=f"{summary.pilot.upper()} run completed",
        message=f"What would you like to see for {summary.date}?",
        options=[
            InteractionOption(id="mysql", label="MySQL Counts"),
            InteractionOption(id="redshift", label="Redshift Counts"),
            InteractionOption(id="scripts", label="Script Summary"),
            InteractionOption(id="pr", label="PR Details"),
            InteractionOption(id="summary", label="Show All"),
        ],
    )


@app.get("/runs/{run_id}/options/card")
def get_run_options_card(run_id: str) -> dict:
    return build_run_options_card(load_run_summary(run_id))


@app.get("/ui", response_class=HTMLResponse, name="ui_home")
def ui_home() -> str:
    return """
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8" />
  <title>Itron Automation</title>
  <style>
    body { font-family: Arial, sans-serif; margin: 32px; background: #f3f6fb; color: #1f2937; }
    h1 { margin-bottom: 8px; }
    h2 { margin-top: 0; }
    .hero { background: linear-gradient(135deg, #0f172a, #1d4ed8); color: white; border-radius: 18px; padding: 28px; margin-bottom: 24px; box-shadow: 0 10px 30px rgba(15, 23, 42, 0.18); }
    .hero p { margin-bottom: 0; color: #dbeafe; }
    .section-title { margin: 26px 0 12px; font-size: 20px; }
    .section-note { margin: 0 0 18px; color: #475569; }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(340px, 1fr)); gap: 20px; }
    .card { background: white; border-radius: 14px; padding: 20px; box-shadow: 0 6px 20px rgba(15, 23, 42, 0.08); border: 1px solid #e2e8f0; }
    .muted { color: #64748b; font-size: 14px; }
    label { display: block; margin-top: 12px; font-weight: 600; }
    input, select { width: 100%; padding: 10px; margin-top: 6px; border: 1px solid #cbd5e1; border-radius: 8px; box-sizing: border-box; }
    button { margin-top: 16px; padding: 10px 16px; border: 0; border-radius: 8px; background: #2563eb; color: white; cursor: pointer; }
    button:hover { background: #1d4ed8; }
    .secondary { background: #0f766e; }
    .secondary:hover { background: #115e59; }
    .ghost { background: #e2e8f0; color: #0f172a; }
    .ghost:hover { background: #cbd5e1; }
    pre { background: #0f172a; color: #e2e8f0; padding: 16px; border-radius: 10px; overflow-x: auto; min-height: 200px; }
    .row { display: flex; gap: 12px; align-items: center; }
    .row > * { flex: 1; }
    .checkbox { width: auto; flex: 0; }
    .button-row { display: flex; gap: 10px; flex-wrap: wrap; }
    .button-row button { margin-top: 12px; }
    .response-card { margin-top: 20px; }
  </style>
</head>
<body>
  <div class="hero">
    <h1>Itron Automation Console</h1>
    <p>Google Chat should show quick counts and status only. Use this page for runs, reruns, special requests, and deeper analysis.</p>
  </div>

  <h2 class="section-title">Latest Summary</h2>
  <p class="section-note">Start here when Google Chat redirects someone for more detail.</p>
  <div class="grid">
    <div class="card">
      <h2>Latest Run Lookup</h2>
      <p class="muted">Fetch the latest saved summary for a configured pilot.</p>
      <div class="row">
        <select id="latest-pilot"></select>
        <button class="secondary" onclick="showLatest()">Show Latest</button>
      </div>
      <div class="button-row">
        <button class="ghost" onclick="showLatestSection('mysql')">MySQL Counts</button>
        <button class="ghost" onclick="showLatestSection('redshift')">Redshift Counts</button>
        <button class="ghost" onclick="showLatestSection('scripts')">Script Summary</button>
        <button class="ghost" onclick="showLatestSection('pr')">PR Details</button>
      </div>
    </div>
  </div>

  <h2 class="section-title">Run Operations</h2>
  <p class="section-note">Use these forms to trigger normal runs, special requests, and optional PR creation.</p>
  <div class="grid">
    <div class="card">
      <h2>Normal Run</h2>
      <p class="muted">Run the standard configured pilot flow and optionally push scripts into Utils.</p>
      <label>Pilot</label>
      <select id="normal-pilot"></select>
      <label>Date</label>
      <input id="normal-date" value="" placeholder="YYYYMMDD" />
      <label>Output Dir</label>
      <input id="normal-output" value="output" />
      <label>Repo Path</label>
      <input id="normal-repo" placeholder="/Users/.../Utils" />
      <label>Google Chat Webhook URL</label>
      <input id="normal-webhook" placeholder="https://chat.googleapis.com/v1/spaces/.../messages?key=...&token=..." />
      <label>Frontend URL Shared In Google Chat</label>
      <input id="normal-frontend-url" placeholder="https://your-host/ui" />
      <div class="row"><input class="checkbox" type="checkbox" id="normal-pr" /><label for="normal-pr">Create PR</label></div>
      <button onclick="runNormal()">Run Normal Flow</button>
    </div>
    <div class="card">
      <h2>Special Request</h2>
      <p class="muted">Use a client-provided S3 meter list for one-off processing without touching the normal daily flow.</p>
      <label>Pilot</label>
      <select id="special-pilot"></select>
      <label>Date</label>
      <input id="special-date" value="" placeholder="YYYYMMDD" />
      <label>Request Name</label>
      <input id="special-name" placeholder="apr29_client_batch" />
      <label>S3 Meter List</label>
      <input id="special-s3" placeholder="s3://bucket/path/file.csv" />
      <label>Output Dir</label>
      <input id="special-output" value="output/special" />
      <label>Repo Path</label>
      <input id="special-repo" placeholder="/Users/.../Utils" />
      <label>Google Chat Webhook URL</label>
      <input id="special-webhook" placeholder="https://chat.googleapis.com/v1/spaces/.../messages?key=...&token=..." />
      <label>Frontend URL Shared In Google Chat</label>
      <input id="special-frontend-url" placeholder="https://your-host/ui" />
      <div class="row"><input class="checkbox" type="checkbox" id="special-pr" /><label for="special-pr">Create PR</label></div>
      <button onclick="runSpecial()">Run Special Request</button>
    </div>
  </div>

  <h2 class="section-title">Deep Analysis</h2>
  <p class="section-note">Open exact sections for a known run when someone needs more than the summary shown in Google Chat.</p>
  <div class="grid">
    <div class="card">
      <h2>Run ID Explorer</h2>
      <p class="muted">Paste a run id from the API response or latest summary and pull only the section you need.</p>
      <label>Run ID</label>
      <input id="analysis-run-id" placeholder="teco-20260506-101530" />
      <div class="button-row">
        <button class="ghost" onclick="showRunSummary()">Full Summary</button>
        <button class="ghost" onclick="showRunSection('mysql')">MySQL Counts</button>
        <button class="ghost" onclick="showRunSection('redshift')">Redshift Counts</button>
        <button class="ghost" onclick="showRunSection('scripts')">Script Summary</button>
        <button class="ghost" onclick="showRunSection('pr')">PR Details</button>
      </div>
    </div>
  </div>

  <div class="card response-card">
    <h2>Response</h2>
    <pre id="output">Ready.</pre>
  </div>
  <script>
    const today = new Date();
    const yyyy = today.getFullYear();
    const mm = String(today.getMonth() + 1).padStart(2, '0');
    const dd = String(today.getDate()).padStart(2, '0');
    const todayValue = `${yyyy}${mm}${dd}`;
    const frontendUrl = `${window.location.origin}/ui`;
    document.getElementById('normal-date').value = todayValue;
    document.getElementById('special-date').value = todayValue;
    document.getElementById('normal-frontend-url').value = frontendUrl;
    document.getElementById('special-frontend-url').value = frontendUrl;
    let latestRunId = null;

    async function loadPilots() {
      const response = await fetch('/pilots');
      const data = await response.json();
      const pilots = data.pilots || [];
      for (const id of ['latest-pilot', 'normal-pilot', 'special-pilot']) {
        const select = document.getElementById(id);
        select.innerHTML = '';
        for (const pilot of pilots) {
          const option = document.createElement('option');
          option.value = pilot.key;
          option.textContent = pilot.display_name;
          select.appendChild(option);
        }
      }
    }

    async function callApi(url, body, method = 'POST') {
      const response = await fetch(url, {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: body ? JSON.stringify(body) : undefined
      });
      const data = await response.json();
      if (response.ok && data.run_id) {
        latestRunId = data.run_id;
        document.getElementById('analysis-run-id').value = data.run_id;
      }
      document.getElementById('output').textContent = JSON.stringify(data, null, 2);
      return data;
    }

    function repoValue(id, checkedId) {
      return document.getElementById(checkedId).checked ? document.getElementById(id).value : null;
    }

    function selectedRunId() {
      return document.getElementById('analysis-run-id').value || latestRunId;
    }

    async function runNormal() {
      await callApi('/run/normal', {
        pilot: document.getElementById('normal-pilot').value,
        date: document.getElementById('normal-date').value,
        create_pr: document.getElementById('normal-pr').checked,
        repo_path: repoValue('normal-repo', 'normal-pr'),
        gchat_webhook_url: document.getElementById('normal-webhook').value || null,
        frontend_url: document.getElementById('normal-frontend-url').value || null,
        output_dir: document.getElementById('normal-output').value,
        checkforev_zero_min_id: 995
      });
    }

    async function runSpecial() {
      await callApi('/run/special', {
        pilot: document.getElementById('special-pilot').value,
        date: document.getElementById('special-date').value,
        meter_list_s3: document.getElementById('special-s3').value,
        request_name: document.getElementById('special-name').value,
        create_pr: document.getElementById('special-pr').checked,
        repo_path: repoValue('special-repo', 'special-pr'),
        gchat_webhook_url: document.getElementById('special-webhook').value || null,
        frontend_url: document.getElementById('special-frontend-url').value || null,
        output_dir: document.getElementById('special-output').value,
        checkforev_zero_min_id: 995
      });
    }

    async function showLatest() {
      const data = await callApi(`/runs/latest/${document.getElementById('latest-pilot').value}`, null, 'GET');
      if (data && data.run_id) {
        latestRunId = data.run_id;
        document.getElementById('analysis-run-id').value = data.run_id;
      }
    }

    async function showLatestSection(section) {
      const data = await callApi(`/runs/latest/${document.getElementById('latest-pilot').value}`, null, 'GET');
      if (data && data.run_id) {
        latestRunId = data.run_id;
        document.getElementById('analysis-run-id').value = data.run_id;
        await callApi(`/runs/${data.run_id}/section/${section}`, null, 'GET');
      }
    }

    async function showRunSummary() {
      const runId = selectedRunId();
      if (!runId) {
        document.getElementById('output').textContent = 'Enter or load a run id first.';
        return;
      }
      await callApi(`/runs/${runId}/summary`, null, 'GET');
    }

    async function showRunSection(section) {
      const runId = selectedRunId();
      if (!runId) {
        document.getElementById('output').textContent = 'Enter or load a run id first.';
        return;
      }
      await callApi(`/runs/${runId}/section/${section}`, null, 'GET');
    }

    loadPilots().catch(error => {
      document.getElementById('output').textContent = `Unable to load pilots: ${error}`;
    });
  </script>
</body>
</html>
"""
