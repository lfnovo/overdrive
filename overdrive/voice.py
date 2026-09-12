"""Small, human-written voice bank. Never randomize labels, errors or business facts."""

import secrets
import time

COPY = {
    "deals": (
        ("Let’s get things moving.", "Less admin. More room for the next conversation."),
        ("Good work. Next gear.", "A little context. A clear next step. Keep it moving."),
        ("Make room for what’s next.", "Your conversations, with the next move in sight."),
        ("Keep the good work rolling.", "Pick up the conversation. Take the next step."),
    ),
    "tasks": (
        "One clear next step beats ten open tabs.",
        "Small moves. Real momentum.",
        "Good follow-through starts here.",
        "A little less juggling. A little more doing.",
    ),
    "contact": (
        "Good business starts with people.",
        "Names worth remembering. Context worth keeping.",
        "Keep the people behind the conversation close.",
    ),
    "organization": (
        "Companies on paper. People at the other end.",
        "Get to know the teams on the other side.",
        "A little context goes a long way.",
    ),
    "login": (
        ("Your next move starts here.", "People, deals and context. Ready when you are."),
        ("Back in the driver’s seat.", "Pick up where the conversation left off."),
        ("A fresh start. Same good work.", "Your workspace for the next conversation."),
    ),
}


def session_voice(session, timestamp=None):
    """Rotate at most every six hours; don't repeat the previous choice in a group.

    Only small integer indexes go in the signed session cookie. Stable across saves,
    reloads and HTMX requests in the same window. No external generation or tracking.
    """
    bucket = int((time.time() if timestamp is None else timestamp) // 21600)
    previous = session.get("voice", {})
    if previous.get("bucket") != bucket or any(
        not isinstance(previous.get(key), int) or not 0 <= previous[key] < len(options)
        for key, options in COPY.items()
    ):
        indexes = {"bucket": bucket}
        for key, options in COPY.items():
            indexes[key] = secrets.choice([i for i in range(len(options)) if i != previous.get(key)])
        session["voice"] = indexes
    return {key: options[session["voice"][key]] for key, options in COPY.items()}
