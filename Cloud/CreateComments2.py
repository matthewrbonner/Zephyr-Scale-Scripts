#!/usr/bin/env python3
import requests
from requests.auth import HTTPBasicAuth
import re
import base64
import json
import os
import time
from typing import Union

# ==============================
# CONFIG (hardcoded for testing)

JIRA_EMAIL = "<Jira Email>"
JIRA_API_TOKEN = "<Jira API Token>"
JIRA_BASE = "<Jira Base URL>"
ZEPHYR_TOKEN = (
    "<Zephyr API Token>"
    )
#Hardcode Values for testing. These could be pulled from external data sources.
PROJECT_ID = 10215
COMMENT_BODY_FIXED = "Matts comment"
CREATED_BY_ACCOUNT_ID = "5d6fdc98dc6e480dbc021aae"
# ==============================


# Jira page that embeds the contextJwt inside SPA state, leave in current state:
JWT_PAGE = JIRA_BASE + "/plugins/servlet/ac/com.kanoah.test-manager/main-project-page"
DUMP_FILE = "jwt_page_dump.html"
TOKEN_FILE = "context_jwt.txt"
ZEPHYR_BASE = "https://api.zephyrscale.smartbear.com/v2"
TM4J_BASE = "https://app.tm4j.smartbear.com/backend/rest/tests/2.0"


# =========================
# Helpers: JWT pretty print
# =========================
def _b64url_json(segment: str):
    try:
        padding = "=" * (-len(segment) % 4)
        return json.loads(base64.urlsafe_b64decode(segment + padding))
    except Exception as e:
        return {"error": "decode failed", "detail": str(e)}

def pretty_print_jwt(jwt_token: str):
    parts = jwt_token.split(".")
    if len(parts) != 3:
        print("Invalid JWT format.")
        return
    header, payload, signature = parts
    print("\n=== Decoded JWT ===")
    print("Header:", json.dumps(_b64url_json(header), indent=4))
    print("\nPayload:", json.dumps(_b64url_json(payload), indent=4))
    print("\nSignature:", signature[:20] + "... (truncated)")

# ============================
# Robust extractor for contextJwt
# ============================
def extract_context_jwt(html_text: str, raw_bytes: bytes = None) -> Union[str, None]:
    def normalize(s: str) -> str:
        from html import unescape
        s = unescape(s)
        s = s.replace("\\/", "/")
        return s

    variants = [html_text]
    if raw_bytes is not None:
        variants.append(raw_bytes.decode("utf-8", errors="replace"))
    variants = variants + [normalize(v) for v in variants]

    patterns = [
        r'"contextJwt"\s*:\s*"([^"]+?)"',
        r'\\"contextJwt\\"\s*:\s*\\"([^"\\]+?)\\"',
        r"'contextJwt'\s*:\s*'([^']+?)'",
        r'contextJwt[^A-Za-z0-9]{1,10}["\']([A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+)["\']',
    ]

    for txt in variants:
        for pat in patterns:
            m = re.search(pat, txt, flags=re.IGNORECASE | re.DOTALL)
            if m:
                tok = m.group(1)
                if tok.count(".") == 2:
                    return tok
    return None

# ==========================================
# Pull the Jira page and extract contextJwt
# ==========================================
def get_context_jwt() -> Union[str, None]:
    print("Fetching page for contextJwt…")
    resp = requests.get(
        JWT_PAGE,
        auth=HTTPBasicAuth(JIRA_EMAIL, JIRA_API_TOKEN),
        headers={"Accept": "text/html,application/xhtml+xml"},
        allow_redirects=True,
        timeout=30,
    )
    print("Status:", resp.status_code)

    with open(DUMP_FILE, "wb") as f:
        f.write(resp.content)

    if resp.status_code != 200:
        return None

    token = extract_context_jwt(resp.text, raw_bytes=resp.content)
    if not token:
        print("contextJwt not found in HTML.")
        return None

    print("\nRetrieved contextJwt (truncated): %s...\n" % token[:60])
    pretty_print_jwt(token)

    with open(TOKEN_FILE, "w") as f:
        f.write(token)

    return token

# ==============================
# Zephyr Scale v2: list 1 testcase
# ==============================
def _z_headers():
    return {
        "Authorization": f"Bearer {ZEPHYR_TOKEN}",  # must be a valid Zephyr API token
        "Accept": "application/json",
    }

def get_one_test_case(project_key: str):
    url = f"{ZEPHYR_BASE}/testcases"
    params = {"projectKey": project_key, "maxResults": 1, "startAt": 0}

    r = requests.get(url, headers=_z_headers(), params=params, timeout=30)
    print(f"[Zephyr] Status: {r.status_code}")
    if r.status_code != 200:
        print(r.text)
        return None

    data = r.json()
    values = data.get("values", [])
    if not values:
        return None

    tc = values[0]
    print("\n=== Single Test Case ===")
    print("Key:        ", tc.get("key"))
    print("ID:         ", tc.get("id"))
    print("Name:       ", tc.get("name"))
    print("========================\n")

    return tc

# ===========================================
# TM4J backend comments API (uses contextJwt)
# ===========================================
def add_test_case_comment(test_case_id: Union[int, str], context_jwt: str):
    url = f"{TM4J_BASE}/testcase/{test_case_id}/comments"
    headers = {
        "Content-Type": "application/json",
        "authorization": f"JWT {context_jwt}",
        "jira-project-id": str(PROJECT_ID),  # hardcoded project id
    }
    payload = {"body": COMMENT_BODY_FIXED, "createdBy": CREATED_BY_ACCOUNT_ID}

    print(f"[TM4J] POST {url}")
    print(f"[TM4J] Payload: {payload}")

    resp = requests.post(url, headers=headers, json=payload, timeout=30)
    print(f"[TM4J] Status: {resp.status_code}")
    try:
        print("[TM4J] Response JSON:", resp.json())
    except Exception:
        print("[TM4J] Response Text:", resp.text)

    resp.raise_for_status()
    return resp.json()

# =========
# __main__
# =========
if __name__ == "__main__":
    token = get_context_jwt()
    if not token:
        print("Could not retrieve contextJwt.")
        exit(1)

    tc = get_one_test_case("MAR")
    if not tc:
        print("No test case found.")
        exit(1)

    tc_id = tc.get("id")
    print(f"Adding fixed comment to test case {tc_id} in project {PROJECT_ID}.")
    add_test_case_comment(tc_id, token)
