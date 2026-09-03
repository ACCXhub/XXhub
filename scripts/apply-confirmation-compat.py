from pathlib import Path

# Temporary branch-only patcher. The canonical result is committed into src/autody/chat.py.
path = Path("src/autody/chat.py")
data = path.read_bytes()
newline = "\r\n" if b"\r\n" in data else "\n"

replacements = [
    (
        "        pre_send_match_count: int,\n",
        "        pre_send_match_count: int | None = None,\n",
    ),
    (
        '''            matching_count_observed = (\n                self._latest_matches(message)\n                and self._matching_outgoing_count(message) > pre_send_match_count\n            )\n''',
        '''            matching_count_observed = (\n                pre_send_match_count is not None\n                and self._latest_matches(message)\n                and self._matching_outgoing_count(message) > pre_send_match_count\n            )\n''',
    ),
]

changed = False
for old, new in replacements:
    old_bytes = old.replace("\n", newline).encode("utf-8")
    new_bytes = new.replace("\n", newline).encode("utf-8")
    old_count = data.count(old_bytes)
    new_count = data.count(new_bytes)
    if old_count == 1 and new_count == 0:
        data = data.replace(old_bytes, new_bytes, 1)
        changed = True
    elif old_count == 0 and new_count == 1:
        continue
    else:
        raise SystemExit(
            f"unexpected patch shape: old={old_count}, new={new_count}"
        )

if changed:
    path.write_bytes(data)
    print("confirmation compatibility patch applied")
else:
    print("confirmation compatibility patch already present")
