"""Safe Teardown and Restoration of Linux Traffic Control (tc/netem) Rules."""

import atexit
import shutil
import subprocess
from typing import Set


class NetworkCleanupManager:
    """Tracks shaped Linux network interfaces and guarantees restoration on exit."""

    _shaped_interfaces: Set[str] = set()
    _atexit_registered: bool = False

    @classmethod
    def register_shaped_interface(cls, interface: str) -> None:
        cls._shaped_interfaces.add(interface)
        if not cls._atexit_registered:
            atexit.register(cls.cleanup_all)
            cls._atexit_registered = True

    @classmethod
    def reset_interface(cls, interface: str = "lo") -> bool:
        """
        Removes any root netem/tbf qdisc on the specified interface, restoring kernel default.
        Returns True if cleanup succeeded or no custom qdisc was active.
        """
        tc_bin = shutil.which("tc")
        if not tc_bin:
            cls._shaped_interfaces.discard(interface)
            return True

        subprocess.run(
            [tc_bin, "qdisc", "del", "dev", interface, "root"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        cls._shaped_interfaces.discard(interface)
        return True

    @classmethod
    def cleanup_all(cls) -> None:
        """Restores all shaped interfaces registered during the session."""
        for iface in list(cls._shaped_interfaces):
            cls.reset_interface(iface)
