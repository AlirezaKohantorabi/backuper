# backuper/bot/forms.py

**Purpose:** a small form engine for Telegram: it walks the user through a list of
[`Field`](../fields.md)s, **one message per field**, with buttons where possible (yes/no, choices,
credentials, groups) and typed answers otherwise. The [Panel](panel.md) decides what happens when a
form is complete (`FormState.kind`).

**Why one question at a time?** Telegram has no real forms; a long message with many inputs is
error-prone on a phone. One question with clear help text, an example and buttons works well.

| Name | What it does |
|---|---|
| `FormState` | The state of one user's open form: kind (`source_add`, `source_edit`, `cred_add`, `group_add`, `once`, …), title, fields, values collected, current values when editing, position, target object, prompt message id, parent form (a "➕ New credential" form opened in the middle of another), and the button choices on screen. |
| `FormState.merged` | Current values + new answers (used for `show_if`). |
| `FormState.current_field` | The next field that should be shown (skips hidden ones). |
| `FormState.visible_count` | "(3/7)" progress in the prompt. |
| `FormState.accept` | Parse and store an answer, move on. |
| `FormState.use_default` / `FormState.keep_current` / `FormState.clear` | The "⏭ Use default", "↩️ Keep current", "🧹 Empty" buttons. |
| `prompt` | Builds the question text (label, help, example, current/default value) and its buttons. Stores which credentials/groups are on screen so a button press maps back to the right one. |
| `summary` | The "Save this source?" overview at the end of the add wizard. |
