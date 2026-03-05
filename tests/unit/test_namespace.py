"""Unit tests for pybox.namespace modules.

These tests do NOT require root — all syscall interactions are mocked.
"""

from __future__ import annotations

import ctypes
from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch

import pytest

from pybox.exceptions import NamespaceError
from pybox.namespace import constants


# ------------------------------------------------------------------
# constants.py
# ------------------------------------------------------------------

class TestNamespaceConstants:
    def test_clone_newpid_value(self) -> None:
        assert constants.CLONE_NEWPID == 0x20000000

    def test_clone_newns_value(self) -> None:
        assert constants.CLONE_NEWNS == 0x00020000

    def test_clone_newnet_value(self) -> None:
        assert constants.CLONE_NEWNET == 0x40000000

    def test_clone_newuts_value(self) -> None:
        assert constants.CLONE_NEWUTS == 0x04000000

    def test_clone_newipc_value(self) -> None:
        assert constants.CLONE_NEWIPC == 0x08000000

    def test_clone_newuser_value(self) -> None:
        assert constants.CLONE_NEWUSER == 0x10000000

    def test_clone_newcgroup_value(self) -> None:
        assert constants.CLONE_NEWCGROUP == 0x02000000

    def test_container_clone_flags_combines_all(self) -> None:
        expected = (
            constants.CLONE_NEWPID
            | constants.CLONE_NEWNS
            | constants.CLONE_NEWNET
            | constants.CLONE_NEWUTS
            | constants.CLONE_NEWIPC
            | constants.CLONE_NEWUSER
        )
        assert constants.CONTAINER_CLONE_FLAGS == expected

    def test_sys_pivot_root_x86_64(self) -> None:
        assert constants.SYS_PIVOT_ROOT == 155


# ------------------------------------------------------------------
# unshare.py
# ------------------------------------------------------------------

class TestUnshare:
    def test_unshare_calls_libc_with_flags(self) -> None:
        from pybox.namespace import unshare as unshare_mod

        mock_libc = MagicMock()
        mock_libc.unshare.return_value = 0  # success

        with patch.object(unshare_mod, "_libc", mock_libc):
            unshare_mod.unshare(constants.CLONE_NEWPID)

        mock_libc.unshare.assert_called_once_with(constants.CLONE_NEWPID)

    def test_unshare_raises_on_failure(self) -> None:
        from pybox.namespace import unshare as unshare_mod

        mock_libc = MagicMock()
        mock_libc.unshare.return_value = -1

        with patch.object(unshare_mod, "_libc", mock_libc):
            with patch("ctypes.get_errno", return_value=1):  # EPERM
                with pytest.raises(NamespaceError, match="unshare"):
                    unshare_mod.unshare(constants.CLONE_NEWPID)

    def test_sethostname_calls_libc(self) -> None:
        from pybox.namespace import unshare as unshare_mod

        mock_libc = MagicMock()
        mock_libc.sethostname.return_value = 0

        with patch.object(unshare_mod, "_libc", mock_libc):
            unshare_mod.sethostname("mycontainer")

        mock_libc.sethostname.assert_called_once()
        call_args = mock_libc.sethostname.call_args[0]
        assert call_args[0] == b"mycontainer"
        assert call_args[1] == len("mycontainer")

    def test_sethostname_raises_on_failure(self) -> None:
        from pybox.namespace import unshare as unshare_mod

        mock_libc = MagicMock()
        mock_libc.sethostname.return_value = -1

        with patch.object(unshare_mod, "_libc", mock_libc):
            with patch("ctypes.get_errno", return_value=1):
                with pytest.raises(NamespaceError, match="sethostname"):
                    unshare_mod.sethostname("test")


# ------------------------------------------------------------------
# pivot_root.py
# ------------------------------------------------------------------

class TestPivotRoot:
    def test_pivot_root_uses_syscall_155(self) -> None:
        from pybox.namespace import pivot_root as pr_mod
        from pybox.namespace.constants import SYS_PIVOT_ROOT

        mock_libc = MagicMock()
        mock_libc.syscall.return_value = 0

        with patch.object(pr_mod, "_libc", mock_libc):
            pr_mod.pivot_root("/new_root", "/new_root/.old_root")

        args = mock_libc.syscall.call_args[0]
        # First arg must be syscall number 155 (pivot_root on x86-64)
        assert args[0] == SYS_PIVOT_ROOT
        assert args[1] == b"/new_root"
        assert args[2] == b"/new_root/.old_root"

    def test_pivot_root_raises_on_failure(self) -> None:
        from pybox.namespace import pivot_root as pr_mod

        mock_libc = MagicMock()
        mock_libc.syscall.return_value = -1

        with patch.object(pr_mod, "_libc", mock_libc):
            with patch("ctypes.get_errno", return_value=22):  # EINVAL
                with pytest.raises(NamespaceError, match="pivot_root"):
                    pr_mod.pivot_root("/new_root", "/new_root/.old_root")


# ------------------------------------------------------------------
# user_map.py
# ------------------------------------------------------------------

class TestUserMap:
    def test_idmapping_str_format(self) -> None:
        from pybox.namespace.user_map import IDMapping

        m = IDMapping(container_id=0, host_id=1000, count=1)
        assert str(m) == "0 1000 1"

    def test_write_uid_map_correct_format(self, tmp_path: Path) -> None:
        from pybox.namespace.user_map import IDMapping, write_uid_map

        uid_map_file = tmp_path / "uid_map"
        uid_map_file.write_text("")  # create file

        mappings = [IDMapping(container_id=0, host_id=1000, count=1)]
        with patch("builtins.open", mock_open()) as m:
            write_uid_map(12345, mappings)
        # Check the path written to
        m.assert_called_once_with("/proc/12345/uid_map", "w")
        handle = m()
        handle.write.assert_called_once_with("0 1000 1\n")

    def test_write_gid_map_denies_setgroups_first(self) -> None:
        from pybox.namespace.user_map import IDMapping, write_gid_map

        mappings = [IDMapping(container_id=0, host_id=1000, count=1)]
        calls: list[str] = []

        def fake_open(path: str, mode: str) -> MagicMock:
            calls.append(path)
            m = MagicMock()
            m.__enter__ = lambda s: s
            m.__exit__ = MagicMock(return_value=False)
            m.write = MagicMock()
            return m

        with patch("builtins.open", side_effect=fake_open):
            write_gid_map(12345, mappings)

        # setgroups must be written before gid_map
        assert calls[0] == "/proc/12345/setgroups"
        assert calls[1] == "/proc/12345/gid_map"

    def test_setup_rootless_id_maps_uses_current_uid(self) -> None:
        from pybox.namespace.user_map import setup_rootless_id_maps

        with patch("pybox.namespace.user_map.write_uid_map") as mock_uid, \
             patch("pybox.namespace.user_map.write_gid_map") as mock_gid, \
             patch("os.getuid", return_value=1001), \
             patch("os.getgid", return_value=1001):
            setup_rootless_id_maps(9999)

        # UID mapping should map container 0 → host 1001
        uid_mappings = mock_uid.call_args[0][1]
        assert uid_mappings[0].container_id == 0
        assert uid_mappings[0].host_id == 1001
        assert uid_mappings[0].count == 1
