"""System Info Skill — get system information."""

import os
import platform
import shutil
import subprocess
import sys


__all__ = [
    'get_cpu_info',
    'get_cwd',
    'get_disk_usage',
    'get_env_vars',
    'get_memory_info',
    'get_os_info',
    'get_python_info',
    'run',
    'run_command'
]

def get_os_info() -> dict:
    """Get OS information.

    Returns:
        Dict with system, release, version, machine, processor
    """
    return {
        "system": platform.system(),
        "release": platform.release(),
        "version": platform.version(),
        "machine": platform.machine(),
        "processor": platform.processor(),
    }


def get_python_info() -> dict:
    """Get Python information.

    Returns:
        Dict with version, executable, prefix, path
    """
    return {
        "version": sys.version,
        "executable": sys.executable,
        "prefix": sys.prefix,
        "path": sys.path[:3],  # First 3 entries
    }


def get_disk_usage(path: str = '/') -> dict:
    """Get disk usage for a path.

    Args:
        path: Path to check (default: '/')

    Returns:
        Dict with total_gb, used_gb, free_gb, percent_used
    """
    usage = shutil.disk_usage(path)
    return {
        "total_gb": usage.total / (1024**3),
        "used_gb": usage.used / (1024**3),
        "free_gb": usage.free / (1024**3),
        "percent_used": (usage.used / usage.total) * 100,
    }


def get_env_vars(prefix: str = None) -> dict:
    """Get environment variables, optionally filtered by prefix.

    Args:
        prefix: If provided, only return vars starting with this prefix.
                 Defaults to 'TAU_' to avoid leaking secrets.
    Returns:
        Dict of environment variables
    """
    if prefix:
        return {k: v for k, v in os.environ.items() if k.startswith(prefix)}
    return {k: v for k, v in os.environ.items() if k.startswith('TAU_')}


def get_cwd() -> str:
    """Get current working directory.

    Returns:
        Current working directory path
    """
    return os.getcwd()


def get_memory_info() -> dict:
    """Get memory information.

    Returns:
        Dict with total_mb, available_mb, percent_used (or error if unavailable)
    """
    try:
        with open("/proc/meminfo", "r") as f:
            meminfo = {}
            for line in f:
                parts = line.split()
                if len(parts) >= 2:
                    key = parts[0].rstrip(":")
                    meminfo[key] = int(parts[1])
            total_kb = meminfo.get("MemTotal", 0)
            available_kb = meminfo.get("MemAvailable", 0)
            return {
                "total_mb": total_kb / 1024,
                "available_mb": available_kb / 1024,
                "percent_used": ((total_kb - available_kb) / total_kb) * 100 if total_kb else 0,
            }
    except Exception as e:
        return {"total_mb": 0, "available_mb": 0, "error": str(e)}


def get_cpu_info() -> dict:
    """Get CPU information.

    Returns:
        Dict with cpu_count, cpu_name
    """
    return {
        "cpu_count": os.cpu_count() or 0,
        "cpu_name": platform.processor() or "unknown",
    }


def run_command(cmd: str) -> dict:
    """Run a shell command and return output.

    Args:
        cmd: Command to execute

    Returns:
        Dict with returncode, stdout, stderr
    """
    result = subprocess.run(cmd, shell=True, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)
    return {
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def run(action: str = "summary", **kwargs) -> dict:
    """Execute system info operation.

    Args:
        action: disk_usage, memory, cpu, env, run, summary
        path: Path for disk_usage (default '/')
        prefix: Prefix filter for env action
        cmd: Command for run action

    Returns:
        dict with 'success' and action-specific results
    """
    try:
        if action == "disk_usage":
            path = kwargs.get("path", "/")
            return {"success": True, **get_disk_usage(path)}

        elif action == "memory":
            return {"success": True, **get_memory_info()}

        elif action == "cpu":
            return {"success": True, **get_cpu_info()}

        elif action == "env":
            prefix = kwargs.get("prefix")
            env_vars = get_env_vars(prefix)
            return {"success": True, "count": len(env_vars), "vars": env_vars}

        elif action == "run":
            cmd = kwargs.get("cmd", "")
            if not cmd:
                return {"success": False, "error": "cmd is required for run action"}
            result = run_command(cmd)
            return {"success": result["returncode"] == 0, **result}

        elif action == "summary":
            os_info = get_os_info()
            py_info = get_python_info()
            return {
                "success": True,
                "platform": f"{os_info['system']} {os_info['release']}",
                "python_version": py_info["version"].split()[0],
                "cpu_count": os.cpu_count() or 0,
                "cwd": get_cwd(),
            }

        else:
            return {"success": False, "error": f"Unknown action: {action}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


# Skill metadata
__skill_name__ = "system_info"
__skill_description__ = "System information: OS, Python, disk, env"
__skill_commands__ = ["get_os_info", "get_python_info", "get_disk_usage", "get_env_vars", "get_cwd", "get_memory_info", "get_cpu_info", "run_command", "run"]
