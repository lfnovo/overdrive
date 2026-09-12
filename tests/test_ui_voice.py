from overdrive.voice import COPY, session_voice


def test_voice_is_stable_and_rotates_without_repeating():
    session = {}
    first = session_voice(session, timestamp=100)
    assert first == session_voice(session, timestamp=200)
    second = session_voice(session, timestamp=21700)
    assert all(second[k] != first[k] for k in COPY)
    assert session_voice(session, timestamp=21800) == second
    session["voice"]["deals"] = 999
    assert session_voice(session, timestamp=21800)["deals"] in COPY["deals"]


async def test_english_shell_and_forms_preserve_user_content(system):
    import re

    from overdrive.service import Actor

    app, c = system
    login = await c.get("/login")
    assert '<html lang="en">' in login.text
    assert "Continue as Administrator" in login.text
    csrf = re.search(r'name="csrf" value="([^"]+)"', login.text)[1]
    await c.post("/auth/dev", data={"csrf": csrf})
    d = await app.state.crm.save(
        Actor("user:initial_admin"), "deal", {"title": "Diagnóstico — conteúdo original"}
    )
    for url, expected in [
        ("/deals", "New deal"),
        ("/tasks", "My tasks"),
        ("/directory/contact", "Contacts"),
        ("/directory/organization", "Organizations"),
        ("/settings", "Settings"),
        ("/new/deal", "Total value (BRL)"),
        ("/new/task?deal=" + d["id"], "Due date"),
        ("/deals/" + d["id"], "Diagnóstico — conteúdo original"),
        ("/deals/" + d["id"] + "?tab=files", "Files & links"),
    ]:
        r = await c.get(url)
        assert r.status_code == 200 and expected in r.text, (url, r.text)
        assert 'class="masthead"' in r.text and "/static/pitlane.css?v=" in r.text
        assert "Configurações" not in r.text and "Minhas tarefas" not in r.text
    error = await c.get("/api/deal/deal:missing")
    assert error.status_code == 404 and error.json()["message"] == "Resource not found."
