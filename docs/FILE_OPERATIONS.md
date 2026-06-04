# File Operations (M2.1)

MimOSA's first **system integration** skill. It lets the user search, open,
create, move, and delete files and folders by voice — while a strict safety
layer guarantees the assistant can never touch system-critical paths or anything
outside the user's home sandbox.

This document complements:

- [`VOICE_PIPELINE.md`](VOICE_PIPELINE.md) — how speech becomes text.
- [`INTENT_SYSTEM.md`](INTENT_SYSTEM.md) — how text is classified and routed.

---

## 1. Where it sits in the pipeline

```
 mic ─▶ STT ─▶ IntentRouter ─▶ FileOperationsSkill ─▶ filesystem (local)
                                      │
                                      └─▶ file_safety (validate every path)
```

The router classifies file commands locally (regex Tier 1, **zero LLM calls**)
and dispatches to `FileOperationsSkill`. The skill performs the work with
`pathlib` / `shutil`, asking `file_safety` to approve **every** path first.

**Privacy:** every operation is 100 % on-device. The skill sets
`uses_llm = False`; nothing about your files is ever sent to the cloud. File
*contents* are only read when you explicitly request a search preview.

---

## 2. Components

| File | Responsibility |
|------|----------------|
| `mimosa/skills/file_ops.py` | `FileOperationsSkill` — NL parsing + the five operations |
| `mimosa/system/file_safety.py` | Path validation, sandbox, blacklist, sensitivity checks |

### `FileOperationsSkill`

Two layers:

1. **Natural-language entry point** — `handle(text)` (called by the router).
   Detects the operation verb (find / open / create / move / delete), extracts
   the target(s), and dispatches.
2. **Programmatic API** — precise, directly testable methods:
   - `search_files(pattern, file_type=None, root=None, limit=None, with_preview=False)`
   - `open_path(raw_path)`
   - `create_file(raw_path, content="")` / `create_directory(raw_path)`
   - `move_path(raw_src, raw_dst, overwrite=False)`
   - `delete_path(raw_path, permanent=False)`

The desktop opener (`xdg-open`) and the Trash backend (`send2trash`) are
**injectable** constructor arguments, which is what keeps the test-suite
hermetic (no real apps launched, no files lost).

---

## 3. Operations

### Search

- Case-insensitive substring match on the file **name**.
- Optional **file-type category** filter — spoken words like *photos*, *songs*,
  *documents* map to extension sets in `FILE_TYPE_EXTENSIONS`
  (`documents`, `pdfs`, `images`, `audio`, `video`, `spreadsheets`,
  `presentations`, `archives`, `code`).
- Walks only inside the sandbox, **skips hidden/dotfile directories**, returns
  the top *N* (default 10) hits sorted newest-modified first, each with size and
  modification date. Optional first-line **content preview** for text files.

### Open

- Validates the path, confirms it exists, then launches it with the desktop
  default app via `xdg-open`. A non-zero return code becomes a spoken error.

### Create

- `create_directory` makes an empty dir (parents included); `create_file`
  writes an optional content string. Both refuse to overwrite an existing entry.

### Move / rename

- Moves or renames with **conflict detection**. If the destination exists, the
  NL layer turns it into a confirmation prompt (overwrite? yes/no). Moving into
  an existing directory is supported ("move a.txt to Documents").

### Delete

- **Trash by default** (`send2trash`) so deletions are recoverable; **permanent**
  deletion is opt-in ("permanently delete …"). Both are **two-step**: MimOSA
  describes the action and waits for confirmation.

---

## 4. Safety model (`file_safety.py`)

Every path flows through `validate_path()` → a `SafetyDecision`:

1. **Resolve fully** (`Path.resolve`) — expands `~`, env vars, and collapses
   `..`, defeating traversal and symlink escapes.
2. **Blacklist check** — reject anything under `/etc`, `/bin`, `/sbin`, `/usr`,
   `/lib*`, `/sys`, `/proc`, `/dev`, `/boot`, `/root`, `/run`, `/var`, `/srv`,
   `/opt`. Matched as path *prefixes* (so `/etcetera` is fine).
3. **Sandbox check** — must be within an allowed root: `$HOME` plus `/tmp`,
   `/mnt`, `/media/$USER`, `/run/media/$USER` (when they exist).
4. **Sensitivity flag** — paths under `~/.ssh`, `~/.gnupg`, `~/.config`,
   `~/.aws`, `~/.kube`, … are flagged so destructive ops demand extra care.

`MIMOSA_FILE_ROOT` overrides the sandbox root to a single directory — used by
the test suite to make every test hermetic.

A disallowed path never raises during normal use: `validate_path` returns
`allowed=False` with a **speakable** `message`, so the assistant responds
naturally ("That's a protected system directory, so I can't touch it.").

---

## 5. Confirmation flow

Destructive actions (delete, overwriting move) are queued as a `_PendingAction`
and the skill returns a yes/no prompt. Because the router is otherwise
stateless, `BaseSkill.has_pending_confirmation()` lets the **router route the
next utterance straight back** to the skill — so a bare "yes" or "no" resolves
the prompt instead of being re-classified as a new intent.

```
User: "delete old-notes.txt"
MimOSA: "I'm about to move the file old-notes.txt to the Trash. Say 'yes' to confirm or 'no' to cancel."
User: "yes"
MimOSA: "I've moved old-notes.txt to the Trash. You can recover it if needed."
```

---

## 6. Intent routing

`file_ops` is a Tier-1 (regex) intent — handled locally with **no LLM call**.
Patterns in `intent_router.py` (`_FILE_PATTERNS`) catch:

- create/make a file|folder|directory (incl. "called/named X"),
- any file verb that mentions a file/folder noun or a token with an extension,
- find/search/list a file-type category,
- "move/rename X to Y".

These run **before** the question-shape heuristic so "where is my budget file"
routes to files, not to the question skill.

---

## 7. Testing

`tests/test_file_ops.py` (74 tests) covers the safety layer, each operation,
NL parsing edge cases, confirmation/cancel flows, and router integration. Every
test redirects the sandbox to a `tmp_path` and injects fake opener/trash
backends, so the suite is fully offline and never touches real user files.

```bash
pytest -q tests/test_file_ops.py
```

---

## 8. Future enhancements

- Recursive content search (grep-like) with previews.
- Disk-usage / "what's taking up space" queries.
- Batch operations ("delete all screenshots older than a month") — will reuse
  the same confirmation + safety machinery.
