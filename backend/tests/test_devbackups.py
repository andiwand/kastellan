from __future__ import annotations

import hashlib
import io
import os
import tarfile
from pathlib import Path

import pytest

from kastellan import devbackups
from kastellan.demo_more import UCI_WIRELESS

KEY = os.urandom(32)
SALT = b"salt"


def tar(files: dict[str, str]) -> bytes:
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:gz") as t:
        for path, text in files.items():
            data = text.encode()
            info = tarfile.TarInfo(path)
            info.size, info.mode, info.mtime = len(data), 0o600, 1_700_000_000
            t.addfile(info, io.BytesIO(data))
        link = tarfile.TarInfo("etc/rc.d/S10boot")
        link.type, link.linkname = tarfile.SYMTYPE, "../init.d/boot"
        t.addfile(link)
    return out.getvalue()


def files(channel: str = "6", key: str = "secret-one") -> dict[str, str]:
    return {
        "etc/config/wireless": UCI_WIRELESS.format(channel=channel, key=key),
        "etc/shadow": "root:$1$x$y:1::::::\n",
        "etc/config/system": "config system\n\toption hostname 'living'\n",
    }


def test_versions_dedup_and_diff(tmp_path: Path) -> None:
    store = devbackups.Store(tmp_path, lambda: KEY)
    first, new = store.add("ap", "openwrt-sysupgrade", devbackups.unpack_tar(tar(files())), keep=10)
    assert new
    again, new = store.add("ap", "openwrt-sysupgrade", devbackups.unpack_tar(tar(files())), keep=10)
    assert not new and again.id == first.id  # the same: no new version, only checked again
    second, new = store.add(
        "ap", "openwrt-sysupgrade", devbackups.unpack_tar(tar(files("11", "secret-two"))), 10
    )
    assert new and len(store.snapshots("ap")) == 2
    # Each content once, encrypted (no plain password in any stored file).
    stored = b"".join(p.read_bytes() for p in (tmp_path / "objects").rglob("*") if p.is_file())
    assert b"secret-one" not in stored and b"root:" not in stored
    d = store.diff("ap", first.id, second.id, SALT)
    assert d["added"] == [] and d["removed"] == []
    (wireless,) = d["changed"]
    text = "\n".join(wireless["diff"])
    assert "-\toption channel '6'" in text and "+\toption channel '11'" in text
    assert "secret-one" not in text and "secret-two" not in text
    assert "option key '[secret " in text  # changed, as fingerprints
    assert devbackups.secret_file("etc/shadow")  # never shown as text, only fingerprinted


def test_the_archive_comes_back_whole(tmp_path: Path) -> None:
    store = devbackups.Store(tmp_path, lambda: KEY)
    snap, _ = store.add("ap", "openwrt-sysupgrade", devbackups.unpack_tar(tar(files())), keep=10)
    name, data = store.archive("ap", snap.id)
    assert name.endswith(".tar.gz")
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as t:
        assert t.extractfile("etc/config/wireless").read().decode() == files()["etc/config/wireless"]
        assert t.getmember("etc/rc.d/S10boot").linkname == "../init.d/boot"


def test_old_versions_go_with_their_files(tmp_path: Path) -> None:
    store = devbackups.Store(tmp_path, lambda: KEY)
    for ch in ("1", "6", "11"):
        store.add("ap", "openwrt-sysupgrade", devbackups.unpack_tar(tar(files(ch))), keep=2)
    assert len(store.snapshots("ap")) == 2
    wanted = {f.sha for s in store.snapshots("ap") for f in s.files if f.sha}
    kept = {p.name for p in (tmp_path / "objects").rglob("*") if p.is_file()}
    assert kept == wanted


def test_changed_encrypted_values_are_kept_even_with_the_same_plain_settings(tmp_path: Path) -> None:
    store = devbackups.Store(tmp_path, lambda: KEY)

    def export(salt: str, ssid: str = "Castle") -> list:
        text = (
            f"**** FRITZ!Box 7590 CONFIGURATION EXPORT\nPassword={salt}\n**** CFGFILE:ar7.cfg\n"
            f'ssid = "{ssid}"; key = "{salt}9";\n**** END OF EXPORT {salt[-4:]}0000 ****\n'
        )
        sha = hashlib.sha256(text.encode()).hexdigest()
        same = "legacy-stable-hash"
        return [
            (devbackups.File("fritzbox.export", sha, len(text), "file", 0o600, 0, "", same), text.encode())
        ]

    a, _ = store.add("gw", "fritzbox-export", export("$$$$AAAA"), 10)
    b, new = store.add("gw", "fritzbox-export", export("$$$$BBBB"), 10)
    assert new and b.id != a.id
    assert store.archive("gw", b.id)[1] == export("$$$$BBBB")[0][1]
    c, new = store.add("gw", "fritzbox-export", export("$$$$CCCC", "Castle 2"), 10)
    assert new
    (changed,) = store.diff("gw", a.id, c.id, SALT)["changed"]
    assert any("Castle 2" in ln for ln in changed["diff"])
    assert not any("$$$$" in ln for ln in changed["diff"])


def test_a_wrong_key_or_a_damaged_file_is_refused(tmp_path: Path) -> None:
    store = devbackups.Store(tmp_path, lambda: KEY)
    snap, _ = store.add("ap", "openwrt-sysupgrade", devbackups.unpack_tar(tar(files())), keep=10)
    other = devbackups.Store(tmp_path, lambda: os.urandom(32))
    from cryptography.exceptions import InvalidTag

    with pytest.raises(InvalidTag):
        other.archive("ap", snap.id)
    with pytest.raises(ValueError):
        store.snapshots("../etc")


def test_masking() -> None:
    text = (
        "config x\n\toption password 'p4ss'\n\tlist key \"abc\"\n"
        "\toption name 'keep'\n\toption api_key 'zzz'\n"
    )
    masked = devbackups.mask("etc/config/wireless", text, SALT)
    assert "p4ss" not in masked and "abc" not in masked and "zzz" not in masked
    assert "option name 'keep'" in masked
    pem = "-----BEGIN PRIVATE KEY-----\nMIIE\n-----END PRIVATE KEY-----"
    assert "MIIE" not in devbackups.mask("x.pem", pem, SALT)


@pytest.mark.parametrize("name", ["../etc/shadow", "../../etc/config/system", "/etc/shadow", "etc/../shadow"])
def test_unsafe_backup_paths_are_refused(name: str) -> None:
    with pytest.raises(ValueError, match="unsafe path"):
        devbackups.unpack_tar(tar({name: "secret"}))


def test_backup_expansion_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(devbackups, "MAX_ARCHIVE", 1024)
    with pytest.raises(ValueError, match="too large"):
        devbackups.unpack_tar(tar({"etc/config/system": "x" * 4096}))


def test_dotfiles_ownership_and_hardlinks_survive(tmp_path: Path) -> None:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as archive:
        f = tarfile.TarInfo("./.hidden")
        f.size, f.uid, f.gid, f.mode = 4, 123, 456, 0o640
        archive.addfile(f, io.BytesIO(b"data"))
        link = tarfile.TarInfo("etc/link")
        link.type, link.linkname = tarfile.LNKTYPE, ".hidden"
        archive.addfile(link)
    store = devbackups.Store(tmp_path, lambda: KEY)
    snap, _ = store.add("ap", "openwrt-sysupgrade", devbackups.unpack_tar(buf.getvalue()), 10)
    with tarfile.open(fileobj=io.BytesIO(store.archive("ap", snap.id)[1]), mode="r:gz") as archive:
        f = archive.getmember(".hidden")
        assert (f.uid, f.gid, f.mode) == (123, 456, 0o640)
        assert archive.extractfile("etc/link").read() == b"data"


def test_quoted_and_multiline_credentials_stay_hidden() -> None:
    for text in ("option 'key' 'private-value' # comment", "option key 'private-\nvalue'"):
        masked = devbackups.mask("etc/config/wireless", text, SALT)
        assert "private-" not in masked and "value" not in masked
    assert "credential" not in devbackups.mask("etc/custom.conf", "credential", SALT)


async def test_fritz_backup_keeps_original_bytes_and_restore_password(tmp_path, monkeypatch) -> None:
    import asyncio
    import zipfile
    from types import SimpleNamespace

    from kastellan.fritzbox import client as fb

    raw = b'**** CONFIGURATION EXPORT\nname="K\xe4stchen";\n'

    async def export(box, password):
        assert password == "restore-password"
        return raw

    monkeypatch.setattr(fb, "config_export", export)
    hub = SimpleNamespace(
        config=SimpleNamespace(devices={"gw": SimpleNamespace(openwrt=None, fritzbox={})}),
        fritz=lambda _: SimpleNamespace(confirming=asyncio.Lock()),
        own_secret=lambda _: "restore-password",
    )
    store = devbackups.Store(tmp_path, lambda: KEY)
    snap, _ = store.add("gw", "fritzbox-export", await devbackups.fetch(hub, "gw"), 10)
    name, data = store.archive("gw", snap.id)
    assert name.endswith(".zip")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert archive.read(next(n for n in archive.namelist() if n.endswith(".export"))) == raw
        assert archive.read("export-password.txt") == b"restore-password"
    assert devbackups.secret_file("export-password.txt")


def test_metadata_changes_and_reduced_retention(tmp_path: Path) -> None:
    store = devbackups.Store(tmp_path, lambda: KEY)
    members = devbackups.unpack_tar(tar(files()))
    first, _ = store.add("ap", "openwrt-sysupgrade", members, keep=10)
    members[-1][0].link = "../init.d/other"
    second, new = store.add("ap", "openwrt-sysupgrade", members, keep=10)
    assert new
    assert store.diff("ap", first.id, second.id, SALT)["changed"][0]["path"] == "etc/rc.d/S10boot"
    same, new = store.add("ap", "openwrt-sysupgrade", members, keep=1)
    assert not new and same.id == second.id
    assert [s.id for s in store.snapshots("ap")] == [second.id]
