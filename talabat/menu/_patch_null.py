#!/usr/bin/env python3
"""One-shot script to fix the null byte in clean_menu_data.py."""
path = r"C:\Users\SagarSingh\OneDrive - Mordor Intelligence\Desktop\Webscraping-Production-Projects\talabat\menu\clean_menu_data.py"
with open(path, "rb") as f:
    content = f.read()

before = content.count(b"\x00")
# Replace literal null byte in the regex pattern with \x01 (skip NUL)
content = content.replace(b"\x00", b"")  # just remove null bytes entirely
after = content.count(b"\x00")
print(f"Null bytes: {before} -> {after}")

with open(path, "wb") as f:
    f.write(content)
print("Done")
