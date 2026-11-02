# Public producer contract fixtures

These are the unmodified public Composition source objects from DEV/k8s, not
live claims or credentials:

- sql-only.json: prepared/cn-staging-shared-postgres-factory/composition-database.yaml
  at merged911798bd2f5f1e0bf40c87e82a74ae05c312e2ad.
- publication.json: the same path at merged71e85e0c9eee11026ff9724e335fcf224c77b286.
- purpose.json: the same path at merged067a0086aa365752674a8dd3b4182e849536d7f0.
- purpose-publication.json: the same path at mergedbb5ec5d0bcf8a0899434c4dd63922a433ba88802.

The profile reference records full source links and canonical spec hashes.
Parser tests construct explicitly synthetic revision metadata around these
specs. Their presence is not evidence that the newer source is active, that a
tenant or credentials exist, or that native TLS has passed runtime acceptance.
