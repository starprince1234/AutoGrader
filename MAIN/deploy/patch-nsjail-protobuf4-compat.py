#!/usr/bin/env python3
"""Backport nsjail 3.5's protobuf 4 log-handler guard onto pinned nsjail 3.4."""

import sys
from pathlib import Path


def replace_once(source: str, old: str, new: str, label: str) -> str:
    count = source.count(old)
    if count != 1:
        raise SystemExit(f"expected one {label} in pinned nsjail source, found {count}")
    return source.replace(old, new, 1)


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: patch-nsjail-protobuf4-compat.py <nsjail-config.cc>")

    config_path = Path(sys.argv[1])
    source = config_path.read_text(encoding="utf-8")
    source = replace_once(
        source,
        "static void logHandler(\n"
        "    google::protobuf::LogLevel level, const char* filename, int line, const std::string& message) {\n"
        "\tLOG_W(\"config.cc: '%s'\", message.c_str());\n"
        "}\n",
        "#if defined(GOOGLE_PROTOBUF_VERSION) && GOOGLE_PROTOBUF_VERSION < 4000000\n"
        "#define NSJAIL_HAS_PROTOBUF_LOG_HANDLER 1\n"
        "#else\n"
        "#define NSJAIL_HAS_PROTOBUF_LOG_HANDLER 0\n"
        "#endif\n"
        "\n"
        "#if NSJAIL_HAS_PROTOBUF_LOG_HANDLER\n"
        "static void logHandler(\n"
        "    google::protobuf::LogLevel level, const char* filename, int line, const std::string& message) {\n"
        "\tLOG_W(\"config.cc: '%s'\", message.c_str());\n"
        "}\n"
        "#endif /* NSJAIL_HAS_PROTOBUF_LOG_HANDLER */\n",
        "protobuf log handler",
    )
    source = replace_once(
        source,
        "\tSetLogHandler(logHandler);\n",
        "#if NSJAIL_HAS_PROTOBUF_LOG_HANDLER\n"
        "\tSetLogHandler(logHandler);\n"
        "#endif /* NSJAIL_HAS_PROTOBUF_LOG_HANDLER */\n",
        "protobuf log handler registration",
    )
    config_path.write_text(source, encoding="utf-8")


if __name__ == "__main__":
    main()
