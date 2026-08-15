"""F-Sec-SSRFGuard — il guard sull'import skill da URL.

Verifica _ssrf_safe_host: blocca IP interni (loopback/private/link-local), lascia
passare i pubblici. IP letterali → getaddrinfo non fa DNS, niente rete.

    /opt/homebrew/opt/python@3.12/bin/python3.12 anja-hub/tests/test_ssrf_guard.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "webapp"))
import server  # noqa: E402

BLOCKED = [
    "http://127.0.0.1/x",                              # loopback
    "http://169.254.169.254/latest/meta-data/",        # link-local (cloud metadata)
    "http://10.0.0.5/skill.md",                        # private A
    "http://192.168.1.1/SKILL.md",                     # private C
    "http://172.16.0.1/x",                             # private B
    "http://[::1]/x",                                  # loopback v6
    "http://0.0.0.0/x",                                # unspecified
]
ALLOWED = [
    "https://8.8.8.8/SKILL.md",                        # pubblico
    "https://1.1.1.1/x",                               # pubblico
]


def main():
    for u in BLOCKED:
        err = server._ssrf_safe_host(u)
        assert err is not None, f"AVREBBE dovuto bloccare: {u}"
    print(f"✓ bloccati {len(BLOCKED)} URL interni (loopback/private/link-local/unspecified)")

    for u in ALLOWED:
        err = server._ssrf_safe_host(u)
        assert err is None, f"AVREBBE dovuto passare: {u} → {err}"
    print(f"✓ passati {len(ALLOWED)} URL pubblici")

    assert server._ssrf_safe_host("http:///nohost") is not None
    print("✓ URL senza host rifiutato")

    print("\nOK")


if __name__ == "__main__":
    main()
