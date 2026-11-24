"""The workspace's temp folder is deleted at quit and swept at start (#120).

Every test works in a tmp folder standing in for the temp folder (the
``root`` argument). Qt-free.
"""

import json
import os
import stat
import subprocess
import sys
import time

import pytest

import workspace_dir as wd

DAY = 24 * 60 * 60


def _link(target, link):
    """A junction on Windows (no special rights needed), a symlink elsewhere."""
    if sys.platform == "win32":
        import _winapi
        _winapi.CreateJunction(str(target), str(link))
    else:
        os.symlink(target, link, target_is_directory=True)


def _age(path, seconds):
    then = time.time() - seconds
    for p in [path, *(os.path.join(path, n) for n in os.listdir(path))]:
        os.utime(p, (then, then))


def _folder(root, name, owner=None, files=("work.pdf",)):
    path = root / name
    path.mkdir()
    for f in files:
        (path / f).write_bytes(b"%PDF-1.7\n")
    if owner is not None:
        (path / wd.OWNER_FILE).write_text(json.dumps(owner), encoding="utf-8")
    return path


def _dead_pid():
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def test_create_records_this_process(tmp_path):
    path = wd.create(str(tmp_path))
    assert os.path.basename(path).startswith(wd.PREFIX) and os.path.dirname(path) == str(tmp_path)
    owner = json.loads((tmp_path / os.path.basename(path) / wd.OWNER_FILE).read_text(encoding="utf-8"))
    assert owner["pid"] == os.getpid()
    assert owner["started"] == wd.process_state(os.getpid())[1]


def test_a_live_owners_folder_is_kept(tmp_path):
    path = wd.create(str(tmp_path))
    _age(path, 3 * DAY)
    assert wd.sweep_stale(str(tmp_path)) == []
    assert os.path.isdir(path)


def test_a_dead_owners_folder_is_removed(tmp_path):
    gone = _folder(tmp_path, wd.PREFIX + "gone", owner={"pid": _dead_pid(), "started": "1"})
    assert wd.sweep_stale(str(tmp_path)) == [str(gone)]
    assert not gone.exists()


def test_a_reused_process_id_is_not_the_owner(tmp_path):
    """This process's id, but another start time: the owner is gone."""
    reused = _folder(tmp_path, wd.PREFIX + "reused", owner={"pid": os.getpid(), "started": "0"})
    assert wd.sweep_stale(str(tmp_path)) == [str(reused)]


def test_an_ownerless_young_folder_is_kept_and_an_old_one_removed(tmp_path):
    young = _folder(tmp_path, wd.PREFIX + "young")
    old = _folder(tmp_path, wd.PREFIX + "old")
    _age(young, DAY - 600)
    _age(old, DAY + 600)
    assert wd.sweep_stale(str(tmp_path)) == [str(old)]
    assert young.exists() and not old.exists()


def test_other_names_are_untouched(tmp_path):
    other = _folder(tmp_path, "someone_elses_folder")
    _age(other, 30 * DAY)
    loose = tmp_path / (wd.PREFIX + "a_file.pdf")
    loose.write_bytes(b"x")
    os.utime(loose, (time.time() - 30 * DAY,) * 2)
    assert wd.sweep_stale(str(tmp_path)) == []
    assert other.exists() and loose.exists()


def test_a_link_named_like_a_workspace_is_not_followed(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.pdf").write_bytes(b"keep")
    root = tmp_path / "temp"
    root.mkdir()
    _link(outside, root / (wd.PREFIX + "link"))
    os.utime(outside, (time.time() - 30 * DAY,) * 2)
    wd.sweep_stale(str(root))
    assert (outside / "keep.pdf").exists()


def test_a_link_inside_a_stale_folder_is_removed_not_followed(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.pdf").write_bytes(b"keep")
    root = tmp_path / "temp"
    root.mkdir()
    stale = _folder(root, wd.PREFIX + "stale", owner={"pid": _dead_pid(), "started": "1"})
    _link(outside, stale / "into-outside")
    assert wd.sweep_stale(str(root)) == [str(stale)]
    assert not stale.exists()
    assert (outside / "keep.pdf").read_bytes() == b"keep"


def test_remove_deletes_its_own_folder_even_with_read_only_files(tmp_path):
    path = wd.create(str(tmp_path))
    locked = os.path.join(path, "working.pdf")
    with open(locked, "wb") as fh:
        fh.write(b"%PDF")
    os.chmod(locked, stat.S_IREAD)
    assert wd.remove(path, str(tmp_path)) is True
    assert not os.path.exists(path)


@pytest.mark.parametrize("where", ["outside", "nested", "unprefixed", "root"])
def test_remove_refuses_anything_but_a_workspace_folder_in_the_temp_folder(tmp_path, where):
    root = tmp_path / "temp"
    root.mkdir()
    (tmp_path / "elsewhere").mkdir()
    target = {
        "outside": tmp_path / "elsewhere" / (wd.PREFIX + "x"),
        "nested": root / (wd.PREFIX + "a") / (wd.PREFIX + "b"),
        "unprefixed": root / "documents",
        "root": root,
    }[where]
    target.mkdir(parents=True, exist_ok=True)
    (target / "keep.pdf").write_bytes(b"keep")
    with pytest.raises(ValueError):
        wd.remove(str(target), str(root))
    assert (target / "keep.pdf").exists()
