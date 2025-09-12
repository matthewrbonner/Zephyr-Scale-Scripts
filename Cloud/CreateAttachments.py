#!/usr/bin/env python3
import os, re, time, json, uuid, base64, mimetypes
from datetime import datetime, timezone
import requests
from requests.auth import HTTPBasicAuth
from html import unescape

# ==============================
# CONFIG (hardcoded for testing)
JIRA_EMAIL = "<Jira Email>"
JIRA_API_TOKEN = "<Jira API Token>"
JIRA_BASE = "<Jira Base URL>"
# Hardcode values for testing
PROJECT_ID = 10215
TEST_CASE_ID = 12749912
ATLASSIAN_ACCOUNT_ID = "5d6fdc98dc6e480dbc021aae"
# ==============================

# Jira page that embeds the contextJwt inside SPA state
JWT_PAGE = JIRA_BASE + "/plugins/servlet/ac/com.kanoah.test-manager/main-project-page"

# Files beside this script
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DUMP_FILE  = os.path.join(SCRIPT_DIR, "jwt_page_dump.html")
TOKEN_FILE = os.path.join(SCRIPT_DIR, "context_jwt.txt")
FILE_NAME  = os.path.join(SCRIPT_DIR, "AWS-IMG.png")  # ensure this exists

# TM4J endpoints
TM4J_UPLOAD_DETAILS = "https://app.tm4j.smartbear.com/backend/rest/tests/2.0/uploaddetails/attachment"
TM4J_SAVE_METADATA  = "https://app.tm4j.smartbear.com/backend/rest/tests/2.0/attachment/metadata"

# =========================
# Helpers: JWT pretty print (handy to sanity check)
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
# Robust extractor for contextJwt (same approach as before + fallback)
def extract_context_jwt(html_text: str, raw_bytes: bytes | None = None) -> str | None:
    def normalize(s: str) -> str:
        s = unescape(s)
        s = s.replace("\\/", "/")
        return s

    variants = [html_text]
    if raw_bytes is not None:
        variants.append(raw_bytes.decode("utf-8", errors="replace"))
        variants.append(raw_bytes.decode("latin1", errors="replace"))
    variants += [normalize(v) for v in variants]

    patterns = [
        r'"contextJwt"\s*:\s*"([^"]+?)"',
        r'\\"contextJwt\\"\s*:\s*\\"([^"\\]+?)\\"',
        r"'contextJwt'\s*:\s*'([^']+?)'",
        r'contextJwt[^A-Za-z0-9]{1,10}["\']([A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+)["\']',
    ]

    matches: list[tuple[str,int,int,int,str]] = []
    for src_idx, txt in enumerate(variants):
        for pat in patterns:
            for m in re.finditer(pat, txt, flags=re.IGNORECASE | re.DOTALL):
                tok = m.group(1)
                if tok.count(".") == 2:
                    matches.append((tok, src_idx, m.start(1), m.end(1), txt))

    if matches:
        tok, src_idx, s, e, src = max(matches, key=lambda t: len(t[0]))
        left = max(0, s - 160); right = min(len(src), e + 160)
        print(f"[extract] Found contextJwt in variant #{src_idx} at index {s}..{e}")
        print("[extract] Context around match:\n%s\n" % src[left:right])
        print("[extract] Token (truncated): %s..." % tok[:60])
        return tok

    # Paranoid fallback: look for any JWT near the word "contextJwt"
    for src_idx, txt in enumerate(variants):
        for m in re.finditer(r'contextJwt.{0,2000}', txt, flags=re.IGNORECASE | re.DOTALL):
            window = txt[m.start():m.end()]
            m2 = re.search(r'([A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,})', window)
            if m2:
                tok = m2.group(1)
                print(f"[extract:fallback] Found JWT near 'contextJwt' in variant #{src_idx}")
                print("[extract:fallback] Window:\n%s\n" % window[:400])
                print("[extract:fallback] Token (truncated): %s..." % tok[:60])
                return tok

    print("[extract] No matches found.")
    return None

# ==========================================
# Pull the Jira page, dump HTML, extract & save token
def get_and_save_context_jwt() -> str | None:
    print("[JWT] Fetching Jira page for contextJwt…")
    resp = requests.get(
        JWT_PAGE,
        auth=HTTPBasicAuth(JIRA_EMAIL, JIRA_API_TOKEN),
        headers={"Accept": "text/html,application/xhtml+xml"},
        allow_redirects=True,
        timeout=30,
    )
    print("Status:", resp.status_code)
    print("Content-Type:", resp.headers.get("Content-Type", ""))

    # Always dump the raw HTML so you can Ctrl+F for contextJwt if needed
    with open(DUMP_FILE, "wb") as f:
        f.write(resp.content)
    print("[+] Full HTML dumped to:", DUMP_FILE)

    if resp.status_code != 200:
        print("[JWT] Non-200, abort.")
        return None

    token = extract_context_jwt(resp.text, raw_bytes=resp.content)
    if not token:
        print("[JWT] contextJwt not found in page.")
        return None

    print("\nRetrieved contextJwt (truncated): %s...\n" % token[:60])
    pretty_print_jwt(token)

    with open(TOKEN_FILE, "w") as f:
        f.write(token)
    print("[+] Saved token to:", TOKEN_FILE)
    return token

# =========================
# Attachment steps
def load_context_jwt_from_file(path: str) -> str | None:
    if not os.path.isfile(path):
        print(f"[JWT] Token file not found: {path}")
        return None
    tok = open(path, "r").read().strip()
    if tok.count(".") != 2:
        print("[JWT] Token in file does not look like a JWT (expected 3 parts).")
        return None
    print("[JWT] Loaded context JWT from file.")
    return tok

def get_upload_details(context_jwt: str) -> dict:
    headers = {"Authorization": f"JWT {context_jwt}", "Accept": "application/json"}
    r = requests.get(TM4J_UPLOAD_DETAILS, headers=headers, timeout=30)
    print("[Step 2] Upload details status:", r.status_code)
    r.raise_for_status()
    data = r.json()
    print("[Step 2] keyPrefix:", data.get("keyPrefix"))
    return data

def s3_upload(upload: dict, project_id: int, test_case_id: int, acct_id: str, file_path: str) -> dict:
    bucket_url = upload["bucketUrl"]
    key_prefix = upload["keyPrefix"]
    credential = upload["credential"]
    date       = upload["date"]
    policy     = upload["policy"]
    signature  = upload["signature"]

    if not os.path.isfile(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")

    file_name = os.path.basename(file_path)
    mime_type = mimetypes.guess_type(file_name)[0] or "application/octet-stream"
    file_size = os.path.getsize(file_path)
    file_uuid = str(uuid.uuid4())

    s3_key = f"{key_prefix}/project/{project_id}/testcase/{test_case_id}/{file_uuid}"

    form = {
        "key": s3_key,
        "acl": "private",
        "Policy": policy,
        "X-Amz-Credential": credential,
        "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
        "X-Amz-Date": date,
        "X-Amz-Signature": signature,
        "X-Amz-Meta-user-account-id": acct_id,
        "X-Amz-Meta-name": file_name,
        "Content-Type": mime_type,
    }

    print(f"[Step 3] Uploading to S3: {bucket_url}")
    print(f"         key={s3_key}")
    with open(file_path, "rb") as f:
        files = {"file": (file_name, f, mime_type)}
        r = requests.post(bucket_url, data=form, files=files, timeout=60)
    print("[Step 3] S3 response status:", r.status_code)
    if r.status_code not in (200, 201, 204):
        print(r.text)
        r.raise_for_status()

    return {"s3_key": s3_key, "file_name": file_name, "mime_type": mime_type, "file_size": file_size}

def save_attachment_metadata(context_jwt: str, project_id: int, test_case_id: int,
                             acct_id: str, s3_key: str, file_name: str,
                             mime_type: str, file_size: int) -> dict:
    headers = {
        "Authorization": f"JWT {context_jwt}",
        "jira-project-id": str(project_id),
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    created_on = datetime.now(timezone.utc).isoformat()
    payload = {
        "createdOn": created_on,
        "mimeType": mime_type,
        "name": file_name,
        "s3Key": s3_key,
        "size": file_size,
        "testCaseId": int(test_case_id),
        "userAccountId": acct_id,
    }
    print("[Step 4] Saving attachment metadata…")
    r = requests.post(TM4J_SAVE_METADATA, headers=headers, json=payload, timeout=30)
    print("[Step 4] Metadata response status:", r.status_code)
    try:
        print("[Step 4] Response JSON:", r.json())
    except Exception:
        print("[Step 4] Response Text:", r.text)
    r.raise_for_status()
    return r.json()

# =========================
# Main
if __name__ == "__main__":
    print("[Init] Script dir:", SCRIPT_DIR)
    print("[Init] Attachment path:", FILE_NAME)

    # 1) Always try to fetch & parse a fresh token (saves to TOKEN_FILE)
    token = get_and_save_context_jwt()
    if not token:
        # 2) If parsing failed, fall back to whatever is already in TOKEN_FILE
        print("[JWT] Falling back to token on disk (if present).")
        token = load_context_jwt_from_file(TOKEN_FILE)
        if not token:
            raise SystemExit("Could not obtain a context JWT.")

    # 3) Proceed with attachments flow
    details = get_upload_details(token)
    s3info = s3_upload(details, PROJECT_ID, TEST_CASE_ID, ATLASSIAN_ACCOUNT_ID, FILE_NAME)
    save_attachment_metadata(
        token,
        PROJECT_ID,
        TEST_CASE_ID,
        ATLASSIAN_ACCOUNT_ID,
        s3info["s3_key"],
        s3info["file_name"],
        s3info["mime_type"],
        s3info["file_size"],
    )
    print("\n Done: attachment uploaded and metadata saved.")
