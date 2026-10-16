"""Read-only guest API checks; print counts/flags only, never response contents.

Credentials are parsed in memory from the user-supplied login script, which is
never executed. The temporary refresh token is revoked when the probe finishes.
"""

import argparse
import asyncio
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiohttp


def credentials_from_script(path):
    source = Path(path).read_text(encoding="utf-8")
    base = re.search(r"^BASE=(https://[^\s]+)", source, re.MULTILINE).group(1)
    payloads = re.findall(r"-d\s+'(\{[^'\r\n]+\})'", source)
    for payload in payloads:
        values = json.loads(payload)
        if "username" in values and "password" in values:
            return base, values["username"], values["password"]
    raise ValueError("No login credentials found")


def summary(value, allowed):
    result = {"kind": type(value).__name__}
    if isinstance(value, (dict, list)):
        result["count"] = len(value)
    if isinstance(value, list):
        ids = [row.get("entity_id") for row in value if isinstance(row, dict)]
        result["unreadable_entity_rows"] = sum(bool(item and item not in allowed) for item in ids)
    if isinstance(value, dict):
        result["contains_real_location"] = bool(value.get("latitude") or value.get("longitude"))
        result["contains_instance_urls"] = bool(
            value.get("external_url") or value.get("internal_url")
        )
        if isinstance(value.get("entities"), list):
            result["entity_rows"] = len(value["entities"])
    return result


async def probe(script):
    base, username, password = credentials_from_script(script)
    timeout = aiohttp.ClientTimeout(total=20)
    refresh_token = None
    async with aiohttp.ClientSession(timeout=timeout, trust_env=False) as session:
        try:
            async with session.post(
                base + "/auth/login_flow",
                json={
                    "client_id": base + "/",
                    "handler": ["homeassistant", None],
                    "redirect_uri": base + "/?auth_callback=1",
                },
            ) as response:
                flow = await response.json()
            async with session.post(
                base + "/auth/login_flow/" + flow["flow_id"],
                json={"client_id": base + "/", "username": username, "password": password},
            ) as response:
                login = await response.json()
            if login.get("type") != "create_entry" or not login.get("result"):
                print(json.dumps({"auth": "failed", "response_type": login.get("type")}))
                return
            async with session.post(
                base + "/auth/token",
                data={
                    "client_id": base + "/",
                    "grant_type": "authorization_code",
                    "code": login["result"],
                },
            ) as response:
                token = await response.json()
            refresh_token = token.get("refresh_token")
            access_token = token.get("access_token")
            if not access_token:
                print('{"auth":"token_failed"}')
                return
            allowed = set()
            private_entity = None
            start = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
            async with session.ws_connect(
                base.replace("https://", "wss://", 1) + "/api/websocket",
                max_msg_size=8 * 1024 * 1024,
            ) as ws:
                await ws.receive_json()
                await ws.send_json({"type": "auth", "access_token": access_token})
                auth = await ws.receive_json()
                if auth.get("type") != "auth_ok":
                    print('{"auth":"websocket_failed"}')
                    return
                sequence = 0

                async def command(name, **params):
                    nonlocal sequence
                    sequence += 1
                    msg_id = sequence
                    await ws.send_json({"id": msg_id, "type": name, **params})
                    while True:
                        message = await asyncio.wait_for(ws.receive_json(), 20)
                        if message.get("id") == msg_id and message.get("type") == "result":
                            break
                    output = {"transport": "ws", "command": name, "success": message.get("success")}
                    if message.get("success"):
                        output.update(summary(message.get("result"), allowed))
                    else:
                        output["error_code"] = message.get("error", {}).get("code")
                    print(json.dumps(output))
                    return message

                states = await command("get_states")
                allowed = {state["entity_id"] for state in states.get("result", [])}
                entities = await command("config/entity_registry/list")
                if entities.get("success"):
                    private_entity = next(
                        (
                            row["entity_id"]
                            for row in entities["result"]
                            if row["entity_id"] not in allowed
                        ),
                        None,
                    )
                for name in (
                    "auth/current_user",
                    "config/entity_registry/list_for_display",
                    "config/device_registry/list",
                    "config/area_registry/list",
                    "config/floor_registry/list",
                    "config/label_registry/list",
                    "get_config",
                    "get_services",
                    "get_panels",
                    "recorder/list_statistic_ids",
                    "person/list",
                    "lovelace/config",
                    "temporary_guest/ui_policy",
                ):
                    result = await command(name)
                    if name == "get_panels" and result.get("success"):
                        other_dashboard = next(
                            (
                                path
                                for path, panel in result["result"].items()
                                if panel.get("component_name") == "lovelace"
                                and path != "dashboard-mzuser"
                            ),
                            None,
                        )
                        if other_dashboard:
                            await command("lovelace/config", url_path=other_dashboard)
                await command("lovelace/config", url_path="dashboard-mzuser")
                await command("logbook/get_events", start_time=start)
                await command("history/history_during_period", start_time=start)
                rendered = await command("render_template", template="{{ states | count }}")
                if rendered.get("success"):
                    while True:
                        event = await asyncio.wait_for(ws.receive_json(), 20)
                        if event.get("id") == rendered["id"] and event.get("type") == "event":
                            value = event.get("event", {}).get("result")
                            print(
                                json.dumps(
                                    {
                                        "template_state_count": int(value)
                                        if str(value).isdigit()
                                        else "non_numeric"
                                    }
                                )
                            )
                            break
                    await command("unsubscribe_events", subscription=rendered["id"])
                if private_entity:
                    await command("config/entity_registry/get", entity_id=private_entity)
                    await command(
                        "history/history_during_period",
                        start_time=start,
                        entity_ids=[private_entity],
                    )
                    await command("search/related", item_type="entity", item_id=private_entity)
                for event_type in (
                    "state_changed",
                    "entity_registry_updated",
                    "device_registry_updated",
                ):
                    response = await command("subscribe_events", event_type=event_type)
                    if response.get("success"):
                        await command("unsubscribe_events", subscription=response["id"])

            headers = {"Authorization": "Bearer " + access_token}
            for path in (
                "/api/config",
                "/api/components",
                "/api/events",
                "/api/services",
                "/api/logbook/" + start,
                "/api/history/period/" + start,
                "/api/calendars",
            ):
                async with session.get(base + path, headers=headers) as response:
                    output = {
                        "transport": "http",
                        "path": path.split("/" + start)[0],
                        "status": response.status,
                    }
                    if response.content_type == "application/json":
                        output.update(summary(await response.json(), allowed))
                    print(json.dumps(output))
        finally:
            if refresh_token:
                async with session.post(
                    base + "/auth/token", data={"action": "revoke", "token": refresh_token}
                ) as response:
                    print(json.dumps({"probe_session_revoked": response.status == 200}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("credential_script")
    arguments = parser.parse_args()
    try:
        asyncio.run(probe(arguments.credential_script))
    except Exception as error:  # noqa: BLE001 - redact errors that may contain credentials
        # Exception text can include a response URL/payload. Print only its class.
        print(json.dumps({"probe_failed": type(error).__name__}))
        raise SystemExit(1) from None
