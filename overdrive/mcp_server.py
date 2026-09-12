import json
from functools import wraps
from typing import Literal

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from .models import Attachment, Contact, Deal, Note, Organization, Task
from .service import Actor, DomainError
from .storage import LocalStorage


def create_mcp(crm, provider, settings):
    mcp = FastMCP(
        "Overdrive",
        instructions="A CRM for humans and agents. Read each record and its version before editing. "
        "Keep idempotency_key stable when retrying. All actions respect the authenticated user.",
        auth_server_provider=provider,
        stateless_http=True,
        json_response=True,
        auth=AuthSettings(
            issuer_url=settings.app_url,
            resource_server_url=settings.app_url + "/mcp",
            validate_token_resource=True,
            required_scopes=["crm"],
            client_registration_options=ClientRegistrationOptions(
                enabled=True, valid_scopes=["crm"], default_scopes=["crm"]
            ),
            revocation_options=RevocationOptions(enabled=True),
        ),
    )

    def tool():
        def register(fn):
            @wraps(fn)
            async def guarded(*args, **kwargs):
                try:
                    return await fn(*args, **kwargs)
                except DomainError as error:
                    raise ToolError(
                        json.dumps(
                            {"error": error.code, "status": error.status, "message": error.message},
                            ensure_ascii=False,
                        )
                    ) from error
                except ValueError as error:
                    raise ToolError(
                        json.dumps({"error": "validation", "message": "Invalid identifier or data."})
                    ) from error

            return mcp.tool()(guarded)

        return register

    def actor():
        token = get_access_token()
        if not token or not token.subject:
            raise DomainError("Authentication required.", 401)
        return Actor(token.subject, "mcp", token.client_id)

    @tool()
    async def whoami() -> dict:
        """Return the authenticated user, current business units and privileges."""
        return await crm.user(actor())

    @tool()
    async def list_records(
        entity: Literal[
            "deal",
            "contact",
            "organization",
            "task",
            "note",
            "attachment",
            "unit",
            "source",
            "user",
            "audit_event",
        ],
        query: str = "",
        deal_id: str = "",
        unit_id: str = "",
        owner_id: str = "",
        archived: bool = False,
        deprecated: bool | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> list[dict]:
        """Search accessible records with pagination. History, files and links accept deal_id."""
        return await crm.listing(
            actor(),
            entity,
            q=query,
            deal=deal_id,
            unit=unit_id,
            owner=owner_id,
            archived=archived,
            deprecated=deprecated,
            offset=offset,
            limit=limit,
        )

    @tool()
    async def get_record(record_id: str) -> dict:
        """Get a record and its version before editing. Missing and inaccessible records share the same response."""
        return await crm.get(actor(), record_id)

    @tool()
    async def record_history(record_id: str, offset: int = 0, limit: int = 50) -> list[dict]:
        """Paginated history for a deal, contact or organization. Shared profiles omit private relationships."""
        return await crm.history(actor(), record_id, offset, limit)

    @tool()
    async def create_deal(data: Deal, idempotency_key: str) -> dict:
        """Create an opportunity in New Lead by default. Only admins can create unassigned deals."""
        return await crm.save(actor(), "deal", data.model_dump(mode="json"), key=idempotency_key)

    @tool()
    async def create_contact(data: Contact, deal_id: str, idempotency_key: str) -> dict:
        """Create a contact and atomically link it to an accessible deal."""
        return await crm.save(
            actor(), "contact", data.model_dump(mode="json"), context_deal=deal_id, key=idempotency_key
        )

    @tool()
    async def create_organization(data: Organization, deal_id: str, idempotency_key: str) -> dict:
        """Create an organization and associate it with a deal."""
        return await crm.save(
            actor(), "organization", data.model_dump(mode="json"), context_deal=deal_id, key=idempotency_key
        )

    @tool()
    async def update_record(
        record_id: str, expected_version: int, changes: dict, idempotency_key: str
    ) -> dict:
        """Edit commercial fields, archive/restore records (archived), complete/reopen tasks (done).
        Deal: stage, outcome open/won/lost, loss_reason, value_cents, owner, expected_close, deal_so_far, source (source record ID or null).
        Discover available sources with list_records(entity="source").
        deal_so_far is the living Markdown summary of useful context and current status.
        Read the current deal/version, synthesize what changed, then update this field.
        Preserve relevant decisions and open questions; use notes for dated events.
        Files/links: title, description, deprecated; links also accept url.
        Unknown fields and business-unit transfers are rejected; use transfer_deal.
        """
        return await crm.save(
            actor(),
            record_id.split(":")[0],
            changes,
            id=record_id,
            version=expected_version,
            key=idempotency_key,
        )

    @tool()
    async def create_task(data: Task, idempotency_key: str) -> dict:
        """Create a task with required deal and owner. due is an ISO date; primary marks the next step."""
        return await crm.save(actor(), "task", data.model_dump(mode="json"), key=idempotency_key)

    @tool()
    async def my_tasks(
        completed: bool = False, unit_id: str = "", offset: int = 0, limit: int = 50
    ) -> list[dict]:
        """Tasks assigned to the authenticated user, restricted to accessible deals."""
        return await crm.listing(
            actor(), "task", mine=True, done=completed, unit=unit_id, offset=offset, limit=limit
        )

    @tool()
    async def add_note(data: Note, idempotency_key: str) -> dict:
        """Add a Markdown note to the history, recording the user and client."""
        return await crm.save(actor(), "note", data.model_dump(mode="json"), key=idempotency_key)

    @tool()
    async def add_link(data: Attachment, idempotency_key: str) -> dict:
        """Add an HTTP/HTTPS link to a deal. Requires a URL; does not fetch it. Upload files with attachment_transfer. Mark older material with update_record(changes={"deprecated": true}); new uploads never supersede older ones automatically."""
        return await crm.save(actor(), "attachment", data.model_dump(mode="json"), key=idempotency_key)

    @tool()
    async def link_contact(deal_id: str, contact_id: str, remove: bool = False) -> dict:
        """Link or unlink an accessible contact without deleting the shared record."""
        return await crm.link(actor(), deal_id, contact_id, remove=remove)

    @tool()
    async def transfer_deal(
        deal_id: str, unit_id: str | None, owner_id: str, expected_version: int, idempotency_key: str
    ) -> dict:
        """Admin: transfer the business unit and reassign tasks whose owners would lose access."""
        return await crm.transfer(actor(), deal_id, unit_id, owner_id, expected_version, idempotency_key)

    @tool()
    async def pipeline_analytics(
        unit_id: str = "",
        source_id: str = "",
        created_from: str = "",
        created_to: str = "",
        include_archived: bool = True,
    ) -> dict:
        """Authorized pipeline metrics for a cohort selected by deal creation date (YYYY-MM-DD, Sao Paulo).
        Units, sources and outcomes use current values. Includes archives by default.
        Stage conversion counts distinct deals observed advancing to any later stage; Contract counts observed wins.
        Time per stage is median completed active visits with a known start; reentries are separate visits.
        Imported first-stage duration is unknown and excluded; partial current age is a lower bound.
        Open deals remain in conversion denominators. Win rate is won/(won+lost).
        """
        return await crm.analytics(
            actor(),
            unit=unit_id,
            source=source_id,
            created_from=created_from,
            created_to=created_to,
            include_archived=include_archived,
        )

    @tool()
    async def admin_save(
        entity: Literal["user", "unit", "source"],
        data: dict,
        idempotency_key: str,
        record_id: str | None = None,
        expected_version: int | None = None,
    ) -> dict:
        """Administer users, business units and sources. User: name,email,active,admin,units. Unit/Source: name,archived. Sources are shared; only admins manage them."""
        return await crm.save(
            actor(), entity, data, id=record_id, version=expected_version, key=idempotency_key
        )

    @tool()
    async def delete_attachment(attachment_id: str, expected_version: int, idempotency_key: str) -> dict:
        """Delete an accessible file or link, including stored bytes, while preserving the audit trail. Irreversible through the UI; use deprecated to retain material. Retrying the same key is safe. External websites are never deleted."""
        storage = LocalStorage(settings.storage_path, settings.max_upload_bytes)
        return await storage.delete(crm, actor(), attachment_id, expected_version, idempotency_key)

    @tool()
    async def attachment_transfer(deal_id: str, attachment_id: str | None = None) -> dict:
        """Create a deal-scoped temporary URL for multipart upload (file field) or download. Upload accepts optional title and description. Single-use upload with Idempotency-Key; no need to copy bearer tokens. Expires in five minutes and respects current permissions and revocation."""
        return await provider.file_ticket(get_access_token().token, deal_id, attachment_id)

    return mcp
