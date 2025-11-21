#!/usr/bin/env python3

"""Utility for crafting JSON-RPC requests to the Google Analytics MCP server."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import uuid
from urllib import request as urllib_request, error as urllib_error


def _load_json_arg(value: str | None) -> dict:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON for --arguments: {exc}") from exc
    if not isinstance(parsed, dict):
        raise SystemExit("--arguments must decode to an object")
    return parsed


def _load_adc(path: Path) -> dict:
    try:
        text = path.read_text()
    except OSError as exc:
        raise SystemExit(f"Unable to read ADC file: {exc}") from exc
    try:
        adc = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"ADC file is not valid JSON: {exc}") from exc
    if not isinstance(adc, dict):
        raise SystemExit("ADC file must decode to a JSON object")
    return adc


def build_request(args: argparse.Namespace) -> dict:
    environment: dict[str, object] = {
        "google_project_id": args.google_project_id,
        "adc": _load_adc(Path(args.adc_path)),
    }

    if args.auth_token and not args.header_token:
        environment["auth_token"] = args.auth_token

    payload = {
        "jsonrpc": "2.0",
        "id": args.request_id or str(uuid.uuid4()),
        "method": "tools/call",
        "params": {
            "name": args.tool,
            "arguments": _load_json_arg(args.arguments),
            "environment": environment,
        },
    }

    return payload


def _post(
    url: str,
    payload: dict,
    auth_token: str | None,
    extra_headers: list[str] | None,
    session_id: str | None = None,
) -> tuple[str, dict[str, str]]:
    body = json.dumps(payload).encode()
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"

    header_list = list(extra_headers or [])
    # Default Accept header satisfies FastMCP's SSE mode (requires both types).
    if not any(h.lower().startswith("accept:") for h in header_list):
        header_list.append("Accept: application/json, text/event-stream")

    for header in header_list:
        if ":" not in header:
            raise SystemExit(f"Invalid header format: {header}")
        name, value = header.split(":", 1)
        headers[name.strip()] = value.strip()

    if session_id:
        headers["MCP-Session-ID"] = session_id

    req = urllib_request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib_request.urlopen(req) as response:
            return response.read().decode(), dict(response.headers)
    except urllib_error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise SystemExit(
            f"HTTP request failed ({exc.code} {exc.reason}): {detail}"
        ) from exc
    except Exception as exc:
        raise SystemExit(f"HTTP request failed: {exc}") from exc


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build and optionally send an MCP HTTP request."
    )
    parser.add_argument(
        "--tool",
        default="get_account_summaries",
        help="Tool name to invoke (default: %(default)s)",
    )
    parser.add_argument(
        "--arguments",
        help="JSON object describing tool arguments. Example: "
        '\'{"property_id":"123456789"}\'',
    )
    parser.add_argument(
        "--google-project-id",
        required=True,
        help="Google Cloud project ID to attach to the request quota/billing.",
    )
    parser.add_argument(
        "--adc-path",
        required=True,
        help="Path to the Application Default Credentials JSON file.",
    )
    parser.add_argument(
        "--auth-token",
        help="Shared-secret auth token. Included in the payload unless "
        "--header-token is also provided.",
    )
    parser.add_argument(
        "--header-token",
        action="store_true",
        help="Send the auth token via Authorization header instead of "
        "inside the environment payload.",
    )
    parser.add_argument(
        "--request-id",
        help="Optional JSON-RPC request id. Defaults to a generated UUID.",
    )
    parser.add_argument(
        "--output",
        help="Write the JSON payload to this file instead of stdout.",
    )
    parser.add_argument(
        "--post",
        metavar="URL",
        help="Send the request to the given URL after building it.",
    )
    parser.add_argument(
        "--http-header",
        action="append",
        help="Additional HTTP headers for --post (format: 'Header: value').",
    )
    parser.add_argument(
        "--stateless",
        action="store_true",
        help="Skip the FastMCP initialization handshake (use when server runs in stateless mode).",
    )

    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)

    if args.header_token and not args.auth_token:
        raise SystemExit("--header-token requires --auth-token.")
    if not args.stateless and not args.auth_token:
        raise SystemExit(
            "--auth-token is required when performing the stateful handshake. "
            "Use --stateless to skip or supply an auth token."
        )

    payload = build_request(args)
    json_payload = json.dumps(payload, indent=2)

    session_id: str | None = None

    # Perform FastMCP handshake in stateful mode.
    if args.post and not args.stateless:
        init_payload = {
            "jsonrpc": "2.0",
            "id": "initialize",
            "method": "initialize",
            "params": {
                "protocolVersion": "1.0",
                "capabilities": {
                    "prompts": {},
                    "resources": {},
                    "tools": {},
                    "completions": {},
                    "logging": {},
                },
                "clientInfo": {
                    "name": "google-analytics-mcp-tester",
                    "version": "1.0.0",
                },
            },
        }

        init_response, init_headers = _post(
            url=args.post,
            payload=init_payload,
            auth_token=args.auth_token,
            extra_headers=args.http_header,
            session_id=None,
        )

        session_id = None
        for key, value in init_headers.items():
            if key.lower() == "mcp-session-id":
                session_id = value
                break
        if not session_id:
            raise SystemExit(
                "Initialization completed but server did not provide MCP-Session-ID."
            )

        if init_response.strip():
            print("Handshake response:")
            print(init_response)

    if args.output:
        Path(args.output).write_text(json_payload + "\n")
        print(f"Wrote request payload to {args.output}")
    else:
        redacted = json.loads(json_payload)
        env = redacted.get("params", {}).get("environment", {})
        if "adc" in env:
            env["adc"] = "<redacted>"
        if "adc_json" in env:
            env["adc_json"] = "<redacted>"
        print(json.dumps(redacted, indent=2))

    if args.post:
        if args.stateless:
            auth_header_token = args.auth_token if args.header_token else None
        else:
            # After initialization, always send the auth token via the header.
            auth_header_token = args.auth_token

        response, _ = _post(
            url=args.post,
            payload=payload,
            auth_token=auth_header_token,
            extra_headers=args.http_header,
            session_id=session_id,
        )
        print("Response:")
        print(response)

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
