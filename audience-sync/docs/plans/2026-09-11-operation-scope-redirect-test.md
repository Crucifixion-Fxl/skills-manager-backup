# Operation scope and redirect regression

Clarify the existing operation inventory without changing it: thirteen Project
operations comprise ten query execution operations and three asset/sync task
reads. Own-key discovery is independent. README and the query reference link
these scopes to the full operation inventory.

Add an offline regression through a real client call and its installed urllib
opener. Only HTTPS transport is replaced with a synthetic redirect response.
For 301, 302, 303 and 307, assert the exact `redirect_rejected` error. For 308,
accept that code or `audience_sync_request_failed`: older urllib rejects 308 as
an HTTP error before redirect dispatch. Every case must expose only its safe
error code and make a single request to the source; a destination request fails
the test before any network activity. Keep runtime source, request schemas and
authorization rules unchanged. Register the test and this note in the source lock.
