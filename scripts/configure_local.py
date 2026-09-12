"""Generate a configuration without overwriting existing files or printing secrets."""

import argparse
import secrets
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--admin-email", required=True)
    parser.add_argument("--admin-name", default="Administrator")
    parser.add_argument("--dev", action="store_true", help="Enable local-only simulated login")
    parser.add_argument("--docker", action="store_true")
    parser.add_argument("--output", type=Path, default=Path(".env"))
    args = parser.parse_args()
    if "@" not in args.admin_email or any(c in args.admin_email + args.admin_name for c in '\n\r$#"'):
        parser.error("Use a valid email and a single-line name without dotenv special characters")
    content = Path(__file__).resolve().parent.parent.joinpath(".env.example").read_text()
    values = {"ADMIN_EMAIL": args.admin_email, "ADMIN_NAME": args.admin_name,
              "DEV_LOGIN": str(args.dev).lower(),
              "DEV_LOGIN_ALLOW_REMOTE": str(args.dev and args.docker).lower()}
    for key in ["SESSION_SECRET", "SURREAL_PASS", "BOOTSTRAP_PASS"]:
        values[key] = secrets.token_urlsafe(48)
    lines = [key + "=" + values[key] if (key := line.split("=", 1)[0]) in values else line
             for line in content.splitlines()]
    with args.output.open("x") as stream:
        stream.write("\n".join(lines) + "\n")
    args.output.chmod(0o600)
    print(f"Created {args.output}. Secrets were not printed.")


if __name__ == "__main__":
    main()
