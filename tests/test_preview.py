import io
import re

from starlette.datastructures import UploadFile

from overdrive.service import Actor
from overdrive.storage import LocalStorage


async def test_authorized_preview_and_safe_markdown(system):
    app, client = system
    crm = app.state.crm
    storage = LocalStorage(app.state.settings.storage_path, app.state.settings.max_upload_bytes)
    actor = Actor("user:initial_admin")
    deal = await crm.save(actor, "deal", {"title": "Preview"})
    md = await storage.save(
        crm,
        actor,
        deal["id"],
        UploadFile(
            filename="brief.md",
            file=io.BytesIO(b"# Brief\n\n<script>alert(1)</script>\n\n[bad](javascript:alert(1))"),
        ),
    )
    pdf = await storage.save(
        crm, actor, deal["id"], UploadFile(filename="deck.pdf", file=io.BytesIO(b"%PDF-test"))
    )
    guest = await client.get(f"/materials/{md['id']}/preview")
    assert guest.status_code in {303, 401}
    login = await client.get("/login")
    csrf = re.search(r'name="csrf" value="([^"]+)"', login.text)[1]
    await client.post("/auth/dev", data={"csrf": csrf})
    response = await client.get(f"/materials/{md['id']}/preview")
    assert response.status_code == 200
    assert "<h1>Brief</h1>" in response.text
    assert "<script>" not in response.text and 'href="javascript:' not in response.text
    assert response.headers["x-frame-options"] == "SAMEORIGIN"
    response = await client.get(f"/files/{pdf['id']}?inline=true")
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"].startswith("inline")
    assert (await client.get(f"/files/{md['id']}?inline=true")).status_code == 415
    page = await client.get(f"/deals/{deal['id']}")
    assert page.status_code == 200
    assert 'class="activity-pane"' in page.text and 'class="materials-pane"' in page.text
