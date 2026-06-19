"""Guard front-door product framing against owner-direction drift."""

from __future__ import annotations

from pathlib import Path


CHECKED_FILES = [
    Path("README.md"),
    Path("CLAUDE.md"),
    Path("pyproject.toml"),
]

FORBIDDEN_PHRASES = [
    "peer-to-peer crypto web",
    "p2p crypto web",
    "pay-token",
    "brand coin",
    "regional token",
    "erc20",
    "major blockchain",
    "major blockchains",
    "blockchain +",
    "blockchains",
    "on-chain",
    "chain id",
    "chainid",
    "mining",
    "miner",
    "premine",
]


def main() -> int:
    failures: list[str] = []
    for path in CHECKED_FILES:
        text = path.read_text(encoding="utf-8")
        lowered = text.lower()
        for phrase in FORBIDDEN_PHRASES:
            if phrase in lowered:
                failures.append(f"{path}: contains forbidden front-door phrase {phrase!r}")

    if failures:
        print("Owner direction guard failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("Owner direction guard ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
