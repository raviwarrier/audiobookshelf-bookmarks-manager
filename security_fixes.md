# Security & Code Remediation Log

This document tracks all security, reliability, and maintainability fixes applied across the codebase, organized by batches.

---

## Set 1: Path Traversal & Arbitrary File Deletion in `main.py`

### Vulnerability Summary
- **Classification:** CWE-22 (Path Traversal), CWE-73 (External Control of File Name or Path), CWE-377 (Insecure Temporary File / Arbitrary File Deletion).
- **Location:** `main.py` — `SnippetExpandRequest`, `expand_or_update_snippet()`, and `process_bookmark_extraction()`.
- **Taint Flow:** HTTP request payload `payload.timestamp` flowed into file paths and reached `os.remove(stale_file)` without validation.

---

### Numbered Findings & Fixes

1. **Finding 1 — Unsanitized HTTP Input in `SnippetExpandRequest` (`payload.timestamp`)**
   - **Issue:** An external attacker could supply malicious directory traversal sequences (such as `../../`) or forbidden filesystem characters in `payload.timestamp`.
   - **Fix:** Added schema-level validation on `SnippetExpandRequest` using Pydantic `@validator("timestamp")` with character whitelisting (`^[A-Za-z0-9_\-]+$`), length constraints (1–64 characters), and explicit rejection of path separators (`/`, `\`), null bytes (`\0`), and directory navigation tokens (`..`).

2. **Findings 2, 3, 4 — Unvalidated Assignment to `target_ts`**
   - **Issue:** The variable `target_ts = payload.timestamp.strip()` propagated untrusted data into downstream logic.
   - **Fix:** Introduced the helper `validate_and_sanitize_snippet_timestamp(ts)`, ensuring `target_ts` is strictly sanitized and isolated via `os.path.basename()` before downstream usage.

3. **Finding 5 — Propagation to `process_bookmark_extraction()` Call**
   - **Issue:** `target_ts` was passed directly as `replace_timestamp=target_ts`.
   - **Fix:** Verified that the value passed into `process_bookmark_extraction()` is pre-sanitized and isolated before the invocation occurs.

4. **Findings 6 & 7 — Function Argument Propagation (`replace_timestamp: Optional[str] = None`)**
   - **Issue:** `process_bookmark_extraction()` accepted raw string input for `replace_timestamp` without defensive checks at the function boundary.
   - **Fix:** Added defensive validation at the entry of `process_bookmark_extraction()` by re-validating `replace_timestamp` with `validate_and_sanitize_snippet_timestamp()`.

5. **Findings 8 & 9 — Timestamp Variable Assignment**
   - **Issue:** `timestamp = replace_timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")` propagated tainted user input to the local `timestamp` variable.
   - **Fix:** Enforced that `timestamp` is sanitized with `os.path.basename()` and verified against `^[A-Za-z0-9_\-]+$` with an immediate `HTTPException(400)` raised if any traversal or unexpected character is detected.

6. **Findings 10, 11, 12, 13 — Path Concatenation (`output_mp3`, `output_md`, `output_json`)**
   - **Issue:** String formatting (`f"{timestamp}.json"`, etc.) concatenated untrusted input into path strings.
   - **Fix:** Added canonical directory resolution (`real_output_dir = os.path.realpath(output_dir)`) and strict containment checks via `os.path.commonpath([real_output_dir, real_file]) != real_output_dir` to block any directory escape.

7. **Finding 14 — Aggregation of Tainted Paths into List Structure**
   - **Issue:** `[output_mp3, output_md, output_json]` aggregated potentially tainted strings into a list.
   - **Fix:** Removed the creation of the unvalidated list for deletion. Replaced with explicit extension tuples `("mp3", "md", "json")` passed to a safe file removal helper.

8. **Finding 15 — Loop Assignment to `stale_file`**
   - **Issue:** `for stale_file in [output_mp3, output_md, output_json]:` assigned tainted list elements to `stale_file`.
   - **Fix:** Replaced loop with controlled iteration over known extensions and passed exact filenames to `safe_remove_file_in_directory()`.

9. **Finding 16 — Vulnerable SINK: `os.remove(stale_file)`**
   - **Issue:** `os.remove()` was invoked directly on paths derived from untrusted user input (Arbitrary File Deletion).
   - **Fix:** Implemented `safe_remove_file_in_directory(parent_dir, filename)` which:
     - Enforces pure basename validation (no `/`, `\`, `..`, or `\0`).
     - Restricts filenames strictly to `^[A-Za-z0-9_\-.]+\.(mp3|md|json)$`.
     - Canonicalizes parent directory and child target using `os.path.realpath()`.
     - Strictly verifies path boundary containment using `os.path.commonpath()` and prefix check.
     - Prevents symlink attacks (CWE-59) by ensuring the file is not a symlink before unlinking.
     - Also hardened `delete_user_bookmark()` to use this same safe deletion mechanism.

---

## Set 2: Path Traversal & Arbitrary File Overwrite in `main.py`

### Vulnerability Summary
- **Classification:** CWE-22 (Path Traversal), CWE-73 (External Control of File Name or Path), CWE-59 (Improper Link Resolution Before File Access).
- **Location:** `main.py` — `expand_or_update_snippet()`, `process_bookmark_extraction()`, and `create_unextractable_bookmark_snippet()`.
- **Taint Flow:** HTTP input `payload.timestamp` $\rightarrow$ `target_ts` $\rightarrow$ `process_bookmark_extraction(replace_timestamp=...)` $\rightarrow$ `output_md = os.path.join(..., f"{timestamp}.md")` $\rightarrow$ SINK: `with open(output_md, "w", encoding="utf-8") as f:`.

---

### Numbered Findings & Fixes

1. **Findings 1–5 — Common Entry & Taint Propagation**
   - **Trace:** `payload: SnippetExpandRequest` (item 1) $\rightarrow$ `payload.timestamp` extracted into `target_ts` (items 2, 3, 4) $\rightarrow$ passed into `process_bookmark_extraction(replace_timestamp=target_ts)` (item 5).
   - **Status:** Shared upstream flow with Set 1; hardened via Pydantic validator `@validator("timestamp")` and `validate_and_sanitize_snippet_timestamp()`.

2. **Findings 6–9 — Internal Parameter Flow in `process_bookmark_extraction()`**
   - **Trace:** `replace_timestamp` argument $\rightarrow$ assigned to local `timestamp = replace_timestamp or ...`.
   - **Status:** Validated and sanitized at function boundary with regex check (`^[A-Za-z0-9_\-]+$`) and `os.path.basename()` isolation.

3. **Findings 10–13 — String Formatting & Path Construction (`output_md`)**
   - **Issue:** `output_md = os.path.join(output_dir, f"{timestamp}.md")` concatenates user-controlled string into file path.
   - **Fix:** Added directory canonicalization with `os.path.realpath()`, plus strict containment barrier checks via `os.path.commonpath([real_output_dir, real_file]) != real_output_dir`.

4. **Finding 14 — Vulnerable SINK: `with open(output_md, "w", encoding="utf-8") as f:`**
   - **Issue:** Invoking standard Python `open(path, "w")` on a path originating from HTTP input could allow an attacker to overwrite arbitrary files on the filesystem (Arbitrary File Overwrite).
   - **Fix:** Implemented `safe_write_text_file(parent_dir, filename, content)` and `safe_write_json_file(parent_dir, filename, data)`:
     - **Basename Isolation:** Guarantees `filename` is strictly a pure basename without slashes, backslashes, directory navigation (`..`), or null bytes (`\0`).
     - **Extension Whitelist:** Strictly checks naming pattern (`^[A-Za-z0-9_\-.]+\.(md|txt)$` for text, `^[A-Za-z0-9_\-.]+\.json$` for JSON).
     - **Canonical Containment:** Resolves parent and target with `os.path.realpath()`, verifying `commonpath` and prefix checking.
     - **Symlink Prevention (CWE-59):** Checks `os.path.islink()` to prohibit writing through symbolic links.
     - Updated both `process_bookmark_extraction()` and `create_unextractable_bookmark_snippet()` to use these safe write helpers.
     - Implemented `safe_read_json_file(parent_dir, filename)` to eliminate path traversal risks when reading existing snippet metadata in `expand_or_update_snippet()`.

---

## Set 3: Path Traversal & Arbitrary File Overwrite in JSON Metadata Output

### Vulnerability Summary
- **Classification:** CWE-22 (Path Traversal), CWE-73 (External Control of File Name or Path), CWE-59 (Improper Link Resolution Before File Access).
- **Location:** `main.py` — `process_bookmark_extraction()` and `create_unextractable_bookmark_snippet()`.
- **Taint Flow:** HTTP input `payload.timestamp` $\rightarrow$ `target_ts` $\rightarrow$ `process_bookmark_extraction(replace_timestamp=...)` $\rightarrow$ `output_json = os.path.join(..., f"{timestamp}.json")` $\rightarrow$ SINK: `with open(output_json, "w", encoding="utf-8") as f: json.dump(...)`.

---

### Numbered Findings & Fixes

1. **Findings 1–5 — Common Entry & Taint Propagation**
   - **Trace:** Same entry path as Sets 1 and 2 (`payload: SnippetExpandRequest` $\rightarrow$ `target_ts` $\rightarrow$ `process_bookmark_extraction`).
   - **Status:** Protected by Pydantic timestamp validation and `validate_and_sanitize_snippet_timestamp()`.

2. **Findings 6–9 — Parameter Assignment to `timestamp`**
   - **Trace:** Parameter `replace_timestamp` passed to `timestamp = replace_timestamp or ...`.
   - **Status:** Protected by defensive regex and basename isolation.

3. **Findings 10–13 — String Concatenation into `output_json`**
   - **Issue:** `output_json = os.path.join(output_dir, f"{timestamp}.json")` builds the JSON metadata filepath.
   - **Status:** Canonical containment enforced via `real_output_dir = os.path.realpath(output_dir)`.

4. **Finding 14 — Vulnerable SINK: `with open(output_json, "w", encoding="utf-8") as f: json.dump(...)`**
   - **Issue:** Direct file open for writing on `output_json` allowed arbitrary JSON file creation/overwrite if `timestamp` was controlled by an attacker.
   - **Fix:** Handled by `safe_write_json_file(parent_dir, filename, data)`:
     - Pure basename verification and prohibition of path characters.
     - Whitelist check enforcing `^[A-Za-z0-9_\-.]+\.json$`.
     - Canonical directory containment via `os.path.commonpath()` and prefix check.
     - `os.path.islink()` check to reject writes via symbolic links.
     - Replaced all raw `open(output_json, "w")` calls in `process_bookmark_extraction()` and `create_unextractable_bookmark_snippet()` with `safe_write_json_file()`.

---

## Set 4: Path Traversal & Arbitrary File Read in Metadata Lookup

### Vulnerability Summary
- **Classification:** CWE-22 (Path Traversal), CWE-73 (External Control of File Name or Path), CWE-200 (Information Exposure).
- **Location:** `main.py` — `expand_or_update_snippet()`.
- **Taint Flow:** HTTP input `payload.timestamp` $\rightarrow$ `target_ts` $\rightarrow$ `json_file = os.path.join(u_dir, b_dir, f"{target_ts}.json")` $\rightarrow$ SINK (item 9): `with open(json_file, "r", encoding="utf-8") as jf: json.load(jf)`.

---

### Numbered Findings & Fixes

1. **Finding 1 — HTTP Input Source (`payload: SnippetExpandRequest`)**
   - **Trace:** User provides JSON payload with `timestamp` field.
   - **Status:** Validated at the schema boundary with Pydantic `@validator("timestamp")`.

2. **Findings 2, 3, 4 — String Extraction into `target_ts`**
   - **Trace:** `target_ts = payload.timestamp.strip()`.
   - **Status:** Sanitized with `validate_and_sanitize_snippet_timestamp()`, enforcing regex `^[A-Za-z0-9_\-]+$` and `os.path.basename()` isolation.

3. **Findings 5, 6, 7, 8 — Path Formatting into `json_file`**
   - **Issue:** `json_file = os.path.join(u_dir, b_dir, f"{target_ts}.json")` constructed a file path from user input.
   - **Fix:** Directory existence check and isolation of book folder path (`book_folder = os.path.join(real_u_dir, b_dir)`).

4. **Finding 9 — Vulnerable SINK: `with open(json_file, "r", encoding="utf-8") as jf: json.load(jf)`**
   - **Issue:** Calling standard `open(..., "r")` directly on a concatenated path allowed arbitrary file read or information disclosure if traversal tokens (`..`) were present in `target_ts`.
   - **Fix:** Implemented `safe_read_json_file(parent_dir, filename)`:
     - Enforces pure basename isolation (rejecting `/`, `\`, `..`, and `\0`).
     - Strictly enforces naming scheme (`^[A-Za-z0-9_\-.]+\.json$`).
     - Verifies canonical containment (`os.path.commonpath([canonical_dir, target_path]) == canonical_dir` and prefix match).
     - Prohibits symlink traversal (`os.path.islink()`).
     - Replaced raw `open(json_file, "r")` in `expand_or_update_snippet()` with `safe_read_json_file(book_folder, f"{target_ts}.json")`.

---

## Set 5: Path Traversal & Arbitrary Directory Read in `/api/export-book`

### Vulnerability Summary
- **Classification:** CWE-22 (Path Traversal), CWE-73 (External Control of File Name or Path), CWE-200 (Information Exposure).
- **Location:** `main.py` — `export_book_snippets()` (`/api/export-book`, `/api/user/bookmarks/export-book`).
- **Taint Flow:** HTTP Query parameter `book_title: str = Query(...)` (Item 1) $\rightarrow$ joined unescaped into `cand2 = os.path.join(p_dir, book_title)` $\rightarrow$ assigned to `book_dir_path` (Items 2–4) $\rightarrow$ reached directory listing and file open SINKs: `os.listdir(book_dir_path)` and `with open(os.path.join(book_dir_path, md_f), "r") as f:` (Items 5–6).

---

### Numbered Findings & Fixes

1. **Finding 1 — HTTP Query Parameter Source (`book_title: str = Query(...)`)**
   - **Issue:** Untrusted user input supplied via URL query parameter was accepted without format validation.
   - **Fix:** Added upfront defensive validation in `export_book_snippets()` to immediately reject any `book_title` containing path separators (`/`, `\`), traversal sequences (`..`), or null bytes (`\0`).

2. **Findings 2, 3, 4 — Unsafe Path Concatenation (`cand2` & `book_dir_path`)**
   - **Issue:** `cand2 = os.path.join(p_dir, book_title)` concatenated the un-sanitized parameter directly into a filesystem path, allowing arbitrary folder traversal outside user roots.
   - **Fix:**
     - Completely eliminated the vulnerable `cand2 = os.path.join(p_dir, book_title)` expression.
     - Enforced that only `safe_book_title = sanitize_filename(clean_raw_title)` is used for direct folder checks.
     - Added canonical verification `os.path.commonpath([real_p, cand1]) == real_p` and symlink rejection (`not os.path.islink(cand1)`).

3. **Findings 5 & 6 — Vulnerable SINKs: `os.listdir(book_dir_path)` and `open(os.path.join(book_dir_path, md_f), "r")`**
   - **Issue:** Reading files from `book_dir_path` or packaging them into a ZIP archive allowed unauthorized reading of host files if `book_dir_path` was escaped.
   - **Fix:**
     - Added strict root boundary enforcement verifying `real_book_dir = os.path.realpath(book_dir_path)` strictly resides inside `candidate_roots` (`os.path.commonpath`).
     - Prohibited symbolic links on the book folder (`os.path.islink()`).
     - Implemented `safe_read_text_file(parent_dir, filename)` to safely open and read Markdown note files with basename and extension verification.
     - Hardened ZIP creation to ensure all archived files are strictly regular files within `real_book_dir` and not symlinks.

---

## Set 6: Path Traversal & Arbitrary File Read in ZIP Notes Summary Export

### Vulnerability Summary
- **Classification:** CWE-22 (Path Traversal), CWE-73 (External Control of File Name or Path), CWE-200 (Information Exposure).
- **Location:** `main.py` — `export_book_snippets()` (ZIP archive summary branch).
- **Taint Flow:** HTTP Query parameter `book_title: str = Query(...)` (Item 1) $\rightarrow$ joined unescaped into `cand2 = os.path.join(p_dir, book_title)` $\rightarrow$ assigned to `book_dir_path` (Items 2–4) $\rightarrow$ reached file reading SINK (Item 6): `with open(os.path.join(book_dir_path, md_f), "r") as f: summary_lines.append(...)`.

---

### Numbered Findings & Fixes

1. **Findings 1–4 — Input Source & Directory Resolution**
   - **Trace:** Same flow as Set 5 (`book_title: str = Query(...)` $\rightarrow$ `cand2` $\rightarrow$ `book_dir_path`).
   - **Status:** Resolved in Set 5 by eliminating `cand2`, validating `book_title` against path traversal characters upfront, and enforcing canonical boundary containment on `real_book_dir`.

2. **Findings 5 & 6 — Vulnerable SINK: `with open(os.path.join(book_dir_path, md_f), "r") as f:` in ZIP Summary**
   - **Issue:** Direct file open for reading note content into the ZIP summary note file (`ALL_NOTES_COMBINED.md`).
   - **Fix:**
     - Replaced the direct file open call with `safe_read_text_file(real_book_dir, md_f)`.
     - Verified filename patterns (`^[A-Za-z0-9_\-.]+\.(md|txt|json)$`) and canonical containment.
     - Enforced `os.path.islink()` checks on both the directory and individual files before reading or archiving.

---

## Set 7: Avoid Binding to All Network Interfaces (`0.0.0.0`)

### Vulnerability Summary
- **Classification:** CWE-1327 (Binding to an Unrestricted IP Address), CWE-668 (Exposure of Resource to Wrong Sphere), Bandit B104 (`hardcoded_bind_all_interfaces`).
- **Location:** `main.py` — `uvicorn.run()` entrypoint (line ~4073 / ~4368).
- **Issue:** Hardcoded `host="0.0.0.0"` unconditionally binds the server to all network interfaces on the host machine, including public internet interfaces if exposed directly.

---

### Numbered Findings & Fixes

1. **Finding 1 — Hardcoded `0.0.0.0` Host Binding in `uvicorn.run()`**
   - **Issue:** Hardcoding `"0.0.0.0"` prevents running the server in a hardened, localhost-only mode and trips static analysis security rules.
   - **Fix:**
     - Replaced hardcoded `host="0.0.0.0"` with configurable host resolution:
       `host = os.environ.get("HOST") or os.environ.get("BIND_ADDRESS") or "127.0.0.1"`
     - Defaults securely to `127.0.0.1` (localhost only).
     - Does **not** break Docker or LAN setups: `ecosystem.config.cjs` and `Dockerfile` pass `HOST: '0.0.0.0'` in containerized / remote production environments, while local standalone runs bind safely to `127.0.0.1`.

---

## Set 8 (`setup.py` Set 1): Path Traversal & Arbitrary Directory Creation in `setup.py`

### Vulnerability Summary
- **Classification:** CWE-22 (Path Traversal), CWE-73 (External Control of File Name or Path), CWE-20 (Improper Input Validation).
- **Location:** `setup.py` — `get_input()` and `main()` directory creation.
- **Taint Flow:** Console input `val = input(...)` (Items 1–4) $\rightarrow$ returned directly into `vol_dir` (Items 5–6) $\rightarrow$ reached directory creation SINK: `os.makedirs(vol_dir, exist_ok=True)` (Item 7).

---

### Numbered Findings & Fixes

1. **Findings 1–4 — Unvalidated Input Source in `get_input()`**
   - **Issue:** `val = input(...).strip()` did not sanitize control characters or null bytes from user-supplied input.
   - **Fix:** Implemented `sanitize_input_string()` to strip null bytes (`\0`) and non-printable control characters from all interactive user input.

2. **Findings 5–6 — Unsanitized Path Variable `vol_dir`**
   - **Issue:** The directory path for `VOLUME_DIR` was accepted as raw string and passed directly into filesystem creation functions.
   - **Fix:**
     - Created `sanitize_directory_path(p, default)` to validate against null bytes, expand environment variables and user home (`~`), and normalize to an absolute canonical path.
     - Also added `sanitize_url()` for `abs_target` and `sanitize_port()` for `PORT` and `SIDECAR_PORT` to ensure strict integer validation (1–65535).

3. **Finding 7 — Vulnerable SINK: `os.makedirs(vol_dir, exist_ok=True)`**
   - **Issue:** Direct directory creation on unvalidated input path could allow directory creation in arbitrary system locations.
   - **Fix:**
     - Sanitized `vol_dir` via `sanitize_directory_path()`.
     - Verified `not os.path.islink(vol_dir)` before calling `os.makedirs()`.

---

## Set 9: Client-Side Request Forgery (CSRF) & DOM-Based SSRF / Storage Injection (`App.tsx` & `authStorage.ts`)

### Vulnerability Summary
- **Classification:** CWE-918 (Server-Side Request Forgery / Client-Side Request Forgery), CWE-79 (Improper Neutralization of Input During Web Page Generation / DOM-Based Storage Injection), CWE-200 (Exposure of Sensitive Information / Bearer Token Exfiltration), SonarQube S5144.
- **Location:** `src/lib/authStorage.ts`, `src/App.tsx`, `src/lib/safeFetch.ts`, `src/components/CaptureView.tsx`, `src/components/SnippetsView.tsx`, `server.ts`.
- **Taint Flow:** Browser storage read `localStorage.getItem()` (Items 1–3) $\rightarrow$ extracted into `raw` and `sidecarUrl` state (Items 4–6) $\rightarrow$ concatenated into `endpoint` string (Items 7–9) $\rightarrow$ reached direct invocation SINK: `fetch(endpoint)` with `Authorization: Bearer <token>` header (Items 10–11).

---

### Numbered Findings & Fixes

1. **Findings 1–3 — Browser Storage Taint Source (`localStorage.getItem`)**
   - **Issue:** Untrusted data residing in browser storage (which can be manipulated by DOM-based XSS, browser extensions, or physical access) could contain malicious target URLs (`javascript:`, `file:`, cloud metadata endpoints such as `169.254.169.254`, or attacker-controlled servers).
   - **Fix:** Added rigorous validation in `getStoredCredentials()` in `src/lib/authStorage.ts`:
     - Enforced a maximum string length barrier (rejecting raw payloads > 4096 bytes).
     - Rejected payloads containing null bytes or non-printable ASCII control characters.
     - Sanitized `authMode` to strictly enforce `'token' | 'userpass'`.
     - Validated tokens against strict regex `^[a-zA-Z0-9_\-.~+/=]{1,512}$`.
     - Sanitized `username` by stripping HTML, quotation, and control characters.

2. **Findings 4–6 — Unsanitized Assignment to `sidecarUrl` & `serverUrl`**
   - **Issue:** An extracted URL could point to internal infrastructure, link-local addresses, or cloud instance metadata.
   - **Fix:** Enhanced `sanitizeStoredUrl()` in `src/lib/authStorage.ts`:
     - Strictly requires `http:` or `https:` protocol; immediately rejects any other scheme.
     - Prohibits embedded userinfo credentials (`user:pass@host`).
     - Explicitly blocks cloud metadata IPs and hostnames (`169.254.169.254`, `169.254.*`, `metadata.google.internal`, `metadata`, `instance-data`).
     - Enforces hostname whitelist format (`^[a-zA-Z0-9.\-_:]+$`).
     - Validates port range (1–65535).
     - Returns a clean origin (`protocol + host`) without path, query parameters, or hash fragments.

3. **Findings 7–9 — Dynamic URL Concatenation into `endpoint`**
   - **Issue:** Concatenating `sidecarUrl` with API paths (e.g. `buildSafeEndpoint(sidecarUrl, ...)` or `${sidecarUrl}/api/...`) created dynamically tainted endpoint strings.
   - **Fix:**
     - Hardened `buildSafeEndpoint()` in `src/App.tsx` to strictly return clean relative paths (`/api/...`) on the same origin, preventing cross-origin URL formation.
     - Created `safeSidecarFetch()` in `src/lib/safeFetch.ts` with `sanitizeApiPath()` to ensure all paths strictly start with `/api/`, `/bookmarks/`, or `/snippets/` and reject traversal characters (`..`, `\\`, `//`).

4. **Findings 10–11 — Vulnerable SINK: `fetch(endpoint)`**
   - **Issue:** Directly calling `fetch(endpoint)` with user credentials (`Authorization: Bearer <activeToken>`) allowed token leakage to arbitrary external destinations or intranet exploitation.
   - **Fix:**
     - Completely eliminated all raw `fetch(endpoint)` invocations across `src/App.tsx`, `src/components/CaptureView.tsx`, and `src/components/SnippetsView.tsx`.
     - Replaced with `safeSidecarFetch()`, which strictly directs traffic to:
       - Same-origin relative paths (`/api/...`) for local sidecar communication.
       - The trusted server-side proxy `/api/proxy/abs` when remote routing is needed.
     - Mounted all sidecar routes (`/api/user/bookmarks`, `/api/user/bookmarks/*`, `/api/snippet`, `/api/user/snippet`, `/api/user/sync-bookmarks`, etc.) in `server.ts` so the dashboard server directly mediates sidecar traffic.
     - `fetch()` is now strictly called with string literals (`'/api/proxy/abs'`) or validated same-origin relative paths, completely breaking the taint chain.

---

## Set 10 (`App.tsx` Set 2): Remote Response Taint & Loopback Request Forgery via `sync_state`

### Vulnerability Summary
- **Classification:** CWE-918 (Client-Side Request Forgery / SSRF), CWE-20 (Improper Input Validation), CWE-79 (DOM Injection via Untrusted API Response).
- **Location:** `src/App.tsx` — `pollStatus` real-time background polling effect.
- **Taint Flow:** Remote server response `statusData` (Item 1) $\rightarrow$ extracted into `sync_state` and `syncState` React state (Item 2) $\rightarrow$ triggered polling effect dependency where `sidecarUrl` was referenced (Items 3–4) $\rightarrow$ concatenated into `endpoint = buildSafeEndpoint(sidecarUrl, ...)` (Items 5–7) $\rightarrow$ reached vulnerable invocation SINK: `fetch(endpoint)` (Items 8–9).

---

### Numbered Findings & Fixes

1. **Finding 1 — Compromised Remote Server Response Source (`statusData`)**
   - **Issue:** Static analysis assumes that an upstream server or network attacker can inject malicious, oversized, or unescaped data into HTTP responses.
   - **Fix:** Added schema-level validation and response parsing guards: all responses are parsed via typed deserialization with fallbacks, and raw response strings are strictly decoupled from DOM insertion or URL construction.

2. **Finding 2 — Tainted Field Extraction (`sync_state`)**
   - **Issue:** `statusData.sync_state` was directly assigned to React state `setSyncState(statusData.sync_state)` without field-level sanitization, allowing contaminated strings (such as injected URLs, script tags, or corrupted numeric values) to propagate into application state and re-trigger effect hooks.
   - **Fix:** Implemented `sanitizeSyncState(raw: any)` in `src/App.tsx`:
     - Guarantees `is_syncing` is strictly a boolean.
     - Strips HTML and control characters (`[<>"']`) from `current_item`, `last_error`, and `last_synced_at`.
     - Validates date strings (`installation_date`, `cutoff_datetime`, `installed_at`) against strict regex `^[0-9T:\-.]+$`.
     - Whitelists `cutoff_mode` strictly to `('from_start' | 'custom_date' | 'from_now')`.
     - Enforces finite numbers on counters (`total_synced`, `skipped_before_cutoff`, `skipped_tombstoned`).

3. **Findings 3 & 4 — State Dependency Propagation (`sidecarUrl` & `syncState?.is_syncing`)**
   - **Issue:** Tainted state updates triggered the `useEffect` hook, which read `sidecarUrl` and passed it into downstream fetch operations.
   - **Fix:** Sanitized all dependencies and decoupled background sync polling from external dynamic URL manipulation.

4. **Findings 5, 6, 7 — Dynamic Endpoint Concatenation (`endpoint`)**
   - **Issue:** `const endpoint = buildSafeEndpoint(sidecarUrl, ...)` constructed a path that incorporated external variables.
   - **Fix:**
     - Enforced that `buildSafeEndpoint` returns strictly same-origin relative paths (`/api/...`).
     - Refactored `pollStatus` and `syncUserBookmarks` to use `safeSidecarFetch('/api/user/bookmarks/status')` and `safeSidecarFetch('/api/user/bookmarks')`, eliminating string concatenation.

5. **Findings 8 & 9 — Vulnerable SINK: `fetch(endpoint)`**
   - **Issue:** `fetch(endpoint)` could be dispatched to an attacker-controlled address if `endpoint` was manipulated.
   - **Fix:**
     - Eliminated `fetch(endpoint)` in `pollStatus` and all bookmark sync handlers.
     - Replaced with `safeSidecarFetch()`, which strictly invokes `/api/proxy/abs` with JSON payload or safe same-origin relative paths `/api/...`.
     - Also sanitized `statusData.recent` events: validated `eventId` with `^[a-zA-Z0-9_\-]+$` and stripped HTML characters from `book_title` before showing notifications.

---

## Set 11: Client-Side Request Forgery via Server URL in `absClient.ts` & `App.tsx`

### Vulnerability Summary
- **Classification:** CWE-918 (Server-Side Request Forgery / Client-Side Request Forgery), CWE-20 (Improper Input Validation), CWE-200 (Credential Exfiltration).
- **Location:** `src/lib/absClient.ts` (`absFetch`, `normalizeServerUrl`, `authenticateAbs`) and `src/App.tsx` (`/api/config` loader).
- **Taint Flow:** Remote server config response `/api/config` (Item 1) $\rightarrow$ extracted into `defaultAbsUrl` / `absTargetServer` $\rightarrow$ assigned to `detected` $\rightarrow$ `initialServer` $\rightarrow$ `targetServerToUse` (Items 2–7) $\rightarrow$ passed to `authenticateAbs(targetServerToUse, ...)` (Items 8–9) $\rightarrow$ `serverUrl` in `absClient.ts` (Items 10–12) $\rightarrow$ `normalizeServerUrl` $\rightarrow$ `cleanUrl` (Items 13–16) $\rightarrow$ concatenated into `${cleanUrl}/login` (Items 17–20) $\rightarrow$ passed as `targetUrl` into `absFetch` (Item 21) $\rightarrow$ reached direct browser invocation SINKs: `fetch(targetUrl)` (Items 22–23).

---

### Numbered Findings & Fixes

1. **Findings 1–5 — Remote Config Taint Source (`/api/config` $\rightarrow$ `defaultAbsUrl` $\rightarrow$ `detected` $\rightarrow$ `initialServer`)**
   - **Issue:** An attacker on the local network or a compromised server could return an unvalidated, malicious server URL in `/api/config`.
   - **Fix:** Applied `sanitizeStoredUrl()` to every field received from `/api/config` (`cfg.defaultAbsUrl`, `cfg.absTargetServer`, `cfg.sidecarUrl`), guaranteeing that only well-formed `http:` / `https:` origins without control characters or cloud metadata addresses are accepted.

2. **Findings 6–9 — Propagation through `targetServerToUse` to `authenticateAbs()`**
   - **Issue:** `targetServerToUse` was passed directly to `authenticateAbs` without enforcing canonical URL sanitization.
   - **Fix:** Enforced `sanitizeStoredUrl(initialServer || savedServer || '')` before passing to `authenticateAbs` and setting React state.

3. **Findings 10–16 — Internal Parameter Flow in `absClient.ts` & `normalizeServerUrl()`**
   - **Issue:** `normalizeServerUrl(url)` accepted any arbitrary string and simply stripped trailing slashes, allowing directory traversal or cloud metadata injection.
   - **Fix:** Integrated `sanitizeStoredUrl` directly into `normalizeServerUrl()`:
     - Validates scheme strictly to `http:` or `https:`.
     - Prohibits embedded credentials (`user:pass@`).
     - Blocks cloud metadata endpoints (`169.254.169.254`, `metadata.google.internal`).
     - Validates hostname and port range (1–65535).
     - Returns clean origin (`protocol + host`).

4. **Findings 17–21 — URL Concatenation into `targetUrl` (`${cleanUrl}/login`, etc.)**
   - **Issue:** String interpolation concatenated `cleanUrl` with endpoints and passed them to `absFetch(targetUrl, ...)`.
   - **Fix:** Validated `targetUrl` at entry of `absFetch` with explicit protocol and metadata containment checks before request forwarding.

5. **Findings 22–23 — Vulnerable SINK: Direct Browser `fetch(targetUrl)`**
   - **Issue:** In `absFetch`, fallback branches attempted direct browser `fetch(targetUrl)` (lines ~125 and ~150), allowing an attacker controlling the server URL to trigger Client-Side Request Forgery and exfiltrate user credentials.
   - **Fix:**
     - Completely eliminated all direct `fetch(targetUrl)` calls from `src/lib/absClient.ts`.
     - All Audiobookshelf communication is routed strictly through the local server proxy `/api/proxy/abs` via `fetch('/api/proxy/abs', { method: 'POST', body: JSON.stringify({ targetUrl: cleanTarget, ... }) })`.
     - `fetch()` is now strictly called with the constant string literal `'/api/proxy/abs'`, fully terminating the taint flow.

---

## Set 12 (`absClient.ts` Set 2): Direct Fetch Fallback Elimination (`fetch(targetUrl)` at Line 150)

### Vulnerability Summary
- **Classification:** CWE-918 (Client-Side Request Forgery / SSRF), CWE-200 (Token Exfiltration).
- **Location:** `src/lib/absClient.ts` — `absFetch()` non-proxy direct fallback.
- **Taint Flow:** Same upstream flow as Set 11 (`/api/config` $\rightarrow$ `initialServer` $\rightarrow$ `targetServerToUse` $\rightarrow$ `authenticateAbs` $\rightarrow$ `normalizeServerUrl` $\rightarrow$ `targetUrl`), but branching to the second direct fallback SINK at line 150 (`await fetch(targetUrl, { method, headers, body })`).

---

### Numbered Findings & Fixes

1. **Findings 1–21 — Common Upstream Taint Flow**
   - **Trace:** Same source, extraction, and concatenation path as Set 11 items 1–21.
   - **Status:** Hardened via `sanitizeStoredUrl()` in `App.tsx` and `normalizeServerUrl()` in `absClient.ts`.

2. **Findings 22–23 — Second Vulnerable SINK: Direct `fetch(targetUrl)` (Line 150)**
   - **Issue:** The fallback branch executed when `useProxy: false` or when direct browser access was attempted invoked `fetch(targetUrl)`. This permitted direct cross-origin HTTP requests with sensitive authorization tokens (`Authorization: Bearer <token>`).
   - **Fix:**
     - Removed the entire direct `fetch(targetUrl)` branch from `absFetch()`.
     - Enforced that 100% of Audiobookshelf API calls are routed via the local backend proxy `/api/proxy/abs`.
     - Ensured `fetch()` only ever receives the constant string literal `'/api/proxy/abs'`. Both sink branches (formerly lines 125 and 150) are completely eliminated from the codebase.

---

## Set 13: Async Event Loop Non-Blocking I/O & Background Task Retention in `main.py`

### Vulnerability & Reliability Summary
- **Classification:** SonarQube python:S6929 / Ruff RUF006 (Asyncio Task Premature Garbage Collection), SonarQube python:S6924 / python:S6925 (Synchronous File Operations in Async Functions), SonarQube python:S6926 (Synchronous HTTP Client in Async Functions), CWE-400 (Uncontrolled Resource Consumption / Event Loop Starvation).
- **Location:** `main.py` — `lifespan`, `_schedule_sync`, `trigger_bookmark_sync`, `get_user_bookmarks`, `create_snippet_or_bookmark`, `expand_or_update_snippet`, `update_cutoff_configuration`, `export_book_snippets`, `delete_user_bookmark`, `render_extractor_dashboard`.
- **Issues Addressed:**
  1. `asyncio.create_task()` invoked without retaining a strong reference, risking task cancellation mid-flight during Python garbage collection sweeps.
  2. Synchronous blocking file I/O (`open()`) inside `async def` endpoints, blocking the FastAPI asyncio event loop and degrading server responsiveness.
  3. Synchronous HTTP request (`_http_session.delete`) executed directly inside an `async def` handler.

---

### Numbered Findings & Fixes

1. **Findings 1, 2, 3 (Lines 799, 802, 803 in original `lifespan`) — Unreferenced Background Tasks in Server Startup**
   - **Issue:** `asyncio.create_task(...)` was called for Whisper model warmup, bookmark sync daemon, and Socket.IO real-time listener without saving the task instances into variables or retaining strong references. Under memory pressure, Python GC sweeps can collect tasks with only weak event loop references, halting background syncing silently.
   - **Fix:** Assigned each task to a named variable (`warmup_task`, `sync_daemon_task`, `socket_listener_task`) and routed creation through `safe_create_background_task(coro, name=...)` which adds each active task to `_background_tasks: set[asyncio.Task]` with a `task.add_done_callback(_background_tasks.discard)` lifecycle cleanup.

2. **Finding 4 (Line 3014 in original `main.py` / `trigger_bookmark_sync` & `_schedule_sync`) — Unreferenced Trigger & Debounce Tasks**
   - **Issue:** Background synchronization dispatch in `trigger_bookmark_sync()` called `asyncio.create_task(asyncio.to_thread(run_bookmark_sync_cycle...))` without retaining a reference.
   - **Fix:** Assigned the returned task to `sync_cycle_task = safe_create_background_task(...)`, ensuring it is strongly held until execution completes. Also updated `self._debounce_task` in `_schedule_sync()` to use `safe_create_background_task()`.

3. **Findings 5 & 6 (Line 3084 in original `main.py` / `create_snippet_or_bookmark`) — Blocking Synchronous Execution in Async Endpoint**
   - **Issue:** `create_snippet_or_bookmark()` is an `async def` FastAPI route that called CPU/IO-heavy `process_bookmark_extraction()` synchronously on the event loop thread.
   - **Fix:** Wrapped the extraction in `result = await asyncio.to_thread(process_bookmark_extraction, ...)`, offloading all audio decoding, Whisper inference, and file writes to worker threads.

4. **Finding 7 (Line 3260 in original `main.py` / `expand_or_update_snippet`) — Synchronous File Lookup & Extraction in Async Endpoint**
   - **Issue:** `expand_or_update_snippet()` read existing metadata from disk and executed `process_bookmark_extraction()` synchronously on the main asyncio event loop.
   - **Fix:** Offloaded both `safe_read_json_file()` and `process_bookmark_extraction()` via `await asyncio.to_thread(...)`.

5. **Findings 8 & 9 (Line 3450 in original `main.py` / `get_user_bookmarks`) — Synchronous `open()` in Async Bookmarks Endpoint**
   - **Issue:** Iterating through user bookmark files used synchronous `with open(json_path, "r")` and `with open(md_path, "r")` directly inside `async def get_user_bookmarks()`, causing event loop starvation on large libraries.
   - **Fix:** Replaced blocking `open()` calls with `async with aiofiles.open(...) as jf: raw_json = await jf.read()` and `async with aiofiles.open(...) as f: raw_md = await f.read()`. Added `aiofiles>=23.2.1` to `requirements.txt` and implemented an async file context shim fallback for environments without pre-installed packages.

6. **Finding 10 (Line 3604 in original `main.py` / `delete_user_bookmark`) — Synchronous `open()` in Async Deletion Endpoint**
   - **Issue:** Reading companion JSON metadata prior to unlinking in `async def delete_user_bookmark()` invoked synchronous `open(json_candidate, "r")`.
   - **Fix:** Replaced with asynchronous `async with aiofiles.open(json_candidate, "r", encoding="utf-8") as jf: raw_json = await jf.read()`.

7. **Finding 11 (Line 3604 in original `main.py` / `delete_user_bookmark`) — Synchronous HTTP Client in Async Endpoint**
   - **Issue:** Notifying the upstream Audiobookshelf server of bookmark deletion called synchronous `_http_session.delete(...)` inside `async def delete_user_bookmark()`.
   - **Fix:** Migrated to asynchronous `httpx.AsyncClient`:
     `async with httpx.AsyncClient(timeout=4.0) as client: await client.delete(abs_del_url, headers={"Authorization": f"Bearer {raw_token}"})`
     with thread-delegated fallback shim support.

8. **Finding 12 (Line 3871 in original `main.py` / `export_book_snippets` & `render_extractor_dashboard`) — Synchronous `open()` in Async View Endpoints**
   - **Issue:** `export_book_snippets()` and `render_extractor_dashboard()` opened Markdown transcripts synchronously with `with open(md_path, "r")`.
   - **Fix:** Converted note reads across `export_book_snippets()` and `render_extractor_dashboard()` to `async with aiofiles.open(...) as f: raw_md = await f.read()`. Also ensured `update_cutoff_configuration()` offloads disk persistence via `await asyncio.to_thread(save_installation_config, updated_config)`.

---

## Set 14: Bash Conditional Test Hardening in `setup.sh`

### Vulnerability & Quality Summary
- **Classification:** ShellCheck SC2292 / SC3010 (Prefer `[[` over `[` in bash scripts), Defensive Programming.
- **Location:** `setup.sh` — Lines 40, 43, 47, 65, 92, 118, 129, 131, 198, 210, 221, 243.
- **Issue:** Using legacy POSIX single brackets `[` inside Bash scripts is subject to unexpected word splitting, unhandled pathname expansions, and syntax errors if variables are unset or contain spaces or special characters.

---

### Numbered Findings & Fixes

1. **Finding 1 (Line 40):** Replaced `if [ -f "$ENV_FILE" ];` with `if [[ -f "$ENV_FILE" ]];`.
2. **Finding 2 (Line 43):** Replaced `while ... || [ -n "$key" ];` with `while ... || [[ -n "$key" ]];`.
3. **Finding 3 (Line 47):** Replaced `[ -z "$key" ] && continue` with `[[ -z "$key" ]] && continue`.
4. **Finding 4 (Line 65):** Replaced `if [ -n "$EXISTING_ABS_SERVER" ] && [[ ... ]];` with combined `if [[ -n "$EXISTING_ABS_SERVER" && "$EXISTING_ABS_SERVER" == *"abs.example.com"* ]];`.
5. **Finding 5 (Line 92):** Replaced `if [ ! -d "$VOLUME_DIR" ];` with `if [[ ! -d "$VOLUME_DIR" ]];`.
6. **Finding 6 (Line 118):** Replaced `if [ "$SIDECAR_PORT" = "13378" ];` with `if [[ "$SIDECAR_PORT" == "13378" ]];`.
7. **Finding 7 (Line 129):** Replaced `if [ "$WEB_PORT" = "13378" ];` with `if [[ "$WEB_PORT" == "13378" ]];`.
8. **Finding 8 (Line 131):** Replaced `elif [ "$WEB_PORT" = "$SIDECAR_PORT" ];` with `elif [[ "$WEB_PORT" == "$SIDECAR_PORT" ]];`.
9. **Finding 9 (Line 198):** Replaced `if [ -f "$GITIGNORE_FILE" ];` with `if [[ -f "$GITIGNORE_FILE" ]];`.
10. **Finding 10 (Line 210):** Replaced `if [ ! -f "$VENV_DIR/bin/python3" ];` with `if [[ ! -f "$VENV_DIR/bin/python3" ]];`.
11. **Finding 11 (Line 221):** Replaced `if [ -f "$VENV_DIR/bin/python3" ];` with `if [[ -f "$VENV_DIR/bin/python3" ]];`.
12. **Finding 12 (Line 243):** Replaced `if [ -f "$SCRIPT_DIR/init_installation_date.py" ];` with `if [[ -f "$SCRIPT_DIR/init_installation_date.py" ]];`.

---

## Set 15: Bash Conditional Test Hardening in `update.sh`

### Vulnerability & Quality Summary
- **Classification:** ShellCheck SC2292 / SC3010 (Prefer `[[` over `[` in bash scripts), Defensive Programming.
- **Location:** `update.sh` — Lines 51, 65, 80, 93.
- **Issue:** Single bracket test operators in update orchestration script risked parsing failures upon encountering empty variables or unquoted glob strings.

---

### Numbered Findings & Fixes

1. **Finding 1 (Line 51):** Replaced `if [ ! -f "$VENV_DIR/bin/python3" ];` with `if [[ ! -f "$VENV_DIR/bin/python3" ]];`.
2. **Finding 2 (Line 65):** Replaced `if [ -f "$VENV_DIR/bin/python3" ];` with `if [[ -f "$VENV_DIR/bin/python3" ]];`.
3. **Finding 3 (Line 80):** Replaced `if [ -f "$SCRIPT_DIR/.env" ];` with `if [[ -f "$SCRIPT_DIR/.env" ]];`.
4. **Finding 4 (Line 93):** Replaced `if [ -f "$SCRIPT_DIR/init_installation_date.py" ];` with `if [[ -f "$SCRIPT_DIR/init_installation_date.py" ]];`.

---

## Set 16: Docker Supply Chain Hardening & Binary Wheel Enforcement in `Dockerfile`

### Vulnerability Summary
- **Classification:** CWE-829 (Inclusion of Functionality from Untrusted Control Sphere), CWE-494 (Download of Code Without Integrity Check), SonarQube docker:S6586, docker:S6587, Hadolint DL3013.
- **Location:** `Dockerfile` (Line 18) and `requirements.txt`.
- **Issues Addressed:**
  1. **Build-Time Arbitrary Code Execution via Setup Scripts:** Installing Python packages from source distributions (sdists) executes untrusted `setup.py` scripts with root privileges inside the container during build.
  2. **Unpinned Dependency Ranges:** Loose version constraints (`>=`) risk breaking builds and open vectors for malicious dependency updates (supply chain compromise).

---

### Numbered Findings & Fixes

1. **Finding 1 (Line 18) — Enforce Binary Wheels (`--only-binary :all:`):**
   - **Issue:** Without `--only-binary :all:`, pip can fallback to downloading source distributions and executing arbitrary Python code in `setup.py` at Docker build time.
   - **Fix:** Updated the pip installation step in `Dockerfile` to:
     `RUN pip install --no-cache-dir --only-binary :all: -r requirements.txt`
     guaranteeing that pip only installs pre-compiled binary `.whl` packages without running setup scripts.

2. **Finding 2 (Line 18 / `requirements.txt`) — Dependency Version Pinning:**
   - **Issue:** Using unpinned minimum versions (`>=`) allowed non-deterministic dependency resolution.
   - **Fix:** Pinned all resolved package versions to exact releases in `requirements.txt` (`fastapi==0.110.0`, `uvicorn[standard]==0.28.0`, `requests==2.31.0`, `httpx==0.27.0`, `websockets==12.0`, `faster-whisper==1.0.0`, `vosk==0.3.45`, `python-multipart==0.0.9`, `jinja2==3.1.3`, `imageio-ffmpeg==0.4.9`, `aiofiles==23.2.1`), guaranteeing reproducible and secure builds.
---

## Sets 17-21: Filesystem Oracle Remediation in `main.export_book_snippets()`

### Vulnerability Summary
- **Classification:** CWE-209 (Information Exposure via Error Messages / Filesystem Oracle), CWE-200 (Exposure of Sensitive Information), CWE-22 (Path Traversal), CWE-20 (Improper Input Validation).
- **Location:** `main.py` - `export_book_snippets()` (`/api/export-book`, `/api/user/bookmarks/export-book`, `/api/snippets/export-book`, `/api/book/export`).
- **Nature of Vulnerability (Filesystem Oracle):**
  When untrusted input (`book_title: str = Query(...)`) was directly concatenated into filesystem paths (`cand1`, `cand2`) and tested against the operating system via `os.path.isdir()`, `os.path.realpath()`, `os.listdir()`, and `os.path.isfile()`, an attacker could submit probed directory or file names and observe differences in HTTP status codes, exceptions, or response timings. This transformed the export endpoint into a side-channel "Filesystem Oracle", allowing remote enumeration of host directory structures even if file reads were blocked.

---

### Numbered Findings and Fixes Across Sets 17-21

1. **Set 17 - Probing SINK on `cand2` / `cand1` (Items 1-4, Lines 3451 & 3511-3512)**
   - **Taint Flow:** HTTP Query `book_title` (Source) -> concatenated into candidate path `cand2` / `cand1` -> tested via `os.path.isdir()` / `os.path.exists()` (SINK).
   - **Fix:**
     - Created `validate_and_sanitize_export_book_title(raw_title: Optional[str]) -> str` with strict bounds (1-200 characters), character whitelist regex (`^[a-zA-Z0-9_\- .',!:?()\[\]]+$`), and explicit rejection of path separators (`/`, `\`, `\0`, `..`, control characters).
     - Completely eliminated `cand1` and `cand2` direct path concatenation expressions. User input is never joined with server roots to construct query paths.

2. **Set 18 - Directory Existence Probing on `book_dir_path` (Item 5, Line 3547)**
   - **Taint Flow:** Untrusted `book_title` -> assigned to `book_dir_path` -> probed via `os.path.isdir(book_dir_path)` (SINK).
   - **Fix:** Decoupled book directory resolution from user input. Book directories are now discovered strictly by server-side enumeration (`os.listdir(real_p)`):
     - Every candidate directory originates 100% from trusted server filesystem metadata (`os.listdir(real_p)`).
     - The sanitized user input is used only for string comparison (`clean_entry == target_clean`), never as a filesystem path argument.
     - `book_dir_path` is assigned exclusively from the matching entry found in the enumerated list, completely cutting off the taint flow to `os.path.isdir()`.

3. **Set 19 - Directory Canonicalization on `book_dir_path` (Item 5, Line 3551)**
   - **Taint Flow:** Untrusted `book_title` -> `book_dir_path` -> canonicalized via `os.path.realpath(book_dir_path)` (SINK).
   - **Fix:** Because `book_dir_path` is derived from enumerated subdirectories within `real_p`, it is pre-canonicalized and verified with `os.path.commonpath([real_p, entry_candidate]) == real_p` before selection, ensuring that `os.path.realpath()` only ever processes verified internal entries.

4. **Set 20 - Directory Enumeration on `real_book_dir` (Item 5, Line 3576)**
   - **Taint Flow:** Untrusted `book_title` -> `book_dir_path` -> `real_book_dir` -> passed to `os.listdir(real_book_dir)` (SINK).
   - **Fix:** With `real_book_dir` assigned exclusively from the safe directory enumeration whitelist (and confirmed inside `candidate_roots`), `os.listdir(real_book_dir)` is isolated from user-controlled paths.

5. **Set 21 - File Status Inspection on `fpath` (Items 5-7, Line 3577)**
   - **Taint Flow:** `real_book_dir` -> joined with child name into `fpath` -> inspected via `os.path.isfile(fpath)` (SINK).
   - **Fix:** Every child filename is constrained with `os.path.basename()`, verified with `os.path.commonpath([real_book_dir, fpath]) == real_book_dir`, and checked with `not os.path.islink(fpath)` before archiving or async file reading.
---

## Set 22: API Path Traversal & Unsanitized Upstream Request Remediation in `main.delete_user_bookmark()`

### Vulnerability Summary
- **Classification:** CWE-22 (Improper Limitation of a Pathname to a Restricted Directory / API Route Traversal), CWE-918 (Server-Side Request Forgery / Upstream Injection), CWE-20 (Improper Input Validation).
- **Location:** `main.py` - `delete_user_bookmark()` (`/api/user/bookmarks/{snippet_id:path}`, `/api/snippets/{snippet_id:path}`).
- **Taint Flow:** HTTP Request Query parameter `request.query_params.get("library_item_id")` (Source, Items 1-5) -> assigned to `req_lib_id` -> propagated to `final_lib_id` (Items 6-7) -> concatenated into upstream URL `abs_del_url = f"{server_url}/api/me/bookmark/{final_lib_id}/{int(final_time)}"` -> dispatched via HTTP client `client.delete(abs_del_url)` (SINK, Items 8-11).
- **Impact:** An attacker supplying directory traversal sequences (such as `../../`) or URL-encoded path segments in the `library_item_id` query parameter could manipulate the upstream URL path, triggering unintended deletions across arbitrary API endpoints on the Audiobookshelf server (API Traversal / SSRF).

---

### Numbered Findings and Fixes

1. **Findings 1-5 (Lines 3606-3624 in original `main.py` / `query_params` Extraction)**
   - **Issue:** `req_lib_id = request.query_params.get("library_item_id")` accepted arbitrary untrusted string values without sanitization.
   - **Fix:** Implemented `validate_and_sanitize_library_item_id(lib_id: Optional[Any]) -> Optional[str]`:
     - Strictly validates input against length bounds (1-128 characters).
     - Prohibits path separators (`/`, `\`), traversal sequences (`..`), URL encoding characters (`%`), null bytes (`\0`), and query/hash characters (`?`, `#`, `&`).
     - Enforces a strict character whitelist regex `^[A-Za-z0-9_\-]+$` matching standard Audiobookshelf item UUIDs and identifiers.
     - Confirms `os.path.basename(clean) == clean`.
     - Also sanitized `req_book_title`, `req_created_at`, and `req_timestamp` against null bytes, HTML, and traversal characters.

2. **Findings 6-7 (Line 3730 in original `main.py` / `final_lib_id` Assignment)**
   - **Issue:** `final_lib_id = req_lib_id or found_metadata.get("library_item_id")` combined query parameter and file metadata without defensive boundary verification.
   - **Fix:** Both `req_lib_id` and `found_metadata.get("library_item_id")` are filtered through `validate_and_sanitize_library_item_id()`, ensuring `final_lib_id` is guaranteed to be clean or `None`.

3. **Findings 8-11 (Lines 3765-3766 in original `main.py` / URL Concatenation & Invocations SINK)**
   - **Issue:** Direct string interpolation `f"{server_url}/api/me/bookmark/{final_lib_id}/{int(final_time)}"` into `abs_del_url` was passed to the HTTP deletion SINK.
   - **Fix:**
     - Applied defensive re-validation `safe_lib_id = validate_and_sanitize_library_item_id(final_lib_id)`.
     - Validated that `final_time` is a safe non-negative integer (`0 <= clean_time_int <= 100000000`).
     - Strictly URL-encoded `safe_lib_id` with `quote(safe_lib_id, safe="")`, ensuring that even if unexpected characters were present they cannot escape the path segment.
     - Parsed the constructed URL with `urlsplit(abs_del_url)` and verified that `parsed_url.path.startswith(f"/api/me/bookmark/{quoted_lib_id}/")` and contains no `..` tokens before dispatching `await client.delete()`.

---

## Sets 23-25: Filesystem Oracle Remediation in `main.serve_bookmark_file()`

### Vulnerability Summary
- **Classification:** CWE-209 (Information Exposure via Error Messages / Filesystem Oracle), CWE-200 (Exposure of Sensitive Information), CWE-22 (Improper Limitation of a Pathname to a Restricted Directory / Path Traversal), CWE-20 (Improper Input Validation).
- **Location:** `main.py` — `serve_bookmark_file()` (`/bookmarks/{username}/{book_title}/{filename}`, `/snippets/{username}/{book_title}/{filename}`).
- **Nature of Vulnerability (Filesystem Oracle):**
  When HTTP request path parameters (`filename`, `username`, `book_title`) were received, `filename` was processed with `safe_filename = os.path.basename(filename)`. Despite taking the basename, `safe_filename` remained tainted user input. It was then concatenated into filesystem query paths (`p = os.path.join(...)`) and probed against the local filesystem using `os.path.isfile(p)` across three distinct code branches:
  1. **Set 23 (Original Lines 3807-3808):** Primary bookmarks branch `os.path.join(root, u, "bookmarks", safe_book_title, safe_filename)` -> `os.path.isfile(p)`.
  2. **Set 24 (Original Lines 3819-3820):** Direct user branch `os.path.join(root, u, safe_book_title, safe_filename)` -> `os.path.isfile(p)`.
  3. **Set 25 (Original Lines 3827-3828):** Snippets fallback branch `os.path.join(root, "snippets", u, safe_book_title, safe_filename)` -> `os.path.isfile(p)`.
  An attacker could craft HTTP requests with arbitrary candidate filenames or probe names to observe whether files exist on the host filesystem via differences in HTTP 200 vs 404 responses or response timings, creating a side-channel Filesystem Oracle.

---

### Numbered Findings and Fixes Across Sets 23-25

1. **Set 23: Primary Bookmarks Branch Probing SINK (Items 1-6, Original Lines 3783, 3791, 3807-3808)**
   - **Taint Flow:** HTTP Request parameter `filename` (Source) -> assigned to `safe_filename` via `os.path.basename()` -> joined into `p = os.path.join(root, u, "bookmarks", safe_book_title, safe_filename)` -> tested via `os.path.isfile(p)` (SINK).
   - **Fix:**
     - Created `validate_and_sanitize_serve_filename(raw_filename: Optional[str]) -> str` with strict bounds (1-255 chars), rejection of path separators (`/`, `\`, null bytes, control chars), directory navigation tokens (`..`), leading dots, and enforcement of a strict whitelist regex `^[a-zA-Z0-9_\- .',!:?()\[\]]+\.(mp3|md|json|txt)$`.
     - Created `validate_and_sanitize_serve_username(raw_username: Optional[str]) -> str` enforcing length bounds, separator rejection, and whitelist regex `^[a-zA-Z0-9_\- .@]+$`.
     - Validated `book_title` using `validate_and_sanitize_export_book_title()`.
     - Completely eliminated path concatenation expression `p = os.path.join(root, u, "bookmarks", safe_book_title, safe_filename)`. User input is never joined with server roots to construct query paths or probe the filesystem.
     - Book directories and files are discovered strictly via server-side directory enumeration (`os.listdir()`). Candidate files are enumerated from verified folders, canonicalized with `os.path.realpath()`, verified with `os.path.commonpath()`, and compared against `target_filename` strictly as an in-memory string comparison (`clean_f_entry.lower() == target_filename.lower()`). The tainted variable `p` and direct SINK call are 100% eliminated.

2. **Set 24: Direct Book Folder Branch Probing SINK (Items 1-6, Original Lines 3783, 3791, 3819-3820)**
   - **Taint Flow:** HTTP Request parameter `filename` (Source) -> assigned to `safe_filename` -> joined into `p = os.path.join(root, u, safe_book_title, safe_filename)` -> tested via `os.path.isfile(p)` (SINK).
   - **Fix:**
     - Completely eliminated `p = os.path.join(root, u, safe_book_title, safe_filename)` and the corresponding `os.path.isfile(p)` call.
     - Direct book folder structures are now discovered and verified through the unified server-side enumeration loop over candidate parent directories, ensuring that all candidate paths originate 100% from trusted directory listings rather than user input.

3. **Set 25: Snippets Fallback Branch Probing SINK (Items 1-6, Original Lines 3783, 3791, 3827-3828)**
   - **Taint Flow:** HTTP Request parameter `filename` (Source) -> assigned to `safe_filename` -> joined into `p = os.path.join(root, "snippets", u, safe_book_title, safe_filename)` -> tested via `os.path.isfile(p)` (SINK).
   - **Fix:**
     - Completely eliminated `p = os.path.join(root, "snippets", u, safe_book_title, safe_filename)` and the corresponding `os.path.isfile(p)` call.
     - The snippets candidate root is incorporated into the safe directory enumeration set, and matched files are returned via `FileResponse` using exclusively the verified server-enumerated path (`matched_file_path`) and clean filename (`matched_filename`).
