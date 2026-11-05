import hashlib
import io
import json

import pytest

from pixelpanel import updater


def test_versions_compare_numerically():
    assert updater.parse_version("v1.2.10") > updater.parse_version("v1.2.9")
    assert updater.parse_version("1.3.0") > updater.parse_version("v1.2.99")
    assert updater.parse_version("v1.2.7") == (1, 2, 7)
    assert updater.parse_version("nonsense") == (0,)
    assert updater.parse_version("v1.4.0.1") > updater.parse_version("1.4.0.0")
    assert updater.parse_version("v1.4.0.0") > updater.parse_version("1.3.0")


def _api(tag, data=b"new exe", digest=True, name="PixelPanel.exe"):
    asset = {"name": name, "size": len(data), "browser_download_url": f"https://github.com/x/releases/download/{tag}/{name}"}
    if digest:
        asset["digest"] = "sha256:" + hashlib.sha256(data).hexdigest()
    return {"tag_name": tag, "html_url": f"https://github.com/x/releases/tag/{tag}", "assets": [asset]}


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def fake_urlopen(routes):
    def opener(req, timeout=0):
        url = req.full_url if hasattr(req, "full_url") else req
        return FakeResponse(routes[url])
    return opener


def test_check_finds_only_newer_releases(monkeypatch):
    monkeypatch.setattr(updater.urllib.request, "urlopen",
                        fake_urlopen({updater.LATEST_URL: json.dumps(_api("v1.3.0")).encode()}))
    rel = updater.check("1.2.7")
    assert rel is not None and rel.version == (1, 3, 0) and rel.size == len(b"new exe")
    assert updater.check("1.3.0") is None
    assert updater.check("1.4.0") is None


def test_check_ignores_releases_without_the_exe(monkeypatch):
    monkeypatch.setattr(updater.urllib.request, "urlopen",
                        fake_urlopen({updater.LATEST_URL: json.dumps(_api("v9.0.0", name="notes.txt")).encode()}))
    assert updater.check("1.0.0") is None


def test_download_verifies_the_checksum(tmp_path, monkeypatch):
    rel_ok = updater.Release.from_api(_api("v1.3.0", b"good"))
    monkeypatch.setattr(updater.urllib.request, "urlopen", fake_urlopen({rel_ok.url: b"good"}))
    path = updater.download(rel_ok, tmp_path)
    assert path.read_bytes() == b"good"

    rel_bad = updater.Release.from_api(_api("v1.3.0", b"good"))
    monkeypatch.setattr(updater.urllib.request, "urlopen", fake_urlopen({rel_bad.url: b"evil"}))
    with pytest.raises(updater.UpdateError):
        updater.download(rel_bad, tmp_path / "b")
    assert not (tmp_path / "b" / "PixelPanel.exe.download").exists()


def test_swap_renames_the_running_exe_and_puts_the_new_one_in_place(tmp_path):
    exe = tmp_path / "PixelPanel.exe"
    exe.write_bytes(b"old")
    new = tmp_path / "PixelPanel.exe.download"
    new.write_bytes(b"new")
    updater.swap(new, exe)
    assert exe.read_bytes() == b"new"
    assert (tmp_path / "PixelPanel.exe.old").read_bytes() == b"old"
    updater.cleanup(exe)
    assert not (tmp_path / "PixelPanel.exe.old").exists()


def test_restarted_exe_does_not_inherit_the_old_pyinstaller_environment(monkeypatch):
    """Otherwise the new exe reuses the old one's unpack folder, which is deleted on exit -> PIL crash."""
    monkeypatch.setenv("_PYI_APPLICATION_HOME_DIR", r"C:\Temp\_MEI123")
    monkeypatch.setenv("_PYI_PARENT_PROCESS_LEVEL", "1")
    monkeypatch.setenv("_MEIPASS2", r"C:\Temp\_MEI123")
    env = updater.restart_env()
    assert env["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
    assert not any(k.startswith(("_PYI_", "_MEI")) for k in env)
    assert env.get("PATH")  # everything else is kept


def test_swap_rolls_back_when_the_new_file_cannot_be_moved(tmp_path):
    exe = tmp_path / "PixelPanel.exe"
    exe.write_bytes(b"old")
    with pytest.raises(updater.UpdateError):
        updater.swap(tmp_path / "missing.download", exe)
    assert exe.read_bytes() == b"old"  # the app is still there and startable
