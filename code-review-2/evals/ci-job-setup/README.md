# CI Job setup evaluation

Source issue: `engineering/ci-templates#158`.

These five cases are review behavior fixtures, not evidence that a particular template SHA or model has passed. Evaluate the fixed request/result identities and the reviewer conclusion together. The static projection never substitutes for a matching Job trace, image inventory, or other independent runtime evidence.

The candidate-template Golf smoke observed during integration found the literal install at line 771 through its alias at line 805, while retaining `inputs_unresolved`, `shell_execution_unresolved`, and `image_and_runtime_dependencies_unverified`. This is parser-only acceptance; it does not claim a model review rerun or runtime dependency success.
