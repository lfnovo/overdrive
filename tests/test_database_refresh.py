from surreal_basics import ConnectionManager, get_async_connection


async def test_refresh_does_not_send_stale_bearer(system):
    app, _ = system
    async with get_async_connection() as connection:
        connection.set_token("expired-token")
    # Force the library's proactive renewal path. It rebuilds the HTTP
    # connection when signin with a rejected token fails.
    ConnectionManager._http_async_token_exp = 0
    assert await app.state.db.rows("RETURN 1;") == 1
