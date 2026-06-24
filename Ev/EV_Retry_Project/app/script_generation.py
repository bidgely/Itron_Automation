from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .exporters import read_single_column_csv
from .pilots import PilotDefinition

MAX_STATEMENTS_PER_SCRIPT = 800


ENV_SWITCH_BLOCK = """if [ "$1" = "prod-ca" ] || [ "$1" = "prod-eu" ] || [ "$1" = "prod-na" ]
then
   echo "Exiting the script as environment is $1"
   exit 0
elif [ "$1" = "uat" ]
then
   USE_STATEMENT='USE bidgelydbuat_itron;'
elif [ "$1" = "platform-qa" ]
then
   USE_STATEMENT='USE bidgelydbprod_platformqa;'
elif [ "$1" = "nonprodqa" ] || [ "$1" = "prod-na-2" ] || [ "$1" = "productqa" ]
then
   USE_STATEMENT='USE bidgelydbprod;'
else
   echo "Incorrect environment so exiting.."
   exit 0
fi"""


SCRIPT_HEADER = """#!/bin/bash
# env=prodna2
# Auto-generated script for meter status update

{env_switch_block}

SQL_FILE=$(mktemp /tmp/sqlfile.XXXX.sql)
LOG_FILE="/tmp/{log_name}_$$.log"
echo "Generated at $(date)" > "$LOG_FILE"

cat > "$SQL_FILE" <<EOF
$USE_STATEMENT
set autocommit=1;
{sql_body}
EOF

mysql -vvv -u "$2" -h "$3" -p"$4" < "$SQL_FILE"
STATUS=$?
rm -f "$SQL_FILE"

if [ $STATUS -eq 0 ]; then
  echo "SQL execution completed successfully."
else
  echo "SQL execution failed."
  exit 1
fi
"""


@dataclass(frozen=True)
class GeneratedScript:
    file_paths: list[Path]
    statement_count: int


@dataclass(frozen=True)
class PilotScriptSummary:
    mark_completed_hsm_has: GeneratedScript
    mark_completed_hsm_only: GeneratedScript
    mark_failed_request_sent: GeneratedScript
    retry_has_ev: GeneratedScript
    retry_hsm_ev: GeneratedScript


def _quote(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "''")


def _build_update_statements(
    *,
    pilot_id: int,
    meter_ids: list[str],
    status_from: str | None,
    status_to: str,
    feature: str | None = None,
    limit_one: bool = False,
) -> list[str]:
    statements: list[str] = []
    for meter_id in meter_ids:
        conditions = [
            f"pilot_id = {pilot_id}",
            f"esn_list = '{_quote(meter_id)}'",
        ]
        if status_from is not None:
            conditions.append(f"status = '{status_from}'")
        if feature is not None:
            conditions.append(f"feature = '{feature}'")

        statement = [
            "UPDATE meter_config_update_request",
            f"SET status = '{status_to}'",
            "WHERE " + "\n  AND ".join(conditions),
        ]
        if limit_one:
            statement.append("LIMIT 1")
        statements.append("\n".join(statement) + ";")
    return statements


def _build_mark_failed_statement(pilot_id: int) -> list[str]:
    return [
        "\n".join(
            [
                "UPDATE meter_config_update_request",
                "SET status = 'FAILED'",
                f"WHERE pilot_id = {pilot_id}",
                "  AND status = 'REQUEST_SENT';",
            ]
        )
    ]


def _build_mark_failed_statements_for_meter_ids(pilot_id: int, meter_ids: list[str]) -> list[str]:
    return _build_update_statements(
        pilot_id=pilot_id,
        meter_ids=meter_ids,
        status_from="REQUEST_SENT",
        status_to="FAILED",
    )


def _render_script(sql_statements: list[str], log_name: str) -> str:
    sql_body = "\n\n".join(sql_statements)
    return SCRIPT_HEADER.format(
        env_switch_block=ENV_SWITCH_BLOCK,
        log_name=log_name,
        sql_body=sql_body,
    )


def _write_script(output_path: Path, content: str) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8")
    output_path.chmod(0o755)


def _chunked(values: list[str], chunk_size: int) -> list[list[str]]:
    if not values:
        return [[]]
    return [values[index : index + chunk_size] for index in range(0, len(values), chunk_size)]


def _script_path(base_path: Path, chunk_index: int, chunk_count: int) -> Path:
    if chunk_count == 1:
        return base_path
    suffix_without_prefix = base_path.name[3:] if len(base_path.name) > 3 and base_path.name[2] == "_" else base_path.name
    return base_path.with_name(f"{chunk_index:02d}_{suffix_without_prefix}")


def _script_label(base_path: Path) -> str:
    return base_path.name[3:] if len(base_path.name) > 3 and base_path.name[2] == "_" else base_path.name


def _write_chunked_scripts(
    *,
    sql_statements: list[str],
    scripts_dir: Path,
    label_name: str,
    log_name: str,
    start_index: int,
) -> GeneratedScript:
    chunks = _chunked(sql_statements, MAX_STATEMENTS_PER_SCRIPT)
    file_paths: list[Path] = []
    for index, chunk in enumerate(chunks, start=1):
        output_path = scripts_dir / f"{start_index + index - 1:02d}_{label_name}"
        chunk_log_name = log_name if len(chunks) == 1 else f"{log_name}_part{index:02d}"
        _write_script(output_path, _render_script(chunk, chunk_log_name))
        file_paths.append(output_path)
    return GeneratedScript(file_paths=file_paths, statement_count=len(sql_statements))


def generate_db_update_scripts(*, pilot: PilotDefinition, output_dir: Path) -> PilotScriptSummary:
    pilot_output_dir = output_dir / pilot.key
    analysis_dir = pilot_output_dir / "analysis"
    scripts_dir = pilot_output_dir / "scripts"

    print(f"[{pilot.display_name}] Step 1/2: reading analysis CSV files for script generation...")

    hsm_has_completed_ids = read_single_column_csv(analysis_dir / f"{pilot.key}_hsm_has_completed.csv")
    has_retry_ids = read_single_column_csv(analysis_dir / f"{pilot.key}_has_retry.csv")
    hsm_retry_ids = read_single_column_csv(analysis_dir / f"{pilot.key}_hsm_retry.csv")
    print(
        f"[{pilot.display_name}]   source counts: "
        f"HSM+HAS={len(hsm_has_completed_ids)}, HAS retry={len(has_retry_ids)}, "
        f"HSM retry={len(hsm_retry_ids)}"
    )

    completed_hsm_has_sql = _build_update_statements(
        pilot_id=pilot.pilot_id,
        meter_ids=hsm_has_completed_ids,
        status_from="REQUEST_SENT",
        status_to="COMPLETED",
        feature="HSM_EV",
    )
    completed_hsm_only_sql = _build_update_statements(
        pilot_id=pilot.pilot_id,
        meter_ids=has_retry_ids,
        status_from="REQUEST_SENT",
        status_to="COMPLETED",
        feature="HSM_EV",
    )
    mark_failed_sql = _build_mark_failed_statement(pilot.pilot_id)
    retry_has_sql = _build_update_statements(
        pilot_id=pilot.pilot_id,
        meter_ids=has_retry_ids,
        status_from="FAILED",
        status_to="CONFIG_GENERATED",
        feature="HAS_EV",
        limit_one=True,
    )
    retry_hsm_sql = _build_update_statements(
        pilot_id=pilot.pilot_id,
        meter_ids=hsm_retry_ids,
        status_from="FAILED",
        status_to="CONFIG_GENERATED",
        feature="HSM_EV",
        limit_one=True,
    )

    mark_completed_hsm_has_path = scripts_dir / "01_mark_completed_hsm_has.sh"
    mark_completed_hsm_only_path = scripts_dir / "02_mark_completed_hsm_only.sh"
    mark_failed_request_sent_path = scripts_dir / "03_mark_failed_request_sent.sh"
    retry_has_ev_path = scripts_dir / "04_retry_has_ev_config_generated.sh"
    retry_hsm_ev_path = scripts_dir / "05_retry_hsm_ev_config_generated.sh"

    next_index = 1
    mark_completed_hsm_has = _write_chunked_scripts(
        sql_statements=completed_hsm_has_sql,
        scripts_dir=scripts_dir,
        label_name=_script_label(mark_completed_hsm_has_path),
        log_name=f"{pilot.key}_mark_completed_hsm_has",
        start_index=next_index,
    )
    next_index += len(mark_completed_hsm_has.file_paths)
    mark_completed_hsm_only = _write_chunked_scripts(
        sql_statements=completed_hsm_only_sql,
        scripts_dir=scripts_dir,
        label_name=_script_label(mark_completed_hsm_only_path),
        log_name=f"{pilot.key}_mark_completed_hsm_only",
        start_index=next_index,
    )
    next_index += len(mark_completed_hsm_only.file_paths)
    mark_failed_request_sent = _write_chunked_scripts(
        sql_statements=mark_failed_sql,
        scripts_dir=scripts_dir,
        label_name=_script_label(mark_failed_request_sent_path),
        log_name=f"{pilot.key}_mark_failed_request_sent",
        start_index=next_index,
    )
    next_index += len(mark_failed_request_sent.file_paths)
    retry_has_ev = _write_chunked_scripts(
        sql_statements=retry_has_sql,
        scripts_dir=scripts_dir,
        label_name=_script_label(retry_has_ev_path),
        log_name=f"{pilot.key}_retry_has_ev",
        start_index=next_index,
    )
    next_index += len(retry_has_ev.file_paths)
    retry_hsm_ev = _write_chunked_scripts(
        sql_statements=retry_hsm_sql,
        scripts_dir=scripts_dir,
        label_name=_script_label(retry_hsm_ev_path),
        log_name=f"{pilot.key}_retry_hsm_ev",
        start_index=next_index,
    )
    print(f"[{pilot.display_name}] Step 2/2: DB update scripts generated in {scripts_dir}")

    return PilotScriptSummary(
        mark_completed_hsm_has=mark_completed_hsm_has,
        mark_completed_hsm_only=mark_completed_hsm_only,
        mark_failed_request_sent=mark_failed_request_sent,
        retry_has_ev=retry_has_ev,
        retry_hsm_ev=retry_hsm_ev,
    )


def generate_special_request_scripts(*, pilot: PilotDefinition, output_dir: Path) -> PilotScriptSummary:
    pilot_output_dir = output_dir / pilot.key
    analysis_dir = pilot_output_dir / "analysis"
    scripts_dir = pilot_output_dir / "scripts"

    print(f"[{pilot.display_name}] Step 1/2: reading special-request analysis CSV files...")

    full_meter_ids = read_single_column_csv(pilot_output_dir / f"{pilot.key}_full_list_meters.csv")
    hsm_has_completed_ids = read_single_column_csv(analysis_dir / f"{pilot.key}_hsm_has_completed.csv")
    has_retry_ids = read_single_column_csv(analysis_dir / f"{pilot.key}_has_retry.csv")
    hsm_retry_ids = read_single_column_csv(analysis_dir / f"{pilot.key}_hsm_retry.csv")
    print(
        f"[{pilot.display_name}]   source counts: "
        f"full={len(full_meter_ids)}, HSM+HAS={len(hsm_has_completed_ids)}, "
        f"HAS retry={len(has_retry_ids)}, HSM retry={len(hsm_retry_ids)}"
    )

    completed_hsm_has_sql = _build_update_statements(
        pilot_id=pilot.pilot_id,
        meter_ids=hsm_has_completed_ids,
        status_from="REQUEST_SENT",
        status_to="COMPLETED",
        feature="HSM_EV",
    )
    completed_hsm_only_sql = _build_update_statements(
        pilot_id=pilot.pilot_id,
        meter_ids=has_retry_ids,
        status_from="REQUEST_SENT",
        status_to="COMPLETED",
        feature="HSM_EV",
    )
    mark_failed_sql = _build_mark_failed_statement(pilot.pilot_id)
    retry_has_sql = _build_update_statements(
        pilot_id=pilot.pilot_id,
        meter_ids=has_retry_ids,
        status_from="FAILED",
        status_to="CONFIG_GENERATED",
        feature="HAS_EV",
        limit_one=True,
    )
    retry_hsm_sql = _build_update_statements(
        pilot_id=pilot.pilot_id,
        meter_ids=hsm_retry_ids,
        status_from="FAILED",
        status_to="CONFIG_GENERATED",
        feature="HSM_EV",
        limit_one=True,
    )

    mark_completed_hsm_has_path = scripts_dir / "01_mark_completed_hsm_has.sh"
    mark_completed_hsm_only_path = scripts_dir / "02_mark_completed_hsm_only.sh"
    mark_failed_request_sent_path = scripts_dir / "03_mark_failed_request_sent.sh"
    retry_has_ev_path = scripts_dir / "04_retry_has_ev_config_generated.sh"
    retry_hsm_ev_path = scripts_dir / "05_retry_hsm_ev_config_generated.sh"

    next_index = 1
    mark_completed_hsm_has = _write_chunked_scripts(
        sql_statements=completed_hsm_has_sql,
        scripts_dir=scripts_dir,
        label_name=_script_label(mark_completed_hsm_has_path),
        log_name=f"{pilot.key}_special_mark_completed_hsm_has",
        start_index=next_index,
    )
    next_index += len(mark_completed_hsm_has.file_paths)
    mark_completed_hsm_only = _write_chunked_scripts(
        sql_statements=completed_hsm_only_sql,
        scripts_dir=scripts_dir,
        label_name=_script_label(mark_completed_hsm_only_path),
        log_name=f"{pilot.key}_special_mark_completed_hsm_only",
        start_index=next_index,
    )
    next_index += len(mark_completed_hsm_only.file_paths)
    mark_failed_request_sent = _write_chunked_scripts(
        sql_statements=mark_failed_sql,
        scripts_dir=scripts_dir,
        label_name=_script_label(mark_failed_request_sent_path),
        log_name=f"{pilot.key}_special_mark_failed_request_sent",
        start_index=next_index,
    )
    next_index += len(mark_failed_request_sent.file_paths)
    retry_has_ev = _write_chunked_scripts(
        sql_statements=retry_has_sql,
        scripts_dir=scripts_dir,
        label_name=_script_label(retry_has_ev_path),
        log_name=f"{pilot.key}_special_retry_has_ev",
        start_index=next_index,
    )
    next_index += len(retry_has_ev.file_paths)
    retry_hsm_ev = _write_chunked_scripts(
        sql_statements=retry_hsm_sql,
        scripts_dir=scripts_dir,
        label_name=_script_label(retry_hsm_ev_path),
        log_name=f"{pilot.key}_special_retry_hsm_ev",
        start_index=next_index,
    )
    print(f"[{pilot.display_name}] Step 2/2: special-request DB update scripts generated in {scripts_dir}")

    return PilotScriptSummary(
        mark_completed_hsm_has=mark_completed_hsm_has,
        mark_completed_hsm_only=mark_completed_hsm_only,
        mark_failed_request_sent=mark_failed_request_sent,
        retry_has_ev=retry_has_ev,
        retry_hsm_ev=retry_hsm_ev,
    )
