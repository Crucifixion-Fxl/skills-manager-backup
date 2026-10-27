# Cross-project Issue link fixture

This is an offline fixture. Its URLs are identifiers only; do not access the network.

- Project ID `201`, path `group-a/project-a`, Issue IID `42`, title `Account recovery`, exact `web_url`: `https://gitlab.addx.ai/group-a/project-a/-/issues/42`.
- Project ID `202`, path `group-b/project-b`, Issue IID `42`, title `Device recovery`, exact `web_url`: `https://gitlab.addx.ai/group-b/project-b/-/issues/42`.
- Project ID `203`, path `group-c/project-c`, Issue IID `77`, capability name `Offline recovery`; the Issue object is available but `web_url 缺失`. Do not construct or guess a URL.

The report must distinguish objects by full project identity. Every displayed Issue ID must link to its own exact `web_url`. For Offline recovery, omit the ID and write `Issue 链接缺失` under coverage gaps.
