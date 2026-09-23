Synthetic `buzz messages thread --channel <uuid> --event <root> --limit 500` output (Buzz CLI 0.5.23).
The tag layout is the recorded real one: a JSON array, root first, replies carry [e, root, "", "reply"], every event an auth tag, a root that mentions someone carries p tags. Ids, keys, signatures and text are made up.
Unknown event or another channel: exit 1 with {"error":"not_found"|"user_error",...} on stdout.
