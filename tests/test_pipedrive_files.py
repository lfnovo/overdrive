import httpx
import pytest

from scripts.import_pipedrive_files import download


async def test_download_does_not_forward_token_to_storage(tmp_path):
    def handle(request):
        if request.url.host == "api.pipedrive.com":
            assert request.headers["x-api-token"] == "test-token"
            return httpx.Response(302, headers={"location": "https://files.s3.amazonaws.com/file.pdf"})
        assert "x-api-token" not in request.headers
        return httpx.Response(200, content=b"%PDF-test")

    target = tmp_path / "file.pdf"
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        await download(client, "test-token", {"id": 4}, target, 100)
    assert target.read_bytes() == b"%PDF-test"


async def test_download_rejects_untrusted_redirect_and_oversize(tmp_path):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(302, headers={"location": "http://localhost/private"})
        )
    ) as client:
        with pytest.raises(ValueError, match="Unexpected download destination"):
            await download(client, "test-token", {"id": 4}, tmp_path / "bad", 100)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"oversize"))
    ) as client:
        with pytest.raises(ValueError, match="exceeds upload limit"):
            await download(client, "test-token", {"id": 4}, tmp_path / "large", 2)
