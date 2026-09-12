"""Generate a one-use activation link; also recovers administrator access without email."""

import argparse
import asyncio

from overdrive.config import Settings
from overdrive.database import Database
from overdrive.local_auth import LocalAuth, initial_admin
from overdrive.service import CRM


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True)
    parser.add_argument("--expect-ns", required=True)
    parser.add_argument("--expect-db", required=True)
    args = parser.parse_args()
    s = Settings()
    if (args.expect_ns, args.expect_db) != (s.surreal_namespace, s.surreal_database):
        parser.error("Configured database does not match; nothing was changed.")
    if not s.local_login:
        parser.error("LOCAL_LOGIN is disabled.")
    db = Database(s)
    await db.connect()
    try:
        await initial_admin(db, s)
        users = await db.rows(
            "SELECT * FROM user WHERE email = $email AND active = true;",
            {"email": args.email.strip().lower()},
        )
        if not users:
            parser.error("No active user with that email. Create the user in Settings first.")
        link = await LocalAuth(db, CRM(db), s).issue(users[0]["id"])
        print("One-use activation link (24 hours). Share privately; it grants access to this account:")
        print(link)
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
