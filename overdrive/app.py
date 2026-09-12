import hashlib
import logging
import secrets
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import nh3
from authlib.integrations.starlette_client import OAuth
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markdown_it import MarkdownIt
from starlette.middleware.sessions import SessionMiddleware

from .auth import OAuthProvider
from .config import Settings
from .database import Database, ensure_record_id
from .mcp_server import create_mcp
from .models import MODELS, SaveEnvelope
from .service import CRM, Actor, DomainError
from .stages import STAGES
from .storage import LocalStorage
from .voice import session_voice

ROOT = Path(__file__).parent
LABELS = {
    "deal": "Deal",
    "contact": "Contact",
    "organization": "Organization",
    "task": "Task",
    "unit": "Business unit",
    "source": "Source",
    "user": "User",
    "note": "Note",
    "attachment": "File or link",
}
FIELD_LABELS = {
    "title": "Title",
    "name": "Name",
    "email": "Email",
    "phone": "Phone",
    "domain": "Domain",
    "unit": "Business unit",
    "source": "Source",
    "organization": "Organization",
    "owner": "Owner",
    "stage": "Stage",
    "outcome": "Outcome",
    "loss_reason": "Loss reason",
    "deal_so_far": "Deal so far",
    "value_cents": "Total value (BRL)",
    "expected_close": "Expected close",
    "archived": "Archived",
    "deal": "Deal",
    "description": "Description",
    "due": "Due date",
    "done": "Completed",
    "primary": "Primary next step",
    "content": "Note",
    "url": "Link URL",
    "deprecated": "Outdated",
    "creator_name": "Added by",
    "active": "Active",
    "admin": "Administrator",
    "units": "Business unit access",
}


from .local_auth import LocalAuth, initial_admin


def create_app(settings=None):
    s = settings or Settings()
    db = Database(s)
    crm = CRM(db)
    provider = OAuthProvider(db, crm, s)
    local_auth = LocalAuth(db, crm, s)
    storage = LocalStorage(s.storage_path, s.max_upload_bytes)
    mcp = create_mcp(crm, provider, s)
    mcp_app = mcp.streamable_http_app()
    oauth = OAuth()
    oauth.register(
        "google",
        client_id=s.google_client_id,
        client_secret=s.google_client_secret,
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"},
    )

    @asynccontextmanager
    async def lifespan(app):
        await db.connect()
        try:
            await initial_admin(db, s)
            async with mcp.session_manager.run():
                yield
        finally:
            await db.close()

    class RedactQuery(logging.Filter):
        def filter(self, record):
            if isinstance(record.args, tuple) and len(record.args) == 5:
                args = list(record.args)
                args[2] = str(args[2]).split("?")[0]
                record.args = tuple(args)
            return True

    logging.getLogger("uvicorn.access").addFilter(RedactQuery())
    app = FastAPI(title="Overdrive", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.local_auth = local_auth
    app.state.oauth = oauth
    app.state.crm, app.state.provider, app.state.settings, app.state.db = crm, provider, s, db
    app.add_middleware(
        SessionMiddleware,
        secret_key=s.session_secret,
        session_cookie="overdrive_session",
        https_only=s.secure,
        same_site="lax",
        max_age=86400,
    )
    templates = Jinja2Templates(directory=ROOT / "templates")
    templates.env.filters["money"] = lambda cents: (
        ("R$ " + f"{(cents or 0) / 100:,.2f}").replace(",", "_").replace(".", ",").replace("_", ".")
    )
    templates.env.filters["md"] = lambda value: nh3.clean(
        MarkdownIt("commonmark", {"html": False}).render(value or "")
    )
    templates.env.filters["shortdate"] = lambda value: (
        datetime.fromisoformat(value[:10]).strftime("%d/%m/%Y") if value else "No due date"
    )
    templates.env.filters["localtime"] = lambda value: (
        datetime.fromisoformat(value).astimezone(ZoneInfo("America/Sao_Paulo")).strftime("%d/%m/%Y · %H:%M")
    )
    templates.env.filters["action_label"] = lambda action: {
        "deal.create": "Deal created",
        "deal.update": "Deal updated",
        "deal.transfer": "Business unit transferred",
        "task.create": "Task added",
        "task.update": "Task updated",
        "note.create": "Note added",
        "note.update": "Note revised",
        "proposal_version.create": "Legacy proposal version added",
        "proposal.sent": "Legacy proposal delivery recorded",
        "proposal.current": "Legacy current proposal selected",
        "attachment.create": "File or link added",
        "attachment.update": "File or link updated",
        "attachment.delete": "File or link deleted",
        "material.migrate": "Legacy content preserved as a file or link",
        "pipedrive.import": "Imported from Pipedrive",
        "contact.link": "Contact linked",
        "contact.unlink": "Contact unlinked",
        "contact.create": "Contact created",
        "organization.create": "Organization created",
    }.get(action, "Record updated")

    def change_fields(changes):
        before, after = changes.get("before", {}), changes.get("after", {})
        ignored = {
            "id",
            "version",
            "created_at",
            "updated_at",
            "storage_key",
            "checksum",
            "actor",
            "deal_so_far_updated_at",
            "deal_so_far_author",
        }
        return [
            (
                FIELD_LABELS.get(
                    k,
                    {
                        "current_proposal": "Legacy current proposal",
                        "sent_at": "Sent on",
                        "number": "Version",
                    }.get(k, k),
                ),
                before.get(k),
                after.get(k),
            )
            for k in sorted(set(before) | set(after))
            if k not in ignored and before.get(k) != after.get(k)
        ]

    templates.env.filters["change_fields"] = change_fields
    templates.env.globals.update(
        labels=LABELS,
        field_labels=FIELD_LABELS,
        stages=STAGES,
        asset_version=hashlib.sha256(
            (ROOT / "static/app.js").read_bytes()
            + (ROOT / "static/app.css").read_bytes()
            + (ROOT / "static/pitlane.css").read_bytes()
        ).hexdigest()[:12],
    )

    def render(request, name, **data):
        request.session.setdefault("csrf", secrets.token_urlsafe(32))
        return templates.TemplateResponse(
            request=request,
            name=name,
            context={
                "csrf": request.session["csrf"],
                "voice": session_voice(request.session),
                "workspace_name": s.workspace_name,
                "dev_login": s.dev_login,
                "local_login": s.local_login,
                "key": secrets.token_urlsafe(24),
                **data,
            },
        )

    async def actor(request, mutation=False):
        if (ticket := request.query_params.get("ticket")) and (
            request.url.path.startswith("/files/") or request.url.path.startswith("/api/deals/")
        ):
            return await provider.use_file_ticket(ticket, request.url.path, request.method)
        header = request.headers.get("authorization", "")
        if header.startswith("Bearer "):
            token = await provider.load_access_token(header[7:])
            if not token or token.resource != s.app_url + "/mcp" or "crm" not in token.scopes:
                raise DomainError("Invalid or revoked token.", 401, "unauthorized")
            return Actor(token.subject, "mcp", token.client_id)
        id = request.session.get("user_id")
        if not id:
            raise DomainError("Sign in to continue.", 401, "unauthorized")
        a = Actor(id)
        await crm.user(a)
        browser_session = await provider.load(request.session.get("sid", ""), "browser_session")
        if (
            not browser_session
            or browser_session.get("subject") != id
            or browser_session.get("auth_epoch") != request.session.get("auth_epoch")
            or request.session.get("auth_epoch") != await local_auth.epoch(id)
        ):
            request.session.clear()
            raise DomainError("Your session expired. Sign in again.", 401)
        if mutation:
            supplied = request.headers.get("x-csrf-token", "")
            if not supplied and "application/json" not in request.headers.get("content-type", ""):
                if "multipart/form-data" in request.headers.get("content-type", ""):
                    parsed_form = await request.form(
                        max_files=1, max_fields=20, max_part_size=s.max_upload_bytes
                    )
                else:
                    parsed_form = await request.form()
                supplied = parsed_form.get("csrf", "")
            if not supplied or not secrets.compare_digest(supplied, request.session.get("csrf", "")):
                raise DomainError("This form has expired. Reload the page.", 403, "csrf")
        return a

    async def context(request):
        a = await actor(request)
        user = await crm.user(a)
        units = await crm.listing(a, "unit", archived=None, limit=200)
        people = await crm.listing(a, "user", limit=200)
        sources = await crm.listing(a, "source", archived=None, limit=200)
        return {
            "user": user,
            "units": units,
            "people": people,
            "sources": sources,
            "source_names": {r["id"]: r["name"] for r in sources},
            "unit_names": {r["id"]: r["name"] for r in units},
            "people_names": {r["id"]: r["name"] for r in people},
            "today": datetime.now(ZoneInfo("America/Sao_Paulo")).date().isoformat(),
        }

    @app.exception_handler(DomainError)
    async def domain_error(request, error):
        if (
            request.url.path.startswith("/api/")
            or request.headers.get("authorization")
            or (request.url.path.startswith("/files/") and request.query_params.get("ticket"))
        ):
            return JSONResponse({"error": error.code, "message": error.message}, status_code=error.status)
        if error.status == 401:
            return RedirectResponse("/login?next=" + request.url.path, status_code=303)
        result = render(request, "error.html", error=error, user=None)
        result.status_code = error.status
        return result

    @app.exception_handler(ValueError)
    async def value_error(request, error):
        return await domain_error(request, DomainError("Invalid data. Check the fields."))

    @app.middleware("http")
    async def headers(request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers["Cache-Control"] = (
            "no-store" if not request.url.path.startswith("/static/") else "public, max-age=3600"
        )
        return response

    @app.get("/health")
    async def health():
        await db.rows("RETURN 1;")
        return {
            "status": "ok",
            "database": "connected",
            "auth": "development" if s.dev_login else "local" if s.local_login else "google",
        }

    @app.get("/login")
    async def login(request: Request, next: str = "/deals"):
        request.session["next"] = (
            next if next.startswith("/") and not next.startswith("//") and "\\" not in next else "/deals"
        )
        dev_email = (s.dev_login_email or s.admin_email).lower()
        local_users = (
            await db.rows(
                "SELECT name FROM user WHERE email = $email AND active = true;", {"email": dev_email}
            )
            if s.dev_login
            else []
        )
        return render(
            request,
            "login.html",
            user=None,
            google_ready=bool(s.google_login and s.google_client_id and s.google_client_secret),
            dev_email=dev_email,
            dev_name=local_users[0]["name"] if local_users else "local user",
        )

    async def public_form(request):
        form = await request.form(max_fields=8)
        if not secrets.compare_digest(str(form.get("csrf", "")), request.session.get("csrf", "invalid")):
            raise DomainError("This form has expired. Reload the page.", 403)
        return form

    async def signed_in(request, subject, epoch):
        sid = secrets.token_urlsafe(32)
        await provider.put(sid, "browser_session", {"subject": subject, "auth_epoch": epoch}, 86400)
        if request.session.get("sid"):
            await provider.delete(request.session["sid"])
        dest = request.session.get("next", "/deals")
        request.session.clear()
        request.session.update(user_id=subject, auth_epoch=epoch, sid=sid, csrf=secrets.token_urlsafe(32))
        return RedirectResponse(dest, 303)

    @app.post("/auth/password")
    async def password_login(request: Request):
        form = await public_form(request)
        try:
            person, epoch = await local_auth.authenticate(
                str(form.get("email", "")), str(form.get("password", "")), request.client.host
            )
        except DomainError as error:
            # Render errors inside the form, without echoing credentials.
            response = render(
                request,
                "login.html",
                user=None,
                error=error.message,
                google_ready=bool(s.google_login and s.google_client_id and s.google_client_secret),
                dev_name="local user",
                dev_email=s.dev_login_email or s.admin_email,
            )
            response.status_code = error.status
            return response
        return await signed_in(request, person["id"], epoch)

    @app.get("/auth/activate")
    async def activation_page(request: Request, token: str = ""):
        await local_auth.throttle(request.client.host, purpose="activation-page")
        await local_auth.activation(token)
        return render(request, "password.html", user=None, token=token, mode="activate")

    @app.post("/auth/activate")
    async def activate_password(request: Request):
        form = await public_form(request)
        token = str(form.get("token", ""))
        try:
            if form.get("password") != form.get("confirm"):
                raise DomainError("Passwords do not match.")
            await local_auth.activate(token, str(form.get("password", "")), request.client.host)
        except DomainError as error:
            response = render(
                request, "password.html", user=None, token=token, mode="activate", error=error.message
            )
            response.status_code = error.status
            return response
        request.session.pop("user_id", None)
        request.session.pop("auth_epoch", None)
        request.session["csrf"] = secrets.token_urlsafe(32)
        return render(request, "password_ready.html", user=None)

    @app.get("/account/password")
    async def password_page(request: Request):
        local_auth.require_enabled()
        ctx = await context(request)
        return render(request, "password.html", **ctx, mode="change")

    @app.post("/account/password")
    async def change_password(request: Request):
        a = await actor(request, True)
        form = await request.form()
        try:
            if form.get("password") != form.get("confirm"):
                raise DomainError("Passwords do not match.")
            epoch = await local_auth.change_password(
                a.id, str(form.get("current", "")), str(form.get("password", "")), request.client.host
            )
        except DomainError as error:
            response = render(
                request, "password.html", **(await context(request)), mode="change", error=error.message
            )
            response.status_code = error.status
            return response
        request.session["next"] = "/settings"
        return await signed_in(request, a.id, epoch)

    @app.get("/settings/users/{id}/activation")
    async def activation_confirm(request: Request, id: str):
        a = await actor(request)
        if not (await crm.user(a))["admin"]:
            raise DomainError("Administrator access required.", 403)
        local_auth.require_enabled()
        person = await crm.get(a, id, "user")
        return render(request, "activation_link.html", **(await context(request)), person=person)

    @app.post("/settings/users/{id}/activation")
    async def activation_issue(request: Request, id: str):
        a = await actor(request, True)
        if not (await crm.user(a))["admin"]:
            raise DomainError("Administrator access required.", 403)
        person = await crm.get(a, id, "user")
        ctx = await context(request)
        link = await local_auth.issue(id)
        return render(request, "activation_link.html", **ctx, person=person, activation_link=link)

    @app.post("/auth/dev")
    async def dev_auth(request: Request):
        from urllib.parse import urlsplit

        local_origin = urlsplit(s.app_url).hostname in {"localhost", "127.0.0.1", "::1"}
        local_client = request.client.host in {"127.0.0.1", "::1", "testclient"}
        if not s.dev_login or s.secure or not local_origin or not (local_client or s.dev_login_allow_remote):
            raise DomainError("Resource not found.", 404)
        form = await request.form()
        if not secrets.compare_digest(str(form.get("csrf", "")), request.session.get("csrf", "invalid")):
            raise DomainError("This form has expired.", 403)
        rows = await db.rows(
            "SELECT * FROM user WHERE email = $email AND active = true;",
            {"email": (s.dev_login_email or s.admin_email).lower()},
        )
        if not rows:
            raise DomainError("Local development user is not configured.", 403)
        return await signed_in(request, rows[0]["id"], await local_auth.epoch(rows[0]["id"]))

    @app.get("/auth/google")
    async def google_login(request: Request):
        if not s.google_login or not s.google_client_id or not s.google_client_secret:
            raise DomainError("Google sign-in is not configured yet.", 503)
        return await oauth.google.authorize_redirect(request, s.app_url + "/auth/google/callback")

    @app.get("/auth/google/callback")
    async def google_callback(request: Request):
        if not s.google_login or not s.google_client_id or not s.google_client_secret:
            raise DomainError("Google sign-in is not configured yet.", 503)
        try:
            token = await oauth.google.authorize_access_token(request)
            info = token["userinfo"]
            if not info.get("email_verified"):
                raise ValueError("email unverified")
        except Exception as exc:
            raise DomainError("Google sign-in could not be verified.", 401) from exc
        async with db.lock:
            rows = await db.rows("SELECT * FROM user WHERE google_sub = $sub;", {"sub": info["sub"]})
            if not rows:
                rows = await db.rows(
                    "SELECT * FROM user WHERE email = $email AND google_sub = NONE;",
                    {"email": info["email"].lower()},
                )
            if not rows or not rows[0]["active"]:
                raise DomainError("Your account does not have access. Ask an administrator to add you.", 403)
            login_epoch = await local_auth.epoch(rows[0]["id"])
            if not rows[0].get("google_sub"):
                await db.query(
                    "UPDATE $id SET google_sub = $sub;",
                    {"id": ensure_record_id(rows[0]["id"]), "sub": info["sub"]},
                )
        return await signed_in(request, rows[0]["id"], login_epoch)

    @app.post("/logout")
    async def logout(request: Request):
        await actor(request, True)
        await provider.delete(request.session["sid"])
        request.session.clear()
        return RedirectResponse("/login", 303)

    @app.get("/oauth/consent")
    async def consent(request: Request, ticket: str):
        if not request.session.get("user_id"):
            return RedirectResponse("/login?next=/oauth/consent?ticket=" + ticket, 303)
        ctx = await context(request)
        pending = await provider.load(ticket, "pending")
        if not pending:
            raise DomainError("This request has expired.")
        client = await provider.get_client(pending["client_id"])
        return render(
            request, "consent.html", **ctx, ticket=ticket, client_name=client.client_name or client.client_id
        )

    @app.post("/oauth/consent")
    async def consent_submit(request: Request):
        a = await actor(request, True)
        form = await request.form()
        if form.get("decision") != "allow":
            pending = await provider.load(form["ticket"], "pending")
            await provider.delete(form["ticket"])
            if pending:
                from urllib.parse import urlencode

                p = pending["params"]
                args = {"error": "access_denied"}
                if p.get("state"):
                    args["state"] = p["state"]
                return RedirectResponse(
                    p["redirect_uri"] + ("&" if "?" in p["redirect_uri"] else "?") + urlencode(args), 303
                )
            return RedirectResponse("/deals", 303)
        return RedirectResponse(await provider.grant(a, form["ticket"], request.session["auth_epoch"]), 303)

    @app.get("/")
    async def home():
        return RedirectResponse("/deals", 303)

    @app.get("/deals")
    async def deals(
        request: Request,
        q: str = "",
        unit: str = "",
        owner: str = "",
        outcome: str = "open",
        archived: str = "false",
        view: str = "board",
        offset: int = 0,
    ):
        a = await actor(request)
        ctx = await context(request)
        records = await crm.listing(
            a,
            "deal",
            q=q,
            unit=unit,
            owner=owner,
            outcome=outcome,
            archived=None if archived == "all" else archived == "true",
            offset=offset,
            limit=100,
        )
        task_rows = await crm.listing(a, "task", done=False, limit=200)
        next_tasks = {}
        for t in sorted(task_rows, key=lambda t: (not t["primary"], t.get("due") or "9999")):
            next_tasks.setdefault(t["deal"], t)
        return render(
            request,
            "deals.html",
            **ctx,
            active="deal",
            records=records,
            next_tasks=next_tasks,
            filters={
                "q": q,
                "unit": unit,
                "owner": owner,
                "outcome": outcome,
                "archived": archived,
                "view": view,
            },
            offset=offset,
        )

    @app.post("/deals/{id}/stage")
    async def move_deal(request: Request, id: str):
        a = await actor(request, True)
        form = await request.form()
        await crm.save(
            a,
            "deal",
            {"stage": form.get("stage", "")},
            id=id,
            version=int(form["version"]),
            key=form.get("key", ""),
        )
        target = form.get("return", "/deals")
        if target not in {"/deals", "/deals/" + id} and not target.startswith("/deals?"):
            target = "/deals"
        return RedirectResponse(target, 303)

    @app.get("/deals/{id}")
    async def detail(
        request: Request, id: str, tab: str = "history", offset: int = 0, hide_deprecated: bool = False
    ):
        a = await actor(request)
        record = await crm.get(a, id, "deal")
        ctx = await context(request)
        tasks = await crm.listing(a, "task", deal=id, limit=200)
        files = await crm.listing(
            a,
            "attachment",
            deal=id,
            limit=100,
            offset=offset if tab == "files" else 0,
            deprecated=False if hide_deprecated else None,
        )
        links = await crm.listing(a, "deal_contact", deal=id, limit=200)
        contacts = [await crm.get(a, link["contact"], "contact") for link in links]
        organization = (
            await crm.get(a, record["organization"], "organization") if record.get("organization") else None
        )
        timeline = await crm.timeline(a, id, offset=offset if tab != "files" else 0)
        pending = sorted(
            (t for t in tasks if not t["done"]),
            key=lambda t: (not t["primary"], t.get("due") or "9999", t["created_at"]),
        )
        return render(
            request,
            "deal.html",
            **ctx,
            active="deal",
            record=record,
            tab=tab,
            offset=offset,
            timeline=timeline,
            tasks=tasks,
            next_task=pending[0] if pending else None,
            hide_deprecated=hide_deprecated,
            files=files,
            contacts=contacts,
            organization=organization,
        )

    @app.get("/deals/{id}/status")
    async def edit_deal_status(request: Request, id: str):
        record = await crm.get(await actor(request), id, "deal")
        return render(
            request,
            "deal_status.html",
            **(await context(request)),
            record=record,
            draft=record.get("deal_so_far", ""),
            error="",
        )

    @app.post("/deals/{id}/status")
    async def save_deal_status(request: Request, id: str):
        a = await actor(request, True)
        form = await request.form()
        draft = str(form.get("deal_so_far", ""))
        try:
            await crm.save(
                a,
                "deal",
                {"deal_so_far": draft},
                id=id,
                version=int(form["version"]),
                key=form.get("key", ""),
            )
        except DomainError as exc:
            if exc.status not in {400, 409}:
                raise
            record = await crm.get(a, id, "deal")
            response = render(
                request,
                "deal_status.html",
                **(await context(request)),
                record=record,
                draft=draft,
                error=exc.message,
            )
            response.status_code = exc.status
            return response
        return RedirectResponse("/deals/" + id, 303)

    @app.get("/tasks")
    async def tasks(request: Request, completed: bool = False, unit: str = "", offset: int = 0):
        a = await actor(request)
        ctx = await context(request)
        rows = await crm.listing(a, "task", mine=True, done=completed, unit=unit, offset=offset, limit=100)
        deals = {t["deal"]: await crm.get(a, t["deal"], "deal") for t in rows}
        groups = (
            {"Completed": []}
            if completed
            else {"Overdue": [], "Today": [], "Upcoming": [], "No due date": []}
        )
        for t in sorted(rows, key=lambda t: t.get("due") or "9999"):
            group = (
                "Completed"
                if completed
                else (
                    "No due date"
                    if not t.get("due")
                    else "Overdue"
                    if t["due"] < ctx["today"]
                    else "Today"
                    if t["due"] == ctx["today"]
                    else "Upcoming"
                )
            )
            groups[group].append(t)
        return render(
            request,
            "tasks.html",
            **ctx,
            active="task",
            groups=groups,
            deals=deals,
            completed=completed,
            selected_unit=unit,
            offset=offset,
            count=len(rows),
        )

    @app.get("/directory/{entity}")
    async def directory(request: Request, entity: str, q: str = "", archived: bool = False, offset: int = 0):
        if entity not in {"contact", "organization"}:
            raise DomainError("Resource not found.", 404)
        a = await actor(request)
        ctx = await context(request)
        records = await crm.listing(a, entity, q=q, archived=archived, offset=offset, limit=100)
        return render(
            request,
            "directory.html",
            **ctx,
            active=entity,
            entity=entity,
            records=records,
            q=q,
            archived=archived,
            offset=offset,
        )

    @app.get("/records/{id}")
    async def record_detail(request: Request, id: str):
        a = await actor(request)
        entity = id.split(":")[0]
        if entity not in {"contact", "organization"}:
            raise DomainError("Resource not found.", 404)
        record = await crm.get(a, id, entity)
        visible_deals = await crm.listing(a, "deal", archived=None, limit=200)
        links = await crm.listing(a, "deal_contact", limit=200)
        linked_ids = {r["deal"] for r in links if r["contact"] == id}
        related = [d for d in visible_deals if d["id"] in linked_ids or d.get("organization") == id]
        contacts = (
            await crm.listing(a, "contact", archived=None, limit=200) if entity == "organization" else []
        )
        if entity == "organization":
            contact_ids = {c["id"] for c in contacts if c.get("organization") == id}
            related_ids = {link["deal"] for link in links if link["contact"] in contact_ids}
            related = [d for d in visible_deals if d.get("organization") == id or d["id"] in related_ids]
        return render(
            request,
            "record.html",
            **(await context(request)),
            active=entity,
            entity=entity,
            record=record,
            organization=await crm.get(a, record["organization"], "organization")
            if entity == "contact" and record.get("organization")
            else None,
            related=related,
            contacts=[c for c in contacts if c.get("organization") == id],
            history=await crm.history(a, id),
        )

    async def form_page(request, entity, id="", deal="", values=None, error=None, field=""):
        if entity not in MODELS:
            raise DomainError("Resource not found.", 404)
        a = await actor(request)
        ctx = await context(request)
        if entity in {"unit", "user", "source"} and not ctx["user"]["admin"]:
            raise DomainError("Administrators only.", 403)
        record = await crm.get(a, id, entity) if id else {}
        values = values if values is not None else record
        if deal:
            parent = await crm.get(a, deal, "deal")
            values = {"deal": deal, **values}
            if entity == "contact" and not record and "organization" not in values:
                values["organization"] = parent.get("organization")
        options = {
            "unit": ctx["units"],
            "units": ctx["units"],
            "owner": ctx["people"],
            "source": [s for s in ctx["sources"] if not s["archived"] or s["id"] == values.get("source")],
            "organization": await crm.listing(a, "organization", limit=200),
            "deal": await crm.listing(a, "deal", archived=None, limit=200),
        }
        if values.get("organization") and not any(
            o["id"] == values["organization"] for o in options["organization"]
        ):
            options["organization"].append(await crm.get(a, values["organization"], "organization"))
        fields = list(MODELS[entity].model_fields)
        if entity == "attachment" and record.get("storage_key"):
            fields.remove("url")
        if entity == "attachment" and record:
            fields.remove("deal")
        if id and entity == "deal":
            fields.remove("unit")
        if not id and "archived" in fields:
            fields.remove("archived")
        if entity == "deal" and request.headers.get("x-overdrive-editor") == "true" and not field:
            order = [
                "title",
                "value_cents",
                "owner",
                "expected_close",
                "organization",
                "source",
                "unit",
                "stage",
                "outcome",
                "loss_reason",
                "deal_so_far",
                "archived",
            ]
            fields = [name for name in order if name in fields]
        if field:
            if field not in fields:
                raise DomainError("This field cannot be edited here.", 400)
            fields = [field] + (["loss_reason"] if field == "outcome" else [])
        return render(
            request,
            "form.html",
            **ctx,
            active=entity,
            entity=entity,
            record=record,
            values=values,
            fields=fields,
            editor=request.headers.get("x-overdrive-editor") == "true",
            selected_field=field,
            options=options,
            context_deal=deal or record.get("deal", ""),
            error=error,
        )

    @app.get("/new/{entity}")
    async def new(request: Request, entity: str, deal: str = ""):
        return await form_page(request, entity, deal=deal)

    @app.get("/edit/{id}")
    async def edit(request: Request, id: str, field: str = ""):
        return await form_page(request, id.split(":")[0], id=id, field=field)

    @app.post("/save/{entity}")
    async def save(request: Request, entity: str):
        a = await actor(request, True)
        form = await request.form()
        if entity not in MODELS:
            raise DomainError("Invalid resource.")
        data = {k: v for k, v in form.items() if k in MODELS[entity].model_fields}
        for k in ["archived", "active", "admin", "done", "primary", "deprecated"]:
            if k in data:
                data[k] = data[k] in {"true", "on", "1"}
        if entity == "user":
            data["units"] = form.getlist("units")
        for k in ["unit", "organization", "source", "owner", "expected_close", "due"]:
            if k in data and not data[k]:
                data[k] = None
        if "value_cents" in data:
            from decimal import Decimal, InvalidOperation

            try:
                val = Decimal(data["value_cents"].replace(",", ".")) * 100
                if val != val.to_integral_value():
                    raise InvalidOperation()
                data["value_cents"] = int(val)
            except InvalidOperation:
                raise DomainError("Invalid amount. Use up to two decimal places.")
        try:
            row = await crm.save(
                a,
                entity,
                data,
                id=form.get("id") or None,
                version=int(form["version"]) if form.get("version") else None,
                context_deal=form.get("context_deal") or None,
                key=form.get("key", ""),
            )
        except DomainError as error:
            result = await form_page(
                request,
                entity,
                id=form.get("id", ""),
                deal=form.get("context_deal", ""),
                values=dict(form),
                error=error.message,
                field=form.get("_field", ""),
            )
            result.status_code = error.status
            return result
        target = (
            "/deals/" + row["id"]
            if entity == "deal"
            else "/deals/" + row["deal"]
            if row.get("deal")
            else "/deals/" + form["context_deal"]
            if form.get("context_deal")
            else "/settings"
            if entity in {"unit", "user", "source"}
            else "/records/" + row["id"]
        )
        if entity == "attachment":
            target += "?tab=files"
        if request.headers.get("x-overdrive-editor") == "true":
            return JSONResponse({"record": row, "target": target})
        return RedirectResponse(target, 303)

    @app.post("/tasks/{id}/toggle")
    async def toggle_task(request: Request, id: str):
        a = await actor(request, True)
        form = await request.form()
        await crm.save(
            a,
            "task",
            {"done": form["done"] == "true"},
            id=id,
            version=int(form["version"]),
            key=form.get("key", ""),
        )
        target = form.get("return", "/tasks")
        return RedirectResponse(
            target if target.startswith("/") and not target.startswith("//") else "/tasks", 303
        )

    @app.post("/deals/{id}/note")
    async def quick_note(request: Request, id: str):
        a = await actor(request, True)
        form = await request.form()
        await crm.save(a, "note", {"deal": id, "content": form["content"]}, key=form.get("key", ""))
        return RedirectResponse("/deals/" + id, 303)

    @app.post("/deals/{id}/links")
    async def contact_link(request: Request, id: str):
        a = await actor(request, True)
        form = await request.form()
        await crm.link(a, id, form["contact"], remove=form.get("remove") == "true")
        return RedirectResponse("/deals/" + id, 303)

    @app.get("/deals/{id}/link")
    async def link_form(request: Request, id: str):
        a = await actor(request)
        record = await crm.get(a, id, "deal")
        return render(
            request,
            "link.html",
            **(await context(request)),
            record=record,
            contacts=await crm.listing(a, "contact", limit=200),
            active="deal",
        )

    @app.post("/materials/{id}/status")
    async def material_status(request: Request, id: str):
        a = await actor(request, True)
        form = await request.form()
        row = await crm.save(
            a,
            "attachment",
            {"deprecated": form.get("deprecated") == "true"},
            id=id,
            version=int(form["version"]),
            key=form.get("key", ""),
        )
        return RedirectResponse("/deals/" + row["deal"] + "?tab=files", 303)

    @app.get("/materials/{id}/delete")
    async def delete_material_form(request: Request, id: str):
        a = await actor(request)
        record = await crm.get(a, id, "attachment")
        return render(
            request, "delete_material.html", **(await context(request)), record=record, active="deal"
        )

    @app.post("/materials/{id}/delete")
    async def delete_material(request: Request, id: str):
        a = await actor(request, True)
        form = await request.form()
        receipt = await storage.delete(crm, a, id, int(form["version"]), form.get("key", ""))
        return RedirectResponse("/deals/" + receipt["deal"] + "?tab=files", 303)

    @app.delete("/api/attachment/{id}")
    async def api_delete_material(request: Request, id: str, version: int):
        return await storage.delete(
            crm, await actor(request, True), id, version, request.headers.get("idempotency-key", "")
        )

    @app.get("/deals/{id}/transfer")
    async def transfer_form(request: Request, id: str):
        ctx = await context(request)
        if not ctx["user"]["admin"]:
            raise DomainError("Administrators only.", 403)
        record = await crm.get(await actor(request), id, "deal")
        return render(
            request,
            "transfer.html",
            **ctx,
            record=record,
            active="deal",
            selected_unit=record.get("unit"),
            selected_owner=record.get("owner"),
            error="",
        )

    @app.post("/deals/{id}/transfer")
    async def transfer(request: Request, id: str):
        a = await actor(request, True)
        f = await request.form()
        try:
            await crm.transfer(a, id, f["unit"] or None, f["owner"], int(f["version"]), f.get("key", ""))
        except DomainError as exc:
            if exc.status not in {400, 409}:
                raise
            record = await crm.get(a, id, "deal")
            response = render(
                request,
                "transfer.html",
                **(await context(request)),
                record=record,
                active="deal",
                selected_unit=f["unit"],
                selected_owner=f["owner"],
                error=exc.message,
            )
            response.status_code = exc.status
            return response
        return RedirectResponse("/deals/" + id, 303)

    @app.get("/settings")
    async def settings_page(request: Request):
        a = await actor(request)
        ctx = await context(request)
        return render(
            request,
            "settings.html",
            **ctx,
            active="settings",
            connections=await provider.connections(a),
            has_local_password=bool((await local_auth.credential(a.id)).get("password_hash")),
            google_ready=bool(s.google_login and s.google_client_id and s.google_client_secret),
        )

    @app.post("/connections/{id}/revoke")
    async def revoke_connection(request: Request, id: str):
        a = await actor(request, True)
        rows = await provider.connections(a)
        row = next((r for r in rows if r["id"] == id), None)
        if not row:
            raise DomainError("Resource not found.", 404)
        await db.query("UPDATE $id SET payload.revoked = true;", {"id": ensure_record_id(id, "auth_record")})
        return RedirectResponse("/settings", 303)

    @app.get("/analytics")
    async def analytics_page(
        request: Request,
        unit: str = "",
        source: str = "",
        created_from: str = "",
        created_to: str = "",
        include_archived: bool = True,
    ):
        report = await crm.analytics(
            await actor(request),
            unit=unit,
            source=source,
            created_from=created_from,
            created_to=created_to,
            include_archived=include_archived,
        )
        return render(
            request, "analytics.html", **(await context(request)), active="analytics", report=report
        )

    @app.get("/api/analytics")
    async def analytics_api(
        request: Request,
        unit: str = "",
        source: str = "",
        created_from: str = "",
        created_to: str = "",
        include_archived: bool = True,
    ):
        return await crm.analytics(
            await actor(request),
            unit=unit,
            source=source,
            created_from=created_from,
            created_to=created_to,
            include_archived=include_archived,
        )

    @app.get("/api/{entity}")
    async def api_list(
        request: Request,
        entity: str,
        q: str = "",
        deal: str = "",
        unit: str = "",
        owner: str = "",
        mine: bool = False,
        done: bool | None = None,
        deprecated: bool | None = None,
        archived: bool = False,
        offset: int = 0,
        limit: int = 50,
    ):
        return await crm.listing(
            await actor(request),
            entity,
            q=q,
            deal=deal,
            unit=unit,
            owner=owner,
            mine=mine,
            done=done,
            deprecated=deprecated,
            archived=archived,
            offset=offset,
            limit=limit,
        )

    @app.get("/api/{entity}/{id}")
    async def api_get(request: Request, entity: str, id: str):
        return await crm.get(await actor(request), id, entity)

    @app.post("/api/{entity}")
    async def api_create(request: Request, entity: str, body: SaveEnvelope):
        a = await actor(request, True)
        return await crm.save(
            a,
            entity,
            body.data,
            context_deal=body.context_deal,
            key=request.headers.get("idempotency-key", ""),
        )

    @app.patch("/api/{entity}/{id}")
    async def api_update(request: Request, entity: str, id: str, body: SaveEnvelope):
        a = await actor(request, True)
        return await crm.save(
            a,
            entity,
            body.data,
            id=id,
            version=body.version,
            key=request.headers.get("idempotency-key", ""),
        )

    @app.post("/api/deals/{id}/files")
    async def upload(request: Request, id: str):
        a = await actor(request, True)
        form = await request.form(max_files=1, max_fields=20, max_part_size=s.max_upload_bytes)
        if not hasattr(form.get("file"), "read"):
            raise DomainError("Select a file.")
        try:
            record = await storage.save(
                crm,
                a,
                id,
                form["file"],
                request.headers.get("idempotency-key", form.get("key", "")),
                title=form.get("title", ""),
                description=form.get("description", ""),
            )
        finally:
            await form.close()
        if (
            request.headers.get("authorization")
            or request.query_params.get("ticket")
            or request.headers.get("accept") == "application/json"
        ):
            return record
        return RedirectResponse("/deals/" + id + "?tab=files", 303)

    @app.get("/materials/{id}/preview")
    async def preview_material(request: Request, id: str):
        record = await crm.get(await actor(request), id, "attachment")
        name = record.get("name", "").lower()
        if not record.get("storage_key") or not name.endswith((".pdf", ".md", ".markdown")):
            raise DomainError("Preview is not available for this file.", 415)
        path = storage.path(record["storage_key"])
        if not path.is_file():
            raise DomainError("File unavailable.", 404)
        pdf = name.endswith(".pdf")
        content = "" if pdf else path.read_text(encoding="utf-8", errors="replace")
        response = render(request, "material_preview.html", record=record, pdf=pdf, content=content)
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["Content-Security-Policy"] = (
            "frame-ancestors 'self'; script-src 'none'; object-src 'none'; img-src 'self' data:; frame-src 'self'"
        )
        return response

    @app.get("/files/{id}")
    async def download(request: Request, id: str, inline: bool = False):
        record = await crm.get(await actor(request), id, "attachment")
        if not record.get("storage_key"):
            raise DomainError("Resource not found.", 404, "not_found")
        path = storage.path(record["storage_key"])
        if not path.is_file():
            raise DomainError("File unavailable. Ask an administrator to restore it.", 404)
        if inline:
            with path.open("rb") as source:
                signature = source.read(5)
            if not record["name"].lower().endswith(".pdf") or signature != b"%PDF-":
                raise DomainError("Inline preview is only available for PDF files.", 415)
            return FileResponse(
                path,
                filename=record["name"],
                media_type="application/pdf",
                content_disposition_type="inline",
                headers={"X-Frame-Options": "SAMEORIGIN"},
            )
        return FileResponse(path, filename=record["name"], media_type="application/octet-stream")

    @app.get("/search")
    async def search(request: Request, q: str = ""):
        a = await actor(request)
        results = []
        if q.strip():
            for table in ["deal", "contact", "organization"]:
                for row in await crm.listing(a, table, q=q, limit=8):
                    results.append(
                        {
                            "name": row.get("title", row.get("name")),
                            "type": LABELS[table],
                            "url": ("/deals/" if table == "deal" else "/records/") + row["id"],
                        }
                    )
        return render(request, "search.html", results=results)

    app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
    app.mount("/", mcp_app)
    return app
