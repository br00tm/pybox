"""CLONE_NEW* flag constants for Linux namespaces.

These values are defined in <linux/sched.h> and are stable across kernel versions
on x86-64. They are passed to clone(2) and unshare(2) syscalls.
"""

from __future__ import annotations

# Isolate filesystem mount tree — containers get their own mount namespace
CLONE_NEWNS: int = 0x00020000

# Isolate UTS namespace (hostname, domainname)
CLONE_NEWUTS: int = 0x04000000

# Isolate IPC namespace (SysV semaphores, message queues, shared memory)
CLONE_NEWIPC: int = 0x08000000

# Isolate user/group ID namespace — enables rootless containers
CLONE_NEWUSER: int = 0x10000000

# Isolate PID namespace — container processes see only their own PIDs
CLONE_NEWPID: int = 0x20000000

# Isolate network namespace (interfaces, routing table, netfilter)
CLONE_NEWNET: int = 0x40000000

# Isolate cgroup namespace — container sees its own cgroup hierarchy root
CLONE_NEWCGROUP: int = 0x02000000

# Composite flag: all namespaces used by a standard PyBox container
CONTAINER_CLONE_FLAGS: int = (
    CLONE_NEWPID
    | CLONE_NEWNS
    | CLONE_NEWNET
    | CLONE_NEWUTS
    | CLONE_NEWIPC
    | CLONE_NEWUSER
)

# x86-64 syscall number for pivot_root(2)
SYS_PIVOT_ROOT: int = 155
