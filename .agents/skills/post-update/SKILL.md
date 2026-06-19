---
name: post-update
description: >-
  Post a standard update comment to a GitHub issue that is already in the chat
  context. Use when the user says "/post-update", asks to update an issue after
  a fix has landed, or wants issue participants told that the fix will be in the
  next release and invited to leave feedback once they can test it.
---

# /post-update

Post a short, friendly GitHub issue comment saying the change will be included
in the next release and asking the reporter to leave feedback after testing.

## Workflow

1. Identify the GitHub issue from the current chat context.
   - Use an issue URL, issue number, or connected GitHub issue context if one is
     clearly available.
   - If more than one issue is plausible, ask which issue to update.
   - If no issue is available, ask the user for the issue URL or number.

2. Confirm the target issue is in this repository unless the chat clearly points
   somewhere else.

3. Post this comment, adjusting only the greeting if the reporter's name is
   obvious:

```text
Thanks for reporting this. The fix has been merged and will be included in the
next release.

Once you have a chance to try that release, please leave feedback here and let
us know whether it resolves the issue for you.
```

4. Do not close the issue. The project owner wants to test or receive feedback
   before issues are closed.

5. After posting, reply with the issue number and a short confirmation. If the
   platform returns a comment URL, include it.

## Tooling

Prefer the GitHub app/connector when available. If not, use the GitHub CLI from
the repository checkout, for example:

```bash
gh issue comment <issue-number-or-url> --body-file <temporary-body-file>
```

Use a temporary file for the comment body to avoid shell quoting mistakes.
