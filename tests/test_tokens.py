import asyncio
import json
import subprocess
import sys
import time

import pytest
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from lumberroom_hermes.tokens import FenceTimeout, FileTokenStorage, RefreshFence, token_paths

URL = "http://fake.lumberroom.test/mcp"
METADATA = {"issuer": "http://fake.lumberroom.test", "authorization_endpoint": "http://fake.lumberroom.test/oauth/authorize",
            "token_endpoint": "http://fake.lumberroom.test/oauth/token", "response_types_supported": ["code"]}


def test_set_tokens_stamps_an_absolute_expiry(tmp_path):
    s = FileTokenStorage(tmp_path / "oauth.json", URL, clock=lambda: 1000.0)
    asyncio.run(s.set_tokens(OAuthToken(access_token="a", token_type="Bearer", expires_in=3600, refresh_token="r")))
    assert s.read().expires_at == 4600.0


def test_set_tokens_without_expires_in_records_no_expiry(tmp_path):
    s = FileTokenStorage(tmp_path / "oauth.json", URL, clock=lambda: 1000.0)
    asyncio.run(s.set_tokens(OAuthToken(access_token="a", token_type="Bearer", refresh_token="r")))
    assert s.read().expires_at is None


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes")
def test_the_token_file_is_private(tmp_path):
    s = FileTokenStorage(tmp_path / "oauth.json", URL)
    asyncio.run(s.set_tokens(OAuthToken(access_token="a", token_type="Bearer")))
    assert (tmp_path / "oauth.json").stat().st_mode & 0o777 == 0o600


def test_a_file_for_another_server_reads_as_empty(tmp_path):
    s = FileTokenStorage(tmp_path / "oauth.json", URL)
    asyncio.run(s.set_tokens(OAuthToken(access_token="a", token_type="Bearer")))
    other = FileTokenStorage(tmp_path / "oauth.json", "https://elsewhere.example/mcp")
    assert asyncio.run(other.get_tokens()) is None


def test_a_corrupt_file_reads_as_logged_out_and_stays_on_disk(tmp_path):
    (tmp_path / "oauth.json").write_text("{not json")
    s = FileTokenStorage(tmp_path / "oauth.json", URL)
    assert asyncio.run(s.get_tokens()) is None
    assert (tmp_path / "oauth.json").read_text() == "{not json"


def test_token_paths_live_under_the_profile(tmp_path):
    token, lock = token_paths(str(tmp_path))
    assert token == tmp_path / "lumberroom" / "oauth.json" and lock == tmp_path / "lumberroom" / "oauth.lock"


def test_tokens_round_trip_through_the_file(tmp_path):
    s = FileTokenStorage(tmp_path / "oauth.json", URL, clock=lambda: 1000.0)
    asyncio.run(s.set_tokens(OAuthToken(access_token="a", token_type="Bearer", expires_in=60, refresh_token="r", scope="mcp")))
    back = asyncio.run(FileTokenStorage(tmp_path / "oauth.json", URL).get_tokens())
    assert (back.access_token, back.refresh_token, back.scope) == ("a", "r", "mcp")


def test_client_info_round_trips_and_keeps_the_tokens(tmp_path):
    s = FileTokenStorage(tmp_path / "oauth.json", URL)
    asyncio.run(s.set_tokens(OAuthToken(access_token="a", token_type="Bearer")))
    info = OAuthClientInformationFull(client_id="c-1", redirect_uris=["http://127.0.0.1:47631/callback"],
                                      token_endpoint_auth_method="none")
    asyncio.run(s.set_client_info(info))
    assert asyncio.run(s.get_client_info()).client_id == "c-1"
    assert asyncio.run(s.get_tokens()).access_token == "a"


def test_save_metadata_keeps_the_tokens_and_reads_back(tmp_path):
    s = FileTokenStorage(tmp_path / "oauth.json", URL)
    asyncio.run(s.set_tokens(OAuthToken(access_token="a", token_type="Bearer")))
    s.save_metadata(METADATA)
    stored = s.read()
    assert stored.oauth_metadata["token_endpoint"] == METADATA["token_endpoint"]
    assert stored.tokens["access_token"] == "a"


def test_the_file_records_which_server_it_belongs_to(tmp_path):
    s = FileTokenStorage(tmp_path / "oauth.json", URL)
    asyncio.run(s.set_tokens(OAuthToken(access_token="a", token_type="Bearer")))
    on_disk = json.loads((tmp_path / "oauth.json").read_text())
    assert set(on_disk) == {"mcp_url", "tokens", "expires_at", "client_info", "oauth_metadata"}
    assert on_disk["mcp_url"] == URL


def test_a_write_for_this_server_replaces_another_servers_file(tmp_path):
    FileTokenStorage(tmp_path / "oauth.json", "https://elsewhere.example/mcp").save_metadata(METADATA)
    s = FileTokenStorage(tmp_path / "oauth.json", URL)
    asyncio.run(s.set_tokens(OAuthToken(access_token="a", token_type="Bearer")))
    assert s.read().oauth_metadata is None and s.read().tokens["access_token"] == "a"


def test_a_file_with_wrong_field_types_reads_as_empty(tmp_path):
    (tmp_path / "oauth.json").write_text(json.dumps({"mcp_url": URL, "tokens": "a", "expires_at": "soon",
                                                     "client_info": [1], "oauth_metadata": 3}))
    s = FileTokenStorage(tmp_path / "oauth.json", URL)
    assert s.read().tokens is None and s.read().expires_at is None and s.read().oauth_metadata is None
    assert asyncio.run(s.get_client_info()) is None


def test_a_token_object_the_sdk_cannot_parse_reads_as_logged_out(tmp_path):
    (tmp_path / "oauth.json").write_text(json.dumps({"mcp_url": URL, "tokens": {"token_type": "Bearer"}}))
    assert asyncio.run(FileTokenStorage(tmp_path / "oauth.json", URL).get_tokens()) is None


def test_the_write_leaves_no_temp_file_behind(tmp_path):
    s = FileTokenStorage(tmp_path / "oauth.json", URL)
    asyncio.run(s.set_tokens(OAuthToken(access_token="a", token_type="Bearer")))
    assert [p.name for p in tmp_path.iterdir()] == ["oauth.json"]


def test_the_first_write_creates_the_profile_directory(tmp_path):
    path, _ = token_paths(str(tmp_path))
    asyncio.run(FileTokenStorage(path, URL).set_tokens(OAuthToken(access_token="a", token_type="Bearer")))
    assert path.exists()


def test_mtime_is_zero_without_a_file_and_moves_on_write(tmp_path):
    s = FileTokenStorage(tmp_path / "oauth.json", URL)
    assert s.mtime_ns() == 0
    asyncio.run(s.set_tokens(OAuthToken(access_token="a", token_type="Bearer")))
    assert s.mtime_ns() > 0


def test_clear_reports_whether_a_file_existed(tmp_path):
    s = FileTokenStorage(tmp_path / "oauth.json", URL)
    assert s.clear() is False
    asyncio.run(s.set_tokens(OAuthToken(access_token="a", token_type="Bearer")))
    assert s.clear() is True and not (tmp_path / "oauth.json").exists()


# A child process loads the package by path, as the conftest does: spawn cannot import a test
# module that pytest loaded under a generated name.
CHILD = r"""
import asyncio, importlib.util, sys
from pathlib import Path
plugin = Path(sys.argv[1])
spec = importlib.util.spec_from_file_location("lumberroom_hermes", plugin / "__init__.py",
                                              submodule_search_locations=[str(plugin)])
module = importlib.util.module_from_spec(spec); sys.modules["lumberroom_hermes"] = module; spec.loader.exec_module(module)
from lumberroom_hermes.tokens import RefreshFence

async def run():
    async with RefreshFence(Path(sys.argv[2]), timeout_s=10):
        with open(sys.argv[3], "a") as f:
            f.write("start\n")
        await asyncio.sleep(float(sys.argv[4]))
        with open(sys.argv[3], "a") as f:
            f.write("end\n")

asyncio.run(run())
"""


def hold(tmp_path, seconds):
    from conftest import PLUGIN_DIR
    return subprocess.Popen([sys.executable, "-c", CHILD, str(PLUGIN_DIR), str(tmp_path / "oauth.lock"),
                             str(tmp_path / "log"), str(seconds)])


def wait_for_start(tmp_path):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if (tmp_path / "log").exists() and "start" in (tmp_path / "log").read_text():
            return
        time.sleep(0.02)
    raise AssertionError("the holder never took the fence")


def test_two_processes_take_the_fence_one_at_a_time(tmp_path):
    procs = [hold(tmp_path, 0.3), hold(tmp_path, 0.3)]
    for p in procs:
        assert p.wait(20) == 0
    assert (tmp_path / "log").read_text().split() == ["start", "end", "start", "end"]


def test_a_fence_held_past_the_timeout_raises_fence_timeout(tmp_path):
    holder = hold(tmp_path, 3)
    wait_for_start(tmp_path)

    async def wait_briefly():
        async with RefreshFence(tmp_path / "oauth.lock", timeout_s=0.5):
            pass
    with pytest.raises(FenceTimeout):
        asyncio.run(wait_briefly())
    holder.wait(10)


PROBE = r"""
import asyncio, importlib.util, sys
from pathlib import Path
plugin = Path(sys.argv[1])
spec = importlib.util.spec_from_file_location("lumberroom_hermes", plugin / "__init__.py",
                                              submodule_search_locations=[str(plugin)])
module = importlib.util.module_from_spec(spec); sys.modules["lumberroom_hermes"] = module; spec.loader.exec_module(module)
from lumberroom_hermes.tokens import FenceTimeout, RefreshFence

async def run():
    async with RefreshFence(Path(sys.argv[2]), timeout_s=0.5):
        pass

try:
    asyncio.run(run())
except FenceTimeout:
    sys.exit(3)
"""


def another_process_can_take_the_fence(tmp_path):
    # filelock lets a thread re-take a path it already holds, so only another process can see a
    # lock this process leaked.
    from conftest import PLUGIN_DIR
    probe = subprocess.run([sys.executable, "-c", PROBE, str(PLUGIN_DIR), str(tmp_path / "oauth.lock")], timeout=20)
    return probe.returncode == 0


# Both tests keep the fence object and the loop alive while the probe runs. Hermes's bridge loop
# lives for the whole process, and filelock releases a lock when its object is collected, so a
# test that lets either go would pass over a real leak.
def test_leaving_the_fence_frees_the_lock_for_other_processes(tmp_path):
    async def enter_and_leave():
        fence = RefreshFence(tmp_path / "oauth.lock", timeout_s=1)
        async with fence:
            pass
        assert await asyncio.to_thread(another_process_can_take_the_fence, tmp_path)
        return fence
    asyncio.run(enter_and_leave())


def test_a_caller_cancelled_while_waiting_does_not_leave_the_fence_held(tmp_path):
    holder = hold(tmp_path, 1.0)
    wait_for_start(tmp_path)

    async def give_up_early():
        fence = RefreshFence(tmp_path / "oauth.lock", timeout_s=5)
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(fence.__aenter__(), 0.2)
        # The abandoned acquire finishes on its worker thread once the holder exits.
        assert await asyncio.to_thread(holder.wait, 10) == 0
        await asyncio.sleep(0.3)
        assert await asyncio.to_thread(another_process_can_take_the_fence, tmp_path)
        return fence
    asyncio.run(give_up_early())
