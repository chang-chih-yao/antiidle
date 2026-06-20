# Development Guidelines

For detailed architecture, data model, validation rules, and keyboard shortcuts, see `README.md`.

## Environment

- Platform: Windows 10 / 11 only
- Python: 3.12+
- Package manager: `uv`
- Install: `uv sync`

## Critical Rules

### Think Before Coding
State assumptions explicitly. If uncertain, ask rather than guess.
Present multiple interpretations when ambiguity exists.
Push back when a simpler approach exists.
Stop when confused. Name what's unclear.

### Simplicity First
Minimum code that solves the problem. Nothing speculative.
No features beyond what was asked. No abstractions for single-use code.
Test: would a senior engineer say this is overcomplicated? If yes, simplify.

### Surgical Changes
Touch only what you must. Clean up only your own mess.
Don't "improve" adjacent code, comments, or formatting.
Don't refactor what isn't broken. Match existing style.

### Goal-Driven Execution
Define success criteria. Loop until verified.
Don't follow steps. Define success and iterate.
Strong success criteria let you loop independently.

### Surface Conflicts, Don't Average Them
If two existing patterns in the codebase contradict, don't blend them.
Pick one (the more recent / more tested), explain why, and flag the other for cleanup.
"Average" code that satisfies both rules is the worst code.

### Read before you write
Before adding code, read exports, immediate callers, shared utilities.
"Looks orthogonal" is dangerous. If unsure why code is structured a way, ask.

### Tests verify intent, not just behavior
Tests must encode WHY behavior matters, not just WHAT it does.
A test that can't fail when business logic changes is wrong.

### Checkpoint after every significant step
Summarize what was done, what's verified, what's left.
Don't continue from a state you can't describe back.
If you lose track, stop and restate.

### Match the codebase's conventions, even if you disagree
Conformance > taste inside the codebase.
If you genuinely think a convention is harmful, surface it. Don't fork silently.

### Fail loud
"Completed" is wrong if anything was skipped silently.
"Tests pass" is wrong if any were skipped.
Default to surfacing uncertainty, not hiding it.

### Never Use Bare Python Commands
Always use `uv run`

### Never Remove Comments Unless Incorrect
Comments may serve as debugging aids or reminders.
Preserve them even if the surrounding code changes.

### Add Comments at Important or Confusing Logic
Wherever the logic is non-trivial, has subtle side effects, or could confuse a reader, add a clear explanatory comment.
Skip comments on straightforward logic that any reader can understand at a glance.
Keep comments concise and to the point — describe the intent clearly without padding or restating what the code obviously does.

### Every Function and Class Should Have a Docstring
Scale the detail to the complexity.
Simple, self-explanatory functions and classes only need a brief one-line description of their purpose.
Larger, complex, or special-purpose functions and classes must have a full docstring that documents parameter types and return type (e.g. `Args:` / `Returns:` sections or inline `: type` annotations) along with any non-obvious behavior, side effects, or invariants — complete enough for a reader to understand usage without reading the implementation.

### Line Length up to 120 Characters
Do not pre-wrap source code, comments, or docstrings at ~80 characters.
Use the full 120-character budget before breaking a line.
Only wrap when a line genuinely exceeds 120 characters or when the break improves readability (e.g. splitting a long argument list at logical boundaries).

### Respond in English and Write All Code/Docs in English
The user may ask questions or describe requirements in Traditional Chinese, but every reply from Claude (chat messages, commit messages, PR descriptions) must be written in English.
Likewise, every file Claude creates or modifies — source code, docstrings, inline comments, UI strings, Markdown docs — must be in English.
When editing a file that already contains Traditional Chinese docstrings or comments, translate them to English as part of the edit rather than leaving them bilingual. (The "Never remove comments unless incorrect" rule still applies — translate, don't delete.)
**Exception**: Traditional Chinese is allowed only when (a) the user explicitly asks Claude to reply in Traditional Chinese for this turn, or (b) the task is specifically about a Traditional Chinese UI where the UI strings themselves must be Traditional Chinese. Absent either condition, default to English even if the user is writing in Traditional Chinese.

## Python Style: Readability Over Brevity

Never compress loops or logic into dense one-liners. Expand for clarity.

### Simple list comprehension is OK:
```python
names = [user.name for user in users]
even_numbers = [n for n in numbers if n % 2 == 0]
```

## Documentation lookup

When answering questions about PySide6 / Qt APIs (signals, models, widgets, QSS, layouts, etc.), always call the context7 MCP first to fetch the version-matched docs for PySide6 6.10.3 before generating code.
Do not rely on training-data recall for Qt APIs.

## Project Structure (high level)
