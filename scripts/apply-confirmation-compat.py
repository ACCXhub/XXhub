from pathlib import Path

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

for old, new in replacements:
    old_bytes = old.replace("\n", newline).encode("utf-8")
    new_bytes = new.replace("\n", newline).encode("utf-8")
    count = data.count(old_bytes)
    if count != 1:
        raise SystemExit(f"expected exactly one replacement target, found {count}")
    data = data.replace(old_bytes, new_bytes, 1)

path.write_bytes(data)
print("confirmation compatibility patch applied")
